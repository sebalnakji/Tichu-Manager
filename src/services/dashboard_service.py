"""
Dashboard Service
개인 분석: 프로필(현재 시즌 순위, 획득 메달) + 시즌별 통계(상대 전적, 파트너 전적)

사람·완료 경기·티츄 기록을 한 번씩만(조회 3번) 읽고, 시즌별로 나눠 함께 계산한다.
경기는 팀 구성·승자·날짜 컬럼만 읽는다 (라운드 JSON 제외).
"""
from collections import defaultdict
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from core import season as season_rules
from dto.dashboard import (
    DashboardMedal,
    DashboardPlayer,
    PeopleRecords,
    PersonRecord,
    PlayerDashboardResponse,
    SeasonRecord,
)
from dto.stats import PlayerStats
from models.models import Match, MatchStats, Player
from services.stats_service import StatsService

MEDAL_RANKS = 3            # 끝난 시즌 최종 1~3위에 메달
HIGHLIGHT_MIN_GAMES = 5    # 맛집·천적·베프·악연은 5판 이상 중에서


class DashboardService:
    """개인 분석 서비스"""

    @staticmethod
    def _ranked(players: List[Player], matches: list, stats: list) -> List[PlayerStats]:
        """경기·티츄 기록 묶음으로 랭킹 화면과 같은 순서의 개인 통계"""
        stats_by_player: Dict[int, list] = defaultdict(list)
        for stat in stats:
            stats_by_player[stat.player_id].append(stat)
        recent = matches[:20]  # matches는 최신순
        return StatsService.sort_leaderboard([
            StatsService._build_player_stats(player, matches, recent, stats_by_player[player.id])
            for player in players
        ])

    @staticmethod
    def _rank_of(ranked: List[PlayerStats], player_id: int) -> Optional[int]:
        for index, stats in enumerate(ranked):
            if stats.player_id == player_id:
                return index + 1
        return None

    @staticmethod
    def _people_records(tally: Dict[int, list], names: Dict[int, str]) -> PeopleRecords:
        """사람별 [경기, 승] → 정렬된 줄 + 가장 좋은/나쁜 사람"""
        records = [
            PersonRecord(
                player_id=person_id,
                name=names[person_id],
                games=played,
                wins=wins,
                losses=played - wins,
                win_rate=round(wins / played * 100),
            )
            for person_id, (played, wins) in tally.items()
            if person_id in names  # 삭제된 플레이어 제외
        ]
        ratio = lambda r: r.wins / r.games
        records.sort(key=lambda r: (-r.games, -ratio(r), r.name))

        eligible = [r for r in records if r.games >= HIGHLIGHT_MIN_GAMES]
        best = max((r for r in eligible if r.wins * 2 > r.games), key=lambda r: (ratio(r), r.games), default=None)
        worst = min((r for r in eligible if r.wins * 2 < r.games), key=lambda r: (ratio(r), -r.games), default=None)
        return PeopleRecords(best=best, worst=worst, people=records)

    @staticmethod
    def _head_to_head(
        player_id: int,
        matches: list,
        names: Dict[int, str],
    ) -> tuple[int, PeopleRecords, PeopleRecords]:
        """상대별 전적(상대 두 명 모두 만난 것으로)과 파트너별 같은 팀 전적"""
        games = 0
        opponents: Dict[int, list] = defaultdict(lambda: [0, 0])  # 사람 id → [경기, 승]
        partners: Dict[int, list] = defaultdict(lambda: [0, 0])
        for match in matches:
            if player_id in match.team_a_ids:
                mine, theirs, won = match.team_a_ids, match.team_b_ids, match.winner_team == "A"
            elif player_id in match.team_b_ids:
                mine, theirs, won = match.team_b_ids, match.team_a_ids, match.winner_team == "B"
            else:
                continue
            games += 1
            for person_id in theirs:
                opponents[person_id][0] += 1
                opponents[person_id][1] += int(won)
            for person_id in mine:
                if person_id != player_id:
                    partners[person_id][0] += 1
                    partners[person_id][1] += int(won)
        return (
            games,
            DashboardService._people_records(opponents, names),
            DashboardService._people_records(partners, names),
        )

    @staticmethod
    def get_dashboard(
        db: Session,
        player_id: int,
        season: Optional[int] = None,
    ) -> Optional[PlayerDashboardResponse]:
        players = db.query(Player).all()
        player = next((p for p in players if p.id == player_id), None)
        if not player:
            return None

        matches = (
            db.query(
                Match.id, Match.play_date, Match.team_a_ids,
                Match.team_b_ids, Match.winner_team,
            )
            .filter(Match.status == "FINISHED")
            .order_by(Match.play_date.desc(), Match.id.desc())
            .all()
        )
        stats = (
            db.query(
                MatchStats.match_id, MatchStats.player_id,
                MatchStats.is_tichu_try, MatchStats.is_tichu_succ,
                MatchStats.is_grand_try, MatchStats.is_grand_succ,
            )
            .join(Match, Match.id == MatchStats.match_id)
            .filter(Match.status == "FINISHED")
            .all()
        )

        # 시즌별로 나누기 (2026년 이전 경기는 시즌 없음 → 전체 기간에만)
        season_by_match = {m.id: season_rules.season_of(m.play_date) for m in matches}
        matches_by_season: Dict[Optional[int], list] = defaultdict(list)
        for match in matches:
            matches_by_season[season_by_match[match.id]].append(match)
        stats_by_season: Dict[Optional[int], list] = defaultdict(list)
        for stat in stats:
            stats_by_season[season_by_match.get(stat.match_id)].append(stat)

        def ranked_for(number: int) -> List[PlayerStats]:
            return DashboardService._ranked(
                players, matches_by_season[number], stats_by_season[number]
            )

        # 프로필: 현재 시즌 순위 + 끝난 시즌 메달
        current = season_rules.current_season()
        current_ranked = ranked_for(current)
        current_rank = DashboardService._rank_of(current_ranked, player_id)
        current_stats = current_ranked[current_rank - 1] if current_rank else None
        current_record = SeasonRecord(
            wins=current_stats.wins,
            losses=current_stats.losses,
            win_rate=current_stats.win_rate,
        ) if current_stats else None
        medals = []
        for number in range(1, current):
            rank = DashboardService._rank_of(ranked_for(number)[:MEDAL_RANKS], player_id)
            if rank:
                medals.append(DashboardMedal(
                    season=number,
                    season_name=season_rules.season_name(number),
                    rank=rank,
                ))

        # 아래 통계: 선택한 시즌 (None = 전체 기간)
        selected = matches if season is None else matches_by_season[season]
        names = {p.id: p.name for p in players if p.id != player_id}
        games, opponents, partners = DashboardService._head_to_head(player_id, selected, names)

        return PlayerDashboardResponse(
            player=DashboardPlayer(id=player.id, name=player.name, profile_url=player.profile_url or ""),
            current_season=current,
            current_season_name=season_rules.season_name(current),
            current_rank=current_rank,
            current_record=current_record,
            medals=medals,
            season=season,
            games=games,
            opponents=opponents,
            partners=partners,
        )
