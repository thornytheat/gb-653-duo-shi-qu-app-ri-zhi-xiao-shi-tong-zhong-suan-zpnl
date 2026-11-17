import sqlite3
import threading

import pytest

from app.config import Settings
from app.errors import DomainError
from app.modules.m3_events import core as m3
from app.modules.m7_evidence import core
from app.modules.m7_evidence.service import EvidenceStore, _PartialFailure

DIRTY = core.load_sample_text("dirty")
BAD_LINE = '{"request_id":"bad1","timestamp":"2026-11-15T04:10:00Z","service":"pay","status":200}\n{"request_id":"bad2"'
CLEAN = core.load_sample_text("clean")


@pytest.fixture
def store(tmp_path):
    return EvidenceStore(str(tmp_path / "evidence.db"))


def seed(store, text=DIRTY):
    return store.intake(text, None, "2026.11")


def original_of(store, batch_id, line=3):
    for item in store.overview(batch_id)["pending_evidence"]:
        if item["source_line"] == line:
            return item
    raise AssertionError("missing original")


def add(store, original_id, source, status=200, timestamp="2026-11-15T01:05:00Z", **extra):
    payload = {
        "original_id": original_id,
        "source": source,
        "timestamp": timestamp,
        "service": "pay",
        "status": status,
    }
    payload.update(extra)
    return store.add_candidate(payload)


def test_intake_uses_real_bad_lines(store):
    batch = seed(store)
    assert batch["input_count"] == 3
    assert batch["valid_count"] == 2
    assert batch["quarantine_count"] == 1
    original = original_of(store, batch["batch_id"])
    assert original["source_line"] == 3
    assert original["reason_code"] == "TRUNCATED"
    assert original["raw_value"] == '{"request_id":"r3"'
    assert original["request_id"] == "r3"


def test_intake_reuses_same_batch(store):
    first = seed(store)
    second = seed(store)
    assert first["batch_id"] == second["batch_id"]
    assert second["replayed"] is True
    assert len(store.overview(first["batch_id"])["pending_evidence"]) == 1


