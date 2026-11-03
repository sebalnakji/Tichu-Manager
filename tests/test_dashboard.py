from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from core import season as season_rules
from core.database import get_db
from models.models import Match
from routers.stats import router as stats_router
from services.dashboard_service import DashboardService


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(stats_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def set_today(monkeypatch, day):
    monkeypatch.setattr(season_rules, "today_kst", lambda: day)


def add_matches(db, day, team_a, team_b, winners):
    db.add_all([
        Match(play_date=day, team_a_ids=team_a, team_b_ids=team_b,
              status="FINISHED", winner_team=winner, rounds=[])
        for winner in winners
    ])
    db.commit()


def ranking_rank(client, season, player_id):
    rows = client.get("/api/stats/leaderboard", params={"season": season}).json()
    return next((r["rank"] for r in rows if r["stats"]["player_id"] == player_id), None)


def test_profile_rank_and_medals_match_ranking(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2027, 8, 1))  # 현재 시즌3, 시즌1·2는 끝남
    p = player_ids
    add_matches(db, date(2025, 12, 1), p[:2], p[2:], ["B"] * 3)       # 시즌 없음
    add_matches(db, date(2026, 3, 1), p[:2], p[2:], ["A"] * 3)        # 시즌1
    add_matches(db, date(2027, 2, 1), [p[0], p[2]], [p[1], p[3]], ["B", "B", "A"])  # 시즌2
    add_matches(db, date(2027, 7, 2), [p[0], p[3]], [p[1], p[2]], ["A"])  # 시즌3 (진행 중)

    body = client.get(f"/api/stats/player/{p[0]}/dashboard").json()

    assert body["current_season"] == 3 and body["current_season_name"] == "시즌3"
    assert body["current_rank"] == ranking_rank(client, 3, p[0])
    expected = [
        {"season": s, "season_name": f"시즌{s}", "rank": ranking_rank(client, s, p[0])}
        for s in (1, 2)
        if ranking_rank(client, s, p[0]) <= 3
    ]
    assert body["medals"] == expected
    assert body["medals"][0]["season"] == 1  # 오래된 시즌이 앞


def test_no_medals_while_first_season_runs(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2026, 11, 3))
    add_matches(db, date(2026, 3, 1), player_ids[:2], player_ids[2:], ["A"])
    body = client.get(f"/api/stats/player/{player_ids[0]}/dashboard").json()
    assert body["medals"] == []
    assert body["current_rank"] == ranking_rank(client, 1, player_ids[0])


def test_opponent_records_favorite_and_nemesis(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2026, 11, 3))
    p = player_ids
    day = date(2026, 5, 1)
    add_matches(db, day, p[:2], p[2:], ["A"] * 5 + ["B"])                     # 상대 p2, p3: 5승 1패
    add_matches(db, day, [p[0], p[2]], [p[1], p[3]], ["B"] * 4 + ["A"])       # 상대 p1, p3: 1승 4패

    h2h = client.get(f"/api/stats/player/{p[0]}/dashboard", params={"season": 1}).json()["opponents"]

    rows = {r["player_id"]: (r["games"], r["wins"], r["losses"], r["win_rate"]) for r in h2h["people"]}
    assert rows == {p[3]: (11, 6, 5, 55), p[2]: (6, 5, 1, 83), p[1]: (5, 1, 4, 20)}
    assert [r["player_id"] for r in h2h["people"]] == [p[3], p[2], p[1]]  # 많이 만난 순
    assert h2h["best"]["player_id"] == p[2]   # 😋 맛집: 5판 이상, 50% 초과 중 최고
    assert h2h["worst"]["player_id"] == p[1]    # 😈 천적: 5판 이상, 50% 미만 중 최저


@pytest.mark.parametrize(
    "winners",
    [
        ["A", "B"] * 3,  # 상대 p2, p3: 3승 3패 = 정확히 50%
        ["A"] * 4,       # 상대 p2, p3: 4승 0패지만 5판 미만
        ["B"] * 4,       # 0승 4패지만 5판 미만
    ],
)
def test_no_highlight_at_fifty_percent_or_under_five_games(client, db, player_ids, monkeypatch, winners):
    set_today(monkeypatch, date(2026, 11, 3))
    p = player_ids
    add_matches(db, date(2026, 5, 1), p[:2], p[2:], winners)

    h2h = client.get(f"/api/stats/player/{p[0]}/dashboard").json()["opponents"]
    assert h2h["best"] is None and h2h["worst"] is None
    assert len(h2h["people"]) == 2
    partners = client.get(f"/api/stats/player/{p[0]}/dashboard").json()["partners"]
    assert partners["best"] is None and partners["worst"] is None


