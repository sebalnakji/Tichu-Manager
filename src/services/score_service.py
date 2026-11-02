"""
Score Service
점수 계산 및 라운드 관리 로직
"""
from typing import List, Dict
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models.models import Match, MatchStats
from core.logging_config import get_logger

logger = get_logger(__name__)


class ScoreService:
    """점수 계산 서비스"""

    @staticmethod
    def validate_round(
        round_number: int,
        team_a_base: int,
        team_b_base: int,
        events: List[dict],
        team_a_ids: List[int],
        team_b_ids: List[int],
        direct: bool = False,
    ) -> None:
        """DB에 저장하기 전에 라운드 입력 규칙을 검증한다."""
        if round_number < 1:
            raise ValueError("라운드 번호는 1 이상이어야 합니다.")

        if direct:
            if any(event["type"] != "custom" for event in events):
                raise ValueError("직접 점수 입력에는 커스텀 메모만 추가할 수 있습니다.")
            return

        one_two_events = [event for event in events if event["type"] == "one_two"]
        if len(one_two_events) > 1:
            raise ValueError("원투는 라운드당 한 팀만 기록할 수 있습니다.")

        if one_two_events:
            if team_a_base != 0 or team_b_base != 0:
                raise ValueError("원투 라운드의 기본 점수는 양 팀 모두 0점이어야 합니다.")
        elif team_a_base + team_b_base != 100:
            raise ValueError("기본 점수 합계는 100점이어야 합니다.")

        participant_ids = set(team_a_ids + team_b_ids)
        player_events = []
        success_events = []

        for event in events:
            if event["type"] in ("tichu", "grand"):
                player_id = event.get("player_id")
                if player_id not in participant_ids:
                    raise ValueError("티츄/라지티츄 플레이어는 해당 경기 참가자여야 합니다.")
                if event.get("success") is None:
                    raise ValueError("티츄/라지티츄의 성공 여부가 필요합니다.")
                player_events.append(event)
                if event["success"]:
                    success_events.append(event)
            elif event["type"] == "one_two" and event.get("team") not in ("A", "B"):
                raise ValueError("원투 팀은 A 또는 B여야 합니다.")

        player_ids = [event["player_id"] for event in player_events]
        if len(player_ids) != len(set(player_ids)):
            raise ValueError("한 플레이어의 티츄/라지티츄를 중복 기록할 수 없습니다.")

        if len(success_events) > 1:
            raise ValueError("티츄/라지티츄 성공은 라운드당 한 명만 가능합니다.")

        if one_two_events and success_events:
            one_two_team = one_two_events[0]["team"]
            success_player_id = success_events[0]["player_id"]
            success_team = "A" if success_player_id in team_a_ids else "B"
            if success_team != one_two_team:
                raise ValueError("원투 상대 팀은 티츄/라지티츄에 성공할 수 없습니다.")

    @staticmethod
    def calculate_round_score(
        team_a_base: int,
        team_b_base: int,
        events: List[dict],
        team_a_ids: List[int],
        team_b_ids: List[int]
    ) -> Dict:
        """
        라운드 최종 점수 계산

        Args:
            team_a_base: 팀 A 기본 점수
            team_b_base: 팀 B 기본 점수
            events: 이벤트 리스트 (티츄/라티/원투)
            team_a_ids: 팀 A 플레이어 ID 리스트
            team_b_ids: 팀 B 플레이어 ID 리스트

        Returns:
            {
                "team_a_total": int,
                "team_b_total": int,
                "team_a_bonus": int,
                "team_b_bonus": int
            }
        """
        # 원투 확인
        one_two_event = next((e for e in events if e["type"] == "one_two"), None)

        if one_two_event:
            # 원투 발생 시 기본 점수 무시
            if one_two_event.get("team") == "A":
                team_a_total = 200
                team_b_total = 0
            else:
                team_a_total = 0
                team_b_total = 200

            # 티츄/라티 보너스는 계산
            team_a_bonus = 0
            team_b_bonus = 0

            for event in events:
                if event["type"] == "tichu":
                    bonus = 100 if event.get("success") else -100
                    if event["player_id"] in team_a_ids:
                        team_a_bonus += bonus
                    else:
                        team_b_bonus += bonus

                elif event["type"] == "grand":
                    bonus = 200 if event.get("success") else -200
                    if event["player_id"] in team_a_ids:
                        team_a_bonus += bonus
                    else:
                        team_b_bonus += bonus

            team_a_total += team_a_bonus
            team_b_total += team_b_bonus

            return {
                "team_a_total": team_a_total,
                "team_b_total": team_b_total,
                "team_a_bonus": team_a_total - (200 if one_two_event.get("team") == "A" else 0),
                "team_b_bonus": team_b_total - (200 if one_two_event.get("team") == "B" else 0)
            }

        else:
            # 원투 없을 때는 기존 로직
            team_a_bonus = 0
            team_b_bonus = 0

            for event in events:
                if event["type"] == "tichu":
                    bonus = 100 if event.get("success") else -100
                    if event["player_id"] in team_a_ids:
                        team_a_bonus += bonus
                    else:
                        team_b_bonus += bonus

                elif event["type"] == "grand":
                    bonus = 200 if event.get("success") else -200
                    if event["player_id"] in team_a_ids:
                        team_a_bonus += bonus
                    else:
                        team_b_bonus += bonus

            return {
                "team_a_total": team_a_base + team_a_bonus,
                "team_b_total": team_b_base + team_b_bonus,
                "team_a_bonus": team_a_bonus,
                "team_b_bonus": team_b_bonus
            }


    @staticmethod
    def save_round_stats(
        match_id: int,
        round_number: int,
        events: List[dict],
        db: Session
    ):
        """
        라운드 이벤트를 MatchStats에 저장

        Args:
            match_id: 매치 ID
            round_number: 라운드 번호
            events: 이벤트 리스트
            db: 데이터베이스 세션
        """
        # 기존 해당 라운드 통계 삭제 (수정 시)
        db.query(MatchStats).filter(
            MatchStats.match_id == match_id,
            MatchStats.round_number == round_number
        ).delete()

        # 새 통계 저장
        for event in events:
            if event["type"] in ["tichu", "grand"]:
                stats = MatchStats(
                    match_id=match_id,
                    player_id=event["player_id"],
                    round_number=round_number,
                    is_tichu_try=(event["type"] == "tichu"),
                    is_tichu_succ=(event["type"] == "tichu" and event.get("success", False)),
                    is_grand_try=(event["type"] == "grand"),
                    is_grand_succ=(event["type"] == "grand" and event.get("success", False))
                )
                db.add(stats)

        logger.info(f"라운드 {round_number} 통계 반영 준비 (매치 ID: {match_id})")


    @staticmethod
    def calculate_totals(
        rounds: List[dict],
        team_a_ids: List[int],
        team_b_ids: List[int],
    ) -> tuple[int, int]:
        """저장 형식의 라운드 목록으로 양 팀 총점을 계산한다."""
        total_a = 0
        total_b = 0
        for round_data in rounds:
            result = ScoreService.calculate_round_score(
                round_data.get("team_a_base", 0),
                round_data.get("team_b_base", 0),
                round_data.get("events", []),
                team_a_ids,
                team_b_ids,
            )
            total_a += result["team_a_total"]
            total_b += result["team_b_total"]
        return total_a, total_b

    @staticmethod
    def is_finished(total_a: int, total_b: int) -> bool:
        """한 팀 이상이 1000점에 도달했더라도 동점이면 다음 라운드를 진행한다."""
        return (total_a >= 1000 or total_b >= 1000) and total_a != total_b

    @staticmethod
    def update_match_total_score(match: Match):
        """
        매치의 총점 계산 및 업데이트
        모든 라운드 점수를 합산

        Args:
            match: 업데이트할 매치
        """
        total_a, total_b = ScoreService.calculate_totals(
            match.rounds or [], match.team_a_ids, match.team_b_ids
        )

        match.score_a = total_a
        match.score_b = total_b

        if ScoreService.is_finished(total_a, total_b):
            match.status = "FINISHED"
            match.winner_team = "A" if total_a > total_b else "B"
        else:
            # 점수 수정으로 종료 조건을 벗어나거나 동점이 된 경우 게임 재개
            if match.status == "FINISHED":
                match.status = "PLAYING"
                match.winner_team = None

        logger.info(f"총점 반영 준비: 매치 ID {match.id}, A={total_a}, B={total_b}")
