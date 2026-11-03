from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from models.models import Match, MatchStats, Player
from routers.stats import router as stats_router
from services.stats_service import StatsService


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(stats_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_year_filter_excludes_other_seasons(db, player_ids):
    current_year = date.today().year
    current_match = Match(
        play_date=date(current_year, 1, 1),
        team_a_ids=player_ids[:2],
        team_b_ids=player_ids[2:],
        status="FINISHED",
        winner_team="A",
        rounds=[],
    )
    old_match = Match(
        play_date=date(current_year - 1, 12, 31),
        team_a_ids=player_ids[:2],
        team_b_ids=player_ids[2:],
        status="FINISHED",
        winner_team="B",
        rounds=[],
    )
    ongoing_match = Match(
        play_date=date(current_year, 1, 2),
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
        for item in StatsService.get_all_player_stats(db, current_year)
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

    current_year = date.today().year
    matches = [
        Match(
            play_date=date(current_year, 1, 1),
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
            play_date=date(current_year, 1, 2),
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

    leaderboard = StatsService.get_leaderboard(db, current_year)

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

    current_year = date.today().year
    matches = [
        Match(
            play_date=date(current_year, 1, 1),
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
            play_date=date(current_year, 1, 2),
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

    leaderboard = StatsService.get_team_leaderboard(db, current_year)

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
    year = date.today().year
    a, b = player_ids[:2], player_ids[2:]
    # 날짜 순: 패 승 패 승 승 승 (같은 날은 id 순) → 3연승, 최근 5경기 승 패 승 승 승
    winners = ["B", "A", "B", "A", "A", "A"]
    db.add_all([
        _finished(date(year, 1, 1 + i // 2), a, b, winner)
        for i, winner in enumerate(winners)
    ])
    # 다른 시즌 경기는 흐름 계산에서 빠진다
    db.add(_finished(date(year + 1, 1, 1), a, b, "B"))
    db.commit()

    stats = {s.player_id: s for s in StatsService.get_all_player_stats(db, year)}
    assert stats[a[0]].streak == 3
    assert stats[a[0]].recent_results == ["W", "L", "W", "W", "W"]
    assert stats[b[0]].streak == -3
    assert stats[b[0]].recent_results == ["L", "W", "L", "L", "L"]

    team = StatsService.get_team_stats(a[0], a[1], db, year)
    assert team.recent_results == ["W", "L", "W", "W", "W"]


def test_recent_form_with_few_or_no_games(db, player_ids):
    year = date.today().year
    a, b = player_ids[:2], player_ids[2:]
    db.add_all([
        _finished(date(year, 3, 1), a, b, "A"),
        _finished(date(year, 3, 2), a, b, "B"),
    ])
    db.commit()

    stats = {s.player_id: s for s in StatsService.get_all_player_stats(db, year)}
    assert stats[a[0]].streak == -1
    assert stats[a[0]].recent_results == ["W", "L"]

    empty = {s.player_id: s for s in StatsService.get_all_player_stats(db, year - 1)}
    assert empty[a[0]].streak == 0
    assert empty[a[0]].recent_results == []


def test_leaderboard_api_includes_recent_form(client, db, player_ids):
    year = date.today().year
    a, b = player_ids[:2], player_ids[2:]
    db.add_all([
        _finished(date(year, 4, 1), a, b, "A"),
        _finished(date(year, 4, 2), a, b, "A"),
    ])
    db.commit()

    players = client.get(f"/api/stats/leaderboard?year={year}").json()
    top = next(p["stats"] for p in players if p["stats"]["player_id"] == a[0])
    assert top["streak"] == 2
    assert top["recent_results"] == ["W", "W"]

    teams = client.get(f"/api/stats/leaderboard/teams?year={year}").json()
    team = next(t["stats"] for t in teams if t["stats"]["player1_id"] == min(a))
    assert team["recent_results"] == ["W", "W"]
