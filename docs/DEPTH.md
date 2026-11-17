# 深度登记 多时区应用日志小时桶重算

        - 规模：中型
        - 核心规则：事件先归一到 UTC，DST 重叠时保留 fold；小时桶右端开，水位只能前进且迟到事件仍进入修订。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 日志采信 | 运行样例并复核证据 | `/api/logs/import` / `/logs` | `app/modules/m1_logs/core.py` `app/modules/m1_logs/service.py` | `tests/test_m1_logs.py` |
| M2 时间解析 | 运行样例并复核证据 | `/api/time/normalize` / `/time` | `app/modules/m2_time/core.py` `app/modules/m2_time/service.py` | `tests/test_m2_time.py` |
| M3 事件去重 | 运行样例并复核证据 | `/api/events/dedupe` / `/events` | `app/modules/m3_events/core.py` `app/modules/m3_events/service.py` | `tests/test_m3_events.py` |
| M4 小时分桶 | 运行样例并复核证据 | `/api/buckets/rebuild` / `/buckets` | `app/modules/m4_buckets/core.py` `app/modules/m4_buckets/service.py` | `tests/test_m4_buckets.py` |
| M5 迟到修订 | 运行样例并复核证据 | `/api/watermark/advance` / `/watermark` | `app/modules/m5_watermark/core.py` `app/modules/m5_watermark/service.py` | `tests/test_m5_watermark.py` |
| M6 批次对账 | 运行样例并复核证据 | `/api/export/buckets.csv` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |
| M7 补证裁决 | 接入真实坏行，补证、裁决、撤销并回读小时桶 | `/api/evidence/*` / `/evidence` | `app/modules/m7_evidence/core.py` `app/modules/m7_evidence/service.py` | `tests/test_m7_evidence.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-03 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_logs/core.py` | `tests/test_m1_logs.py::test_trap_1` |
| TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_time/core.py` | `tests/test_m2_time.py::test_trap_1` |
| TRAP-07 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_events/core.py` | `tests/test_m3_events.py::test_trap_1` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_buckets/core.py` | `tests/test_m4_buckets.py::test_trap_1` |
| TRAP-11 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_watermark/core.py` | `tests/test_m5_watermark.py::test_trap_1` |
| TRAP-12 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-13 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_logs/core.py` | `tests/test_m1_logs.py::test_trap_2` |
| TRAP-14 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_time/core.py` | `tests/test_m2_time.py::test_trap_2` |
| TRAP-16 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_events/core.py` | `tests/test_m3_events.py::test_trap_2` |
| TRAP-23 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_buckets/core.py` | `tests/test_m4_buckets.py::test_trap_2` |
| TRAP-24 | 供应方重发与人工补录给出不同状态 | 覆盖原隔离行，或保留隔离导致漏算 | 原行只读留证；同一原行并存候选，按期望版本裁决，冲突未裁决不入桶，采纳后更新有效/隔离计数与小时桶 | `app/modules/m7_evidence/core.py` `app/modules/m7_evidence/service.py` | `tests/test_m7_evidence.py::test_original_line_is_read_only` `tests/test_m7_evidence.py::test_conflicting_candidates_are_kept_and_pending` |
| TRAP-25 | 并发裁决与重试 | 旧版本覆盖、重试重复贡献 | `expected_version` + `STALE_VERSION` 拒绝旧版本；裁决与贡献同事务；重试按版本/幂等键回放，`resume` 从已采纳位置补账 | `app/modules/m7_evidence/service.py` | `tests/test_m7_evidence.py::test_concurrent_adjudication_rejects_old_version` `tests/test_m7_evidence.py::test_resume_recovers_missing_contribution` |
| TRAP-26 | 撤销裁决 | 按原行整体回冲，误伤改判后的采纳 | 撤销只回冲本次采纳自己的贡献行 | `app/modules/m7_evidence/service.py` | `tests/test_m7_evidence.py::test_revoke_only_reverses_its_own_contribution` |

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。
        - 补证不改变原批次守恒式：`input_count = 有效 + 隔离` 始终成立；采纳一条后有效 +1、隔离 -1，撤销后回退。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。
