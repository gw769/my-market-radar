# MY Market Radar

面向马来西亚市场的 Shopee 与 Lazada 公开搜索结果竞品分析器。

输入一个商品关键词，系统会按顺序采集所选平台的公开结果，保存价格、公开已售数、评分、评论、地区、排名、广告标记、图片和链接，并给出带数据门槛的机会评分与 Excel 报告。

## 本地启动

要求：Python 3.11+、Node.js 18+、Google Chrome 或 Chromium。

```bash
cd backend
python -m venv venv
# Linux/macOS
source venv/bin/activate
# Windows PowerShell: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd ../frontend
npm ci
npm run build

cd ..
python start.py
```

`start.py` 只监听 `127.0.0.1:8011`，并显式打开项目自己的 Chrome/Chromium。浏览器使用独立持久化目录 `backend/data/browser-profile`，采集器通过 Chrome DevTools Protocol 读取公开页面，默认 CDP 端口为 `9231`。

如果项目根目录**没有 `.env`**、数据库又是全新的，`start.py` 为本机首次体验临时提供 `admin@market.my / admin123`。这个便利只存在于本机启动器路径；Docker、systemd、直接运行 uvicorn 或有 `.env` 的环境不会自动创建已知默认密码账号。

## Docker

先复制配置：

```bash
cp .env.docker.simple .env
```

然后至少填写：

- `SECRET_KEY`：替换模板值
- `BOOTSTRAP_ADMIN_PASSWORD`：首次空数据库管理员密码
- 可按需修改 `BOOTSTRAP_ADMIN_EMAIL`

再启动：

```bash
docker compose -f docker-compose.simple.yml up -d --build
```

Windows 也可以双击 `start_docker.bat`；启动器会在构建前检查上述安全配置。Compose 默认只映射 `127.0.0.1:8011:8000`，并给 Chromium 分配更大的 `/dev/shm`，减少搜索页随机崩溃。

Dockerfile 使用 Node 多阶段构建前端，并在运行镜像中安装 Chromium。无桌面环境下普通采集使用 headless Chromium；若平台触发验证码/风控，仍需切换到有桌面的本机完成人工验证。

## 账号与注册

- 空数据库只有在显式设置 `BOOTSTRAP_ADMIN_PASSWORD` 时才会自动创建管理员。
- 数据库已经存在任何用户时，启动过程不会偷偷补一个默认管理员。
- `/api/auth/register` 默认关闭；只有设置 `ALLOW_REGISTRATION=true` 才开放自助注册。
- 登录页不包含预填密码。

## 运行时默认值

下面这些 `.env` 配置会同时驱动后端 API 默认值和 Analyze 页面，不再被前端硬编码覆盖：

```env
DEFAULT_RESULTS_LIMIT=20
DEFAULT_DAILY_TIME=20:00
DEFAULT_TIMEZONE=Asia/Kuala_Lumpur
COLLECTION_TIMEOUT_SECONDS=45
```

Windows 环境会通过 Python 依赖 `tzdata` 获得 IANA 时区数据库，因此 `Asia/Kuala_Lumpur` 不依赖操作系统自带时区文件。

## 数据口径

- 只使用页面公开可见信息，不登录平台账号，不调用卖家后台数据。
- 公开已售数只在各平台内部解释，不跨平台直接比较。
- 需求信号、进入门槛、价格空间均为启发式指标，不是利润或真实销量预测。
- 搜索结果先做关键词相关性过滤；配件、明显多件装和低相关漂移不会进入机会评分。
- 缺失字段保留为空，不填零，也不会把剩余权重放大；证据不足时明确显示“数据不足”。
- 有稳定卖家标识时才启用卖家集中度；缺失时不猜。
- 商品族排序来自重复标题属性的轻量聚类，用于缩小验证范围，不等同于平台官方类目。

## 任务、历史与稳定结果

每个 run 创建时会冻结本次 `keyword / platforms / results_limit`，所以运行过程中修改跟踪设置不会改变已经开始的任务口径。

- `latest_run` 表示最近/当前任务，可能是 pending、running、failed 或 needs_verification。
- `latest_result_run` 表示最近一次可用的 completed/partial 结果。
- 新任务运行或失败时，分析、竞品和报告页面继续保留上一份稳定结果。
- failed/partial 点击重试会创建新的 run，不覆盖旧任务和旧快照。
- 验证码属于同一次任务暂停，验证完成后继续原 run。
- 所有平台都没有有效商品时任务会标记为 `failed`，不会生成伪 partial 报告。

Excel 的趋势页只纳入 completed/partial 快照，运行中、验证码暂停和失败任务的恢复 checkpoint 不参与趋势。

## 每日跟踪与人工验证

默认计划由 `DEFAULT_DAILY_TIME` 和 `DEFAULT_TIMEZONE` 决定。所有关键词串行执行，应用错过当天时间后下一次启动只补跑当天一次，不追赶历史任务。

遇到验证码或风控页时任务会暂停为“需要人工验证”。在桌面环境点击“验证”会激活同一个项目 Chrome 标签页；手动完成验证后保持窗口打开，再点击“继续”。验证检测使用明确的 challenge/captcha 信号，不会因为普通的 `Verified Seller` 文案暂停任务。系统不会绕过验证码，也不做浏览器指纹伪造。

## API

- `GET /api/marketplace-defaults`：当前分析默认配置
- `POST /api/keywords`：创建关键词并立即分析
- `PATCH /api/keywords/{id}`：启停或修改每日跟踪
- `POST /api/keywords/{id}/runs`：手动重新采集
- `GET /api/runs/{id}`：任务进度与结论
- `GET /api/runs/{id}/items`：竞品快照
- `POST /api/runs/{id}/verification-browser`：激活人工验证浏览器
- `POST /api/runs/{id}/resume`：验证后继续，或为 failed/partial 创建新 retry run
- `GET /api/runs/{id}/report.xlsx`：下载 Excel

## 测试

```bash
cd backend
venv/bin/python -m unittest discover -s tests -v
cd ../frontend
npm run build
```

GitHub Actions 同时执行后端 unittest 与前端 TypeScript/Vite build。
