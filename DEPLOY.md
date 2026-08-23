# 部署说明

MY Market Radar 默认按“本机私有工具”运行。验证码处理需要可见桌面，因此长期稳定使用优先推荐桌面本机；Docker 无桌面模式适合普通公开页采集，但遇到验证码时不能在容器里完成人工操作。

## 本机运行

```bash
cd backend
python -m venv venv
# Linux/macOS
source venv/bin/activate
# Windows PowerShell 可使用：.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd ../frontend
npm ci
npm run build

cd ..
python start.py
```

`start.py` 默认只监听 `127.0.0.1:8011`，并优先在项目专用 Google Chrome/Chromium 中打开界面。采集浏览器配置保存在 `backend/data/browser-profile`，CDP 默认端口为 `9231`。

只有在根目录没有 `.env` 且数据库为空时，本机 `start.py` 才临时提供 `admin@market.my / admin123`。任何正式部署都不要依赖这个便利路径。

## Docker

Dockerfile 使用多阶段构建自动生成前端，不需要手工提前构建 `frontend/dist`：

```bash
cp .env.docker.simple .env
```

启动前必须在 `.env` 至少设置：

```env
SECRET_KEY=<随机私密字符串>
BOOTSTRAP_ADMIN_EMAIL=<你的管理员邮箱>
BOOTSTRAP_ADMIN_PASSWORD=<首次管理员密码>
ALLOW_REGISTRATION=false
RUN_HEARTBEAT_SECONDS=10
RUN_STALE_AFTER_SECONDS=240
```

然后：

```bash
docker compose -f docker-compose.simple.yml up -d --build
```

Windows 的 `start_docker.bat` 会拒绝空 `BOOTSTRAP_ADMIN_PASSWORD` 和模板 `SECRET_KEY`。密码校验交给 PowerShell，不通过 cmd delayed expansion 解析，因此 `!`、`%`、`&` 等强密码字符不会被启动脚本改写。

访问 `http://localhost:8011`。Compose 默认映射为 `127.0.0.1:8011:8000`，不会直接暴露到局域网/公网。SQLite、浏览器配置与导出数据保存在 `marketplace_data` 数据卷中。Chromium 的 `/dev/shm` 配置为 1GB，降低复杂搜索页随机 tab crash 的概率。

Docker 镜像内置 Chromium；无桌面环境时使用 headless CDP 完成普通采集。如果 Shopee/Lazada 触发验证码，接口会明确提示当前环境不能可视化验证，此时请改在桌面本机运行并人工完成验证。系统不会绕过验证码。

## 首次账号策略

后端不会再无条件创建已知密码账号：

- 数据库为空 + 配置了 `BOOTSTRAP_ADMIN_PASSWORD`：创建一次首次管理员。
- 数据库为空 + 未配置 bootstrap 密码：不创建账号，并记录警告。
- 数据库已有任何用户：不再自动补管理员账号。
- `/api/auth/register` 默认返回 403；只有 `ALLOW_REGISTRATION=true` 才开放。

因此 systemd / Docker / 直接 uvicorn 的首次部署要先准备根目录 `.env`。

## Worker heartbeat 与自动恢复

运行中的 `analysis_runs` 会记录普通的 `worker_id` 和 `heartbeat_at`：

- worker 默认每 `RUN_HEARTBEAT_SECONDS=10` 秒续租。
- scheduler 每 30 秒巡检 running run。
- 超过 `RUN_STALE_AFTER_SECONDS=240` 秒没有心跳时，将该 run 恢复成 pending 并重新排队。
- checkpoint 与最终写入都会校验 worker_id；旧 worker 后续恢复也不能覆盖新 worker 结果。

这里不使用签名、加密或额外密钥，只用普通 worker 标识 + 时间戳控制租约。

正常的 Chrome/CDP 调用本身都有超时，因此 stale 阈值应明显大于单次 `COLLECTION_TIMEOUT_SECONDS`。默认 `45 / 240` 不建议反过来配置。

## Collector health 与 Evidence grade

每次 run 会在 `analysis` JSON 中保存 collector health：raw 卡片数、parsed 数、解析率和关键字段覆盖。分析页和 Excel 都会展示。

Evidence A/B/C/D 会综合：

- 各平台评分是否达到 eligible 门槛
- 数据完整度
- collector health
- 相关样本量

Evidence D 不输出强机会结论；Evidence C 的强推荐会降级为谨慎观察。这样页面结构变化时不会把“采集器坏了”误当成“市场差”。

最终双平台机会分使用平台 `confidence` 作为聚合权重；商品族排序也会按样本量、平台覆盖和 confidence 向中性分收缩，避免 4～8 条极端样本直接冲榜。

## systemd（当前服务器布局）

仓库提供：

- `deploy/my-market-radar.service`：后端服务，工作目录 `/home/james/my-market-radar/backend`
- `deploy/market-verification-browser.service`：项目专用可见 Chrome，profile 位于 `/home/james/my-market-radar/backend/data/browser-profile`

两个服务都使用 `9231` 作为项目 Chrome 的调试端口。Pydantic 配置会读取仓库根目录 `.env`，所以首次启动 systemd 前同样需要设置 `SECRET_KEY` 和 `BOOTSTRAP_ADMIN_PASSWORD`。

部署前请确认 Python 虚拟环境路径与 service 中 `ExecStart` 一致；当前 service 约定 `/home/james/my-market-radar/backend/.venv/bin/python`。如果你的环境叫 `venv`，请把该路径改成 `/home/james/my-market-radar/backend/venv/bin/python`。

更新 service 后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart market-verification-browser.service
sudo systemctl restart my-market-radar.service
```

## 公网入口

`deploy/market.deepworm.bio.conf` 是显式公网反向代理配置，不属于默认本机模式。公网部署时至少应：

- 设置随机 `SECRET_KEY` 和私有 `BOOTSTRAP_ADMIN_PASSWORD`
- 保持 `ALLOW_REGISTRATION=false`，除非确实需要开放注册
- 保持 `/api/auth/register`、`/docs`、`/redoc`、`/openapi.json` 在反向代理层继续关闭
- 仅通过 HTTPS 反向代理暴露，不直接开放应用端口
- 不在登录 UI、文档或启动日志中发布明文密码
- 自行确认自动访问目标平台符合当地法律与平台条款，并控制采集频率

## 更新已有部署

这轮会为 `analysis_runs` 自动补两个 nullable 列：`worker_id`、`heartbeat_at`。现有 `_sync_schema()` 可以完成这种 additive 列升级，不需要引入额外迁移框架。

更新代码后：

1. 备份数据库。
2. 安装最新 Python 依赖（Windows 需要 `tzdata`）。
3. 前端重新 `npm ci && npm run build`，或用 Docker 自动构建。
4. 检查 `.env`：`DEFAULT_RESULTS_LIMIT`、`DEFAULT_DAILY_TIME`、`DEFAULT_TIMEZONE`、`COLLECTION_TIMEOUT_SECONDS`、`RUN_HEARTBEAT_SECONDS`、`RUN_STALE_AFTER_SECONDS`。
5. 重启服务；启动日志应看到 schema sync 自动补列或确认列已存在。
6. 跑一个双平台关键词，确认分析页出现 Evidence 等级和 Collector Health 表。

新任务会冻结创建时的平台/样本数配置；修改跟踪设置不会改变已经运行中的任务。failed/partial 重试会创建新 run，旧历史不会再被覆盖。
