# ─────────────────────────────────────────────────────────────
#  MY Market Radar · 后端 Docker 镜像
#  单容器方案：FastAPI 后端直接托管前端构建产物(frontend/dist)
#  对方电脑只需 Docker Desktop，无需安装 Python/Node/MySQL/Redis
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

# 1) Python 依赖
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/backend/requirements.txt

# 2) 应用代码 + 前端构建产物
COPY backend/ /app/backend/
COPY frontend/dist/ /app/frontend/dist/

# 3) 数据目录（SQLite、报告和独立浏览器配置持久化）
RUN mkdir -p /app/backend/data /app/backend/logs

WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
