"""
Dashboard DTO
개인 분석 화면 응답
"""
from typing import List, Optional

from pydantic import BaseModel


class DashboardPlayer(BaseModel):
    id: int
    name: str
    profile_url: str


class DashboardMedal(BaseModel):
    """끝난 시즌 개인 랭킹 최종 1~3위"""
    season: int
    season_name: str
    rank: int


class PersonRecord(BaseModel):
    """사람별 내 전적 (상대로 만났을 때 / 같은 팀이었을 때)"""
    player_id: int
    name: str
    games: int
    wins: int
    losses: int
    win_rate: int  # 정수 %


class PeopleRecords(BaseModel):
    # 5판 이상 중 승률 50% 초과 최고(😋 맛집 / 💞 베프), 50% 미만 최저(😈 천적 / 💔 악연)
    best: Optional[PersonRecord] = None
    worst: Optional[PersonRecord] = None
    people: List[PersonRecord]  # 많이 만난 순 → 승률 높은 순


class PlayerDashboardResponse(BaseModel):
    player: DashboardPlayer
    # 프로필 카드 (시즌 선택과 무관하게 고정)
    current_season: int
    current_season_name: str
    current_rank: Optional[int]  # 현재 시즌 경기가 없으면 None
    medals: List[DashboardMedal]  # 오래된 시즌 → 최근 시즌
    # 아래 통계 (선택한 시즌, None = 전체 기간)
    season: Optional[int]
    games: int
    opponents: PeopleRecords  # ⚔️ 상대 전적
    partners: PeopleRecords   # 🤝 파트너 전적 (같은 팀 승률)
