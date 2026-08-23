from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings
from app.core.logging import logger

settings = get_settings()
SQLITE_BUSY_TIMEOUT_MS = 30_000
MYSQL_CONNECT_TIMEOUT_SECONDS = 10


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def _configure_mysql_connection(dbapi_connection, _connection_record) -> None:
    # Model server defaults use NOW(). Pin the session to UTC so naive DateTime rows match the
    # UTC-naive values written by application code and _iso() never mislabels server-local time.
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET time_zone = '+00:00'")
    finally:
        cursor.close()


def _attach_engine_guards(engine, db_type: str) -> None:
    if db_type == "sqlite":
        event.listen(engine, "connect", _configure_sqlite_connection)
    elif db_type == "mysql":
        event.listen(engine, "connect", _configure_mysql_connection)


def _sync_engine_options(db_type: str) -> dict:
    if db_type == "sqlite":
        return {"connect_args": {"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000}}
    if db_type == "mysql":
        return {"connect_args": {"connect_timeout": MYSQL_CONNECT_TIMEOUT_SECONDS}}
    return {}


def _create_sync_engine():
    requested_type = settings.DATABASE_TYPE
    try:
        engine = create_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            pool_pre_ping=True,
            **_sync_engine_options(requested_type),
        )
        _attach_engine_guards(engine, requested_type)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("数据库连接成功: %s", requested_type)
        return engine, requested_type
    except Exception as exc:
        logger.error("%s 数据库连接失败: %s", requested_type, exc)
        if requested_type != "mysql":
            raise
        if not settings.ALLOW_DATABASE_FALLBACK:
            raise RuntimeError(
                "已显式配置 MySQL，但数据库不可用。为避免静默切到空 SQLite，应用已停止；"
                "修复 MySQL 连接，或明确设置 ALLOW_DATABASE_FALLBACK=true。"
            ) from exc

    logger.warning("已明确允许数据库降级：MySQL 不可用，切换到 SQLite")
    sqlite_url = f"sqlite:///{settings.data_path / 'marketplace_ai.db'}"
    engine = create_engine(
        sqlite_url,
        echo=settings.DEBUG,
        pool_pre_ping=True,
        **_sync_engine_options("sqlite"),
    )
    _attach_engine_guards(engine, "sqlite")
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine, "sqlite"


def _create_async_engine(db_type: str, db_url: str):
    if db_type == "mysql":
        async_engine = create_async_engine(
            db_url.replace("mysql+pymysql://", "mysql+aiomysql://"),
            echo=settings.DEBUG,
            pool_pre_ping=True,
            connect_args={"connect_timeout": MYSQL_CONNECT_TIMEOUT_SECONDS},
        )
        _attach_engine_guards(async_engine.sync_engine, "mysql")
        return async_engine
    async_engine = create_async_engine(
        db_url.replace("sqlite:///", "sqlite+aiosqlite:///"),
        echo=settings.DEBUG,
        pool_pre_ping=True,
        connect_args={"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000},
    )
    _attach_engine_guards(async_engine.sync_engine, "sqlite")
    return async_engine


engine, _active_db_type = _create_sync_engine()
async_engine = _create_async_engine(_active_db_type, str(engine.url))

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
async_session = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    async with async_session() as session:
        yield session


def init_db():
    Base.metadata.create_all(bind=engine)
    _sync_schema()


def _sync_schema():
    """Best-effort additive compatibility for old local databases.

    This is deliberately limited to adding columns. It is not a substitute for a real
    migration system; failed additions are reported loudly so an old incompatible database is
    not mistaken for a healthy schema.
    """
    failures: list[str] = []
    try:
        inspector = inspect(engine)
        db_tables = set(inspector.get_table_names())
        statements: list[str] = []
        for table_name, table_obj in Base.metadata.tables.items():
            if table_name not in db_tables:
                continue
            db_cols = {column["name"] for column in inspector.get_columns(table_name)}
            for column in table_obj.columns:
                if column.name in db_cols:
                    continue
                col_type = column.type.compile(dialect=engine.dialect).upper()
                nullable = "NULL" if column.nullable else "NOT NULL"
                default = ""
                if column.default is not None and column.default.arg is not None:
                    arg = column.default.arg
                    if callable(arg):
                        arg = None
                    if isinstance(arg, bool):
                        default = f" DEFAULT {1 if arg else 0}"
                    elif isinstance(arg, str):
                        escaped = arg.replace("'", "''")
                        default = f" DEFAULT '{escaped}'"
                    elif arg is not None:
                        default = f" DEFAULT {arg}"
                if _active_db_type == "mysql" and col_type == "BOOLEAN":
                    col_type = "TINYINT(1)"
                statements.append(f"ALTER TABLE `{table_name}` ADD COLUMN `{column.name}` {col_type} {nullable}{default}")
                logger.info("DB sync: 发现缺失列 %s.%s", table_name, column.name)

        if statements:
            with engine.begin() as conn:
                for stmt in statements:
                    try:
                        conn.execute(text(stmt))
                        logger.info("DB sync: 成功执行 %s", stmt[:100])
                    except Exception as exc:
                        failures.append(f"{stmt}: {exc}")
                        logger.error("DB sync failed: %s -> %s", stmt[:100], exc)
            logger.info("DB sync: 处理 %s 个缺失列", len(statements))
    except Exception as exc:
        logger.error("DB schema sync failed before migration: %s", exc, exc_info=True)
        raise RuntimeError(f"数据库结构检查失败: {exc}") from exc

    if failures:
        raise RuntimeError(
            "数据库结构自动补齐失败；请先备份数据库并处理迁移。失败项: "
            + " | ".join(failures[:3])
        )
