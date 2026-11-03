from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import season as season_rules
from core.database import get_db
from models.models import Match, MatchStats, Player
from routers.stats import router as stats_router
from services.stats_service import StatsService

# 테스트 기준 시즌: 현재 시즌과 그 기간
SEASON = season_rules.current_season()
START, END = season_rules.season_bounds(SEASON)


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(stats_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_season_filter_excludes_other_seasons(db, player_ids):
    current_match = Match(
        play_date=START,
        team_a_ids=player_ids[:2],
        team_b_ids=player_ids[2:],
        status="FINISHED",
        winner_team="A",
        rounds=[],
    )
    old_match = Match(
        play_date=START - timedelta(days=1),
        team_a_ids=player_ids[:2],
        team_b_ids=player_ids[2:],
        status="FINISHED",
        winner_team="B",
        rounds=[],
    )
    ongoing_match = Match(
        play_date=START + timedelta(days=1),
        team_a_ids=player_ids[:2],
        team_b_ids=player_ids[2:],
        status="PLAYING",
        rounds=[],
    )
    db.add_all([current_match, old_match, ongoing_match])
    db.commit()
    db.add(
        MatchStats(
            match_id=ongoing_match.id,
            player_id=player_ids[0],
            round_number=1,
            is_tichu_try=True,
            is_tichu_succ=True,
        )
    )
    db.commit()

    stats = {
        item.player_id: item
        for item in StatsService.get_all_player_stats(db, SEASON)
    }

    assert stats[player_ids[0]].total_games == 1
    assert stats[player_ids[0]].wins == 1
    assert stats[player_ids[0]].tichu_try == 0


def test_performance_indexes_are_declared():
    match_indexes = {index.name for index in Match.__table__.indexes}
    stats_indexes = {index.name for index in MatchStats.__table__.indexes}

    assert "ix_tichu_matches_status_id" in match_indexes
    assert "ix_tichu_matches_status_play_date" in match_indexes
    assert "ix_tichu_match_stats_match_round" in stats_indexes
    assert "ix_tichu_match_stats_player" in stats_indexes
    assert "ix_tichu_match_stats_match_player" in stats_indexes


def test_leaderboard_prioritizes_qualified_players_then_win_rate(db, player_ids):
    extras = [
        Player(name=f"extra-{index}", code=f"extra-code-{index}", profile_url="")
        for index in range(1, 5)
    ]
    db.add_all(extras)
    db.commit()

    matches = [
        Match(
            play_date=START,
            team_a_ids=player_ids[:2],
            team_b_ids=player_ids[2:],
            status="FINISHED",
            winner_team="A",
            rounds=[],
        )
        for _ in range(10)
    ]
    matches.extend(
        Match(
            play_date=START + timedelta(days=1),
            team_a_ids=[extras[0].id, extras[1].id],
            team_b_ids=[extras[2].id, extras[3].id],
            status="FINISHED",
            winner_team="A",
            rounds=[],
        )
        for _ in range(2)
    )
    db.add_all(matches)
    db.commit()

    leaderboard = StatsService.get_leaderboard(db, SEASON)

    qualified = [stats for stats in leaderboard if stats.total_games >= 10]
    provisional = [stats for stats in leaderboard if stats.total_games < 10]

    assert [stats.total_games for stats in qualified] == [10, 10, 10, 10]
    assert [stats.win_rate for stats in qualified] == [100.0, 100.0, 0.0, 0.0]
    assert [stats.total_games for stats in provisional] == [2, 2, 2, 2]
    assert [stats.win_rate for stats in provisional] == [100.0, 100.0, 0.0, 0.0]


def test_team_leaderboard_prioritizes_qualified_teams_then_win_rate(
    db,
    player_ids,
):
    extras = [
        Player(
            name=f"team-extra-{index}",
            code=f"team-extra-code-{index}",
            profile_url="",
        )
        for index in range(1, 5)
    ]
    db.add_all(extras)
    db.commit()

    matches = [
        Match(
            play_date=START,
            team_a_ids=player_ids[:2],
            team_b_ids=player_ids[2:],
            status="FINISHED",
            winner_team="A",
            rounds=[],
        )
        for _ in range(10)
    ]
    matches.extend(
        Match(
            play_date=START + timedelta(days=1),
            team_a_ids=[extras[0].id, extras[1].id],
            team_b_ids=[extras[2].id, extras[3].id],
            status="FINISHED",
            winner_team="A",
            rounds=[],
        )
        for _ in range(2)
    )
    db.add_all(matches)
    db.commit()

    leaderboard = StatsService.get_team_leaderboard(db, SEASON)

    assert [stats.total_games for stats in leaderboard] == [10, 10, 2, 2]
    assert [stats.win_rate for stats in leaderboard] == [100.0, 0.0, 100.0, 0.0]


def _finished(play_date, team_a, team_b, winner):
    return Match(
        play_date=play_date,
        team_a_ids=team_a,
        team_b_ids=team_b,
        status="FINISHED",
        winner_team=winner,
        rounds=[],
    )


def test_recent_form_streak_and_last_five(db, player_ids):
    a, b = player_ids[:2], player_ids[2:]
    # 날짜 순: 패 승 패 승 승 승 (같은 날은 id 순) → 3연승, 최근 5경기 승 패 승 승 승
    winners = ["B", "A", "B", "A", "A", "A"]
    db.add_all([
        _finished(START + timedelta(days=i // 2), a, b, winner)
        for i, winner in enumerate(winners)
    ])
    # 다음 시즌 경기는 흐름 계산에서 빠진다
    db.add(_finished(END, a, b, "B"))
    db.commit()

    stats = {s.player_id: s for s in StatsService.get_all_player_stats(db, SEASON)}
    assert stats[a[0]].streak == 3
    assert stats[a[0]].recent_results == ["W", "L", "W", "W", "W"]
    assert stats[b[0]].streak == -3
    assert stats[b[0]].recent_results == ["L", "W", "L", "L", "L"]

    team = StatsService.get_team_stats(a[0], a[1], db, SEASON)
    assert team.recent_results == ["W", "L", "W", "W", "W"]


def test_recent_form_with_few_or_no_games(db, player_ids):
    a, b = player_ids[:2], player_ids[2:]
    db.add_all([
        _finished(START + timedelta(days=1), a, b, "A"),
        _finished(START + timedelta(days=2), a, b, "B"),
    ])
    db.commit()

    stats = {s.player_id: s for s in StatsService.get_all_player_stats(db, SEASON)}
    assert stats[a[0]].streak == -1
    assert stats[a[0]].recent_results == ["W", "L"]

    empty = {s.player_id: s for s in StatsService.get_all_player_stats(db, SEASON + 1)}
    assert empty[a[0]].streak == 0
    assert empty[a[0]].recent_results == []


def test_leaderboard_api_includes_recent_form(client, db, player_ids):
    a, b = player_ids[:2], player_ids[2:]
    db.add_all([
        _finished(START + timedelta(days=3), a, b, "A"),
        _finished(START + timedelta(days=4), a, b, "A"),
    ])
    db.commit()

    players = client.get(f"/api/stats/leaderboard?season={SEASON}").json()
    top = next(p["stats"] for p in players if p["stats"]["player_id"] == a[0])
    assert top["streak"] == 2
    assert top["recent_results"] == ["W", "W"]

    teams = client.get(f"/api/stats/leaderboard/teams?season={SEASON}").json()
    team = next(t["stats"] for t in teams if t["stats"]["player1_id"] == min(a))
    assert team["recent_results"] == ["W", "W"]


def test_single_pass_rankings_match_per_player_and_per_team_calculation(db):
    """랭킹(한 번 훑기)이 한 사람·한 팀씩 따로 계산한 결과와 숫자가 모두 같은지"""
    import random
    from itertools import combinations

    rng = random.Random(7)
    players = [Player(name=f"eq-{i}", code=f"eq-code-{i}", profile_url="") for i in range(6)]
    db.add_all(players)
    db.commit()
    ids = [p.id for p in players]
    for index in range(120):
        four = rng.sample(ids, 4)
        match = Match(
            play_date=START + timedelta(days=index // 4),
            team_a_ids=four[:2], team_b_ids=four[2:],
            status="FINISHED", winner_team=rng.choice("AB"), rounds=[],
        )
        db.add(match)
        db.flush()
        for player_id in rng.sample(four, rng.randint(0, 3)):
            kind = rng.choice(["tichu", "tichu", "grand"])
            success = rng.random() < 0.6
            db.add(MatchStats(
                match_id=match.id, player_id=player_id, round_number=1,
                is_tichu_try=kind == "tichu", is_tichu_succ=kind == "tichu" and success,
                is_grand_try=kind == "grand", is_grand_succ=kind == "grand" and success,
            ))
    db.add(Match(play_date=START, team_a_ids=ids[:2], team_b_ids=ids[2:4], status="PLAYING", rounds=[]))
    db.commit()

    for season in (SEASON, None):
        bulk = {s.player_id: s for s in StatsService.get_all_player_stats(db, season)}
        for player_id in ids:
            assert bulk[player_id] == StatsService.get_player_stats(player_id, db, season)

        team_board = StatsService.get_team_leaderboard(db, season)
        expected = [
            StatsService.get_team_stats(a, b, db, season)
            for a, b in combinations(ids, 2)
        ]
        expected = [t for t in expected if t.total_games > 0]
        assert sorted(team_board, key=lambda t: (t.player1_id, t.player2_id)) == \
            sorted(expected, key=lambda t: (t.player1_id, t.player2_id))
        assert len(team_board) == len(expected) > 0
