# Worker Recovery

MY Market Radar 的正常采集队列保持严格串行：`runner._executor` 只有 1 个 worker，避免不同关键词同时操作 Shopee/Lazada 浏览器。

## Heartbeat

运行中的 `AnalysisRun` 保存：

- `worker_id`
- `heartbeat_at`

worker 周期续租。scheduler 每 30 秒检查 `running` run，超过 `RUN_STALE_AFTER_SECONDS` 没有心跳时判定 stale。

## 为什么 stale 不能重新走普通 submit_run

普通 `submit_run()` 会用内存 `_queued_run_ids` 去重。

如果原采集线程是真正卡死：

1. 旧 run_id 仍留在 `_queued_run_ids`；
2. watchdog 即使把数据库状态重置成 pending；
3. 再调用普通 `submit_run(run_id)` 也只会记录“旧线程结束后再排队”；
4. 旧线程永远不结束时，新 worker 永远不会启动。

这会让 heartbeat 看起来存在，但实际无法恢复真正的 hang。

## 当前修复

- **进程启动恢复**：旧进程的线程已经不存在，因此继续使用正常串行 `submit_run()`。
- **运行中 stale 恢复**：先清空旧 `worker_id / heartbeat_at`，再使用独立 `recovery_worker` executor 启动 replacement。
- recovery executor 只用于已经被 watchdog 判定 stale 的 run；正常任务仍严格单线程串行。
- 新 recovery worker 启动后会写入新的 `worker_id`。
- 旧 worker 如果随后恢复，在下一次 heartbeat/checkpoint/finalize 时因为 worker_id 不匹配而失去写入权限。

这里仍然只是普通 worker 标识和数据库状态控制，不使用签名、加密或额外密钥。

## 应用关闭

FastAPI lifespan 在启动 scheduler 后使用 `try/finally` 包住运行阶段。无论服务正常退出，还是 lifespan body 因异常退出，都会调用 `stop_scheduler()` 再结束应用。

这避免开发重载、测试异常或关闭阶段错误让 scheduler 线程跳过显式收口。对应测试同时覆盖正常退出和异常退出两条路径。

## 并发边界

stale replacement 极少数情况下可能和正在苏醒的旧浏览器调用短暂重叠，但旧 worker 已经失去数据库租约，不能提交 checkpoint 或最终结果。正常非 stale 任务仍不会并发执行。
