"""M7 补证裁决：持久化、并发裁决与小时桶回读。

关键约束：
- 原批次快照（含隔离原行）只写一次，后续补证只追加候选/裁决，不覆盖原行。
- 每个原行带版本号；裁决必须携带期望版本，版本不符按 STALE_VERSION 拒绝。
- 一次裁决的元数据与它对桶的贡献在同一事务内提交；重试命中同版本同候选时
  只回放已有结果，不再产生第二份贡献。
- 撤销裁决回冲该次采纳自己的贡献行，不影响同一原行的其他采纳。
- ``resume`` 以已提交的裁决为位置，按贡献行做幂等补账。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.errors import DomainError
from . import core

ACTIVE = "APPLIED"
REVOKED = "REVOKED"

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_batches (
    batch_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE,
    rule_version TEXT NOT NULL,
    input_count INTEGER NOT NULL,
    valid_count INTEGER NOT NULL,
    quarantine_count INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_originals (
    original_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    request_id TEXT,
    reason_code TEXT NOT NULL,
    raw_value TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE (batch_id, source_line)
);
CREATE TABLE IF NOT EXISTS evidence_candidates (
    candidate_id TEXT PRIMARY KEY,
    original_id TEXT NOT NULL,
    source TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    signature TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    service TEXT NOT NULL,
    status INTEGER NOT NULL,
    actor TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (original_id, source, content_digest)
);
CREATE TABLE IF NOT EXISTS evidence_decisions (
    decision_id TEXT PRIMARY KEY,
    original_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    action TEXT NOT NULL,
    candidate_id TEXT,
    reverses_decision_id TEXT,
    reason TEXT NOT NULL,
    actor TEXT,
    expected_version INTEGER NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE (original_id, version)
);
CREATE TABLE IF NOT EXISTS evidence_contributions (
    contribution_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    original_id TEXT NOT NULL,
    hour TEXT NOT NULL,
    service TEXT NOT NULL,
    count_delta INTEGER NOT NULL,
    errors_delta INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS evidence_one_active_contribution
    ON evidence_contributions (original_id) WHERE active = 1;
"""