def test_original_line_is_read_only(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    add(store, original["original_id"], "SUPPLIER_RESEND", 200)
    after = store.original_detail(original["original_id"])
    assert after["raw_value"] == original["raw_value"]
    assert after["reason_code"] == original["reason_code"]
    assert after["source_line"] == original["source_line"]
    assert after["version"] == 0
    assert len(after["candidates"]) == 1


def test_conflicting_candidates_are_kept_and_pending(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    supplier = add(store, original["original_id"], "SUPPLIER_RESEND", 200)
    manual = add(store, original["original_id"], "MANUAL_ENTRY", 500)
    overview = store.overview(batch["batch_id"])
    pending = overview["pending_adjudication"]
    assert len(pending) == 1
    assert pending[0]["conflict"] is True
    assert {row["candidate_id"] for row in pending[0]["candidates"]} == {
        supplier["candidate_id"], manual["candidate_id"]
    }
    # 未裁决的冲突不进桶。
    buckets = store.buckets(batch["batch_id"])["buckets"]
    assert [b["count"] for b in buckets] == [2]
    assert overview["counts"]["adopted_count"] == 0
    assert overview["counts"]["effective_valid_count"] == 2
    assert overview["counts"]["effective_quarantine_count"] == 1


def test_adopt_updates_effective_counts_and_bucket(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    candidate = add(store, original["original_id"], "SUPPLIER_RESEND", 500)
    result = store.adjudicate({
        "original_id": original["original_id"],
        "candidate_id": candidate["candidate_id"],
        "expected_version": 0,
        "reason": "供应方重发与工单一致",
        "actor": "reviewer",
    })
    assert result["adopted_count"] == 1
    counts = result["counts"]
    assert counts["effective_valid_count"] == 3
    assert counts["effective_quarantine_count"] == 0
    assert counts["balanced"] is True
    assert result["bucket_changes"][0]["count"] == 3
    assert result["bucket_changes"][0]["errors"] == 2
    detail = store.original_detail(original["original_id"])
    assert detail["adopted"]["reason"] == "供应方重发与工单一致"
    assert detail["adopted"]["candidate_id"] == candidate["candidate_id"]
    assert detail["state"] == "ADOPTED"
    # 原行与全部候选都还在。
    assert detail["raw_value"] == original["raw_value"]
    assert len(detail["candidates"]) == 1


def test_adopt_requires_reason_and_matching_candidate(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    candidate = add(store, original["original_id"], "SUPPLIER_RESEND")
    with pytest.raises(DomainError) as exc:
        store.adjudicate({
            "original_id": original["original_id"],
            "candidate_id": candidate["candidate_id"],
            "expected_version": 0,
            "reason": "   ",
        })
    assert exc.value.code == "EMPTY_VALUE"
    with pytest.raises(DomainError) as exc:
        store.adjudicate({
            "original_id": original["original_id"],
            "candidate_id": "cand-missing",
            "expected_version": 0,
            "reason": "x",
        })
    assert exc.value.code == "INVALID_VALUE"
    assert store.overview(batch["batch_id"])["counts"]["adopted_count"] == 0


def test_candidate_without_offset_is_rejected(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    with pytest.raises(DomainError) as exc:
        add(store, original["original_id"], "MANUAL_ENTRY", timestamp="2026-11-15T01:05:00")
    assert exc.value.code == "INVALID_VALUE"


def test_candidate_request_id_must_match(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    with pytest.raises(DomainError) as exc:
        add(store, original["original_id"], "MANUAL_ENTRY", request_id="other")
    assert exc.value.code == "CANDIDATE_MISMATCH"


def test_stale_version_rejected(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    first = add(store, original["original_id"], "SUPPLIER_RESEND", 200)
    second = add(store, original["original_id"], "MANUAL_ENTRY", 500)
    store.adjudicate({
        "original_id": original["original_id"],
        "candidate_id": first["candidate_id"],
        "expected_version": 0,
        "reason": "先采纳",
    })
    with pytest.raises(DomainError) as exc:
        store.adjudicate({
            "original_id": original["original_id"],
            "candidate_id": second["candidate_id"],
            "expected_version": 0,
            "reason": "旧版本",
        })
    assert exc.value.code == "STALE_VERSION"
    assert store.overview(batch["batch_id"])["counts"]["active_contributions"] == 1


def test_concurrent_adjudication_rejects_old_version(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    first = add(store, original["original_id"], "SUPPLIER_RESEND", 200)
    second = add(store, original["original_id"], "MANUAL_ENTRY", 500)
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(candidate_id, reason):
        barrier.wait()
        try:
            result = store.adjudicate({
                "original_id": original["original_id"],
                "candidate_id": candidate_id,
                "expected_version": 0,
                "reason": reason,
            })
            outcomes.append(("ok", result["adopted_count"]))
        except DomainError as exc:
            outcomes.append(("error", exc.code))

    threads = [
        threading.Thread(target=worker, args=(first["candidate_id"], "甲")),
        threading.Thread(target=worker, args=(second["candidate_id"], "乙")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == [("error", "STALE_VERSION"), ("ok", 1)]
    buckets = store.buckets(batch["batch_id"])["buckets"]
    assert [b["adopted_count"] for b in buckets] == [1]


def test_retry_replays_without_second_contribution(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    candidate = add(store, original["original_id"], "SUPPLIER_RESEND", 500)
    body = {
        "original_id": original["original_id"],
        "candidate_id": candidate["candidate_id"],
        "expected_version": 0,
        "reason": "重试",
        "idempotency_key": "req-1",
    }
    first = store.adjudicate(body)
    second = store.adjudicate(body)
    assert first["adopted_count"] == 1
    assert second["adopted_count"] == 0
    assert second["replayed_count"] == 1
    assert second["counts"]["active_contributions"] == 1
    assert store.buckets(batch["batch_id"])["buckets"][0]["adopted_count"] == 1


def test_resume_recovers_missing_contribution(store, tmp_path):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    candidate = add(store, original["original_id"], "SUPPLIER_RESEND")
    store.adjudicate({
        "original_id": original["original_id"],
        "candidate_id": candidate["candidate_id"],
        "expected_version": 0,
        "reason": "已采纳落库",
    })
    # 模拟裁决已提交、贡献行写入失败。
    db = sqlite3.connect(store.path)
    db.execute("DELETE FROM evidence_contributions")
    db.commit()
    db.close()
    assert store.buckets(batch["batch_id"])["buckets"][0]["adopted_count"] == 0

    repaired = store.resume(batch["batch_id"])
    assert repaired["repaired_count"] == 1
    assert store.buckets(batch["batch_id"])["buckets"][0]["adopted_count"] == 1
    # 重试不重复贡献。
    assert store.resume(batch["batch_id"])["repaired_count"] == 0
    assert store.buckets(batch["batch_id"])["buckets"][0]["adopted_count"] == 1


def test_revoke_only_reverses_its_own_contribution(store):
    batch = seed(store)
    original = original_of(store, batch["batch_id"])
    supplier = add(store, original["original_id"], "SUPPLIER_RESEND", 200)
    manual = add(store, original["original_id"], "MANUAL_ENTRY", 500)
    store.adjudicate({
        "original_id": original["original_id"],
        "candidate_id": supplier["candidate_id"],
        "expected_version": 0,
        "reason": "先采纳供应方",
    })
    # 改判到人工补录：同一原行的旧采纳只能被替换，不能叠加。
    switched = store.adjudicate({
        "original_id": original["original_id"],
        "candidate_id": manual["candidate_id"],
        "expected_version": 1,
        "reason": "人工补录与回访一致",
    })
    assert switched["counts"]["active_contributions"] == 1
    bucket = store.buckets(batch["batch_id"])["buckets"][0]
    assert bucket["adopted_count"] == 1
    assert bucket["adopted_errors"] == 1
    assert bucket["superseded_count"] == 1

    revoked = store.revoke({
        "original_id": original["original_id"],
        "expected_version": 2,
        "reason": "撤销人工补录",
    })
    assert revoked["reversed_contribution"]["decision_id"] == store.original_detail(
        original["original_id"]
    )["decisions"][-2]["decision_id"]
    assert revoked["decision"]["reverses_decision_id"] == switched["adopted"][0]["decision_id"]
    bucket = store.buckets(batch["batch_id"])["buckets"][0]
    assert bucket["adopted_count"] == 0
    assert bucket["reverted_count"] == 1
    assert bucket["reverted_errors"] == 1
    counts = revoked["counts"]
    assert counts["effective_valid_count"] == 2
    assert counts["effective_quarantine_count"] == 1
    assert counts["balanced"] is True
    # 撤销重试也幂等。
    again = store.revoke({
        "original_id": original["original_id"],
        "expected_version": 2,
        "reason": "撤销人工补录",
    })
    assert again["replayed"] is True
    assert store.buckets(batch["batch_id"])["buckets"][0]["adopted_count"] == 0


def test_batch_failure_keeps_committed_and_reports_position(store):
    batch = store.intake(BAD_LINE, None, "2026.11")
    assert batch["quarantine_count"] == 1
    original = original_of(store, batch["batch_id"], 2)
    candidate = add(store, original["original_id"], "SUPPLIER_RESEND")
    other = store.overview(batch["batch_id"])["pending_evidence"]
    assert other == [] or all(row["original_id"] != original["original_id"] for row in other)
    payload = {
        "decisions": [
            {
                "original_id": original["original_id"],
                "candidate_id": candidate["candidate_id"],
                "expected_version": 0,
                "reason": "第一条",
            },
            {
                "original_id": original["original_id"],
                "candidate_id": candidate["candidate_id"],
                "expected_version": 0,
                "reason": "第二条用了旧版本",
            },
        ]
    }
    with pytest.raises(_PartialFailure) as exc:
        store.adjudicate(payload)
    assert exc.value.info["index"] == 1
    assert exc.value.info["error"] == "STALE_VERSION"
    assert exc.value.info["committed"][0]["version"] == 1
    # 已提交位置可继续：只重试后面的条目。
    retry = store.adjudicate({
        "original_id": original["original_id"],
        "candidate_id": candidate["candidate_id"],
        "expected_version": 1,
        "reason": "第二条重试",
    })
    assert retry["adopted_count"] == 1
    assert store.overview(batch["batch_id"])["counts"]["active_contributions"] == 1


def test_readback_lists_all_three_states(store):
    batch = store.intake(
        BAD_LINE + '\n{"request_id":"bad3","timestamp":"2026-11-15T05:00:00Z","service":"pay","status":500}\n{"request_id":"bad4"}\n{"request_id":"bad5"',
        None,
        "2026.11",
    )
    assert batch["quarantine_count"] == 3
    originals = {row["source_line"]: row for row in store.overview(batch["batch_id"])["pending_evidence"]}
    add(store, originals[2]["original_id"], "SUPPLIER_RESEND")
    adopted_candidate = add(store, originals[4]["original_id"], "MANUAL_ENTRY")
    store.adjudicate({
        "original_id": originals[4]["original_id"],
        "candidate_id": adopted_candidate["candidate_id"],
        "expected_version": 0,
        "reason": "已确认",
    })
    overview = store.overview(batch["batch_id"])
    assert [row["source_line"] for row in overview["pending_evidence"]] == [5]
    assert [row["source_line"] for row in overview["pending_adjudication"]] == [2]
    assert [row["source_line"] for row in overview["adopted"]] == [4]
    assert overview["counts"]["pending_evidence_count"] == 1
    assert overview["counts"]["pending_adjudication_count"] == 1
    assert overview["counts"]["adopted_count"] == 1

def test_service_timezone_and_dedupe_rules_untouched():
    settings = Settings()
    assert settings.timezone == "Asia/Shanghai"
    assert settings.rule_version == "2026.11"
    # M3 的请求去重规则保持原样：首次出现者胜出，不同取值记为冲突。
    out = m3.dedupe([
        {"request_id": "a", "v": 1},
        {"request_id": "a", "v": 2},
    ])
    assert out["accepted"][0]["v"] == 1
    assert len(out["conflict"]) == 1


def test_http_adjudication_flow(client):
    intake = client.post("/api/evidence/intake", json={"sample": "dirty"}).json()
    batch = client.get("/api/evidence/batch").json()
    original = batch["pending_evidence"][0]
    supplier = client.post("/api/evidence/candidates", json={
        "original_id": original["original_id"],
        "source": "SUPPLIER_RESEND",
        "timestamp": "2026-11-15T01:05:00Z",
        "service": "pay",
        "status": 200,
    }).json()["candidates"][0]
    manual = client.post("/api/evidence/candidates", json={
        "original_id": original["original_id"],
        "source": "MANUAL_ENTRY",
        "timestamp": "2026-11-15T01:05:00Z",
        "service": "pay",
        "status": 500,
    }).json()["candidates"][0]
    batch = client.get("/api/evidence/batch").json()
    assert batch["pending_adjudication"][0]["conflict"] is True
    assert client.get("/api/evidence/buckets").json()["buckets"][0]["count"] == 2

    stale = client.post("/api/evidence/adjudicate", json={
        "original_id": original["original_id"],
        "candidate_id": manual["candidate_id"],
        "expected_version": 9,
        "reason": "旧版本",
    })
    assert stale.status_code == 422
    assert stale.json()["error"] == "STALE_VERSION"

    ok = client.post("/api/evidence/adjudicate", json={
        "original_id": original["original_id"],
        "candidate_id": supplier["candidate_id"],
        "expected_version": 0,
        "reason": "供应方重发",
    })
    assert ok.status_code == 200
    assert ok.json()["counts"]["effective_quarantine_count"] == 0

    page = client.get("/evidence")
    assert page.status_code == 200
    assert "补证裁决记录" in page.text
    assert intake["batch_id"] in client.get("/api/evidence/batch").json()["batch"]["batch_id"]
