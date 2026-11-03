"""
History Service
게임 기록 화면: 날짜 단위 페이지, 개인/팀 필터, 하루 기록표, 시즌 요약
"""
from datetime import date
from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from dto.history import (
    HistoryDay,
    HistoryDayRecord,
    HistoryMatch,
    HistoryPageResponse,
    HistoryPlayer,
    HistoryPlayerOption,
    HistoryRecord,
)
from models.models import Match, MatchStats, Player
from services.stats_service import StatsService

DEFAULT_PAGE_DAYS = 3


class HistoryService:
    """게임 기록 조회 서비스"""

    @staticmethod
    def _season_matches(db: Session, season: Optional[int]) -> List[Match]:
        """시즌의 완료 경기를 최신 날짜순으로 조회한다."""
        query = db.query(Match).filter(Match.status == "FINISHED")
        query = StatsService._filter_query_by_season(query, Match.play_date, season)
        return query.order_by(Match.play_date.desc(), Match.id.desc()).all()

    @staticmethod
    def _team_of(match: Match, player_id: int) -> Optional[str]:
        if player_id in match.team_a_ids:
            return "A"
        if player_id in match.team_b_ids:
            return "B"
        return None

    @staticmethod
    def _filter_matches(matches: Iterable[Match], player_ids: List[int]) -> List[Match]:
        """1명이면 그 사람이 뛴 경기, 2명이면 둘이 같은 팀이었던 경기.

        팀 구성은 JSON 배열이라 DB(SQLite/PostgreSQL)마다 검색 방식이 달라
        랭킹 계산과 같이 파이썬에서 거른다.
        """
        if not player_ids:
            return list(matches)
        result = []
        for match in matches:
            teams = {HistoryService._team_of(match, pid) for pid in player_ids}
            if len(teams) == 1 and None not in teams:
                result.append(match)
        return result

    @staticmethod
    def _add_result(record: HistoryRecord, won: bool) -> None:
        if won:
            record.wins += 1
        else:
            record.losses += 1

    @staticmethod
    def _add_tichu(record: HistoryRecord, stat: MatchStats) -> None:
        record.tichu_try += int(bool(stat.is_tichu_try))
        record.tichu_success += int(bool(stat.is_tichu_succ))
        record.grand_try += int(bool(stat.is_grand_try))
        record.grand_success += int(bool(stat.is_grand_succ))

    @staticmethod
    def _stats_by_match(db: Session, match_ids: List[int]) -> Dict[int, List[MatchStats]]:
        if not match_ids:
            return {}
        result: Dict[int, List[MatchStats]] = {}
        for stat in db.query(MatchStats).filter(MatchStats.match_id.in_(match_ids)).all():
            result.setdefault(stat.match_id, []).append(stat)
        return result

    @staticmethod
    def _summary(
        matches: List[Match],
        player_ids: List[int],
        stats_by_match: Dict[int, List[MatchStats]],
    ) -> HistoryRecord:
        """선택한 사람(팀) 기준 시즌 전적. 팀이면 두 사람의 티츄 기록을 합산한다."""
        summary = HistoryRecord()
        for match in matches:
            team = HistoryService._team_of(match, player_ids[0])
            HistoryService._add_result(summary, match.winner_team == team)
            for stat in stats_by_match.get(match.id, []):
                if stat.player_id in player_ids:
                    HistoryService._add_tichu(summary, stat)
        return summary

    @staticmethod
    def _day_records(
        matches: List[Match],
        stats_by_match: Dict[int, List[MatchStats]],
        players_by_id: Dict[int, Player],
    ) -> List[HistoryDayRecord]:
        records: Dict[int, HistoryDayRecord] = {}

        def record_for(player_id: int) -> HistoryDayRecord:
            if player_id not in records:
                player = players_by_id.get(player_id)
                records[player_id] = HistoryDayRecord(
                    player_id=player_id,
                    name=player.name if player else "알 수 없음",
                )
            return records[player_id]

        for match in matches:
            for team, ids in (("A", match.team_a_ids), ("B", match.team_b_ids)):
                for player_id in ids:
                    HistoryService._add_result(record_for(player_id), match.winner_team == team)
            for stat in stats_by_match.get(match.id, []):
                HistoryService._add_tichu(record_for(stat.player_id), stat)

        # 승수 많은 순 > 패 적은 순(승률) > 티츄 성공 많은 순 > 이름
        return sorted(
            records.values(),
            key=lambda r: (-r.wins, r.losses, -(r.tichu_success + r.grand_success), r.name),
        )

    @staticmethod
    def _to_player(player_id: int, players_by_id: Dict[int, Player]) -> HistoryPlayer:
        player = players_by_id.get(player_id)
        return HistoryPlayer(
            id=player_id,
            name=player.name if player else "알 수 없음",
            profile_url=(player.profile_url or "") if player else "",
        )

    @staticmethod
    def get_history_page(
        db: Session,
        season: Optional[int] = None,
        player_ids: Optional[List[int]] = None,
        before_date: Optional[date] = None,
        days: int = DEFAULT_PAGE_DAYS,
    ) -> HistoryPageResponse:
        """경기가 있었던 날짜 `days`일치를 하루 단위로 통째로 돌려준다."""
        player_ids = player_ids or []
        season_matches = HistoryService._filter_matches(
            HistoryService._season_matches(db, season), player_ids
        )
        remaining = [m for m in season_matches if before_date is None or m.play_date < before_date]

        page_dates: List[date] = []
        for match in remaining:
            if match.play_date not in page_dates:
                if len(page_dates) == days:
                    break
                page_dates.append(match.play_date)
        page_matches = [m for m in remaining if m.play_date in page_dates]
        has_more = len(page_matches) < len(remaining)

        # 시즌 요약은 첫 페이지에서만 계산 (시즌 전체 티츄 기록 필요)
        include_summary = bool(player_ids) and before_date is None
        stat_match_ids = [m.id for m in (season_matches if include_summary else page_matches)]
        stats_by_match = HistoryService._stats_by_match(db, stat_match_ids)

        involved_ids = {pid for m in page_matches for pid in m.team_a_ids + m.team_b_ids}
        players_by_id = {
            p.id: p
            for p in (db.query(Player).filter(Player.id.in_(involved_ids)).all() if involved_ids else [])
        }

        day_list = []
        for day in page_dates:
            day_matches = [m for m in page_matches if m.play_date == day]
            day_list.append(HistoryDay(
                date=day,
                game_count=len(day_matches),
                records=HistoryService._day_records(day_matches, stats_by_match, players_by_id),
                matches=[
                    HistoryMatch(
                        id=m.id,
                        play_date=m.play_date,
                        team_a=[HistoryService._to_player(pid, players_by_id) for pid in m.team_a_ids],
                        team_b=[HistoryService._to_player(pid, players_by_id) for pid in m.team_b_ids],
                        score_a=m.score_a,
                        score_b=m.score_b,
                        winner_team=m.winner_team,
                    )
                    for m in day_matches
                ],
            ))

        return HistoryPageResponse(
            days=day_list,
            next_before_date=page_dates[-1] if has_more else None,
            summary=(
                HistoryService._summary(season_matches, player_ids, stats_by_match)
                if include_summary else None
            ),
        )

    @staticmethod
    def get_player_options(db: Session, season: Optional[int] = None) -> List[HistoryPlayerOption]:
        """시즌에 경기한 사람을 경기 수 많은 순으로, 같은 팀을 했던 사람과 함께."""
        games: Dict[int, int] = {}
        partners: Dict[int, set] = {}
        for match in HistoryService._season_matches(db, season):
            for ids in (match.team_a_ids, match.team_b_ids):
                for player_id in ids:
                    games[player_id] = games.get(player_id, 0) + 1
                    partners.setdefault(player_id, set()).update(
                        pid for pid in ids if pid != player_id
                    )
        if not games:
            return []

        players = db.query(Player).filter(Player.id.in_(games.keys())).all()
        options = [
            HistoryPlayerOption(
                id=player.id,
                name=player.name,
                profile_url=player.profile_url or "",
                games=games[player.id],
                partner_ids=sorted(partners.get(player.id, set())),
            )
            for player in players
        ]
        return sorted(options, key=lambda o: (-o.games, o.name))
