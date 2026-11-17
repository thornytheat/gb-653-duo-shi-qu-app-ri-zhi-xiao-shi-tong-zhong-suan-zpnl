REASONS = {
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    "CANDIDATE_MISMATCH": "候选与原行业务键不一致", "NOT_ADOPTED": "该原行没有待撤销的采纳",
    "NOT_FOUND": "原行不存在",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")
