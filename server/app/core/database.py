from sqlalchemy import create_engine
from pathlib import Path
from sqlalchemy import event

from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import get_settings

settings = get_settings()

database_url = settings.DATABASE_URL
if database_url.startswith("sqlite:///./"):
    relative_path = database_url.removeprefix("sqlite:///./")
    database_path = Path(__file__).resolve().parents[2] / relative_path
    database_url = f"sqlite:///{database_path}"

if database_url.startswith("sqlite:"):
    engine_kwargs = {
        "connect_args": {
            "check_same_thread": False,
            # 排程、登录保活和企业微信投递可能并发写 SQLite，避免瞬时
            # 写锁直接冒泡为 500；WAL 允许读写并行。
            "timeout": 30,
        },
    }
else:
    engine_kwargs = {}
engine = create_engine(database_url, **engine_kwargs)
if database_url.startswith("sqlite:"):
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
