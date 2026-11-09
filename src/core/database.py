"""
데이터베이스 연결 및 세션 관리
SQLAlchemy를 사용하여 SQLite/PostgreSQL 추상화
"""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

from core.properties import settings, validate_required_settings

validate_required_settings()

# SQLAlchemy Engine 생성
# check_same_thread=False: SQLite에서 멀티스레드 사용 허용
engine_options = {"echo": settings.DEBUG}
if "sqlite" in settings.DATABASE_URL:
    engine_options["connect_args"] = {"check_same_thread": False}
else:
    # Supabase session pool의 연결 한도를 넘지 않도록 프로세스당 상한을 제한한다.
    engine_options.update(
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,
        pool_pre_ping=True,
    )

engine = create_engine(settings.DATABASE_URL, **engine_options)

# Session Factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base Class for Models
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    데이터베이스 세션 의존성 주입용 함수
    FastAPI의 Depends()에서 사용

    Example:
        @app.get("/users")
        def get_users(db: Session = Depends(get_db)):
            return db.query(User).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    데이터베이스 초기화
    모든 테이블 생성 (애플리케이션 시작 시 호출)
    """
    from models.models import Player, Match, MatchStats, DeletedMatch  # noqa: F401 (순환 import 방지)
    Base.metadata.create_all(bind=engine, checkfirst=True)
