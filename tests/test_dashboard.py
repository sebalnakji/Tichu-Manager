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
    add_matches(db, day, p[:2], p[2:], (["A"] * 5 + ["B"]) * 2)               # 상대 p2, p3: 10승 2패
    add_matches(db, day, [p[0], p[2]], [p[1], p[3]], (["B"] * 4 + ["A"]) * 2) # 상대 p1, p3: 2승 8패 (딱 10판)

    h2h = client.get(f"/api/stats/player/{p[0]}/dashboard", params={"season": 1}).json()["opponents"]

    rows = {r["player_id"]: (r["games"], r["wins"], r["losses"], r["win_rate"]) for r in h2h["people"]}
    assert rows == {p[3]: (22, 12, 10, 55), p[2]: (12, 10, 2, 83), p[1]: (10, 2, 8, 20)}
    assert [r["player_id"] for r in h2h["people"]] == [p[3], p[2], p[1]]  # 많이 만난 순
    assert h2h["best"]["player_id"] == p[2]   # 😋 맛집: 10판 이상, 승패 차이 최대
    assert h2h["worst"]["player_id"] == p[1]    # 😈 천적: 10판 이상(딱 10판 포함), 승패 차이 최소


@pytest.mark.parametrize(
    "winners",
    [
        ["A", "B"] * 5,  # 상대 p2, p3: 5승 5패 = 정확히 50%
        ["A"] * 9,       # 상대 p2, p3: 9승 0패지만 10판 미만
        ["B"] * 9,       # 0승 9패지만 10판 미만
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
    add_matches(db, day, p[:2], p[2:], (["A"] * 5 + ["B"]) * 2)               # p1과 같은 팀: 10승 2패
    add_matches(db, day, [p[0], p[2]], [p[1], p[3]], (["B"] * 4 + ["A"]) * 2) # p2와 같은 팀: 2승 8패 (딱 10판)
    add_matches(db, day, [p[0], p[3]], [p[1], p[2]], ["A", "B"])          # p3와 같은 팀: 1승 1패

    partners = client.get(f"/api/stats/player/{p[0]}/dashboard", params={"season": 1}).json()["partners"]

    rows = {r["player_id"]: (r["games"], r["wins"], r["losses"], r["win_rate"]) for r in partners["people"]}
    assert rows == {p[1]: (12, 10, 2, 83), p[2]: (10, 2, 8, 20), p[3]: (2, 1, 1, 50)}
    assert [r["player_id"] for r in partners["people"]] == [p[1], p[2], p[3]]  # 같이한 판 많은 순
    assert partners["best"]["player_id"] == p[1]   # 💞 베프: 10판 이상, 같은 팀 승패 차이 최대
    assert partners["worst"]["player_id"] == p[2]  # 💔 악연: 10판 이상, 같은 팀 승패 차이 최소
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


def test_highlights_pick_by_net_wins_then_win_rate(client, db, player_ids, monkeypatch):
    """맛집·천적은 승률이 아니라 승패 차이로, 동점이면 승률로"""
    from models.models import Player
    set_today(monkeypatch, date(2026, 11, 3))
    extra = [Player(name=f"nw-{i}", code=f"nw-code-{i}", profile_url="") for i in range(4)]
    db.add_all(extra)
    db.commit()
    me, partner = player_ids[0], player_ids[1]
    a, b, c, d = player_ids[2], player_ids[3], extra[0].id, extra[1].id
    e, f = extra[2].id, extra[3].id
    day = date(2026, 6, 1)
    # 상대 a·b: 12승 6패 (+6, 67%) / 상대 c·d: 7승 3패 (+4, 70%) → 승률은 c·d가 높지만 맛집은 a·b
    add_matches(db, day, [me, partner], [a, b], ["A"] * 12 + ["B"] * 6)
    add_matches(db, day, [me, partner], [c, d], ["A"] * 7 + ["B"] * 3)
    # 상대 e·f: 10승 4패 (+6, 71%) → a·b와 승패 차이 동점, 승률이 더 높으니 맛집은 e·f
    add_matches(db, day, [me, partner], [e, f], ["A"] * 10 + ["B"] * 4)

    opponents = client.get(f"/api/stats/player/{me}/dashboard").json()["opponents"]
    assert opponents["best"]["player_id"] in (e, f)
    assert (opponents["best"]["wins"], opponents["best"]["losses"]) == (10, 4)

    # 상대 a 쪽에서 보면 나(와 파트너)에게 6승 12패(−6) → 천적
    worst = client.get(f"/api/stats/player/{a}/dashboard").json()["opponents"]["worst"]
    assert worst["player_id"] in (me, partner)
    assert (worst["wins"], worst["losses"]) == (6, 12)


def test_highlight_prefers_bigger_net_over_higher_rate(client, db, player_ids, monkeypatch):
    """7승 3패(+4, 70%)보다 12승 6패(+6, 67%)가 맛집"""
    from models.models import Player
    set_today(monkeypatch, date(2026, 11, 3))
    extra = [Player(name=f"nr-{i}", code=f"nr-code-{i}", profile_url="") for i in range(2)]
    db.add_all(extra)
    db.commit()
    me, partner, a, b = player_ids
    add_matches(db, date(2026, 6, 1), [me, partner], [a, b], ["A"] * 12 + ["B"] * 6)
    add_matches(db, date(2026, 6, 2), [me, partner], [extra[0].id, extra[1].id], ["A"] * 7 + ["B"] * 3)

    best = client.get(f"/api/stats/player/{me}/dashboard").json()["opponents"]["best"]
    assert best["player_id"] in (a, b)
    assert (best["wins"], best["losses"]) == (12, 6)

