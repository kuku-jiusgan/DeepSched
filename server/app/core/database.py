from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import get_settings

settings = get_settings()

database_url = settings.DATABASE_URL
if not database_url.startswith("mysql+pymysql://"):
    raise RuntimeError("DATABASE_URL 必须使用 MySQL PyMySQL 连接，例如 mysql+pymysql://user:password@host:3306/database")

engine = create_engine(database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
