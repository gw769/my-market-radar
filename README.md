# MY Market Radar

面向马来西亚市场的 Shopee × Lazada 公开搜索结果分析工具。

当前版本已经从最初的“输入关键词后看搜索结果”升级成一套完整的本地市场验证流程：

- 关键词分析
- 机会发现候选池
- 一键深度扫描
- Shopee / Lazada 公开竞品采集
- 相关性、配件、多件装与价格异常过滤
- 机会评分与商品族排序
- Collector Health
- Evidence A/B/C/D
- 卖家集中度
- Temporal Evidence（跨快照近期动量）
- 每日跟踪
- 验证码人工恢复
- Excel 报告
- Windows / Docker 本地部署

它不是利润预测软件，也不是平台官方热销榜。它的目标是：**把公开可见的市场证据整理成一套可解释、可复查的选品判断。**

---

## 当前推荐使用流程

### 1. 先用“机会发现”快速筛候选

侧边栏进入 **机会发现**，选择一个市场方向：

- 家居收纳
- 厨房饮水
- 宠物用品
- 办公桌面
- 运动出行

当前 Discovery MVP 使用固定、可审计的 seed，不依赖 LLM、embedding、向量库或外部趋势 API。

每组默认 4 个候选，新候选使用约 `10~15` 条/平台做第一轮快速扫描，并且：

```text
tracking_enabled = false
```

不会自动污染每日跟踪任务。

### 2. 对高质量候选点“一键深扫”

已有稳定结果后，可以直接在机会榜点击：

```text
深扫 20 / 40
```

深扫只修改**这一次 AnalysisRun 的冻结 request_config**，不会修改关键词的长期配置：

- 不修改长期 `results_limit`
- 不修改长期平台选择
- 不自动打开每日跟踪
- 不修改 daily time / timezone

API：

```text
POST /api/discovery/keywords/{keyword_id}/deep-scan
```

### 3. 看 Evidence，而不是只看机会分

分析页同时展示：

- 总机会分
- Shopee / Lazada 平台分
- 数据完整度
- Collector Health
- Evidence A/B/C/D
- 商品族排序
- `ranking_reliability`
- 卖家集中度
- 采集异常警告

推荐优先关注 **Evidence A/B**；Evidence C 只能当辅助信号，Evidence D 不输出强选品结论。

### 4. 第二次稳定扫描后看 Temporal Evidence

同一关键词有至少两次 completed / partial 稳定快照后，分析页会显示近期动量：

- 同商品匹配数
- 快照间隔
- 最近有增长商品占比
- Sold / 日
- Review / 日
- 价格波动
- 排名变化
- Temporal reliability

Temporal Evidence 当前是**辅助证据**，暂时不会直接改写主机会分。

这样可以减少“老链接累计 sold 很高，所以看起来需求很强”的误判。

### 5. 最后再看供应链和利润

机会分只回答：

> 当前公开搜索结果是否显示出值得进一步验证的市场空间？

真正采购前仍然需要人工核算：

- 采购成本
- 国际/本地物流
- 平台佣金
- 广告成本
- 退货损耗
- 仓储
- 税费
- 售后
- 实际毛利

---

## 本地启动

要求：

- Python 3.11+
- Node.js 18+
- Google Chrome 或 Chromium

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

