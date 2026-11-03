"""
Match DTO
매치 관련 요청/응답 스키마
"""
from pydantic import BaseModel
from typing import List, Optional, Literal
from datetime import date


class RoundEvent(BaseModel):
    """라운드 내 개별 이벤트"""
    type: Literal["tichu", "grand", "one_two", "custom"]
    player_id: Optional[int] = None  # 티츄/라티인 경우 필수
    team: Optional[str] = None  # 원투인 경우 "A" or "B"
    success: Optional[bool] = None  # 티츄/라티 성공 여부
    text: Optional[str] = None  # 커스텀 배지 텍스트


class RoundScoreRequest(BaseModel):
    """라운드 점수 입력/수정 요청"""
    round_number: int
    team_a_base_score: int
    team_b_base_score: int
    events: List[RoundEvent] = []
    direct: bool = False  # 직접 입력 여부


class RoundsReplaceRequest(BaseModel):
    """관리자 기록 수정: 라운드 전체 일괄 저장 요청"""
    rounds: List[RoundScoreRequest]


class RoundScoreResponse(BaseModel):
    """라운드 점수 응답"""
    round_number: int
    team_a_base_score: int
    team_b_base_score: int
    team_a_total: int  # 이벤트 포함 최종 점수
    team_b_total: int
    team_a_bonus: int  # 보너스 점수
    team_b_bonus: int
    events: List[RoundEvent]
    direct: bool = False  # 직접 입력 여부


class PlayerInfo(BaseModel):
    """플레이어 정보"""
    id: int
    name: str
    profile_url: str
    win_rate: float
    recent_win_rate: float


class MatchDetailResponse(BaseModel):
    """매치 상세 정보 (점수판용)"""
    id: int
    play_date: str
    status: str

    # 팀 A 정보
    team_a_ids: List[int]
    team_a_players: List[PlayerInfo]
    team_a_total_score: int
    team_a_team_win_rate: float  # 팀 승률
    team_a_team_games: int  # 함께한 게임 수 (시즌)
    team_a_team_wins: int = 0  # 함께 이긴 게임 수 (시즌)

    # 팀 B 정보
    team_b_ids: List[int]
    team_b_players: List[PlayerInfo]
    team_b_total_score: int
    team_b_team_win_rate: float
    team_b_team_games: int
    team_b_team_wins: int = 0

    # 라운드 기록
    rounds: List[RoundScoreResponse]

    # 승자
    winner_team: Optional[str]


class MatchStateResponse(BaseModel):
    """라운드 변경 후 프런트에서 갱신할 경기 상태"""
    id: int
    status: str
    team_a_total_score: int
    team_b_total_score: int
    rounds: List[RoundScoreResponse]
    winner_team: Optional[str]


class TeamAssignment(BaseModel):
    """팀 배정 결과"""
    team_a: List[int]
    team_b: List[int]


class MatchCreate(BaseModel):
    """매치 생성 요청"""
    team_a_ids: List[int]
    team_b_ids: List[int]


class MatchResponse(BaseModel):
    """매치 응답"""
    id: int
    play_date: date
    team_a_ids: List[int]
    team_b_ids: List[int]
    score_a: int
    score_b: int
    winner_team: Optional[str]
    status: str

    class Config:
        from_attributes = True
