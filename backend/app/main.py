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
KNOWN_INSECURE_SECRET_KEYS = {
    "",
    "dev-secret-key-change-in-production",
    "please-change-me-to-a-random-32-char-string",
}

_frontend_out = _base_dir / "frontend" / "dist"
if not _frontend_out.exists():
    _frontend_out = _project_root / "frontend" / "dist"


def _safe_frontend_path(relative_path: str) -> Path | None:
    """Resolve a SPA/static fallback path without allowing it to escape frontend/dist."""
    try:
        base = _frontend_out.resolve()
        candidate = (_frontend_out / relative_path).resolve()
        candidate.relative_to(base)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _validate_runtime_security() -> None:
    secret = settings.SECRET_KEY.strip()
    if secret in KNOWN_INSECURE_SECRET_KEYS or len(secret) < 32:
        raise RuntimeError(
            "SECRET_KEY 未安全配置。正式启动前请在 .env 设置至少 32 个字符的随机私密字符串；"
            "本机无 .env 的 start.py 会自动生成临时本地密钥。"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("应用启动中...")
    _validate_runtime_security()
    init_db()
    logger.info("数据库初始化完成")
    _ensure_default_admin()
    recover_interrupted_runs()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        logger.info("应用关闭")


def _ensure_default_admin():
    """Bootstrap the very first account only when an explicit password is configured."""
    from app.core.database import SessionLocal
    from app.core.security import get_password_hash
    from app.models.user import User

    db = SessionLocal()
    try:
        if db.query(User).first():
            logger.info("已有用户账户，跳过管理员引导")
            return

        password = settings.BOOTSTRAP_ADMIN_PASSWORD
        if not password:
            logger.warning(
                "数据库尚无用户，但未设置 BOOTSTRAP_ADMIN_PASSWORD；不会自动创建已知默认密码账户"
            )
            return
        if len(password) < 6:
            logger.error("BOOTSTRAP_ADMIN_PASSWORD 至少需要 6 个字符，管理员账户未创建")
            return

        admin = User(
            username=settings.BOOTSTRAP_ADMIN_USERNAME,
            email=settings.BOOTSTRAP_ADMIN_EMAIL,
            password_hash=get_password_hash(password),
        )
        db.add(admin)
        db.commit()
        logger.info("首次管理员已创建: %s", settings.BOOTSTRAP_ADMIN_EMAIL)
    except Exception as exc:
        db.rollback()
        logger.error("创建首次管理员失败: %s", exc, exc_info=True)
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
    logger.warning(f"应用异常 [{exc.status_code}]: {exc.detail} - {request.url}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.detail},
    )


@app.exception_handler(FastAPIHTTPException)
async def http_exception_handler(request: Request, exc: FastAPIHTTPException):
    logger.warning(f"HTTP异常 [{exc.status_code}]: {exc.detail} - {request.url}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.detail},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
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
            file_path = _safe_frontend_path(full_path)
            if file_path and file_path.is_file():
                return FileResponse(str(file_path))
            return JSONResponse({"error": "Not found"}, status_code=404)

        file_path = _safe_frontend_path(full_path)
        if file_path and file_path.is_file():
            return FileResponse(str(file_path))

        html_path = _safe_frontend_path(f"{full_path}.html")
        if html_path and html_path.is_file():
            return FileResponse(str(html_path))

        return FileResponse(str(_frontend_out / "index.html"))
else:
    @app.get("/")
    def root():
        return {"message": "MY Marketplace Analyzer API", "version": settings.APP_VERSION}
