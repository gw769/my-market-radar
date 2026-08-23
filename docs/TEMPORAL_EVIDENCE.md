# Temporal Evidence

MY Market Radar 的基础机会分主要来自当前一次公开搜索快照。累计 `sold` 很有用，但它有一个明显局限：老 listing 可能因为历史累积看起来需求很强，却不代表最近仍然活跃。

Temporal Evidence 用最近两次**稳定 completed/partial run** 的同平台同 `item_id` 快照做对比，补充“近期有没有继续增长”的证据。

## 当前行为

只读 API：

```text
GET /api/trends/keywords/{keyword_id}
```

它不会修改机会分、关键词设置或历史快照。

第一次只有一份稳定结果时返回：

```text
status = insufficient_history
```

有两份稳定结果后，系统匹配：

```text
(platform, item_id)
```

并计算：

- `matched_items`：两次都出现的商品数；
- `match_rate`：当前商品里有历史匹配的比例；
- `sold_delta`：公开累计已售的非负增量；
- `review_delta`：评论数的非负增量；
- `activity_share`：sold 或 review 至少一项有增长的匹配商品占比；
- `median_sold_velocity_per_day`：按快照间隔换算的中位 sold 增量/日；
- `median_review_velocity_per_day`：中位 review 增量/日；
- `median_abs_price_change_pct`：匹配商品价格绝对变化的中位百分比；
- `median_rank_change`：搜索排名中位变化，正值表示排名改善；
- `reliability`：按匹配样本、匹配率和时间间隔计算的证据可靠度。

## 时间门槛

两次稳定快照至少间隔 6 小时，才计算“每日速度”。

间隔不足 6 小时：

- 仍显示匹配和原始增量；
- `median_*_velocity_per_day` 保持为空；
- 状态标记为 `weak`；
- 明确提示短间隔可能受平台展示刷新噪声影响。

默认每日跟踪的 24 小时间隔更适合看这个指标。

## 如何解读

当可靠度足够且 `activity_share >= 60%`：

> 多数匹配商品最近仍有 sold/review 增长，当前需求不只是历史累计。

当可靠度足够且 `activity_share <= 20%`：

> 当前页面可能存在高累计销量，但近期增长面较窄，应谨慎解读 demand score。

当价格中位绝对变化超过 20%：

> 价格带近期波动较大，可能受促销、规格或价格策略变化影响，利润测算要留余量。

## 为什么暂时不直接改机会分

这是第一版历史证据层。不同品类的公开 sold 刷新粒度、商品生命周期和销量速度差异很大，直接给 `sold/day` 一个固定权重容易过拟合。

当前策略是：

1. 先把历史动量独立展示；
2. 收集真实运行数据；
3. 确认不同品类的稳定分布；
4. 再决定是否把 temporal signal 温和加入 demand calibration。

这样不会因为新增一个未经校准的指标，反而破坏现有可解释评分。
