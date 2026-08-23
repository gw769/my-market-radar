import os
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    _base_dir = Path(sys.executable).parent
    os.chdir(_base_dir)
    _project_root = _base_dir.parent
else:
    _base_dir = Path(__file__).parent.parent
    os.chdir(_base_dir)
    _project_root = _base_dir.parent

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from contextlib import asynccontextmanager
from app.core.config import get_settings
from app.core.database import init_db
from app.core.logging import logger
from app.core.exceptions import AppException
import app.models  # noqa: F401 - 确保所有模型注册到 Base.metadata
from app.api import api_router
from app.services.marketplace.recovery import recover_interrupted_runs
from app.services.marketplace.scheduler import start_scheduler, stop_scheduler

settings = get_settings()

_frontend_out = _base_dir / "frontend" / "dist"
if not _frontend_out.exists():
    _frontend_out = _project_root / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("应用启动中...")
    init_db()
    logger.info("数据库初始化完成")
    _ensure_default_admin()
    recover_interrupted_runs()
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("应用关闭")


def _ensure_default_admin():
    """首次启动时自动创建默认管理员账户"""
    from app.core.database import SessionLocal
    from app.core.security import get_password_hash
    from app.models.user import User

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == "admin@market.my").first()
        if not existing:
            admin = User(
                username="admin",
                email="admin@market.my",
                password_hash=get_password_hash("admin123"),
            )
            db.add(admin)
            db.commit()
            logger.info("默认管理员已创建: admin@market.my / admin123")
        else:
            logger.info("管理员账户已存在，跳过创建")
    except Exception as e:
        logger.error(f"创建默认管理员失败: {e}")
    finally:
        db.close()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """处理自定义应用异常"""
    logger.warning(f"应用异常 [{exc.status_code}]: {exc.detail} - {request.url}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.detail},
    )


@app.exception_handler(FastAPIHTTPException)
async def http_exception_handler(request: Request, exc: FastAPIHTTPException):
    """处理FastAPI HTTP异常（含验证错误）"""
    logger.warning(f"HTTP异常 [{exc.status_code}]: {exc.detail} - {request.url}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.detail},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """处理未捕获的全局异常"""
    logger.error(f"未处理异常: {str(exc)} - {request.url}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": "服务器内部错误，请稍后重试"},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8011", "http://localhost:8011"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
def health():
    return {"status": "ok"}


if _frontend_out.exists():
    _assets_dir = _frontend_out / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/")
    async def serve_index():
        return FileResponse(str(_frontend_out / "index.html"))

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith("api"):
            return JSONResponse({"error": "Not found"}, status_code=404)

        if full_path.startswith("assets/"):
            file_path = _frontend_out / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            return JSONResponse({"error": "Not found"}, status_code=404)

        file_path = _frontend_out / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))

        html_path = _frontend_out / f"{full_path}.html"
        if html_path.exists():
            return FileResponse(str(html_path))

        return FileResponse(str(_frontend_out / "index.html"))
else:
    @app.get("/")
    def root():
        return {"message": "MY Marketplace Analyzer API", "version": settings.APP_VERSION}