def _rollback(db: sqlite3.Connection) -> None:
    try:
        db.execute("ROLLBACK")
    except sqlite3.Error:
        # SQLite 已自动回滚时无需再次回滚。
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class EvidenceStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.session() as db:
            db.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def session(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    # ---------------------------------------------------------------- intake
    def intake(self, text: str | None, sample: str | None, rule_version: str) -> dict:
        source_text = text if text is not None else core.load_sample_text(sample or "dirty")
        parsed = core.parse_intake(source_text, rule_version)
        now = _now()

        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT batch_id FROM evidence_batches WHERE digest=?", (parsed["digest"],)
                ).fetchone()
                if existing:
                    db.execute("COMMIT")
                    return self._batch_view(existing["batch_id"], replayed=True)
                db.execute(
                    "INSERT INTO evidence_batches VALUES (?,?,?,?,?,?,?,?)",
                    (
                        parsed["batch_id"],
                        parsed["digest"],
                        parsed["rule_version"],
                        parsed["input_count"],
                        parsed["valid_count"],
                        parsed["quarantine_count"],
                        json.dumps(parsed["snapshot"], ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
                for item in parsed["snapshot"]["quarantine"]:
                    db.execute(
                        "INSERT INTO evidence_originals VALUES (?,?,?,?,?,?,?,?)",
                        (
                            core.original_id(parsed["batch_id"], item["source_line"]),
                            parsed["batch_id"],
                            item["source_line"],
                            item["request_id"],
                            item["reason_code"],
                            item["raw_value"],
                            0,
                            now,
                        ),
                    )
                db.execute("COMMIT")
            except Exception:
                _rollback(db)
                raise
        return self._batch_view(parsed["batch_id"], replayed=False)

    # ------------------------------------------------------------ candidates
    def add_candidates(self, payload: dict) -> dict:
        items = payload.get("candidates") if isinstance(payload.get("candidates"), list) else [payload]
        # 先全部校验再落库：单条非法不会留下半批候选。
        prepared = []
        for item in items:
            original = self._require_original(item.get("original_id") if isinstance(item, dict) else None)
            prepared.append(
                core.prepare_candidate(
                    {
                        "original_id": original["original_id"],
                        "request_id": original["request_id"],
                    },
                    item,
                )
            )
        now = _now()
        results = []
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for candidate in prepared:
                    existing = db.execute(
                        "SELECT * FROM evidence_candidates WHERE original_id=? AND source=? AND content_digest=?",
                        (candidate["original_id"], candidate["source"], candidate["content_digest"]),
                    ).fetchone()
                    if existing:
                        results.append(self._candidate_view(existing, replayed=True))
                        continue
                    db.execute(
                        "INSERT INTO evidence_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            candidate["candidate_id"],
                            candidate["original_id"],
                            candidate["source"],
                            candidate["content_digest"],
                            core.candidate_signature(candidate),
                            candidate["timestamp_utc"],
                            candidate["service"],
                            candidate["status"],
                            candidate["actor"],
                            candidate["note"],
                            now,
                        ),
                    )
                    results.append(self._candidate_view(
                        db.execute(
                            "SELECT * FROM evidence_candidates WHERE candidate_id=?",
                            (candidate["candidate_id"],),
                        ).fetchone(),
                        replayed=False,
                    ))
                db.execute("COMMIT")
            except Exception:
                _rollback(db)
                raise
        return {"candidates": results, "count": len(results)}

    def add_candidate(self, payload: dict) -> dict:
        return self.add_candidates(payload)["candidates"][0]

    # ----------------------------------------------------------- adjudicate
    def adjudicate(self, payload: dict) -> dict:
        items = payload.get("decisions") if isinstance(payload.get("decisions"), list) else [payload]
        shared_key = payload.get("idempotency_key")
        applied, replayed = [], []
        for index, item in enumerate(items):
            try:
                result = self._adjudicate_one(item, shared_key, index)
            except DomainError as exc:
                # 已经提交的采纳在下一次重试时按版本回放，不会二次贡献。
                error = _partial_error({
                    "index": index,
                    "original_id": item.get("original_id") if isinstance(item, dict) else None,
                    "error": exc.code,
                    "message": exc.message,
                    "committed": [{k: v for k, v in row.items() if k in
                                   ("decision_id", "original_id", "version", "candidate_id")}
                                  for row in applied + replayed],
                    "resume_from": index,
                    "retry": {"from_index": index, "remaining": items[index:]},
                })
                raise error from exc
            (replayed if result["replayed"] else applied).append(result)
        batch_ids = []
        for row in applied + replayed:
            if row.get("batch_id"):
                batch_ids.append(row["batch_id"])
        batch_id = batch_ids[0] if batch_ids else self._single_batch_id()
        return {
            "status": "READY",
            "adopted": applied,
            "replayed": replayed,
            "adopted_count": len(applied),
            "replayed_count": len(replayed),
            "counts": self._counts_for_batch(batch_id),
            "bucket_changes": self._bucket_changes(batch_id),
        }

    def _adjudicate_one(self, payload: dict, shared_key: str | None, index: int) -> dict:
        if not isinstance(payload, dict):
            raise DomainError("INVALID_VALUE", "裁决条目必须是对象")
        original_id = payload.get("original_id")
        candidate_id = payload.get("candidate_id")
        reason = payload.get("reason")
        if not original_id:
            raise DomainError("EMPTY_VALUE", "original_id 为空")
        if not candidate_id:
            raise DomainError("EMPTY_VALUE", "candidate_id 为空")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("EMPTY_VALUE", "采纳理由为空")
        expected = _expected_version(payload)
        key = payload.get("idempotency_key") or (
            f"{shared_key}#{index}" if shared_key else None
        )
        actor = str(payload.get("actor")).strip() if payload.get("actor") else None
        now = _now()

        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                original = self._require_original(original_id, db=db)
                candidate = db.execute(
                    "SELECT * FROM evidence_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
                if candidate is None or candidate["original_id"] != original_id:
                    raise DomainError("INVALID_VALUE", "候选不存在或不属于该原行")

                if key:
                    by_key = db.execute(
                        "SELECT * FROM evidence_decisions WHERE idempotency_key=?", (key,)
                    ).fetchone()
                    if by_key is not None:
                        if (
                            by_key["original_id"] != original_id
                            or by_key["candidate_id"] != candidate_id
                            or by_key["action"] != "ADOPT"
                            or by_key["reason"] != reason.strip()
                        ):
                            raise DomainError(
                                "STALE_VERSION",
                                "幂等键已用于不同的补证裁决，拒绝重用",
                            )
                        db.execute("COMMIT")
                        return self._decision_view(db, by_key, replayed=True)

                replay = self._replay_decision(
                    db, original, expected, action="ADOPT", candidate_id=candidate_id, reason=reason
                )
                if replay is not None:
                    db.execute("COMMIT")
                    return self._decision_view(db, replay, replayed=True)

                if original["version"] != expected:
                    raise DomainError(
                        "STALE_VERSION",
                        f"原行版本已变：期望 {expected}，当前 {original['version']}",
                    )
                new_version = expected + 1
                decision_id = core.decision_key(original_id, new_version)
                contribution = core.contribution_for(dict(candidate))
                contribution_id = "con-" + core.digest12(decision_id)
                # 同一原行只保留本次采纳的贡献；旧采纳转为 SUPERSEDED，不做撤销。
                db.execute(
                    "UPDATE evidence_contributions SET active=0, updated_at=? "
                    "WHERE original_id=? AND active=1",
                    (now, original_id),
                )
                db.execute(
                    "UPDATE evidence_decisions SET status='SUPERSEDED' "
                    "WHERE original_id=? AND action='ADOPT' AND status=?",
                    (original_id, ACTIVE),
                )
                db.execute(
                    "INSERT INTO evidence_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        decision_id,
                        original_id,
                        original["batch_id"],
                        "ADOPT",
                        candidate_id,
                        None,
                        reason.strip(),
                        actor,
                        expected,
                        new_version,
                        ACTIVE,
                        key,
                        now,
                    ),
                )
                db.execute(
                    "UPDATE evidence_originals SET version=?, updated_at=? WHERE original_id=?",
                    (new_version, now, original_id),
                )
                db.execute(
                    "INSERT INTO evidence_contributions VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        contribution_id,
                        decision_id,
                        original_id,
                        contribution["hour"],
                        contribution["service"],
                        contribution["count_delta"],
                        contribution["errors_delta"],
                        1,
                        now,
                        now,
                    ),
                )
                db.execute("COMMIT")
            except Exception:
                _rollback(db)
                raise
            return self._decision_view(db, db.execute(
                "SELECT * FROM evidence_decisions WHERE decision_id=?", (decision_id,)
            ).fetchone(), replayed=False)

    # --------------------------------------------------------------- revoke
    def revoke(self, payload: dict) -> dict:
        original_id = payload.get("original_id")
        reason = payload.get("reason")
        if not original_id:
            raise DomainError("EMPTY_VALUE", "original_id 为空")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("EMPTY_VALUE", "撤销理由为空")
        expected = _expected_version(payload)
        key = payload.get("idempotency_key")
        actor = str(payload.get("actor")).strip() if payload.get("actor") else None
        now = _now()

        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                original = self._require_original(original_id, db=db)
                if key:
                    by_key = db.execute(
                        "SELECT * FROM evidence_decisions WHERE idempotency_key=?", (key,)
                    ).fetchone()
                    if by_key is not None:
                        if (
                            by_key["original_id"] != original_id
                            or by_key["action"] != "REVOKE"
                            or by_key["reason"] != reason.strip()
                        ):
                            raise DomainError("STALE_VERSION", "幂等键已用于不同的补证裁决，拒绝重用")
                        db.execute("COMMIT")
                        return {
                            "status": "READY",
                            "replayed": True,
                            "decision": self._decision_view(db, by_key, replayed=True),
                            "counts": self._counts_for_batch(original["batch_id"]),
                            "bucket_changes": self._bucket_changes(original["batch_id"]),
                        }
                replay = self._replay_decision(
                    db, original, expected, action="REVOKE", candidate_id=None, reason=reason
                )
                if replay is not None:
                    db.execute("COMMIT")
                    return {
                        "status": "READY",
                        "replayed": True,
                        "decision": self._decision_view(db, replay, replayed=True),
                        "counts": self._counts_for_batch(original["batch_id"]),
                        "bucket_changes": self._bucket_changes(original["batch_id"]),
                    }

                if original["version"] != expected:
                    raise DomainError(
                        "STALE_VERSION",
                        f"原行版本已变：期望 {expected}，当前 {original['version']}",
                    )
                adopted = db.execute(
                    "SELECT * FROM evidence_decisions WHERE original_id=? AND action='ADOPT' "
                    "AND status=? ORDER BY version DESC LIMIT 1",
                    (original_id, ACTIVE),
                ).fetchone()
                if adopted is None:
                    raise DomainError("NOT_ADOPTED", "该原行没有处于采纳状态的裁决")
                if payload.get("decision_id") and payload["decision_id"] != adopted["decision_id"]:
                    raise DomainError(
                        "STALE_VERSION",
                        f"指定裁决已不是当前采纳：{payload['decision_id']}",
                    )

                new_version = expected + 1
                decision_id = core.decision_key(original_id, new_version)
                db.execute(
                    "INSERT INTO evidence_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        decision_id,
                        original_id,
                        original["batch_id"],
                        "REVOKE",
                        None,
                        adopted["decision_id"],
                        reason.strip(),
                        actor,
                        expected,
                        new_version,
                        ACTIVE,
                        key,
                        now,
                    ),
                )
                db.execute(
                    "UPDATE evidence_originals SET version=?, updated_at=? WHERE original_id=?",
                    (new_version, now, original_id),
                )
                # 只回冲本次采纳自己的贡献行。
                db.execute(
                    "UPDATE evidence_contributions SET active=0, updated_at=? WHERE decision_id=?",
                    (now, adopted["decision_id"]),
                )
                db.execute(
                    "UPDATE evidence_decisions SET status=? WHERE decision_id=?",
                    (REVOKED, adopted["decision_id"]),
                )
                db.execute("COMMIT")
            except Exception:
                _rollback(db)
                raise
            view = self._decision_view(
                db,
                db.execute(
                    "SELECT * FROM evidence_decisions WHERE decision_id=?", (decision_id,)
                ).fetchone(),
                replayed=False,
            )
        return {
            "status": "READY",
            "replayed": False,
            "decision": view,
            "reversed_contribution": {
                "decision_id": adopted["decision_id"],
                "count_delta": -1,
                "errors_delta": -1 if view["reversed_errors"] else 0,
            },
            "counts": self._counts_for_batch(original["batch_id"]),
            "bucket_changes": self._bucket_changes(original["batch_id"]),
        }

    # --------------------------------------------------------------- resume
    def resume(self, batch_id: str | None = None) -> dict:
        """以已提交裁决为位置补账；重复执行结果一致，不会重复贡献。"""
        repaired: list[dict] = []
        now = _now()
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if batch_id:
                    decisions = db.execute(
                        "SELECT * FROM evidence_decisions WHERE batch_id=? ORDER BY original_id, version",
                        (batch_id,),
                    ).fetchall()
                else:
                    decisions = db.execute(
                        "SELECT * FROM evidence_decisions ORDER BY original_id, version"
                    ).fetchall()
                for decision in decisions:
                    existing = db.execute(
                        "SELECT * FROM evidence_contributions WHERE decision_id=?",
                        (decision["decision_id"],),
                    ).fetchone()
                    if decision["action"] == "ADOPT":
                        candidate = db.execute(
                            "SELECT * FROM evidence_candidates WHERE candidate_id=?",
                            (decision["candidate_id"],),
                        ).fetchone()
                        if candidate is None:
                            # 候选都缺失时仍要保证撤销回冲结果可恢复。
                            raise DomainError("INVALID_VALUE", "裁决引用的候选缺失，无法恢复")
                        fact = core.contribution_for(dict(candidate))
                        desired_active = 1 if decision["status"] == ACTIVE else 0
                        if existing is not None and existing["active"] == desired_active:
                            continue
                        if existing is None:
                            db.execute(
                                "INSERT INTO evidence_contributions VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (
                                    "con-" + core.digest12(decision["decision_id"]),
                                    decision["decision_id"],
                                    decision["original_id"],
                                    fact["hour"],
                                    fact["service"],
                                    fact["count_delta"],
                                    fact["errors_delta"],
                                    desired_active,
                                    now,
                                    now,
                                ),
                            )
                            repaired.append({"decision_id": decision["decision_id"], "action": "INSERT_CONTRIBUTION"})
                        else:
                            db.execute(
                                "UPDATE evidence_contributions SET active=?, updated_at=? WHERE decision_id=?",
                                (desired_active, now, decision["decision_id"]),
                            )
                            repaired.append({"decision_id": decision["decision_id"], "action": "SET_ACTIVE"})
                    else:
                        reversed_row = db.execute(
                            "SELECT * FROM evidence_contributions WHERE decision_id=?",
                            (decision["reverses_decision_id"],),
                        ).fetchone()
                        if reversed_row is not None and reversed_row["active"] != 0:
                            db.execute(
                                "UPDATE evidence_contributions SET active=0, updated_at=? WHERE decision_id=?",
                                (now, decision["reverses_decision_id"]),
                            )
                            repaired.append({"decision_id": decision["decision_id"], "action": "REVERSE_CONTRIBUTION"})
                # 采纳后若被改判，旧贡献只能有一份有效；以最后一条 APPLIED 采纳为准。
                for decision in decisions:
                    if decision["action"] != "ADOPT" or decision["status"] != ACTIVE:
                        continue
                    db.execute(
                        "UPDATE evidence_contributions SET active=0, updated_at=? "
                        "WHERE original_id=? AND active=1 AND decision_id<>?",
                        (now, decision["original_id"], decision["decision_id"]),
                    )
                db.execute("COMMIT")
            except Exception:
                _rollback(db)
                raise
        return {"status": "READY", "repaired": repaired, "repaired_count": len(repaired),
                "buckets": self.buckets(batch_id)}

    # -------------------------------------------------------------- readback
    def overview(self, batch_id: str | None = None) -> dict:
        batch_id = batch_id or self._single_batch_id()
        if batch_id is None:
            return {"status": "EMPTY", "batch": None, "counts": _zero_counts(),
                    "pending_evidence": [], "pending_adjudication": [], "adopted": [],
                    "bucket_changes": []}
        batch = self._batch_view(batch_id)
        originals = self._original_views(batch_id)
        pending_evidence, pending_adjudication, adopted = [], [], []
        for original in originals:
            if not original["candidates"]:
                pending_evidence.append(original)
            elif original["state"] == "ADOPTED":
                adopted.append(original)
            else:
                pending_adjudication.append(original)
        return {
            "status": "READY",
            "batch": batch,
            "counts": self._counts_for_batch(batch_id),
            "pending_evidence": pending_evidence,
            "pending_adjudication": pending_adjudication,
            "adopted": adopted,
            "bucket_changes": self._bucket_changes(batch_id),
        }

    def original_detail(self, original_id: str) -> dict:
        original = self._require_original(original_id)
        view = self._original_views(original["batch_id"], original_id)[0]
        view["batch_id"] = original["batch_id"]
        return view

    def buckets(self, batch_id: str | None = None) -> dict:
        """回读小时桶：原批次基线 + 已采纳事实，并保留撤销/改判的桶变化。"""
        batch_id = batch_id or self._single_batch_id()
        if batch_id is None:
            return {"status": "EMPTY", "buckets": [], "counts": _zero_counts()}
        snapshot = self._snapshot(batch_id)

        def empty_item(hour: str, service: str) -> dict:
            return {
                "hour": hour,
                "service": service,
                "base_count": 0,
                "base_errors": 0,
                "adopted_count": 0,
                "adopted_errors": 0,
                "reverted_count": 0,
                "reverted_errors": 0,
                "superseded_count": 0,
                "superseded_errors": 0,
                "origins": [],
            }

        base: dict[tuple[str, str], dict] = {}
        for row in snapshot["valid"]:
            key = (core.hour_key_utc(row["timestamp_utc"]), row["service"])
            item = base.setdefault(key, empty_item(*key))
            item["base_count"] += 1
            item["base_errors"] += 1 if int(row["status"]) >= 500 else 0

        with self.session() as db:
            rows = db.execute(
                "SELECT c.*, d.status AS decision_status "
                "FROM evidence_contributions c JOIN evidence_decisions d ON d.decision_id=c.decision_id "
                "WHERE d.batch_id=? ORDER BY c.hour, c.service, c.created_at",
                (batch_id,),
            ).fetchall()
        for row in rows:
            key = (row["hour"], row["service"])
            item = base.setdefault(key, empty_item(*key))
            if row["active"]:
                item["adopted_count"] += row["count_delta"]
                item["adopted_errors"] += row["errors_delta"]
            elif row["decision_status"] == REVOKED:
                item["reverted_count"] += row["count_delta"]
                item["reverted_errors"] += row["errors_delta"]
            else:
                item["superseded_count"] += row["count_delta"]
                item["superseded_errors"] += row["errors_delta"]
            item["origins"].append(
                {
                    "original_id": row["original_id"],
                    "decision_id": row["decision_id"],
                    "active": bool(row["active"]),
                    "decision_status": row["decision_status"],
                }
            )
        out = []
        for item in sorted(base.values(), key=lambda x: (x["hour"], x["service"])):
            item["count"] = item["base_count"] + item["adopted_count"]
            item["errors"] = item["base_errors"] + item["adopted_errors"]
            item["delta_count"] = item["count"] - item["base_count"]
            item["delta_errors"] = item["errors"] - item["base_errors"]
            out.append(item)
        return {
            "status": "READY",
            "batch_id": batch_id,
            "buckets": out,
            "counts": self._counts_for_batch(batch_id),
        }

    # --------------------------------------------------------------- helpers
    def _single_batch_id(self) -> str | None:
        with self.session() as db:
            rows = db.execute(
                "SELECT batch_id FROM evidence_batches ORDER BY created_at DESC, batch_id"
            ).fetchall()
        return rows[0]["batch_id"] if rows else None

    def _snapshot(self, batch_id: str) -> dict:
        with self.session() as db:
            row = db.execute(
                "SELECT snapshot FROM evidence_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
        if row is None:
            raise DomainError("INVALID_VALUE", "批次不存在")
        return json.loads(row["snapshot"])

    def _batch_view(self, batch_id: str, replayed: bool = False) -> dict:
        with self.session() as db:
            row = db.execute(
                "SELECT * FROM evidence_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
        if row is None:
            raise DomainError("INVALID_VALUE", "批次不存在")
        return {
            "batch_id": row["batch_id"],
            "digest": row["digest"],
            "rule_version": row["rule_version"],
            "input_count": row["input_count"],
            "valid_count": row["valid_count"],
            "quarantine_count": row["quarantine_count"],
            "created_at": row["created_at"],
            "replayed": replayed,
        }

    def _require_original(self, original_id, db: sqlite3.Connection | None = None) -> dict:
        if not original_id:
            raise DomainError("EMPTY_VALUE", "original_id 为空")
        conn = db or self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM evidence_originals WHERE original_id=?", (original_id,)
            ).fetchone()
        finally:
            if db is None:
                conn.close()
        if row is None:
            raise DomainError("NOT_FOUND", f"原行不存在: {original_id}")
        return dict(row)

    def _candidate_view(self, row: sqlite3.Row, replayed: bool = False) -> dict:
        return {
            "candidate_id": row["candidate_id"],
            "original_id": row["original_id"],
            "source": row["source"],
            "source_label": core.CANDIDATE_SOURCES.get(row["source"], row["source"]),
            "signature": row["signature"],
            "timestamp_utc": row["timestamp_utc"],
            "service": row["service"],
            "status": row["status"],
            "actor": row["actor"],
            "note": row["note"],
            "created_at": row["created_at"],
            "replayed": replayed,
        }

    def _decision_view(self, db: sqlite3.Connection, row: sqlite3.Row, replayed: bool = False) -> dict:
        contribution = db.execute(
            "SELECT * FROM evidence_contributions WHERE decision_id=?", (row["decision_id"],)
        ).fetchone()
        reversed_row = None
        if row["reverses_decision_id"]:
            reversed_row = db.execute(
                "SELECT * FROM evidence_contributions WHERE decision_id=?",
                (row["reverses_decision_id"],),
            ).fetchone()
        return {
            "decision_id": row["decision_id"],
            "original_id": row["original_id"],
            "batch_id": row["batch_id"],
            "action": row["action"],
            "candidate_id": row["candidate_id"],
            "reverses_decision_id": row["reverses_decision_id"],
            "reason": row["reason"],
            "actor": row["actor"],
            "expected_version": row["expected_version"],
            "version": row["version"],
            "status": row["status"],
            "created_at": row["created_at"],
            "contribution": _row_dict(contribution),
            "reversed_errors": bool(reversed_row["errors_delta"]) if reversed_row else False,
            "replayed": replayed,
        }

    def _replay_decision(
        self,
        db: sqlite3.Connection,
        original: dict,
        expected: int,
        action: str,
        candidate_id: str | None,
        reason: str,
    ) -> sqlite3.Row | None:
        """同原行、同期望版本的裁决已提交时，返回既有裁决用于幂等回放。"""
        if original["version"] != expected + 1:
            return None
        row = db.execute(
            "SELECT * FROM evidence_decisions WHERE original_id=? AND version=?",
            (original["original_id"], expected + 1),
        ).fetchone()
        if row is None or row["action"] != action:
            return None
        if (row["candidate_id"] or None) != (candidate_id or None):
            return None
        if row["reason"] != reason.strip():
            raise DomainError(
                "STALE_VERSION",
                "同一期望版本的裁决理由不一致，拒绝覆盖既有证据",
            )
        return row

    def _original_views(self, batch_id: str, original_id: str | None = None) -> list[dict]:
        with self.session() as db:
            if original_id:
                originals = db.execute(
                    "SELECT * FROM evidence_originals WHERE original_id=?", (original_id,)
                ).fetchall()
            else:
                originals = db.execute(
                    "SELECT * FROM evidence_originals WHERE batch_id=? ORDER BY source_line",
                    (batch_id,),
                ).fetchall()
            views = []
            for original in originals:
                candidates = db.execute(
                    "SELECT * FROM evidence_candidates WHERE original_id=? ORDER BY created_at, candidate_id",
                    (original["original_id"],),
                ).fetchall()
                decisions = db.execute(
                    "SELECT * FROM evidence_decisions WHERE original_id=? ORDER BY version",
                    (original["original_id"],),
                ).fetchall()
                views.append(self._original_view(db, original, candidates, decisions))
        return views

    def _original_view(
        self,
        db: sqlite3.Connection,
        original: sqlite3.Row,
        candidates: list[sqlite3.Row],
        decisions: list[sqlite3.Row],
    ) -> dict:
        signatures = {row["signature"] for row in candidates}
        latest = decisions[-1] if decisions else None
        active_adopt = None
        for row in reversed(decisions):
            if row["action"] == "ADOPT" and row["status"] == ACTIVE:
                active_adopt = row
                break
        if active_adopt is not None:
            state = "ADOPTED"
        elif candidates:
            state = "PENDING_ADJUDICATION"
        else:
            state = "PENDING_EVIDENCE"
        contribution = None
        if active_adopt is not None:
            contribution = db.execute(
                "SELECT * FROM evidence_contributions WHERE decision_id=?",
                (active_adopt["decision_id"],),
            ).fetchone()
        return {
            "original_id": original["original_id"],
            "source_line": original["source_line"],
            "request_id": original["request_id"],
            "reason_code": original["reason_code"],
            "raw_value": original["raw_value"],
            "version": original["version"],
            "state": state,
            "candidate_count": len(candidates),
            "conflict": len(signatures) > 1,
            "candidates": [self._candidate_view(row) for row in candidates],
            "adopted": self._decision_view(db, active_adopt) if active_adopt else None,
            "decisions": [self._decision_view(db, row) for row in decisions],
            "contribution": _row_dict(contribution),
            "latest_version": latest["version"] if latest else 0,
        }

    def _counts_for_batch(self, batch_id: str | None) -> dict:
        if batch_id is None:
            return _zero_counts()
        batch = self._batch_view(batch_id)
        with self.session() as db:
            adopted = db.execute(
                "SELECT COUNT(DISTINCT c.original_id) FROM evidence_contributions c "
                "JOIN evidence_decisions d ON d.decision_id=c.decision_id "
                "WHERE c.active=1 AND d.batch_id=?",
                (batch_id,),
            ).fetchone()[0]
            contributions = db.execute(
                "SELECT COUNT(*) FROM evidence_contributions c "
                "JOIN evidence_decisions d ON d.decision_id=c.decision_id "
                "WHERE c.active=1 AND d.batch_id=?",
                (batch_id,),
            ).fetchone()[0]
            pending_evidence = db.execute(
                "SELECT COUNT(*) FROM evidence_originals o WHERE o.batch_id=? AND NOT EXISTS "
                "(SELECT 1 FROM evidence_candidates c WHERE c.original_id=o.original_id)",
                (batch_id,),
            ).fetchone()[0]
            total_originals = db.execute(
                "SELECT COUNT(*) FROM evidence_originals WHERE batch_id=?", (batch_id,)
            ).fetchone()[0]
        pending_adjudication = total_originals - pending_evidence - adopted
        effective_quarantine = batch["quarantine_count"] - adopted
        effective_valid = batch["valid_count"] + adopted
        return {
            "input_count": batch["input_count"],
            # 有效/隔离计数按复核后的事实给出；原批次计数另存 original_*。
            "valid_count": effective_valid,
            "quarantine_count": effective_quarantine,
            "effective_valid_count": effective_valid,
            "effective_quarantine_count": effective_quarantine,
            "original_valid_count": batch["valid_count"],
            "original_quarantine_count": batch["quarantine_count"],
            "adopted_count": adopted,
            "active_contributions": contributions,
            "pending_evidence_count": pending_evidence,
            "pending_adjudication_count": pending_adjudication,
            "balanced": batch["input_count"] == effective_valid + effective_quarantine,
        }

    def _bucket_changes(self, batch_id: str | None) -> list[dict]:
        if batch_id is None:
            return []
        delta = self.buckets(batch_id)
        return [
            item
            for item in delta["buckets"]
            if item["adopted_count"]
            or item["adopted_errors"]
            or item["reverted_count"]
            or item["reverted_errors"]
            or item["superseded_count"]
            or item["superseded_errors"]
        ]


def _expected_version(payload: dict) -> int:
    value = payload.get("expected_version")
    if value is None:
        raise DomainError("EMPTY_VALUE", "expected_version 为空")
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainError("INVALID_VALUE", "expected_version 必须是整数")
    if value < 0:
        raise DomainError("INVALID_VALUE", "expected_version 不能为负")
    return value


def _zero_counts() -> dict:
    return {
        "input_count": 0,
        "valid_count": 0,
        "quarantine_count": 0,
        "effective_valid_count": 0,
        "effective_quarantine_count": 0,
        "original_valid_count": 0,
        "original_quarantine_count": 0,
        "adopted_count": 0,
        "active_contributions": 0,
        "pending_evidence_count": 0,
        "pending_adjudication_count": 0,
        "balanced": True,
    }


class _PartialFailure(DomainError):
    def __init__(self, info: dict):
        self.info = info
        super().__init__(info["error"], info["message"])


def _partial_error(info: dict) -> DomainError:
    return _PartialFailure(info)
