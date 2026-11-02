"""
History DTO
게임 기록(일별/개인/팀) 조회 응답 스키마
"""
from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class HistoryPlayer(BaseModel):
    """경기 카드에 표시할 플레이어"""
    id: int
    name: str
    profile_url: str


class HistoryRecord(BaseModel):
    """전적 + 스몰/라지 티츄 기록 (하루 기록표, 시즌 요약 공용)"""
    wins: int = 0
    losses: int = 0
    tichu_try: int = 0
    tichu_success: int = 0
    grand_try: int = 0
    grand_success: int = 0


class HistoryDayRecord(HistoryRecord):
    """하루 기록표의 한 사람"""
    player_id: int
    name: str


class HistoryMatch(BaseModel):
    """목록의 경기 카드"""
    id: int
    play_date: date
    team_a: List[HistoryPlayer]
    team_b: List[HistoryPlayer]
    score_a: int
    score_b: int
    winner_team: Optional[str]


class HistoryDay(BaseModel):
    """날짜 묶음 하나 (그날 전체 경기 + 기록표)"""
    date: date
    game_count: int
    records: List[HistoryDayRecord]
    matches: List[HistoryMatch]


class HistoryPageResponse(BaseModel):
    """게임 기록 한 페이지 (날짜 N일치)"""
    days: List[HistoryDay]
    next_before_date: Optional[date]  # 다음 페이지 커서, 없으면 마지막
    summary: Optional[HistoryRecord] = None  # 개인/팀 필터 + 첫 페이지일 때만


class HistoryPlayerOption(BaseModel):
    """사람 선택 버튼 줄 항목"""
    id: int
    name: str
    profile_url: str
    games: int
    partner_ids: List[int]  # 같은 팀을 한 적 있는 사람 (팀 탭 선택 가능 여부)