cd ..
python start.py
```

访问：

```text
http://localhost:8011
```

`start.py`：

- 只监听 `127.0.0.1:8011`
- 显式使用项目专用 Chrome / Chromium
- 不调用系统默认浏览器作为主流程
- 使用独立浏览器 profile：

```text
backend/data/browser-profile
```

- 默认 Chrome DevTools Protocol 端口：

```text
9231
```

如果根目录**没有 `.env`**、数据库又是空的，本机 `start.py` 为首次体验临时提供：

```text
admin@market.my / admin123
```

这个便利只存在于本机 launcher。Docker、systemd、直接 uvicorn 或已有 `.env` 的环境不会自动创建这个已知密码账户。

---

## Windows 启停

本机模式：

```text
start_local.bat
stop.bat
```

`start_local.bat` 会按顺序寻找：

```text
backend/.venv
backend/venv
系统 Python
```

`stop.bat` 不再按端口无条件杀进程：

- 只停止明确属于 MY Market Radar 的本机 backend
- 不自动杀占用 `3000` 的其他前端项目
- 不能确认属于本项目的 `8011` listener 只提示，不强杀
- `9231` 作为项目专用 Chrome CDP 端口处理
- 不会用通配符误杀普通 Chrome 窗口

Docker 模式请使用：

```text
start_docker.bat
stop_docker.bat
```

不要用 `stop.bat` 去停止 Docker Desktop。

详细说明：

```text
docs/WINDOWS_LAUNCHERS.md
```

---

## Docker

复制配置：

```bash
cp .env.docker.simple .env
```

至少填写：

```env
SECRET_KEY=<至少32字符的私密随机字符串>
BOOTSTRAP_ADMIN_PASSWORD=<首次管理员密码>
BOOTSTRAP_ADMIN_EMAIL=<管理员邮箱>
```

然后：

```bash
docker compose -f docker-compose.simple.yml up -d --build
```

Compose 默认：

```text
127.0.0.1:8011:8000
```

不会直接暴露到局域网或公网。

Docker 镜像内置 Chromium。无桌面环境可以完成普通 headless 采集；遇到验证码 / 风控页时，仍然需要切换到有桌面的本机人工处理。

---

## 账号与安全默认值

- 空数据库只有显式设置 `BOOTSTRAP_ADMIN_PASSWORD` 才会创建管理员
- 已有任何用户后不会偷偷补默认管理员
- `/api/auth/register` 默认关闭
- 只有 `ALLOW_REGISTRATION=true` 才开放自助注册
- 登录页不预填密码
- MySQL 明确配置但连接失败时默认 fail-fast，不静默切到一个空 SQLite
- JWT `SECRET_KEY` 会检查最低长度和已知不安全模板值

Excel 报告也做了公式注入保护：

- 用户关键词
- 商品标题
- 店铺
- 地区
- 建议文字
- marketplace 公开文本

如果字符串以：

```text
=  +  -  @
```

等 Excel 公式触发字符开头，会按普通文本写入，而不是公式。

详细说明：

```text
docs/REPORT_SECURITY.md
```

FastAPI 的 SPA fallback 也对静态文件路径做 containment 校验，`../`、绝对路径以及指向 `frontend/dist` 外部的 symlink 不会被当成静态文件返回。

详细说明：

```text
docs/STATIC_SERVING.md
```

---

## 运行时配置

常用 `.env`：

```env
DEFAULT_RESULTS_LIMIT=20
DEFAULT_DAILY_TIME=20:00
DEFAULT_TIMEZONE=Asia/Kuala_Lumpur
COLLECTION_TIMEOUT_SECONDS=45
RUN_HEARTBEAT_SECONDS=10
RUN_STALE_AFTER_SECONDS=240
```

Windows 通过 Python `tzdata` 获得 IANA 时区数据，因此 `Asia/Kuala_Lumpur` 不依赖 Windows 自带时区文件。

---

## 数据采集与 Chrome

项目通过 Chrome DevTools Protocol 读取公开页面。

### 浏览器行为

- Windows / macOS / Linux 自动查找 Chrome / Chromium
- 可以通过 `BROWSER_EXECUTABLE` 手工指定
- 使用项目自己的 profile
- CDP 默认 `9231`
- 不复用系统日常浏览器 profile

### 多标签页选择

如果 Chrome 中同时存在：

- Shopee/Lazada 首页
- 搜索页
- 验证码页
- security/challenge 页

系统不会再简单拿 `/json/list` 的第一个域名匹配项。

正常采集优先：

```text
Shopee /search
Lazada /catalog
```

人工验证优先：

```text
captcha / challenge / verify / security
Shopee xiapibuy
Lazada acs-m security host
```

并且按真实 hostname 匹配，不会因为查询参数里出现 `shopee.com.my` 就误认成 Shopee 页面。

详细说明：

```text
docs/BROWSER_TABS.md
```

---

## 数据口径

系统只使用页面公开可见信息。

不会：

- 登录卖家后台
- 调用商家私有数据
- 推算真实 GMV
- 推算真实月销量
- 推算曝光率或转化率
- 绕过验证码
- 做浏览器指纹伪造

### 搜索结果过滤

进入机会评分前会过滤：

- 明显配件 / replacement
- spare part
- cover / case
- bundle
- multipack
- `1+1`
- `buy 1 free 1`
- `2 units`
- 低关键词相关搜索漂移

例如关键词：

```text
water bottle
```

不会因为大量 bottle lid / replacement cap 的销量而直接给主体水杯加分。

### 价格

类似：

```text
RM 10 - RM 40
```

不会简单拿 RM10 当作真实可比单价。

### 公开计数

支持：

```text
1.2k sold
1.2k+ sold
1.2k reviews
```

但不同平台公开 sold 的定义不保证一致，因此**只在平台内部解释，不直接跨平台比较绝对 sold 数**。

---

## 机会评分

每个平台基础评分仍是确定性规则：

```text
需求信号      40%
进入可行性    35%
价格空间      25%
```

缺失字段：

- 不填 0
- 不把剩余权重放大到 100%
- 缺失维度向中性证据处理
- 完整度不足时直接“数据不足”

### 卖家集中度

有稳定 seller/shop identity 时才启用。

真正进入评分的是归一化集中度指标；Top5 share 主要用于解释，不再直接把“小样本 Top5=100%”误判成严重垄断。

卖家身份覆盖不足时，这个维度自动降权或不参与，不伪造数据。

---

## 跨平台 calibration

平台基础评分完成后，再进入 calibration：

- 双平台总机会分按 `confidence` 加权
- 任一所选平台未达到 eligible 门槛时，不靠另一平台强行补总分
- 商品族内部平台分同样按 confidence 加权
- 小样本商品族向 50 中性分收缩
- 单平台 cluster 会降低排名可靠度

商品族保留：

```text
raw_opportunity_score
opportunity_score
ranking_reliability
```

避免 4~8 条极端样本直接冲到第一名。

代码：

```text
backend/app/services/marketplace/calibration.py
```

---

## Collector Health

市场真的没数据和采集器坏了都可能表现成“样本少”，所以两者分开判断。

每个平台记录：

- raw DOM 行数
- raw 唯一商品数
- parsed 商品数
- 解析率
- 目标样本覆盖
- price coverage
- sold coverage
- review coverage
- rating coverage
- seller identity coverage

状态：

```text
healthy
degraded
unhealthy
empty
error
```

如果页面明明存在大量商品卡，但 parser 突然只能解析很少结果，会提示可能出现页面结构变化，而不是直接解读成“市场需求弱”。

---

## Evidence A/B/C/D

综合以下证据：

- 平台评分是否 eligible
- 数据完整度
- Collector Health
- 相关样本量
- 平台覆盖

等级：

### A · 高可信

双平台证据完整，适合进入下一步验证。

### B · 可参考

核心数据可用，但完整度/平台/样本略弱。

### C · 弱证据

只能作为辅助信号。即使原始算法给出“建议尝试”，也会降级成“谨慎观察”。

### D · 证据不足

- 不输出强机会结论
- 商品族 verdict 同样降为数据不足
- 移除“最高商品族优先验证”一类强建议

---

## Temporal Evidence

需要至少两次稳定快照。

匹配方式：

```text
(platform, item_id)
```

指标包括：

- matched items
- match rate
- activity share
- sold delta
- sold velocity / day
- review delta
- review velocity / day
- price change
- rank change
- temporal reliability

两次扫描间隔少于 6 小时会标为 weak，避免把平台刷新噪声当趋势。

Temporal Evidence 当前只作为辅助证据，不直接修改主机会分。

详细说明：

```text
docs/TEMPORAL_EVIDENCE.md
```

---

## 任务、历史与稳定结果

每个 run 创建时冻结：

```text
keyword
platforms
results_limit
```

因此运行过程中修改长期关键词设置，不会改变已经开始的任务。

前端区分：

```text
latest_run
latest_result_run
```

- `latest_run`：当前/最近任务，可能 pending、running、failed、needs_verification
- `latest_result_run`：最近一次 completed / partial 稳定结果

因此：

- 新任务正在运行时，分析页继续显示上一份稳定结果
- 新任务失败时，上一份稳定结果不会消失
- Competitors / Reports / Dashboard / Analysis 页面在页面可见时自动刷新
- 页面隐藏时暂停前端轮询，后端采集和 scheduler 不受影响

---

## Worker heartbeat 与恢复

运行中的 `AnalysisRun` 保存：

```text
worker_id
heartbeat_at
```

scheduler 定期检查 stale worker。

普通任务仍然严格单 worker 串行，避免多个关键词同时操作浏览器。

如果某个 worker 真正卡死：

- watchdog 使旧 worker lease 失效
- live stale recovery 使用独立 recovery executor 启动 replacement
- replacement 获得新的 worker id
- 旧 worker 即使后来恢复，也不能再 checkpoint / finalize 覆盖新结果

这里仅使用普通 worker id + 时间戳，不引入签名、加密、额外密钥或复杂分布式锁。

FastAPI lifespan 也使用 `try/finally` 保证正常关闭或异常退出时都会停止 scheduler。

详细说明：

```text
docs/WORKER_RECOVERY.md
```

---

## 每日跟踪与人工验证

默认计划：

```text
DEFAULT_DAILY_TIME
DEFAULT_TIMEZONE
```

关键词按队列串行执行。

平台触发验证码时：

1. run 进入 `needs_verification`
2. 点击“验证”
3. 项目 Chrome 激活真正的 challenge/security tab
4. 用户手动完成验证码
5. 保持 Chrome 窗口打开
6. 点击“继续”

系统不会自动绕过验证码。

---

## Excel 报告

报告包含：

- 综合结论
- Shopee 竞品
- Lazada 竞品
- 每日价格与排名趋势
- 数据口径说明

趋势页只纳入：

```text
completed
partial
```

不会把：

```text
running
needs_verification
failed
```

产生的恢复 checkpoint 混进正式趋势。

---

## API

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

## 文档

更细的实现说明：

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

---

## 已知限制

当前版本仍然有明确边界：

1. **Discovery 还是 preset-based MVP**
   - 不是全站自动扫描
   - 不是官方趋势榜
   - 还没有外部关键词/类目趋势源

2. **商品族是轻量文本聚类**
   - 用于缩小验证范围
   - 不等同于平台官方类目或语义 embedding cluster

3. **Lazada seller identity 覆盖可能较低**
   - 缺稳定 seller/shop identity 时不会强算卖家集中度

4. **Temporal Evidence 仍是辅助证据**
   - 当前不会自动改变主机会分
   - 需要至少两次稳定快照

5. **公开页面结构会变化**
   - Collector Health 会尽量区分市场数据弱和 parser 异常
   - adapter 仍需要持续维护

6. **机会分不是利润预测**
   - 不包含真实采购、物流、广告、平台费和退货成本

---

## 测试

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

GitHub Actions 会同时运行：

- backend unittest
- frontend TypeScript / Vite build

建议所有功能改动都在 CI 全绿后再合入 `main`。
