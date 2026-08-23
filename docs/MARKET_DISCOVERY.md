# Market Discovery

`/discovery` 是 MY Market Radar 的候选商品快速筛选入口。当前版本刻意保持确定性和简单：不用 LLM、embedding、向量库或外部趋势 API，直接复用项目已有的 Shopee/Lazada 采集与评分链。

## 第一轮快速发现

每个类目 preset 固定 4 个候选 seed。新建候选默认：

- 双平台按当前 marketplace defaults 扫描；
- 每平台快速样本上限为 10~15 条；
- `tracking_enabled=false`，不会自动进入每日跟踪；
- 结果继续使用 Collector Health、Evidence A/B/C/D、校准机会分和商品族 `ranking_reliability`。

同一组里某个 seed 提交失败不会中止其他候选；页面会汇总失败名单，可再次补提交。

整组按钮只负责**补齐没有稳定结果的候选**：

- 已有 completed/partial 稳定结果的候选直接保留；
- 已有 pending/running/needs_verification 的候选直接等待；
- 只有未扫描、或没有任何稳定结果的候选才补入快速队列。

这样已经做过 20~40 条深扫的候选，不会因为再次点击整组按钮被 10~15 条快速扫描覆盖。

## 一键深度扫描

候选已有稳定结果后，机会榜会显示 `深扫 N` 按钮。深度扫描调用：

```text
POST /api/discovery/keywords/{keyword_id}/deep-scan
```

请求示例：

```json
{
  "results_limit": 40
}
```

也可以临时指定平台：

```json
{
  "results_limit": 40,
  "platforms": ["shopee", "lazada"]
}
```

### 关键行为

深度扫描只修改**本次 AnalysisRun 的冻结 request_config**：

```json
{
  "keyword": "water bottle",
  "platforms": ["shopee", "lazada"],
  "results_limit": 40
}
```

它不会修改 `TrackedKeyword` 的长期设置：

- `results_limit` 保持原值；
- `platforms` 保持原值；
- `tracking_enabled` 保持原值；
- `daily_time / timezone` 保持原值。

因此可以用 15 条/平台做第一轮筛选，再对少数候选临时提升到 20~40 条/平台验证，而不会把快速发现配置永久写回每日跟踪。

如果该关键词已经存在 `pending / running / needs_verification` 任务，deep-scan API 不会重复入队，而是返回当前 active run。

## 推荐使用流程

1. 在“机会发现”选一个方向；
2. 扫描 4 个快速候选；
3. 等 Evidence 和校准机会分稳定；
4. 对 Evidence A/B 且排名靠前的 1~2 个候选点击深扫；
5. 深扫完成后再看商品族可靠度、价格带、竞争压力和 Collector Health；
6. 最后才做供应链、平台费、物流、广告和利润核算。

机会分和发现榜都不是利润预测，也不是平台官方热销榜。
