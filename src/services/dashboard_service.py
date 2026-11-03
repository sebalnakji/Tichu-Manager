"""
Dashboard Service
개인 분석: 프로필(현재 시즌 순위, 획득 메달) + 시즌별 통계(상대 전적, 파트너 전적, 시즌 기록)

사람·완료 경기·티츄 기록을 한 번씩만 읽고 시즌별로 나눠 함께 계산한다 (조회 3번).
경기는 팀 구성·승자·날짜 컬럼만 읽고, 라운드가 필요한 시즌 기록은
이 사람의 선택 시즌 경기만 따로 읽는다 (조회 1번 추가, 총 4번).
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
    MatchMargin,
    PlayerDashboardResponse,
    SeasonRecord,
    SeasonRecords,
)
from dto.stats import PlayerStats
from models.models import Match, MatchStats, Player
from services.score_service import ScoreService
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
    def _cumulative_scores(rounds: list, team_a_ids: list, team_b_ids: list) -> List[tuple]:
        """라운드가 끝날 때마다의 누적 점수 [(A, B), ...] — 점수판과 같은 계산"""
        totals, total_a, total_b = [], 0, 0
        for round_data in sorted(rounds or [], key=lambda r: r.get("round", 0)):
            result = ScoreService.calculate_round_score(
                round_data.get("team_a_base", 0),
                round_data.get("team_b_base", 0),
                round_data.get("events", []),
                team_a_ids,
                team_b_ids,
            )
            total_a += result["team_a_total"]
            total_b += result["team_b_total"]
            totals.append((total_a, total_b))
        return totals

    @staticmethod
    def _grand_one_two(round_data: dict, side: str, side_ids: list) -> bool:
        """그 라운드에 한 팀이 라지 티츄 성공과 원투를 함께 냈는지 (직접 입력 라운드는 판단 안 함)"""
        if round_data.get("direct"):
            return False
        events = round_data.get("events", [])
        grand = any(
            e.get("type") == "grand" and e.get("success") and e.get("player_id") in side_ids
            for e in events
        )
        one_two = any(e.get("type") == "one_two" and e.get("team") == side for e in events)
        return grand and one_two

    @staticmethod
    def _comeback(match, side: str) -> tuple[bool, bool]:
        """(역전승 기회, 역전패 기회) — 결과와 합쳐 성공/실패를 센다

        역전승 기회: 어느 라운드 끝에 상대 800점 이상 & 우리가 200점 이상 뒤짐,
                    또는 마지막 라운드 직전 100점 이상 뒤지다가 그 라운드에 우리 팀 라지 성공 + 원투로 승리
        역전패 기회: 같은 기준을 반대로
        """
        their_side = "B" if side == "A" else "A"
        mine_ids = match.team_a_ids if side == "A" else match.team_b_ids
        their_ids = match.team_b_ids if side == "A" else match.team_a_ids
        totals = [
            (a, b) if side == "A" else (b, a)
            for a, b in DashboardService._cumulative_scores(match.rounds, match.team_a_ids, match.team_b_ids)
        ]
        won = match.winner_team == side

        # 경기가 아직 진행 중이던 라운드 끝만 본다 (마지막 라운드 = 최종 점수는 제외)
        in_progress = totals[:-1]
        behind_cliff = any(theirs >= 800 and theirs - mine >= 200 for mine, theirs in in_progress)
        ahead_cliff = any(mine >= 800 and mine - theirs >= 200 for mine, theirs in in_progress)

        rounds = sorted(match.rounds or [], key=lambda r: r.get("round", 0))
        last_shot_win = last_shot_loss = False
        if rounds:
            before_mine, before_theirs = totals[-2] if len(totals) > 1 else (0, 0)
            last = rounds[-1]
            last_shot_win = (
                won
                and before_theirs - before_mine >= 100
                and DashboardService._grand_one_two(last, side, mine_ids)
            )
            last_shot_loss = (
                not won
                and before_mine - before_theirs >= 100
                and DashboardService._grand_one_two(last, their_side, their_ids)
            )
        return behind_cliff or last_shot_win, ahead_cliff or last_shot_loss

    @staticmethod
    def _season_records(player_id: int, games: list, stats: list) -> Optional[SeasonRecords]:
        """🏅 시즌 기록. games: 이 사람이 뛴 경기(점수·라운드 포함)"""
        if not games:
            return None
        ordered = sorted(games, key=lambda m: (m.play_date, m.id))
        longest_win = longest_lose = run = 0
        margins = []
        biggest_win = biggest_loss = None
        comeback_wins = comeback_win_chances = comeback_losses = comeback_loss_chances = 0

        for match in ordered:
            side = "A" if player_id in match.team_a_ids else "B"
            won = match.winner_team == side
            # 연승/연패 (run: +N 연승 / -N 연패)
            if won:
                run = run + 1 if run > 0 else 1
            else:
                run = run - 1 if run < 0 else -1
            longest_win = max(longest_win, run)
            longest_lose = max(longest_lose, -run)
            # 점수 차 (우리 − 상대). 같은 차이면 최근 경기
            margin = (match.score_a - match.score_b) * (1 if side == "A" else -1)
            margins.append(margin)
            if won and (biggest_win is None or margin >= biggest_win.margin):
                biggest_win = MatchMargin(match_id=match.id, margin=margin)
            if not won and (biggest_loss is None or margin <= biggest_loss.margin):
                biggest_loss = MatchMargin(match_id=match.id, margin=margin)
            # 역전승 / 역전패 (한 게임은 최대 1번)
            win_chance, loss_chance = DashboardService._comeback(match, side)
            comeback_win_chances += int(win_chance)
            comeback_wins += int(win_chance and won)
            comeback_loss_chances += int(loss_chance)
            comeback_losses += int(loss_chance and not won)

        game_ids = {m.id for m in games}
        mine = [s for s in stats if s.player_id == player_id and s.match_id in game_ids]
        count = len(games)
        return SeasonRecords(
            longest_win_streak=longest_win,
            longest_lose_streak=longest_lose,
            biggest_win=biggest_win,
            biggest_loss=biggest_loss,
            comeback_wins=comeback_wins,
            comeback_win_chances=comeback_win_chances,
            comeback_losses=comeback_losses,
            comeback_loss_chances=comeback_loss_chances,
            average_margin=round(sum(margins) / count),
            tichu_calls_per_game=round(sum(1 for s in mine if s.is_tichu_try) / count, 1),
            grand_calls_per_game=round(sum(1 for s in mine if s.is_grand_try) / count, 1),
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

        # 🏅 시즌 기록: 이 사람이 뛴 그 시즌 경기만 점수·라운드까지 읽는다 (조회 1번 추가)
        my_ids = [
            m.id for m in selected
            if player_id in m.team_a_ids or player_id in m.team_b_ids
        ]
        my_games = (
            db.query(
                Match.id, Match.play_date, Match.team_a_ids, Match.team_b_ids,
                Match.winner_team, Match.score_a, Match.score_b, Match.rounds,
            )
            .filter(Match.id.in_(my_ids))
            .all()
            if my_ids else []
        )
        selected_stats = stats if season is None else stats_by_season[season]
        records = DashboardService._season_records(player_id, my_games, selected_stats)

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
            records=records,
        )
