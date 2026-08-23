# 功能与评分说明

## 机会评分

每个平台独立计算：需求信号 40%、进入门槛 35%、价格空间 25%。搜索结果先按标题与关键词做保守相关性过滤，明显配件、替换件、多件装和低相关漂移仍保留在原始快照中，但不进入机会评分。

缺失公开字段不会当成 0，也不会把剩余权重重新放大到 100%。缺失证据会把对应维度向中性分收缩，并降低数据完整度。只有相关样本、价格覆盖、需求证据、核心评分证据和总体完整度达到最低门槛时，平台才有资格输出结论；否则显示“数据不足”。

选择多个平台时，所有所选平台都必须达到 eligible 门槛才生成总体机会分。总体分不是简单平均，而是按各平台 `confidence` 加权；完整度高的平台影响更大。只选择单平台时，该平台达到门槛即可形成总体结论。

价格空间不会简单把“越分散越好”。极端价格离散通常意味着不同规格、套装、促销或子品类混合，会被惩罚并提示进一步拆分。

## 卖家集中度

在能稳定识别 `shop_id` 或 seller name 时，系统使用归一化 HHI 衡量卖家集中度。卖家身份覆盖不完整时，该指标权重按证据可靠度衰减；覆盖不足时不猜，也不会输出强“头部垄断”提示。

## 商品族校准

搜索标题中的重复属性会形成轻量商品族，用来缩小验证范围，不等于平台官方类目。

商品族的平台分按 confidence 加权；样本少、只出现在单平台或 cluster confidence 较低时，最终分会向 50 中性分收缩。页面同时展示：

- `raw_opportunity_score`：校准前信号；
- `opportunity_score`：校准后排名分；
- `ranking_reliability`：排序可靠度。

## Collector Health 与 Evidence A/B/C/D

评分证据和采集器是否正常是两件事。每个平台额外记录 Collector Health：

- DOM raw 行数和去重后的唯一商品数；
- parsed 商品数与解析率；
- 价格、销量、评论、评分、卖家标识覆盖率；
- healthy / degraded / unhealthy / empty / error 状态。

如果页面存在大量唯一 raw cards，但 parser 突然只能解析很少结果，会提示页面结构可能变化，不会把采集器故障误判成市场差。

Evidence 综合平台 eligible、数据完整度、Collector Health 和样本量：

- A：高可信；
- B：可参考；
- C：弱证据，强推荐会降级为“谨慎观察”；
- D：证据不足，不输出强总分，商品族也不输出强 verdict。

## 快照、稳定结果与历史

每个 run 创建时冻结本次 `keyword / platforms / results_limit`。运行中修改关键词设置不会改变已经开始的任务口径。

`latest_run` 表示最近任务；`latest_result_run` 表示最近一次 completed/partial 稳定结果。新任务运行、失败或等待验证码时，分析、竞品和报告页面继续保留上一份稳定结果。

Excel 趋势只纳入 completed/partial 快照；running、needs_verification、failed 的恢复 checkpoint 不进入稳定趋势。

## Temporal Evidence

有至少两次稳定快照后，系统按同平台同 `item_id` 对比最近两次结果，显示近期：

- sold / review 增量；
- 换算到每日的中位增量；
- 有增长商品占比；
- 价格变化；
- 搜索排名变化；
- 历史匹配率和 temporal reliability。

两次快照少于 6 小时时不计算每日速度。第一版 Temporal Evidence 只作为辅助证据，不直接改写主机会分，避免不同品类的 sold 刷新口径未经校准就污染评分。

详见 `docs/TEMPORAL_EVIDENCE.md`。

## Market Discovery

“机会发现”提供固定、可审计的类目候选 preset。每组 4 个 seed，用较小样本做第一轮快速筛选，复用同一套 Shopee/Lazada 采集、Collector Health、Evidence 和评分链，不引入 LLM、向量库或第二套评分器。

新发现候选默认 `tracking_enabled=false`。已有稳定候选不会被整组快速扫描覆盖；高价值候选可点“深扫 N”临时提高单次 run 的 `results_limit`，但不会修改关键词长期设置。

详见 `docs/MARKET_DISCOVERY.md`。

## Worker Heartbeat / Recovery

运行中的 AnalysisRun 保存普通 `worker_id + heartbeat_at`。worker 周期续租；scheduler 会巡检超时 running run 并重新排队。checkpoint 和最终写入通过当前 worker_id 条件更新校验租约，旧 worker 恢复后不能覆盖新 worker 结果。

这里不使用签名、加密或额外密码学机制。

## 浏览器与验证

本地启动显式使用项目自己的 Google Chrome/Chromium，使用独立 `backend/data/browser-profile` 和 CDP 调试端口，不依赖系统默认浏览器。Windows、macOS、Linux 会分别查找 Chrome/Chromium，也可通过 `BROWSER_EXECUTABLE` 指定。

Docker 镜像内置 Chromium，可在无桌面环境使用 headless CDP 完成普通公开页面采集；验证码/风控页仍需要可见桌面和人工操作。系统不会绕过验证码，也不做浏览器指纹伪造。

## 前端实时状态

Dashboard、Tracking、Competitors、Analysis、Reports、Discovery 等运行状态页面在可见时会定期刷新；浏览器标签页隐藏后暂停前端轮询，后台采集和 scheduler 不受影响。稳定结果完成后页面会自动切换，不需要手动刷新。

## 边界

本工具不提供真实曝光率、转化率、广告费、退货率、利润预测、推算月销量或自动备货计划。公开已售、评论、价格与排名都按页面当时可见口径保存；最终采购仍需要供应链、物流、平台费、广告和利润核算。