def test_stats_follow_selected_season_but_profile_stays(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2027, 8, 1))
    p = player_ids
    add_matches(db, date(2026, 3, 1), p[:2], p[2:], ["A"] * 2)   # 시즌1
    add_matches(db, date(2027, 7, 2), p[:2], p[2:], ["B"])       # 시즌3

    def get(season):
        params = {"season": season} if season else {}
        return client.get(f"/api/stats/player/{p[0]}/dashboard", params=params).json()

    s1, s2, s3, total = get(1), get(2), get(3), get(None)
    assert (s1["games"], s2["games"], s3["games"], total["games"]) == (2, 0, 1, 3)
    assert s2["opponents"]["people"] == []
    profiles = {(d["current_rank"], str(d["medals"])) for d in (s1, s2, s3, total)}
    assert len(profiles) == 1  # 프로필은 시즌 선택과 무관


def test_dashboard_errors(client, player_ids, monkeypatch):
    set_today(monkeypatch, date(2026, 11, 3))
    assert client.get("/api/stats/player/999/dashboard").status_code == 404
    assert client.get(f"/api/stats/player/{player_ids[0]}/dashboard", params={"season": 2}).status_code == 422


def test_dashboard_uses_four_queries(db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2028, 8, 1))  # 끝난 시즌 4개
    p = player_ids
    for day in (date(2026, 3, 1), date(2027, 2, 1), date(2027, 8, 1), date(2028, 2, 1)):
        add_matches(db, day, p[:2], p[2:], ["A", "B"])
    count = {"n": 0}

    def count_query(*_args):
        count["n"] += 1

    event.listen(db.bind, "before_cursor_execute", count_query)
    try:
        DashboardService.get_dashboard(db, p[0], None)
    finally:
        event.remove(db.bind, "before_cursor_execute", count_query)
    assert count["n"] <= 4  # 사람·경기·티츄 + 시즌 기록용 내 경기 라운드


def test_partner_records_best_friend_and_bad_blood(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2026, 11, 3))
    p = player_ids
    day = date(2026, 5, 1)
    add_matches(db, day, p[:2], p[2:], ["A"] * 5 + ["B"])                 # p1과 같은 팀: 5승 1패
    add_matches(db, day, [p[0], p[2]], [p[1], p[3]], ["B"] * 4 + ["A"])   # p2와 같은 팀: 1승 4패
    add_matches(db, day, [p[0], p[3]], [p[1], p[2]], ["A", "B"])          # p3와 같은 팀: 1승 1패

    partners = client.get(f"/api/stats/player/{p[0]}/dashboard", params={"season": 1}).json()["partners"]

    rows = {r["player_id"]: (r["games"], r["wins"], r["losses"], r["win_rate"]) for r in partners["people"]}
    assert rows == {p[1]: (6, 5, 1, 83), p[2]: (5, 1, 4, 20), p[3]: (2, 1, 1, 50)}
    assert [r["player_id"] for r in partners["people"]] == [p[1], p[2], p[3]]  # 같이한 판 많은 순
    assert partners["best"]["player_id"] == p[1]   # 💞 베프: 5판 이상, 팀 승률 50% 초과 중 최고
    assert partners["worst"]["player_id"] == p[2]  # 💔 악연: 5판 이상, 50% 미만 중 최저
    assert all(r["player_id"] != p[0] for r in partners["people"])  # 나 자신은 없음


def test_profile_record_matches_ranking(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2027, 8, 1))  # 현재 시즌3
    p = player_ids
    add_matches(db, date(2027, 2, 1), p[:2], p[2:], ["A"] * 4)          # 시즌2 (프로필에 안 들어감)
    add_matches(db, date(2027, 7, 2), p[:2], p[2:], ["A", "A", "B"])    # 시즌3: 2승 1패

    rows = client.get("/api/stats/leaderboard", params={"season": 3}).json()
    ranking = next(r["stats"] for r in rows if r["stats"]["player_id"] == p[0])
    body = client.get(f"/api/stats/player/{p[0]}/dashboard", params={"season": 2}).json()

    assert body["current_record"] == {"wins": 2, "losses": 1, "win_rate": 66.7}
    assert body["current_record"] == {k: ranking[k] for k in ("wins", "losses", "win_rate")}


