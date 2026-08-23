# MY Market Radar

面向马来西亚市场的 Shopee 与 Lazada 公开搜索结果竞品分析器。

输入一个商品关键词，系统会按顺序采集所选平台的公开结果，保存价格、公开已售数、评分、评论、地区、排名、广告标记、图片和链接，并给出带数据门槛的机会评分与 Excel 报告。

## 本地启动

要求：Python 3.11+、Node.js 18+、Google Chrome 或 Chromium。

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd ../frontend
npm install
npm run build

cd ..
python start.py
```

启动器会显式打开项目自己的 Chrome/Chromium，而不是系统默认浏览器。浏览器使用独立持久化目录 `backend/data/browser-profile`，采集器通过 Chrome DevTools Protocol 读取公开页面。

应用地址为 `http://localhost:8011`，默认账号：`admin@market.my`，密码：`admin123`。首次登录后建议修改 `SECRET_KEY` 并自行增加账号管理。

## Docker

复制配置并启动：

```bash
cp .env.docker.simple .env
docker compose -f docker-compose.simple.yml up -d --build
```

Dockerfile 使用 Node 多阶段构建前端，并在运行镜像中安装 Chromium，因此不需要预先提交或手工生成 `frontend/dist`。无桌面环境下普通采集使用 headless Chromium；若平台触发验证码/风控，仍需切换到有桌面的本机完成人工验证。

## 数据口径

- 只使用页面公开可见信息，不登录平台账号，不调用卖家后台数据。
- 公开已售数只在各平台内部解释，不跨平台直接比较。
- 需求信号、进入门槛、价格空间均为启发式指标，不是利润或真实销量预测。
- 搜索结果先做关键词相关性过滤，明显偏离关键词的结果不进入机会评分。
- 缺失字段保留为空，不填零，也不会把剩余权重放大；证据不足时明确显示“数据不足”。
- 所选平台都达到样本与完整度门槛后才生成总体机会分；只选一个平台时，该平台达标即可。

## 每日跟踪与人工验证

默认每天 20:00（Asia/Kuala_Lumpur，UTC+8）串行更新启用的关键词。应用错过当天时间后，下一次启动只补跑当天一次，不追赶历史任务。

遇到验证码或风控页时任务会暂停为“需要人工验证”。在桌面环境点击“验证”会激活同一个项目 Chrome 标签页；手动完成验证后保持窗口打开，再点击“继续”。系统不会绕过验证码，也不做浏览器指纹伪造。

## API

- `POST /api/keywords`：创建关键词并立即分析
- `PATCH /api/keywords/{id}`：启停或修改每日跟踪
- `POST /api/keywords/{id}/runs`：手动重新采集
- `GET /api/runs/{id}`：任务进度与结论
- `GET /api/runs/{id}/items`：竞品快照
- `POST /api/runs/{id}/verification-browser`：激活人工验证浏览器
- `POST /api/runs/{id}/resume`：验证后继续
- `GET /api/runs/{id}/report.xlsx`：下载 Excel

## 测试

```bash
cd backend
venv/bin/python -m unittest discover -s tests -v
cd ../frontend
npm run build
```
