# 部署说明

推荐在有桌面环境的本机运行，因为验证码处理需要用户看到并操作独立 Chromium 窗口。

## Docker

先构建前端，再启动容器：

```bash
cd frontend && npm install && npm run build && cd ..
cp .env.docker.simple .env
docker compose -f docker-compose.simple.yml up -d --build
```

访问 `http://localhost:8011`。SQLite、浏览器配置与导出文件保存在 `marketplace_data` 数据卷中。

纯无头 Docker 环境不能完成可视化人工验证；遇到验证码时建议改在桌面本机运行。部署方需自行确认自动访问目标网站符合当地法律及平台条款，并控制采集频率。
