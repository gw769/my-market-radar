# MY Market Radar

面向马来西亚市场的 Shopee × Lazada 公开搜索结果分析工具。

当前版本推荐直接运行在 **Windows / macOS / Linux 桌面环境**，使用项目自己的可见 Chrome / Chromium 完成采集和人工验证码处理。Docker 相关文件仍保留，但不再作为推荐运行方式。

项目目标不是预测利润或复制平台后台，而是把公开搜索结果整理成一套可解释、可复查的市场验证流程。

## 主要功能

- Shopee Malaysia / Lazada Malaysia 公开搜索结果采集
- 关键词分析
- preset-based 机会发现
- 一键深扫 20 / 40 条
- 配件、多件装、低相关搜索漂移过滤
- 价格区间和公开 sold / review 解析
- demand / entry_ease / price_room 机会评分
- 双平台 confidence 加权
- 商品族轻量拆分和 `ranking_reliability`
- 卖家集中度（有稳定 seller identity 时才启用）
- Collector Health
- Evidence A/B/C/D
- Temporal Evidence 跨快照近期动量
- 每日跟踪
- worker heartbeat / stale recovery
- 人工验证码恢复
- Excel 报告

## 推荐使用流程

1. 在“机会发现”里先快速扫候选，或直接输入关键词。
2. 先看 Collector Health 和 Evidence，不要只看一个机会分。
3. 对 Evidence A/B 且排名靠前的候选点“深扫 20 / 40”。
4. 第二次稳定扫描后看 Temporal Evidence，判断累计 sold 是否真的还有近期增长。
5. 最后再人工核算采购、物流、平台费、广告、退货、税费和真实毛利。

机会分不是利润预测，也不是平台官方热销榜。

---

# 本机启动（推荐）

要求：

- Python 3.11+
- Node.js 18+
- Google Chrome 或 Chromium

第一次安装：

```bash
cd backend
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows PowerShell
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt

cd ../frontend
npm ci
npm run build
```

然后从项目根目录启动：

```bash
python start.py
```

访问：

```text
http://localhost:8011
```

`start.py` 会：

- 只监听 `127.0.0.1:8011`
- 显式使用项目专用 Chrome / Chromium
- 使用独立浏览器 profile：`backend/data/browser-profile`
- 使用 CDP 端口 `9231`
- 保持浏览器会话，方便 Shopee / Lazada 人工验证

如果项目根目录没有 `.env`，并且数据库是全新的，本机 launcher 首次体验会临时提供：

```text
admin@market.my / admin123
```

这个便利只用于 localhost 本机启动器，不应用于公网部署。

## Windows

直接使用：

```text
start_local.bat
stop.bat
```

`start_local.bat` 会依次寻找：

```text
backend/.venv
backend/venv
系统 Python
```

`stop.bat` 采用保守停止策略：

- 只停止确认属于 MY Market Radar 的 backend
- 不按端口无条件杀进程
- 不自动杀其他项目常用的 3000 端口
- 9231 只用于项目专用 Chrome
- 不会用窗口标题通配符误杀普通 Chrome

详细说明见 `docs/WINDOWS_LAUNCHERS.md`。

---

# 为什么现在推荐本机而不是 Docker

普通公开搜索页在 headless Chromium 中可以采集，但 Shopee / Lazada 一旦触发验证码或风控页，就需要人工处理。

本机模式下：

1. run 进入 `needs_verification`
2. 页面点击“验证”
3. 项目 Chrome 激活真正的 challenge / captcha / security tab
4. 用户手动完成验证
5. 保持 Chrome 窗口打开
6. 点击“继续”

项目不会绕过验证码，也不做浏览器指纹伪造。

纯 Docker headless 环境没有可见桌面，因此无法完成这一步。为了让采集和验证码恢复保持同一个浏览器 profile、同一个 CDP 会话和最简单的运维路径，当前推荐直接使用本机桌面模式。

Dockerfile、`docker-compose.simple.yml`、`start_docker.bat` 等文件暂时保留，主要用于无验证码的 headless 场景或后续部署实验，不作为当前首选。

---

# 浏览器行为

项目通过 Chrome DevTools Protocol 读取公开页面。

正常采集优先选择：

```text
Shopee /search
Lazada /catalog
```

人工验证优先选择：

```text
captcha
challenge
verify
security
Shopee xiapibuy
Lazada acs-m security host
```

平台标签页按真实 hostname 判断，不会因为查询参数中出现平台域名而误选错误 tab。

详细说明见 `docs/BROWSER_TABS.md`。

---

# 数据采集与深扫

深扫滚动过程中会累计已经出现过的唯一商品，而不是只保留最后一屏 DOM。

去重优先级：

1. `shop_id + item_id`
2. `item_id`
3. canonical product href
4. 标题 / 价格等保守 fallback

同一商品后续再次出现时可以补全 sold、title 等字段，同时保持第一次出现的搜索顺序。

这主要解决 Shopee / Lazada 虚拟列表滚动后旧卡片退出 DOM，导致 20~40 条深扫实际只剩最后一屏的问题。

---

# 搜索结果过滤

进入评分前会过滤明显不属于主体商品的结果，例如：

- replacement / spare part
- cover / case / lid / cap
- bundle / combo
- multipack
- `1+1`
- `buy 1 free 1`
- `2 units`
- 低关键词相关搜索漂移

