"""잘못된 판 휴지통: 삭제 = 7일 보관(상태 DELETED), 되돌리기, 7일 지나면 영구 삭제"""
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import season as season_rules
from core.database import get_db
from core.properties import settings
from models.models import DeletedMatch, Match, MatchStats
from routers.admin import router as admin_router
from routers.matches import router as matches_router
from routers.stats import router as stats_router
from services import match_trash_service as match_trash

ADMIN = {"Authorization": settings.ADMIN_CODE}
NOW = datetime(2026, 11, 9, 3, 0)  # 한국 2026-11-09 12:00


@pytest.fixture
def clock(monkeypatch):
    """현재 시각을 바꿀 수 있는 시계 (UTC), 한국 날짜도 같이 맞춤"""
    state = {"now": NOW}
    monkeypatch.setattr(match_trash, "utcnow", lambda: state["now"])
    monkeypatch.setattr(season_rules, "today_kst", lambda: (state["now"] + timedelta(hours=9)).date())
    return state


@pytest.fixture
def client(db, clock):
    app = FastAPI()
    for router in (matches_router, stats_router, admin_router):
        app.include_router(router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _finished(db, player_ids, day=date(2026, 11, 1), status="FINISHED"):
    match = Match(
        play_date=day, team_a_ids=player_ids[:2], team_b_ids=player_ids[2:],
        status=status, winner_team="A" if status == "FINISHED" else None,
        score_a=1000, score_b=200, rounds=[],
    )
    db.add(match)
    db.commit()
    db.add(MatchStats(match_id=match.id, player_id=player_ids[0], round_number=1, is_tichu_try=True, is_tichu_succ=True))
    db.commit()
    return match.id


def _games(client, player_id):
    """랭킹·게임 기록·개인 분석·기록 수정 목록에 보이는 판 수"""
    board = {r["stats"]["player_id"]: r["stats"]["total_games"] for r in client.get("/api/stats/leaderboard?season=1").json()}
    history = client.get("/api/matches/history?season=1").json()
    dashboard = client.get(f"/api/stats/player/{player_id}/dashboard?season=1").json()
    finished = client.get("/api/matches/finished").json()
    return (
        board.get(player_id, 0),
        sum(len(day["matches"]) for day in history["days"]),
        dashboard["games"],
        len(finished),
    )


def test_delete_moves_to_trash_and_hides_everywhere(client, db, player_ids):
    deleted_id = _finished(db, player_ids)
    _finished(db, player_ids, day=date(2026, 11, 2))
    p1 = player_ids[0]
    assert _games(client, p1) == (2, 2, 2, 2)

    assert client.delete(f"/api/admin/matches/{deleted_id}", headers=ADMIN).status_code == 204

    assert _games(client, p1) == (1, 1, 1, 1)
    db.expire_all()
    match = db.get(Match, deleted_id)
    assert match.status == "DELETED"  # 판과 티츄 기록은 아직 남아 있음
    assert db.query(MatchStats).filter(MatchStats.match_id == deleted_id).count() == 1

    trash = client.get("/api/admin/matches/trash", headers=ADMIN).json()
    assert [(t["id"], t["expires_in_days"], t["score_a"]) for t in trash] == [(deleted_id, 7, 1000)]
    assert trash[0]["team_a_names"] == ["player-1", "player-2"]

    # 판 번호로 열기·라운드 수정·초기화도 없는 판
    assert client.get(f"/api/matches/{deleted_id}").status_code == 404
    round_body = {"round_number": 2, "team_a_base_score": 50, "team_b_base_score": 50, "events": []}
    assert client.post(f"/api/matches/{deleted_id}/rounds", json=round_body).status_code == 404
    assert client.delete(f"/api/matches/{deleted_id}/rounds/1").status_code == 404
    assert client.post(f"/api/matches/{deleted_id}/reset").status_code == 404
    # 이미 휴지통이면 다시 삭제 404
    assert client.delete(f"/api/admin/matches/{deleted_id}", headers=ADMIN).status_code == 404


def test_restore_brings_match_back(client, db, player_ids):
    match_id = _finished(db, player_ids)
    client.delete(f"/api/admin/matches/{match_id}", headers=ADMIN)

    assert client.post(f"/api/admin/matches/{match_id}/restore", headers=ADMIN).status_code == 204

    assert _games(client, player_ids[0]) == (1, 1, 1, 1)
    assert client.get("/api/admin/matches/trash", headers=ADMIN).json() == []
    assert client.get(f"/api/matches/{match_id}").status_code == 200
    # 휴지통에 없으면 404
    assert client.post(f"/api/admin/matches/{match_id}/restore", headers=ADMIN).status_code == 404


def test_purge_after_seven_days(client, db, player_ids, clock):
    old_id = _finished(db, player_ids)
    new_id = _finished(db, player_ids, day=date(2026, 11, 2))
    client.delete(f"/api/admin/matches/{old_id}", headers=ADMIN)
    clock["now"] = NOW + timedelta(days=1)
    client.delete(f"/api/admin/matches/{new_id}", headers=ADMIN)

    # 6일 23시간 뒤: 남아 있음, 마지막 날이라 0일(오늘 영구 삭제)
    clock["now"] = NOW + timedelta(days=6, hours=23)
    trash = client.get("/api/admin/matches/trash", headers=ADMIN).json()
    assert [(t["id"], t["expires_in_days"]) for t in trash] == [(new_id, 1), (old_id, 0)]

    # 7일 뒤: 먼저 지운 판만 판·티츄 기록·휴지통 기록까지 영구 삭제, 되돌리기 404
    clock["now"] = NOW + timedelta(days=7)
    assert [t["id"] for t in client.get("/api/admin/matches/trash", headers=ADMIN).json()] == [new_id]
    db.expire_all()
    assert db.get(Match, old_id) is None
    assert db.query(MatchStats).filter(MatchStats.match_id == old_id).count() == 0
    assert db.get(DeletedMatch, old_id) is None
    assert client.post(f"/api/admin/matches/{old_id}/restore", headers=ADMIN).status_code == 404


def test_purge_also_runs_on_delete_and_restore(client, db, player_ids, clock):
    old_id = _finished(db, player_ids)
    other_id = _finished(db, player_ids, day=date(2026, 11, 2))
    client.delete(f"/api/admin/matches/{old_id}", headers=ADMIN)
    clock["now"] = NOW + timedelta(days=8)

    client.delete(f"/api/admin/matches/{other_id}", headers=ADMIN)
    db.expire_all()
    assert db.get(Match, old_id) is None


def test_only_finished_match_can_be_deleted(client, db, player_ids):
    playing_id = _finished(db, player_ids, status="PLAYING")
    assert client.delete(f"/api/admin/matches/{playing_id}", headers=ADMIN).status_code == 409
    assert client.delete("/api/admin/matches/999", headers=ADMIN).status_code == 404


def test_ongoing_ignores_trashed_latest_match(client, db, player_ids):
    playing_id = _finished(db, player_ids, status="PLAYING")
    finished_id = _finished(db, player_ids)
    client.delete(f"/api/admin/matches/{finished_id}", headers=ADMIN)

    ongoing = client.get(f"/api/matches/ongoing/{player_ids[0]}").json()
    assert [m["id"] for m in ongoing] == [playing_id]


def test_trash_endpoints_require_admin(client, db, player_ids):
    match_id = _finished(db, player_ids)
    for method, url in [
        ("DELETE", f"/api/admin/matches/{match_id}"),
        ("POST", f"/api/admin/matches/{match_id}/restore"),
        ("GET", "/api/admin/matches/trash"),
    ]:
        assert client.request(method, url).status_code == 401
        assert client.request(method, url, headers={"Authorization": "wrong"}).status_code == 403
    db.expire_all()
    assert db.get(Match, match_id).status == "FINISHED"


def test_reset_matches_clears_trash_rows(client, db, player_ids):
    match_id = _finished(db, player_ids)
    client.delete(f"/api/admin/matches/{match_id}", headers=ADMIN)

    response = client.request("DELETE", "/api/admin/reset", json={"table": "matches", "season": None}, headers=ADMIN)
    assert response.status_code == 200
    db.expire_all()
    assert db.query(Match).count() == 0
    assert db.query(DeletedMatch).count() == 0
