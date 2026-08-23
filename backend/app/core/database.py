from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings
from app.core.logging import logger

settings = get_settings()


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _attach_engine_guards(engine, db_type: str) -> None:
    if db_type == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)


def _create_sync_engine():
    requested_type = settings.DATABASE_TYPE.lower()
    try:
        engine = create_engine(settings.DATABASE_URL, echo=settings.DEBUG, pool_pre_ping=True)
        _attach_engine_guards(engine, requested_type)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("数据库连接成功: %s", requested_type)
        return engine, requested_type
    except Exception as exc:
        logger.warning("%s 数据库连接失败: %s", requested_type, exc)
        if requested_type != "mysql":
            raise

    logger.warning("自动降级到 SQLite 数据库")
    sqlite_url = f"sqlite:///{settings.data_path / 'marketplace_ai.db'}"
    engine = create_engine(sqlite_url, echo=settings.DEBUG, pool_pre_ping=True)
    _attach_engine_guards(engine, "sqlite")
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine, "sqlite"


def _create_async_engine(db_type: str, db_url: str):
    if db_type == "mysql":
        return create_async_engine(
            db_url.replace("mysql+pymysql://", "mysql+aiomysql://"),
            echo=settings.DEBUG,
            pool_pre_ping=True,
        )
    return create_async_engine(
        db_url.replace("sqlite:///", "sqlite+aiosqlite:///"),
        echo=settings.DEBUG,
        pool_pre_ping=True,
    )


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
                col_type = str(column.type).upper()
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
                        logger.warning("DB sync warning: %s -> %s", stmt[:100], exc)
            logger.info("DB sync: 处理 %s 个缺失列", len(statements))
    except Exception as exc:
        logger.warning("DB schema sync skipped: %s", exc)
