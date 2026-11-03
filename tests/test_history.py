from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from models.models import Match, MatchStats, Player
from routers.matches import router as matches_router


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(matches_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def five_ids(db, player_ids):
    extra = Player(name="player-5", code="code-5", profile_url="")
    db.add(extra)
    db.commit()
    return player_ids + [extra.id]


def add_match(db, day, team_a, team_b, winner="A", status="FINISHED", tichu=()):
    """tichu: (player_id, type, success) 목록"""
    match = Match(
        play_date=day,
        team_a_ids=list(team_a),
        team_b_ids=list(team_b),
        score_a=1000 if winner == "A" else 500,
        score_b=1000 if winner == "B" else 500,
        rounds=[],
        status=status,
        winner_team=winner if status == "FINISHED" else None,
    )
    db.add(match)
    db.flush()
    for player_id, kind, success in tichu:
        db.add(MatchStats(
            match_id=match.id,
            player_id=player_id,
            round_number=1,
            is_tichu_try=kind == "tichu",
            is_tichu_succ=kind == "tichu" and success,
            is_grand_try=kind == "grand",
            is_grand_succ=kind == "grand" and success,
        ))
    db.commit()
    return match.id


def test_history_pages_by_whole_days(db, client, player_ids):
    a, b = player_ids[:2], player_ids[2:]
    days = [date(2026, 3, d) for d in (1, 2, 4, 7, 9)]
    for index, day in enumerate(days):
        for _ in range(index + 1):  # 날짜마다 경기 수가 다름 (1~5)
            add_match(db, day, a, b)
    add_match(db, date(2026, 3, 9), a, b, status="PLAYING")  # 진행 중 제외
    add_match(db, date(2025, 12, 31), a, b)  # 다른 시즌 제외

    first = client.get("/api/matches/history", params={"season": 1}).json()
    assert [d["date"] for d in first["days"]] == ["2026-03-09", "2026-03-07", "2026-03-04"]
    assert [d["game_count"] for d in first["days"]] == [5, 4, 3]
    assert [len(d["matches"]) for d in first["days"]] == [5, 4, 3]
    assert first["next_before_date"] == "2026-03-04"
    assert first["summary"] is None

    second = client.get(
        "/api/matches/history",
        params={"season": 1, "before_date": first["next_before_date"]},
    ).json()
    assert [d["date"] for d in second["days"]] == ["2026-03-02", "2026-03-01"]
    assert second["next_before_date"] is None

    everything = client.get("/api/matches/history").json()
    assert everything["days"][0]["date"] == "2026-03-09"


def test_day_records_count_results_and_tichu(db, client, player_ids):
    p1, p2, p3, p4 = player_ids
    day = date(2026, 5, 5)
    add_match(db, day, [p1, p2], [p3, p4], winner="A",
              tichu=[(p1, "tichu", True), (p3, "grand", False)])
    add_match(db, day, [p1, p3], [p2, p4], winner="B",
              tichu=[(p1, "tichu", False), (p2, "tichu", True)])

    records = client.get("/api/matches/history").json()["days"][0]["records"]
    by_id = {r["player_id"]: r for r in records}

    assert (by_id[p2]["wins"], by_id[p2]["losses"]) == (2, 0)
    assert (by_id[p1]["wins"], by_id[p1]["losses"]) == (1, 1)
    assert (by_id[p3]["wins"], by_id[p3]["losses"]) == (0, 2)
    assert (by_id[p1]["tichu_try"], by_id[p1]["tichu_success"]) == (2, 1)
    assert (by_id[p3]["grand_try"], by_id[p3]["grand_success"]) == (1, 0)
    assert records[0]["player_id"] == p2  # 승수 많은 순
    assert records[-1]["player_id"] == p3


def test_player_filter_and_summary(db, client, five_ids):
    p1, p2, p3, p4, p5 = five_ids
    add_match(db, date(2026, 1, 1), [p1, p2], [p3, p4], winner="A",
              tichu=[(p1, "tichu", True), (p2, "tichu", True)])
    add_match(db, date(2026, 1, 2), [p3, p4], [p1, p5], winner="A",
              tichu=[(p1, "grand", False)])
    add_match(db, date(2026, 1, 3), [p2, p3], [p4, p5], winner="A")  # p1 없음

    page = client.get("/api/matches/history", params={"player_ids": str(p1)}).json()
    match_ids = [m["id"] for d in page["days"] for m in d["matches"]]
    assert len(match_ids) == 2
    assert page["summary"] == {
        "wins": 1, "losses": 1,
        "tichu_try": 1, "tichu_success": 1,  # p2의 티츄는 포함 안 됨
        "grand_try": 1, "grand_success": 0,
    }


def test_team_filter_only_same_team_matches(db, client, five_ids):
    p1, p2, p3, p4, p5 = five_ids
    same_team = add_match(db, date(2026, 2, 1), [p1, p2], [p3, p4], winner="B",
                          tichu=[(p1, "tichu", True), (p2, "grand", True), (p3, "tichu", True)])
    add_match(db, date(2026, 2, 2), [p1, p3], [p2, p4], winner="A")  # 서로 상대 팀
    same_team_b = add_match(db, date(2026, 2, 3), [p3, p5], [p2, p1], winner="B")

    page = client.get("/api/matches/history", params={"player_ids": f"{p2},{p1}"}).json()
    match_ids = [m["id"] for d in page["days"] for m in d["matches"]]
    assert match_ids == [same_team_b, same_team]
    assert page["summary"] == {
        "wins": 1, "losses": 1,
        "tichu_try": 1, "tichu_success": 1,
        "grand_try": 1, "grand_success": 1,  # 두 사람 합산, 상대(p3) 제외
    }


def test_summary_only_on_first_page(db, client, player_ids):
    p1 = player_ids[0]
    for day in range(1, 6):
        add_match(db, date(2026, 4, day), player_ids[:2], player_ids[2:])
    first = client.get("/api/matches/history", params={"player_ids": str(p1)}).json()
    second = client.get(
        "/api/matches/history",
        params={"player_ids": str(p1), "before_date": first["next_before_date"]},
    ).json()
    assert first["summary"]["wins"] == 5  # 시즌 전체 기준
    assert second["summary"] is None


def test_player_options_sorted_by_games_with_partners(db, client, five_ids):
    p1, p2, p3, p4, p5 = five_ids
    add_match(db, date(2026, 6, 1), [p1, p2], [p3, p4])
    add_match(db, date(2026, 6, 2), [p1, p3], [p2, p5])
    add_match(db, date(2025, 6, 2), [p4, p5], [p2, p3])  # 다른 시즌

    options = client.get("/api/matches/history/players", params={"season": 1}).json()
    assert [o["id"] for o in options][:3] == [p1, p2, p3]  # 2경기씩, 이름순
    by_id = {o["id"]: o for o in options}
    assert by_id[p1]["games"] == 2
    assert by_id[p5]["games"] == 1
    assert by_id[p1]["partner_ids"] == sorted([p2, p3])
    assert by_id[p4]["partner_ids"] == [p3]  # 2025 시즌의 p5 파트너 기록은 제외


@pytest.mark.parametrize("raw", ["1,2,3", "abc"])
def test_invalid_player_ids_rejected(client, raw):
    response = client.get("/api/matches/history", params={"player_ids": raw})
    assert response.status_code == 422


def test_empty_history(client):
    assert client.get("/api/matches/history").json() == {
        "days": [], "next_before_date": None, "summary": None,
    }
    assert client.get("/api/matches/history/players").json() == []
