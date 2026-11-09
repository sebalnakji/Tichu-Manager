from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import season as season_rules
from core.database import get_db
from core.properties import settings
from models.models import Match, Player
from routers.admin import router as admin_router
from routers.matches import router as matches_router
from routers.stats import router as stats_router


@pytest.fixture
def client(db):
    app = FastAPI()
    for router in (matches_router, stats_router, admin_router):
        app.include_router(router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def set_today(monkeypatch, day):
    monkeypatch.setattr(season_rules, "today_kst", lambda: day)


# ── 시즌 규칙 ──

@pytest.mark.parametrize(
    "number, start, end",
    [
        (1, date(2026, 1, 1), date(2027, 1, 1)),
        (2, date(2027, 1, 1), date(2027, 7, 1)),
        (3, date(2027, 7, 1), date(2028, 1, 1)),
        (4, date(2028, 1, 1), date(2028, 7, 1)),
        (5, date(2028, 7, 1), date(2029, 1, 1)),
    ],
)
def test_season_bounds(number, start, end):
    assert season_rules.season_bounds(number) == (start, end)


def test_season_bounds_rejects_zero():
    with pytest.raises(ValueError):
        season_rules.season_bounds(0)


@pytest.mark.parametrize(
    "day, number",
    [
        (date(2025, 12, 31), None),
        (date(2026, 1, 1), 1),
        (date(2026, 12, 31), 1),
        (date(2027, 1, 1), 2),
        (date(2027, 6, 30), 2),
        (date(2027, 7, 1), 3),
        (date(2027, 12, 31), 3),
        (date(2028, 1, 1), 4),
    ],
)
def test_season_of(day, number):
    assert season_rules.season_of(day) == number


def test_season_names():
    assert [season_rules.season_name(n) for n in (1, 2, 3, 4)] == [
        "시즌1", "시즌2", "시즌3", "시즌4",
    ]


def test_season_context_lists_seasons_up_to_today(monkeypatch):
    set_today(monkeypatch, date(2027, 7, 1))
    assert season_rules.season_context() == {
        "current": 3,
        "list": [
            {"number": 3, "name": "시즌3"},
            {"number": 2, "name": "시즌2"},
            {"number": 1, "name": "시즌1"},
        ],
    }


def test_today_is_korean_date(monkeypatch):
    # UTC 6월 30일 16시 = 한국 7월 1일 01시 → 한국 날짜는 7월 1일 (다음 시즌)
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 6, 30, 16, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(season_rules, "datetime", FakeDatetime)
    assert season_rules.today_kst() == date(2027, 7, 1)
    assert season_rules.current_season() == 3


def test_kst_midnight_as_utc():
    assert season_rules.kst_midnight_as_utc(date(2027, 7, 1)) == datetime(2027, 6, 30, 15, 0)


# ── 한국 날짜 / 시즌 파라미터 적용 ──

def test_new_match_gets_korean_date(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2027, 7, 1))
    match_id = client.post(
        "/api/matches/",
        json={"team_a_ids": player_ids[:2], "team_b_ids": player_ids[2:]},
    ).json()["id"]
    assert db.get(Match, match_id).play_date == date(2027, 7, 1)


def _finished(day, team_a, team_b, winner="A"):
    return Match(
        play_date=day, team_a_ids=team_a, team_b_ids=team_b,
        status="FINISHED", winner_team=winner, rounds=[],
    )


def test_leaderboard_filters_by_half_year_season(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2027, 8, 1))
    a, b = player_ids[:2], player_ids[2:]
    db.add_all([
        _finished(date(2026, 12, 31), a, b),   # 시즌1
        _finished(date(2027, 6, 30), a, b),    # 시즌2
        _finished(date(2027, 7, 1), a, b),     # 시즌3
        _finished(date(2027, 7, 2), a, b),     # 시즌3
    ])
    db.commit()

    def games(season):
        params = {"season": season} if season else {}
        rows = client.get("/api/stats/leaderboard", params=params).json()
        return next(r["stats"]["total_games"] for r in rows if r["stats"]["player_id"] == a[0])

    assert (games(1), games(2), games(3), games(None)) == (1, 1, 2, 4)


@pytest.mark.parametrize("season", [0, 2])
def test_unknown_season_is_rejected(client, monkeypatch, season):
    set_today(monkeypatch, date(2026, 11, 3))  # 현재 시즌 = 1
    for path in ("/api/stats/leaderboard", "/api/stats/leaderboard/teams", "/api/matches/history"):
        assert client.get(path, params={"season": season}).status_code == 422


def test_scoreboard_uses_current_season(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2027, 7, 1))  # 시즌3
    a, b = player_ids[:2], player_ids[2:]
    db.add_all([_finished(date(2027, 6, 30), a, b), _finished(date(2027, 7, 1), a, b, "B")])
    db.commit()
    match_id = client.post("/api/matches/", json={"team_a_ids": a, "team_b_ids": b}).json()["id"]

    body = client.get(f"/api/matches/{match_id}").json()
    assert (body["team_a_team_games"], body["team_a_team_wins"]) == (1, 0)


def test_admin_reset_deletes_only_that_season(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2027, 8, 1))
    a, b = player_ids[:2], player_ids[2:]
    db.add_all([_finished(date(2027, 6, 30), a, b), _finished(date(2027, 7, 1), a, b)])
    # 가입일(UTC): 한국 7월 1일 00:30 = UTC 6월 30일 15:30 → 시즌3
    db.add_all([
        Player(name="s2", code="s2-code", profile_url="", created_at=datetime(2027, 6, 30, 14, 59)),
        Player(name="s3", code="s3-code", profile_url="", created_at=datetime(2027, 6, 30, 15, 30)),
    ])
    db.commit()
    headers = {"Authorization": settings.ADMIN_CODE}

    response = client.request("DELETE", "/api/admin/reset", json={"table": "matches", "season": 3}, headers=headers)
    assert response.status_code == 200
    assert [m.play_date for m in db.query(Match).all()] == [date(2027, 6, 30)]

    client.request("DELETE", "/api/admin/reset", json={"table": "players", "season": 3}, headers=headers)
    names = {p.name for p in db.query(Player).all()}
    assert "s2" in names and "s3" not in names

    bad = client.request("DELETE", "/api/admin/reset", json={"table": "matches", "season": 9}, headers=headers)
    assert bad.status_code == 422

