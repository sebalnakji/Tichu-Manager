"""성능 가드: 목록·통계 조회가 경기 라운드 기록(JSON)을 읽지 않는지 확인"""
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from core import season as season_rules
from core.database import get_db
from models.models import Match
from routers.matches import router as matches_router
from routers.stats import router as stats_router


@pytest.fixture
def client(db, player_ids, monkeypatch):
    monkeypatch.setattr(season_rules, "today_kst", lambda: date(2026, 11, 3))
    rounds = [{"round": 1, "team_a_base": 60, "team_b_base": 40, "events": []}]
    db.add_all([
        Match(play_date=date(2026, 5, d), team_a_ids=player_ids[:2], team_b_ids=player_ids[2:],
              status="FINISHED", winner_team="A", score_a=1000, score_b=500, rounds=rounds)
        for d in (1, 2, 3)
    ])
    db.add(Match(play_date=date(2026, 5, 4), team_a_ids=player_ids[:2], team_b_ids=player_ids[2:],
                 status="PLAYING", rounds=[]))
    db.commit()

    app = FastAPI()
    app.include_router(matches_router)
    app.include_router(stats_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def rounds_reads(db, client, path):
    """요청 중 경기 라운드 칸을 읽은 SQL 수"""
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        assert client.get(path).status_code == 200
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    return sum("tichu_matches.rounds" in s for s in statements)


@pytest.mark.parametrize(
    "path",
    [
        "/api/stats/leaderboard",
        "/api/stats/leaderboard?season=1",
        "/api/stats/leaderboard/teams",
        "/api/stats/players/current-season",
        "/api/stats/player/1",
        "/api/matches/history",
        "/api/matches/history?player_ids=1",
        "/api/matches/history/players",
    ],
)
def test_lists_and_stats_do_not_read_rounds(db, client, path):
    assert rounds_reads(db, client, path) == 0


def test_scoreboard_reads_rounds_only_for_its_own_match(db, client):
    playing_id = db.query(Match.id).filter(Match.status == "PLAYING").scalar()
    assert rounds_reads(db, client, f"/api/matches/{playing_id}") == 1
