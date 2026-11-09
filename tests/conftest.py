import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

# 테스트가 로컬 .env나 운영 DB 설정을 읽지 않도록 import 전에 고정한다.
os.environ["APP_ENV"] = "local"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_CLEANUP_SCHEDULER"] = "false"

from core.database import Base  # noqa: E402
from models.models import DeletedMatch, Match, MatchStats, Player  # noqa: E402,F401


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def player_ids(db):
    players = [
        Player(name=f"player-{index}", code=f"code-{index}", profile_url="")
        for index in range(1, 5)
    ]
    db.add_all(players)
    db.commit()
    return [player.id for player in players]
