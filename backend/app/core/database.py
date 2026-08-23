from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import get_settings
from app.core.logging import logger

settings = get_settings()

# 数据库连接 - 支持自动降级
def _create_sync_engine():
    """创建同步数据库引擎，失败时降级到SQLite"""
    try:
        engine = create_engine(settings.DATABASE_URL, echo=settings.DEBUG, pool_pre_ping=True)
        # 测试连接
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info(f"数据库连接成功: {settings.DATABASE_TYPE}")
        return engine, settings.DATABASE_TYPE
    except Exception as e:
        logger.warning(f"{settings.DATABASE_TYPE} 数据库连接失败: {e}")
        if settings.DATABASE_TYPE == "mysql":
            logger.warning("自动降级到 SQLite 数据库")
            from pathlib import Path
            db_path = Path(__file__).parent.parent.parent / 'marketplace_ai.db'
            sqlite_url = f"sqlite:///{db_path}"
            engine = create_engine(sqlite_url, echo=settings.DEBUG, pool_pre_ping=True)
            return engine, "sqlite"
        raise

def _create_async_engine(db_type, db_url):
    """创建异步数据库引擎"""
    if db_type == "mysql":
        return create_async_engine(
            db_url.replace("mysql+pymysql://", "mysql+aiomysql://"),
            echo=settings.DEBUG,
            pool_pre_ping=True,
        )
    else:
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
    """同步模型与数据库表结构，补齐缺失的列（SQLite/Mysql 通用）"""
    try:
        insp = inspect(engine)
        db_tables = set(insp.get_table_names())
        statements = []

        for table_name, table_obj in Base.metadata.tables.items():
            if table_name not in db_tables:
                continue
            db_cols = {c["name"] for c in insp.get_columns(table_name)}
            for col in table_obj.columns:
                if col.name not in db_cols:
                    col_type = str(col.type).upper()
                    nullable = "NULL" if col.nullable else "NOT NULL"
                    default = ""
                    if col.default is not None and col.default.arg is not None:
                        arg = col.default.arg
                        if isinstance(arg, bool):
                            default = f" DEFAULT {1 if arg else 0}"
                        elif isinstance(arg, str):
                            default = f" DEFAULT '{arg}'"
                        else:
                            default = f" DEFAULT {arg}"

                    if settings.DATABASE_TYPE == "mysql":
                        from sqlalchemy import Boolean
                        if isinstance(col.type, Boolean) or col_type == "BOOLEAN":
                            col_type = "TINYINT(1)"
                        stmt = f"ALTER TABLE `{table_name}` ADD COLUMN `{col.name}` {col_type} {nullable}{default}"
                    else:
                        stmt = f"ALTER TABLE `{table_name}` ADD COLUMN `{col.name}` {col_type} {nullable}{default}"
                    statements.append(stmt)
                    logger.info(f"DB sync: 发现缺失列 {table_name}.{col.name}")

        if statements:
            with engine.connect() as conn:
                for stmt in statements:
                    try:
                        conn.execute(text(stmt))
                        logger.info(f"DB sync: 成功执行 {stmt[:100]}")
                    except Exception as e:
                        logger.warning(f"DB sync warning: {stmt[:100]} -> {e}")
                conn.commit()
            logger.info(f"DB sync: 处理 {len(statements)} 个缺失列")
    except Exception as e:
        logger.warning(f"DB schema sync skipped: {e}")
