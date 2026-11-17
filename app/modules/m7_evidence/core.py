"""M7 补证裁决：纯函数。

约定：
- 原行（evidence originals）只读；补证候选单独存放，采纳不覆盖原行。
- 候选时间一律先归一到 UTC 再进小时桶；未带偏移的时间直接拒绝，
  不借用服务时区（服务时区保持 Asia/Shanghai 仅用于展示）。
- 候选与采纳理由都要留档，同一原行可有多个互不相同的候选。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.errors import DomainError
from app.manifest import canonical_digest
from app.modules.m1_logs import core as m1
from app.modules.m2_time import core as m2

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_FILES = {
    "dirty": ROOT / "sample_data" / "events_dirty.jsonl",
    "clean": ROOT / "sample_data" / "events_clean.jsonl",
}

CANDIDATE_SOURCES = {
    "SUPPLIER_RESEND": "供应方重发",
    "MANUAL_ENTRY": "人工补录",
}
SOURCE_ALIASES = {
    "SUPPLIER_RESEND": "SUPPLIER_RESEND",
    "SUPPLIER": "SUPPLIER_RESEND",
    "RESEND": "SUPPLIER_RESEND",
    "供应方重发": "SUPPLIER_RESEND",
    "MANUAL_ENTRY": "MANUAL_ENTRY",
    "MANUAL": "MANUAL_ENTRY",
    "人工补录": "MANUAL_ENTRY",
}

_REQ_ID = re.compile(r'"request_id"\s*:\s*"((?:[^"\\]|\\.)*)"')


def digest12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def original_id(batch_id: str, source_line: int) -> str:
    return "orig-" + digest12(f"{batch_id}\x00{source_line}")


def candidate_id(original: str, source: str, content_digest: str) -> str:
    return "cand-" + digest12(f"{original}\x00{source}\x00{content_digest}")


def decision_key(original_id: str, version: int) -> str:
    return "dec-" + digest12(f"{original_id}\x00{version}")


def decode_transport_newlines(text: str) -> str:
    """样例文件把换行保存成了字面量 ``\\n``；仅在确认是多条记录时还原。

    整段文本能解析成单条 JSON 时保持原样，避免误改字符串里合法的 ``\\n`` 转义。
    """
    if "\n" in text or "\r" in text or "\\n" not in text:
        return text
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    head = text.split("\\n", 1)[0]
    try:
        json.loads(head)
    except json.JSONDecodeError:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


def load_sample_text(name: str = "dirty") -> str:
    path = SAMPLE_FILES.get(name)
    if path is None:
        raise DomainError("INVALID_VALUE", f"未登记样例: {name}")
    return path.read_text(encoding="utf-8")


def request_id_from_raw(raw: str | None) -> str | None:
    if not raw:
        return None
    match = _REQ_ID.search(raw)
    if not match:
        return None
    try:
        return json.loads('"' + match.group(1) + '"')
    except json.JSONDecodeError:
        return match.group(1)


def _status_value(value) -> int:
    if isinstance(value, bool):
        raise DomainError("INVALID_VALUE", "status 不能是布尔值")
    if isinstance(value, int):
        status = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        status = int(value.strip())
    else:
        raise DomainError("INVALID_VALUE", "status 必须是整数")
    if not 100 <= status <= 599:
        raise DomainError("INVALID_VALUE", "status 超出 HTTP 状态码范围")
    return status


def validate_event(row: dict, raw: str) -> dict:
    """把 M1 解析出的行归一为可入桶事实；不可用则抛 DomainError。"""
    timestamp = row.get("timestamp")
    if timestamp is None or not str(timestamp).strip():
        raise DomainError("EMPTY_VALUE", "timestamp 为空")
    service = row.get("service")
    if service is None or not str(service).strip():
        raise DomainError("EMPTY_VALUE", "service 为空")
    if "status" not in row or row.get("status") in (None, ""):
        raise DomainError("EMPTY_VALUE", "status 为空")
    try:
        timestamp_utc = m2.normalize(str(timestamp).strip())
    except (ValueError, TypeError, KeyError) as exc:
        raise DomainError("INVALID_VALUE", f"timestamp 无法归一为 UTC: {exc}") from exc
    return {
        "request_id": str(row.get("request_id", "")),
        "timestamp": str(timestamp),
        "timestamp_utc": timestamp_utc,
        "service": str(service).strip(),
        "status": _status_value(row.get("status")),
        "source_line": int(row.get("source_line", 0)),
        "raw": raw,
    }


def parse_intake(text: str, rule_version: str) -> dict:
    """解析真实坏行，返回不可变批次快照（尚未落库）。

    有效行用于原批次基线桶；隔离行保留原始行号、原因码与原值。
    """
    normalized = decode_transport_newlines(text or "")
    parsed = m1.parse(normalized)
    lines = normalized.splitlines()

    def raw_at(line_no: int) -> str:
        if 1 <= line_no <= len(lines):
            return lines[line_no - 1]
        return ""

    valid: list[dict] = []
    quarantine: list[dict] = []
    for row in parsed["rows"]:
        raw = raw_at(row["source_line"])
        try:
            valid.append(validate_event(row, raw))
        except DomainError as exc:
            quarantine.append({
                "source_line": row["source_line"],
                "request_id": str(row.get("request_id", "")) or None,
                "reason_code": exc.code,
                "raw_value": raw,
                "detail": exc.message,
            })
    for item in parsed["quarantine"]:
        raw = item.get("raw", raw_at(item["source_line"]))
        quarantine.append({
            "source_line": item["source_line"],
            "request_id": request_id_from_raw(raw),
            "reason_code": item.get("reason", "TRUNCATED"),
            "raw_value": raw,
            "detail": "原始行无法解析，保留原值",
        })
    quarantine.sort(key=lambda item: item["source_line"])

    input_count = len(valid) + len(quarantine)
    batch_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    batch_id = "batch-" + batch_digest[:12]
    snapshot = {
        "valid": [
            {
                "source_line": row["source_line"],
                "request_id": row["request_id"],
                "timestamp": row["timestamp"],
                "timestamp_utc": row["timestamp_utc"],
                "service": row["service"],
                "status": row["status"],
                "raw": row["raw"],
            }
            for row in sorted(valid, key=lambda item: item["source_line"])
        ],
        "quarantine": quarantine,
    }
    return {
        "batch_id": batch_id,
        "digest": batch_digest,
        "rule_version": rule_version,
        "input_count": input_count,
        "valid_count": len(valid),
        "quarantine_count": len(quarantine),
        "snapshot": snapshot,
    }


def normalize_source(value) -> str:
    if not isinstance(value, str):
        raise DomainError("EMPTY_VALUE", "source 为空")
    source = SOURCE_ALIASES.get(value.strip().upper()) or SOURCE_ALIASES.get(value.strip())
    if not source:
        raise DomainError("INVALID_VALUE", f"未登记的候选来源: {value}")
    return source


def prepare_candidate(original: dict, payload: dict) -> dict:
    """校验候选并生成可入库字段；不修改原行。"""
    source = normalize_source(payload.get("source"))
    timestamp = payload.get("timestamp")
    if timestamp is None or not str(timestamp).strip():
        raise DomainError("EMPTY_VALUE", "timestamp 为空")
    try:
        timestamp_utc = m2.normalize(
            str(timestamp).strip(), payload.get("zone"), payload.get("fold", 0)
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise DomainError("INVALID_VALUE", f"timestamp 无法归一为 UTC: {exc}") from exc

    service = payload.get("service")
    if service is None or not str(service).strip():
        raise DomainError("EMPTY_VALUE", "service 为空")
    if "status" not in payload or payload.get("status") in (None, ""):
        raise DomainError("EMPTY_VALUE", "status 为空")
    status = _status_value(payload.get("status"))

    request_id = payload.get("request_id")
    if request_id not in (None, "") and original.get("request_id"):
        if str(request_id) != original["request_id"]:
            raise DomainError(
                "CANDIDATE_MISMATCH",
                f"候选 request_id 与原行不一致: {request_id} != {original['request_id']}",
            )

    fact = {
        "timestamp_utc": timestamp_utc,
        "service": str(service).strip(),
        "status": status,
    }
    content_digest = canonical_digest(fact)
    return {
        "candidate_id": candidate_id(original["original_id"], source, content_digest),
        "original_id": original["original_id"],
        "source": source,
        "content_digest": content_digest,
        "timestamp": str(timestamp).strip(),
        "timestamp_utc": timestamp_utc,
        "service": fact["service"],
        "status": status,
        "payload": fact,
        "actor": (str(payload.get("actor")).strip() if payload.get("actor") else None),
        "note": (str(payload.get("note")).strip() if payload.get("note") else None),
    }


def hour_key_utc(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")


def contribution_for(candidate: dict) -> dict:
    return {
        "hour": hour_key_utc(candidate["timestamp_utc"]),
        "service": candidate["service"],
        "count_delta": 1,
        "errors_delta": 1 if int(candidate["status"]) >= 500 else 0,
    }


def candidate_signature(candidate: dict) -> str:
    return canonical_digest({
        "timestamp_utc": candidate["timestamp_utc"],
        "service": candidate["service"],
        "status": int(candidate["status"]),
    })
