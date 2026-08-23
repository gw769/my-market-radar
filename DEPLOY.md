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

`start.py` 默认只监听 `127.0.0.1:8011`，并优先在项目专用 Google Chrome/Chromium 中打开界面。采集浏览器配置保存在 `backend/data/browser-profile`，CDP 默认端口为 `9231`，与旧项目的 `9223` 隔离。

## Docker

Dockerfile 已经使用多阶段构建自动生成前端，不需要手工提前构建 `frontend/dist`：

```bash
cp .env.docker.simple .env
# 至少修改 SECRET_KEY
docker compose -f docker-compose.simple.yml up -d --build
```

访问 `http://localhost:8011`。Compose 默认映射为 `127.0.0.1:8011:8000`，不会直接暴露到局域网/公网。SQLite、浏览器配置与导出数据保存在 `marketplace_data` 数据卷中。

Docker 镜像内置 Chromium；无桌面环境时使用 headless CDP 完成普通采集。如果 Shopee/Lazada 触发验证码，接口会明确提示当前环境不能可视化验证，此时请改在桌面本机运行并人工完成验证。系统不会绕过验证码。

## systemd（当前服务器布局）

仓库提供：

- `deploy/my-market-radar.service`：后端服务，工作目录 `/home/james/my-market-radar/backend`
- `deploy/market-verification-browser.service`：项目专用可见 Chrome，profile 位于 `/home/james/my-market-radar/backend/data/browser-profile`

两个服务都使用 `9231` 作为项目 Chrome 的调试端口。部署前请确认 Python 虚拟环境路径与 service 中 `ExecStart` 一致；当前 service 约定 `/home/james/my-market-radar/backend/.venv/bin/python`。如果你的环境叫 `venv`，请把该路径改成 `/home/james/my-market-radar/backend/venv/bin/python`。

更新 service 后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart market-verification-browser.service
sudo systemctl restart my-market-radar.service
```

## 公网入口

`deploy/market.deepworm.bio.conf` 是显式公网反向代理配置，不属于默认本机模式。公网部署时至少应：

- 修改 `.env` 中的 `SECRET_KEY`
- 不使用示例默认密码
- 保持 `/api/auth/register`、`/docs`、`/redoc`、`/openapi.json` 对公网关闭
- 仅通过 HTTPS 反向代理暴露，不直接开放应用端口
- 自行确认自动访问目标平台符合当地法律与平台条款，并控制采集频率