例如关键词：

```text
water bottle
```

`Replacement Lid for Water Bottle` 不会因为销量高就给主体水杯市场加分。

类似：

```text
RM 10 - RM 40
```

的变体价格区间也不会直接拿 RM10 当作可比单价。

---

# 机会评分

每个平台基础维度：

```text
需求信号      40%
进入可行性    35%
价格空间      25%
```

缺失字段：

- 不填 0
- 不把剩余权重放大到 100%
- 缺失维度向中性证据收缩
- 核心证据不足时直接“数据不足”

双平台最终分按各平台 `confidence` 加权；任一所选平台未达到 eligible 门槛时，不靠另一平台强行补出总分。

商品族小样本和单平台 cluster 会向 50 中性分收缩，并显示：

```text
raw_opportunity_score
opportunity_score
ranking_reliability
```

---

# Collector Health 与 Evidence

市场真的没数据和 parser 坏了都会表现成“样本少”，所以系统分开判断。

Collector Health 会看：

- raw DOM 行数
- raw 唯一商品数
- parsed 商品数
- 解析率
- 目标样本覆盖
- price / sold / review / rating coverage
- seller identity coverage

状态包括：

```text
healthy
degraded
unhealthy
empty
error
```

Evidence 综合平台 eligible、数据完整度、Collector Health、样本量和平台覆盖：

- **A · 高可信**：适合进入下一步验证
- **B · 可参考**：核心证据可用
- **C · 弱证据**：只能辅助判断；强结论会降级
- **D · 证据不足**：不输出强机会结论

---

# Temporal Evidence

至少需要两次 completed / partial 稳定快照。

匹配方式：

```text
(platform, item_id)
```

指标包括：

- match rate
- activity share
- sold delta / sold velocity per day
- review delta / review velocity per day
- price change
- rank change
- temporal reliability

Temporal Evidence 与主评分使用相同相关性门槛。配件、替换件、明显套装和低相关漂移不会参与近期动量，因此不会出现“杯盖最近销量暴涨 → 水杯需求被抬高”的污染。

两次扫描间隔少于 6 小时会标为 weak。

当前 Temporal Evidence 仍是辅助证据，不直接修改主机会分。

详细说明见 `docs/TEMPORAL_EVIDENCE.md`。

---

# 任务与恢复

每个 run 创建时冻结：

```text
keyword
platforms
results_limit
```

前端区分：

```text
latest_run
latest_result_run
```

新任务运行或失败时，上一份稳定 completed / partial 结果继续保留。

运行中的任务保存：

```text
worker_id
heartbeat_at
```

普通采集保持单 worker 串行。如果 worker 真正卡死，watchdog 会使旧 lease 失效，并使用独立 recovery worker 启动 replacement；旧 worker 即使恢复，也不能再覆盖新的 checkpoint / finalize。

这里使用普通 worker id + 时间戳，不引入额外密码学或复杂分布式锁。

详细说明见 `docs/WORKER_RECOVERY.md`。

---

# Excel 报告

报告包含：

- 综合结论
- Shopee 竞品
- Lazada 竞品
- 每日价格与排名趋势
- 数据口径说明

所有外部字符串经过 Excel formula sanitization。商品标题、店铺、地区、关键词或建议文字以 `= + - @` 等公式触发字符开头时，会作为普通文本写入。

趋势页只纳入 `completed / partial` 稳定快照。

详细说明见 `docs/REPORT_SECURITY.md`。

---

# API

主要接口：

```text
GET    /api/marketplace-defaults
POST   /api/keywords
GET    /api/keywords
PATCH  /api/keywords/{id}
POST   /api/keywords/{id}/runs
POST   /api/discovery/keywords/{id}/deep-scan
GET    /api/runs/{id}
GET    /api/runs/{id}/items
POST   /api/runs/{id}/verification-browser
POST   /api/runs/{id}/resume
GET    /api/runs/{id}/report.xlsx
GET    /api/trends/keywords/{id}
GET    /api/dashboard
```

---

# 已知限制

1. Discovery 仍是 preset-based MVP，不是平台全站趋势引擎。
2. 商品族是轻量文本聚类，不等同于平台官方类目或 embedding cluster。
3. Lazada seller identity 公开覆盖可能不足，因此卖家集中度有时不会参与评分。
4. Temporal Evidence 仍是辅助证据，需要至少两次稳定快照。
5. Shopee / Lazada 页面结构会变化，adapter 仍需要维护；Collector Health 用于尽早发现 parser drift。
6. 机会分不包含真实采购、物流、广告、平台费、退货和税费，因此不是利润预测。

---

# 文档

```text
DOCS.md
DEPLOY.md
docs/MARKET_DISCOVERY.md
docs/TEMPORAL_EVIDENCE.md
docs/WORKER_RECOVERY.md
docs/BROWSER_TABS.md
docs/WINDOWS_LAUNCHERS.md
docs/REPORT_SECURITY.md
docs/STATIC_SERVING.md
```

# 测试

后端：

```bash
cd backend
python -m unittest discover -s tests -v
```

前端：

```bash
cd frontend
npm ci
npm run build
```

GitHub Actions 同时运行 backend unittest 和 frontend TypeScript / Vite build。功能改动建议在 CI 全绿后再合入 `main`。
