"""
Stats DTO
통계 관련 데이터 전송 객체
"""
from pydantic import BaseModel
from typing import List, Optional


class PlayerStats(BaseModel):
    """개인 통계"""
    player_id: int
    player_name: str
    profile_url: str
    total_games: int
    wins: int
    losses: int
    win_rate: float
    recent_games: int
    recent_wins: int
    recent_win_rate: float
    tichu_try: int
    tichu_success: int
    tichu_success_rate: float
    grand_try: int
    grand_success: int
    grand_success_rate: float
    streak: int = 0  # 최근부터 이어진 승패: +3 = 3연승, -2 = 2연패
    recent_results: List[str] = []  # 최근 5경기 "W"/"L", 오래된 → 최근 순


class TeamStats(BaseModel):
    """팀 통계"""
    player1_id: int
    player2_id: int
    player1_name: str
    player2_name: str
    player1_profile_url: str
    player2_profile_url: str
    team_name: str
    total_games: int
    wins: int
    losses: int
    win_rate: float
    tichu_try: int  # 추가
    tichu_success: int  # 추가
    tichu_success_rate: float
    grand_try: int  # 추가
    grand_success: int  # 추가
    grand_success_rate: float
    recent_results: List[str] = []  # 최근 5경기 "W"/"L", 오래된 → 최근 순


class LeaderboardResponse(BaseModel):
    """개인 랭킹 응답"""
    rank: int
    stats: PlayerStats


class TeamLeaderboardResponse(BaseModel):
    """팀 랭킹 응답"""
    rank: int
    stats: TeamStats