def test_profile_record_hidden_without_current_season_games(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2027, 8, 1))
    add_matches(db, date(2027, 2, 1), player_ids[:2], player_ids[2:], ["A"])  # 시즌2만
    body = client.get(f"/api/stats/player/{player_ids[0]}/dashboard").json()
    assert body["current_rank"] is None and body["current_record"] is None



# ── 🏅 시즌 기록 ──

def _rounds(*specs):
    """("base", a, b) 또는 ("events", [...]) 를 저장 형식 라운드로"""
    rounds = []
    for number, (a, b, events) in enumerate(specs, start=1):
        rounds.append({"round": number, "team_a_base": a, "team_b_base": b, "events": events})
    return rounds


def _game(db, day, team_a, team_b, rounds):
    from services.score_service import ScoreService
    score_a, score_b = ScoreService.calculate_totals(rounds, team_a, team_b)
    match = Match(play_date=day, team_a_ids=team_a, team_b_ids=team_b, status="FINISHED",
                  winner_team="A" if score_a > score_b else "B", score_a=score_a, score_b=score_b, rounds=rounds)
    db.add(match)
    db.commit()
    return match.id


def test_season_records(client, db, player_ids, monkeypatch):
    from models.models import MatchStats
    set_today(monkeypatch, date(2026, 11, 3))
    p = player_ids
    a, b = p[:2], p[2:]
    one_two_a = [{"type": "one_two", "team": "A"}]
    shot = [{"type": "grand", "player_id": p[0], "success": True}, {"type": "one_two", "team": "A"}]

    # g1 벼랑 끝 역전승: 0 : 800 (상대 800+, 800 뒤짐) → 원투 5번으로 1000 : 800
    g1 = _game(db, date(2026, 5, 1), a, b, _rounds(*[(0, 100, [])] * 8, *[(0, 0, one_two_a)] * 5))
    # g2 마지막 판 한 방: 850 : 950 (100 뒤짐) → 라지 성공 + 원투로 1250 : 950
    g2 = _game(db, date(2026, 5, 2), a, b, _rounds(*[(50, 50, [])] * 17, (0, 100, []), (0, 0, shot)))
    # g3 평범한 패배: 45 : 55 씩 → 855 : 1045 (상대 800+일 때 200 미만 차이 → 기회 아님)
    g3 = _game(db, date(2026, 5, 3), a, b, _rounds(*[(45, 55, [])] * 19))
    # g4 벼랑 끝에서 못 뒤집음: 0 : 1000
    g4 = _game(db, date(2026, 5, 4), a, b, _rounds(*[(0, 100, [])] * 10))
    db.add_all([
        MatchStats(match_id=g2, player_id=p[0], round_number=19, is_grand_try=True, is_grand_succ=True),
        MatchStats(match_id=g3, player_id=p[0], round_number=1, is_tichu_try=True),
        MatchStats(match_id=g4, player_id=p[0], round_number=1, is_tichu_try=True),
        MatchStats(match_id=g4, player_id=p[0], round_number=2, is_grand_try=True),
        MatchStats(match_id=g4, player_id=p[2], round_number=3, is_tichu_try=True),  # 다른 사람은 안 셈
    ])
    db.commit()

    mine = client.get(f"/api/stats/player/{p[0]}/dashboard").json()["records"]
    assert (mine["longest_win_streak"], mine["longest_lose_streak"]) == (2, 2)
    assert mine["biggest_win"] == {"match_id": g2, "margin": 300}
    assert mine["biggest_loss"] == {"match_id": g4, "margin": -1000}
    assert (mine["comeback_wins"], mine["comeback_win_chances"]) == (2, 3)   # g1, g2 성공 / g4 실패
    assert (mine["comeback_losses"], mine["comeback_loss_chances"]) == (0, 0)
    assert mine["average_margin"] == round((200 + 300 - 190 - 1000) / 4)
    assert (mine["tichu_calls_per_game"], mine["grand_calls_per_game"]) == (0.5, 0.5)

    # 상대 팀 쪽에서 보면 역전패: g1, g2는 뒤집힘 / g4는 지켜냄
    theirs = client.get(f"/api/stats/player/{p[2]}/dashboard").json()["records"]
    assert (theirs["comeback_losses"], theirs["comeback_loss_chances"]) == (2, 3)
    assert (theirs["comeback_wins"], theirs["comeback_win_chances"]) == (0, 0)


def test_season_records_none_without_games(client, db, player_ids, monkeypatch):
    set_today(monkeypatch, date(2026, 11, 3))
    body = client.get(f"/api/stats/player/{player_ids[0]}/dashboard").json()
    assert body["games"] == 0 and body["records"] is None
