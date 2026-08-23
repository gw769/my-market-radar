# MY Market Radar

面向马来西亚市场的 Shopee 与 Lazada 公开搜索结果竞品分析器。

输入一个商品关键词，系统会按顺序采集两个平台各最多 20 条公开结果，保存价格、公开已售数、评分、评论、店铺、地区、排名、广告标记、图片和链接，并给出可复现的机会评分与 Excel 报告。

## 本地启动

要求：Python 3.11+、Node.js 18+。

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

浏览器打开 `http://localhost:8011`，默认账号：`admin@market.my`，密码：`admin123`。首次登录后建议修改 `SECRET_KEY` 并自行增加账号管理。

## 数据口径

- 只使用页面公开可见信息，不登录平台账号，不调用卖家后台数据。
- 公开已售数只在各平台内部解释，不跨平台直接比较。
- 需求信号、进入门槛、价格空间均为启发式指标，不是利润或真实销量预测。
- 两个平台各至少 10 条有效结果时才给出综合评分；否则仅展示平台结论和数据完整度。
- 缺失字段保留为空，不填零、不虚构。

## 每日跟踪与人工验证

默认每天 20:00（Asia/Kuala_Lumpur，UTC+8）串行更新启用的关键词。应用错过当天时间后，下一次启动只补跑当天一次，不追赶历史任务。

浏览器使用独立持久化目录 `backend/data/browser-profile`，Shopee 与 Lazada 两个 Google Chrome 标签页常驻。采集器只通过 Chrome 自带调试协议读取公开页面，不依赖浏览器自动化框架。遇到验证码或风控页时任务会暂停为“需要人工验证”，保留上次成功数据；手动完成验证后保持窗口打开，再点击“继续”。系统不会绕过验证码，也不做浏览器指纹伪造。

## API

- `POST /api/keywords`：创建关键词并立即分析
- `PATCH /api/keywords/{id}`：启停或修改每日跟踪
- `POST /api/keywords/{id}/runs`：手动重新采集
- `GET /api/runs/{id}`：任务进度与结论
- `GET /api/runs/{id}/items`：竞品快照
- `POST /api/runs/{id}/verification-browser`：打开人工验证浏览器
- `POST /api/runs/{id}/resume`：验证后继续
- `GET /api/runs/{id}/report.xlsx`：下载 Excel

## 测试

```bash
cd backend
venv/bin/python -m unittest discover -s tests -v
cd ../frontend
npm run build
```
