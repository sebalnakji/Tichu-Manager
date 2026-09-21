from datetime import date

from models.models import Match, MatchStats, Player
from services.stats_service import StatsService


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
