"""
Stats Service
통계 계산 및 랭킹 로직
"""
from typing import List, Optional

from sqlalchemy.orm import Session
from itertools import combinations

from core import season as season_rules
from models.models import Player, Match, MatchStats
from dto.stats import PlayerStats, TeamStats


class StatsService:
    """통계 계산 서비스"""

    @staticmethod
    def _filter_query_by_season(query, column, season: Optional[int]):
        """시즌 기간으로 거르기 (None이면 전체 기간)"""
        if not season:
            return query

        start_date, end_date = season_rules.season_bounds(season)
        return query.filter(column >= start_date, column < end_date)

    RECENT_FORM_GAMES = 5

    @staticmethod
    def _recent_form(results: List[tuple[Match, bool]]) -> tuple[int, List[str]]:
        """(경기, 승리 여부) 목록으로 연승/연패와 최근 5경기 결과를 구한다.

        Returns:
            (streak, recent_results): streak은 +N 연승 / -N 연패,
            recent_results는 오래된 → 최근 순 "W"/"L"
        """
        ordered = sorted(
            results,
            key=lambda item: (item[0].play_date, item[0].id),
            reverse=True,
        )
        streak = 0
        for _, won in ordered:
            if streak == 0:
                streak = 1 if won else -1
            elif (streak > 0) == won:
                streak += 1 if won else -1
            else:
                break

        recent = [
            "W" if won else "L"
            for _, won in ordered[:StatsService.RECENT_FORM_GAMES]
        ]
        return streak, list(reversed(recent))

    @staticmethod
    def _build_player_stats(
        player: Player,
        matches: List[Match],
        recent_matches: List[Match],
        tichu_stats: List[MatchStats],
    ) -> PlayerStats:
        """이미 조회한 데이터로 개인 통계를 계산한다."""
        player_id = player.id
        total_games = 0
        wins = 0
        losses = 0
        results: List[tuple[Match, bool]] = []

        for match in matches:
            if player_id in match.team_a_ids:
                total_games += 1
                won = match.winner_team == "A"
            elif player_id in match.team_b_ids:
                total_games += 1
                won = match.winner_team == "B"
            else:
                continue
            if won:
                wins += 1
            else:
                losses += 1
            results.append((match, won))

        streak, recent_results = StatsService._recent_form(results)

        recent_games = 0
        recent_wins = 0
        for match in recent_matches:
            if recent_games >= 10:
                break

            if player_id in match.team_a_ids:
                recent_games += 1
                if match.winner_team == "A":
                    recent_wins += 1
            elif player_id in match.team_b_ids:
                recent_games += 1
                if match.winner_team == "B":
                    recent_wins += 1

        tichu_try = sum(1 for stat in tichu_stats if stat.is_tichu_try)
        tichu_success = sum(1 for stat in tichu_stats if stat.is_tichu_succ)
        grand_try = sum(1 for stat in tichu_stats if stat.is_grand_try)
        grand_success = sum(1 for stat in tichu_stats if stat.is_grand_succ)

        win_rate = (wins / total_games * 100) if total_games > 0 else 0.0
        recent_win_rate = (
            recent_wins / recent_games * 100 if recent_games > 0 else 0.0
        )
        tichu_success_rate = (
            tichu_success / tichu_try * 100 if tichu_try > 0 else 0.0
        )
        grand_success_rate = (
            grand_success / grand_try * 100 if grand_try > 0 else 0.0
        )

        return PlayerStats(
            player_id=player.id,
            player_name=player.name,
            profile_url=player.profile_url,
            total_games=total_games,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 1),
            recent_games=recent_games,
            recent_wins=recent_wins,
            recent_win_rate=round(recent_win_rate, 1),
            tichu_try=tichu_try,
            tichu_success=tichu_success,
            tichu_success_rate=round(tichu_success_rate, 2),
            grand_try=grand_try,
            grand_success=grand_success,
            grand_success_rate=round(grand_success_rate, 2),
            streak=streak,
            recent_results=recent_results,
        )

    @staticmethod
    def _get_finished_matches(db: Session, season: Optional[int]) -> List[Match]:
        query = db.query(Match).filter(Match.status == "FINISHED")
        query = StatsService._filter_query_by_season(query, Match.play_date, season)
        return query.all()

    @staticmethod
    def _get_recent_matches(db: Session, season: Optional[int]) -> List[Match]:
        query = db.query(Match).filter(Match.status == "FINISHED")
        query = StatsService._filter_query_by_season(query, Match.play_date, season)
        return query.order_by(Match.play_date.desc(), Match.id.desc()).limit(20).all()

    @staticmethod
    def _get_match_stats(db: Session, season: Optional[int]) -> List[MatchStats]:
        query = db.query(MatchStats).join(Match).filter(
            Match.status == "FINISHED"
        )
        if season:
            query = StatsService._filter_query_by_season(
                query,
                Match.play_date,
                season,
            )
        return query.all()

    @staticmethod
    def get_match_context_stats(
        team_a_ids: List[int],
        team_b_ids: List[int],
        db: Session,
        season: int,
    ):
        """점수판에 필요한 선수·개인·팀 통계를 세 번의 조회로 계산한다 (해당 시즌 기준)."""
        player_ids = list(dict.fromkeys(team_a_ids + team_b_ids))
        players = db.query(Player).filter(Player.id.in_(player_ids)).all()
        players_by_id = {player.id: player for player in players}

        finished_matches = (
            db.query(Match)
            .filter(Match.status == "FINISHED")
            .order_by(Match.play_date.desc(), Match.id.desc())
            .all()
        )
        all_stats = (
            db.query(MatchStats)
            .filter(MatchStats.player_id.in_(player_ids))
            .all()
        )

        start_date, end_date = season_rules.season_bounds(season)
        season_matches = [
            match
            for match in finished_matches
            if start_date <= match.play_date < end_date
        ]
        season_match_ids = {match.id for match in season_matches}
        recent_matches = season_matches[:20]

        player_stats = {}
        for player_id, player in players_by_id.items():
            player_tichu_stats = [
                stat
                for stat in all_stats
                if stat.player_id == player_id
                and stat.match_id in season_match_ids
            ]
            player_stats[player_id] = StatsService._build_player_stats(
                player,
                season_matches,
                recent_matches,
                player_tichu_stats,
            )

        def build_team_stats(team_ids: List[int]) -> Optional[TeamStats]:
            if len(team_ids) != 2:
                return None

            player1 = players_by_id.get(team_ids[0])
            player2 = players_by_id.get(team_ids[1])
            if not player1 or not player2:
                return None

            # 점수판의 "시즌" 팀 승패·승률: 개인 통계와 같이 해당 시즌 경기만
            team_matches = [
                match
                for match in season_matches
                if (
                    player1.id in match.team_a_ids
                    and player2.id in match.team_a_ids
                )
                or (
                    player1.id in match.team_b_ids
                    and player2.id in match.team_b_ids
                )
            ]
            team_match_ids = {match.id for match in team_matches}
            team_tichu_stats = [
                stat
                for stat in all_stats
                if stat.match_id in team_match_ids
                and stat.player_id in {player1.id, player2.id}
            ]
            return StatsService._build_team_stats(
                player1,
                player2,
                team_matches,
                team_tichu_stats,
            )

        return (
            players_by_id,
            player_stats,
            build_team_stats(team_a_ids),
            build_team_stats(team_b_ids),
        )

    @staticmethod
    def get_all_player_stats(
        db: Session,
        season: Optional[int] = None,
    ) -> List[PlayerStats]:
        """모든 개인 통계를 고정된 네 번의 DB 조회로 계산한다."""
        players = db.query(Player).all()
        matches = StatsService._get_finished_matches(db, season)
        recent_matches = StatsService._get_recent_matches(db, season)
        all_tichu_stats = StatsService._get_match_stats(db, season)

        stats_by_player: dict[int, List[MatchStats]] = {}
        for stat in all_tichu_stats:
            stats_by_player.setdefault(stat.player_id, []).append(stat)

        return [
            StatsService._build_player_stats(
                player,
                matches,
                recent_matches,
                stats_by_player.get(player.id, []),
            )
            for player in players
        ]

    @staticmethod
    def get_player_stats(
        player_id: int,
        db: Session,
        season: Optional[int] = None
    ) -> Optional[PlayerStats]:
        """
        개인 전적 통계 조회

        Args:
            player_id: 플레이어 ID
            db: 데이터베이스 세션
            season: 시즌 필터 (None이면 전체 기간)

        Returns:
            PlayerStats: 플레이어 통계 또는 None
        """
        player = db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return None

        matches = StatsService._get_finished_matches(db, season)
        recent_matches = StatsService._get_recent_matches(db, season)
        stats_query = (
            db.query(MatchStats)
            .join(Match)
            .filter(
                MatchStats.player_id == player_id,
                Match.status == "FINISHED",
            )
        )
        if season:
            stats_query = StatsService._filter_query_by_season(
                stats_query,
                Match.play_date,
                season,
            )
        tichu_stats = stats_query.all()

        return StatsService._build_player_stats(
            player,
            matches,
            recent_matches,
            tichu_stats,
        )

    @staticmethod
    def _build_team_stats(
        player1: Player,
        player2: Player,
        matches: List[Match],
        tichu_stats: List[MatchStats],
    ) -> TeamStats:
        """이미 조회한 데이터로 팀 통계를 계산한다."""
        player1_id = player1.id
        player2_id = player2.id
        names = sorted([player1.name, player2.name])
        total_games = 0
        wins = 0
        losses = 0
        results: List[tuple[Match, bool]] = []

        for match in matches:
            if player1_id in match.team_a_ids and player2_id in match.team_a_ids:
                total_games += 1
                won = match.winner_team == "A"
            elif player1_id in match.team_b_ids and player2_id in match.team_b_ids:
                total_games += 1
                won = match.winner_team == "B"
            else:
                continue
            if won:
                wins += 1
            else:
                losses += 1
            results.append((match, won))

        _, recent_results = StatsService._recent_form(results)

        tichu_try = sum(1 for stat in tichu_stats if stat.is_tichu_try)
        tichu_success = sum(1 for stat in tichu_stats if stat.is_tichu_succ)
        grand_try = sum(1 for stat in tichu_stats if stat.is_grand_try)
        grand_success = sum(1 for stat in tichu_stats if stat.is_grand_succ)

        win_rate = (wins / total_games * 100) if total_games > 0 else 0.0
        tichu_success_rate = (
            tichu_success / tichu_try * 100 if tichu_try > 0 else 0.0
        )
        grand_success_rate = (
            grand_success / grand_try * 100 if grand_try > 0 else 0.0
        )

        if player1_id > player2_id:
            player1, player2 = player2, player1

        return TeamStats(
            player1_id=player1.id,
            player2_id=player2.id,
            player1_name=player1.name,
            player2_name=player2.name,
            player1_profile_url=player1.profile_url or "",
            player2_profile_url=player2.profile_url or "",
            team_name=f"{names[0]}/{names[1]} 팀",
            total_games=total_games,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 1),
            tichu_try=tichu_try,
            tichu_success=tichu_success,
            tichu_success_rate=round(tichu_success_rate, 1),
            grand_try=grand_try,
            grand_success=grand_success,
            grand_success_rate=round(grand_success_rate, 1),
            recent_results=recent_results,
        )

    @staticmethod
    def get_team_stats(
        player1_id: int,
        player2_id: int,
        db: Session,
        season: Optional[int] = None
    ) -> Optional[TeamStats]:
        """
        팀 전적 통계 조회 (순서 무관)

        Args:
            player1_id: 플레이어 1 ID
            player2_id: 플레이어 2 ID
            db: 데이터베이스 세션
            season: 시즌 필터

        Returns:
            TeamStats: 팀 통계 또는 None
        """
        players = db.query(Player).filter(
            Player.id.in_([player1_id, player2_id])
        ).all()
        players_by_id = {player.id: player for player in players}
        player1 = players_by_id.get(player1_id)
        player2 = players_by_id.get(player2_id)
        if not player1 or not player2:
            return None

        matches = StatsService._get_finished_matches(db, season)
        team_matches = [
            match
            for match in matches
            if (
                player1_id in match.team_a_ids
                and player2_id in match.team_a_ids
            )
            or (
                player1_id in match.team_b_ids
                and player2_id in match.team_b_ids
            )
        ]
        match_ids = {match.id for match in team_matches}
        tichu_stats = (
            db.query(MatchStats)
            .filter(
                MatchStats.match_id.in_(match_ids),
                MatchStats.player_id.in_([player1_id, player2_id]),
            )
            .all()
            if match_ids
            else []
        )

        return StatsService._build_team_stats(
            player1,
            player2,
            team_matches,
            tichu_stats,
        )

    @staticmethod
    def get_team_records(db: Session, teams: List[List[int]]) -> List[dict]:
        """2인 팀들의 전체 기간 승패를 한 번의 조회로 계산한다 (매치업 창용).

        완료 경기의 팀 구성·승자 컬럼만 읽고(라운드 JSON은 읽지 않음),
        여러 팀을 같은 결과로 함께 센다.

        Returns:
            팀 순서대로 {"total_games", "wins", "win_rate"}
        """
        rows = (
            db.query(Match.team_a_ids, Match.team_b_ids, Match.winner_team)
            .filter(Match.status == "FINISHED")
            .all()
        )
        records = []
        for team in teams:
            members = set(team)
            games = wins = 0
            for team_a_ids, team_b_ids, winner_team in rows:
                if members <= set(team_a_ids):
                    side = "A"
                elif members <= set(team_b_ids):
                    side = "B"
                else:
                    continue
                games += 1
                if winner_team == side:
                    wins += 1
            records.append({
                "total_games": games,
                "wins": wins,
                "win_rate": round(wins / games * 100, 1) if games else 0.0,
            })
        return records

    @staticmethod
    def get_team_leaderboard(
        db: Session,
        season: Optional[int] = None
    ) -> List[TeamStats]:
        """
        팀 랭킹 조회
        정렬 우선순위: 10경기 이상 > 승률 > 경기 수 > 승수 >
        티츄 성공률 > 라지티츄 성공률

        Args:
            db: 데이터베이스 세션
            season: 시즌 필터

        Returns:
            List[TeamStats]: 정렬된 팀 통계 리스트
        """
        players = db.query(Player).all()
        matches = StatsService._get_finished_matches(db, season)
        all_tichu_stats = StatsService._get_match_stats(db, season)
        team_combinations = list(combinations(players, 2))

        stats_list = []
        for player1, player2 in team_combinations:
            team_matches = [
                match
                for match in matches
                if (
                    player1.id in match.team_a_ids
                    and player2.id in match.team_a_ids
                )
                or (
                    player1.id in match.team_b_ids
                    and player2.id in match.team_b_ids
                )
            ]
            match_ids = {match.id for match in team_matches}
            team_tichu_stats = [
                stat
                for stat in all_tichu_stats
                if stat.match_id in match_ids
                and stat.player_id in {player1.id, player2.id}
            ]
            stats = StatsService._build_team_stats(
                player1,
                player2,
                team_matches,
                team_tichu_stats,
            )
            if stats and stats.total_games > 0:  # 함께 게임한 기록이 있는 팀만
                stats_list.append(stats)

        # 정렬: 10경기 이상 > 승률 > 경기 수 > 승수 >
        # 티츄 성공률 > 라지티츄 성공률
        stats_list.sort(
            key=lambda x: (
                -(x.total_games >= 10),
                -x.win_rate,
                -x.total_games,
                -x.wins,
                -x.tichu_success_rate,
                -x.grand_success_rate,
            )
        )

        return stats_list

    @staticmethod
    def get_leaderboard(
        db: Session,
        season: Optional[int] = None
    ) -> List[PlayerStats]:
        """
        전체 플레이어 랭킹 조회
        정렬 우선순위: 10경기 이상 > 승률 > 경기 수 > 승수 >
        티츄 성공률 > 라지티츄 성공률

        Args:
            db: 데이터베이스 세션
            season: 시즌 필터

        Returns:
            List[PlayerStats]: 정렬된 플레이어 통계 리스트
        """
        stats_list = [
            stats
            for stats in StatsService.get_all_player_stats(db, season)
            if stats.total_games > 0
        ]

        # 정렬: 10경기 이상 > 승률 > 경기 수 > 승수 >
        # 티츄 성공률 > 라지티츄 성공률
        stats_list.sort(
            key=lambda x: (
                -(x.total_games >= 10),
                -x.win_rate,
                -x.total_games,
                -x.wins,  # 승수 내림차순
                -x.tichu_success_rate,  # 티츄 성공률 내림차순
                -x.grand_success_rate,  # 라지티츄 성공률 내림차순
            )
        )

        return stats_list
