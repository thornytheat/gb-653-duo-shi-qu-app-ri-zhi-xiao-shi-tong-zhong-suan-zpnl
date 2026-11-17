# 多时区应用日志小时桶重算

        面向一线复核人员的单服务数据工作台。样例数据随仓库提供，结论保留行号、原因码和原值。

        ## 业务模块

        - M1 日志采信：`/api/logs/import`
- M2 时间解析：`/api/time/normalize`
- M3 事件去重：`/api/events/dedupe`
- M4 小时分桶：`/api/buckets/rebuild`
- M5 迟到修订：`/api/watermark/advance`
- M6 批次对账：`/api/export/buckets.csv`
- M7 补证裁决：`/api/evidence/intake`、`/api/evidence/candidates`、`/api/evidence/adjudicate`、`/api/evidence/revoke`、`/api/evidence/resume`

        ## 核心规则

        事件先归一到 UTC，DST 重叠时保留 fold；小时桶右端开，水位只能前进且迟到事件仍进入修订。

        ## 补证裁决

        - 原批次只读：接入 `/sample_data/events_dirty.jsonl` 后，隔离原行（行号、原因码、原值）单独留证，补证不覆盖原行。
        - 同一原行可并存多个候选（供应方重发、人工补录），候选一律先归一到 UTC 再入桶；未裁决的冲突不入桶。
        - 裁决必须携带 `expected_version`，并发提交时旧版本按 `STALE_VERSION` 拒绝；采纳需给出 `reason`，采纳理由与原行、候选一并留档。
        - 采纳后更新有效/隔离计数与小时桶；重试同一裁决只回放结果，不重复贡献；`/api/evidence/resume` 可从已采纳位置补账。
        - 撤销只回冲本次采纳自己的贡献，不影响同一原行的其他采纳。
        - 回读：`/api/evidence/batch` 返回待补证、待裁决、已采纳；`/api/evidence/buckets` 返回桶与每次贡献的来源。

        ## 启动

        ```bash
        docker compose up --build
        ```

        浏览器访问 `http://localhost:18212`。运行测试：

        ```bash
        docker compose exec -T app pytest -q
        ```

        ## 样例流程

        依次运行六个模块；最后在批次页复核输入、有效、隔离与输出合计。同一输入摘要重复执行时复用原 `run_id`。补证裁决页 `/evidence` 用于坏行复核。

        ## 已知不覆盖

        不处理加密文件与需要外部密钥的对象；不连接真实生产存储。
