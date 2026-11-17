# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |
| `batch_id` | string | 否 | 原批次编号，由原文摘要派生 |
| `original_id` | string | 否 | 隔离原行编号，由批次与物理行号派生 |
| `candidate_id` | string | 否 | 补证候选编号，由原行、来源与候选内容摘要派生 |
| `source` | string | 否 | 候选来源：`SUPPLIER_RESEND` / `MANUAL_ENTRY` |
| `candidate.signature` | string | 否 | 候选归一后的事实指纹，用于识别冲突 |
| `candidate.timestamp_utc` | string | 否 | 候选时间归一后的 UTC 值，入桶只用该值 |
| `decision_id` | string | 否 | 裁决编号，由原行与裁决后版本派生 |
| `action` | string | 否 | `ADOPT` / `REVOKE` |
| `reason` | string | 否 | 采纳或撤销理由，裁决前必填 |
| `expected_version` | integer | 否 | 提交方看到的原行版本，用于乐观并发 |
| `version` | integer | 否 | 裁决后的原行版本，单调递增 |
| `status` | string | 否 | 裁决状态：`APPLIED` / `REVOKED` / `SUPERSEDED` |
| `contribution` | object | 是 | 一次采纳对小时桶的增量，撤销只回冲该行 |
| `replayed` | boolean | 否 | 本次请求是否回放已提交结果（不产生新贡献） |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。
