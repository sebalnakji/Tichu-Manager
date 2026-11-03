"""
Stats Router
통계 및 랭킹 관련 API 엔드포인트
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core import season as season_rules
from core.database import get_db
from dto.stats import PlayerStats, LeaderboardResponse, TeamLeaderboardResponse
from dto.dashboard import PlayerDashboardResponse
from services.dashboard_service import DashboardService
from services.stats_service import StatsService
from routers.season_param import season_query

router = APIRouter(prefix="/api/stats", tags=["Stats"])


@router.get("/player/{player_id}", response_model=PlayerStats)
def get_player_stats(
    player_id: int,
    season: Optional[int] = Depends(season_query),
    db: Session = Depends(get_db)
):
    """
    개인 전적 통계 조회
    """
    stats = StatsService.get_player_stats(player_id, db, season)
    if not stats:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"플레이어 ID {player_id}를 찾을 수 없습니다."
        )
    return stats


@router.get("/player/{player_id}/dashboard", response_model=PlayerDashboardResponse)
def get_player_dashboard(
    player_id: int,
    season: Optional[int] = Depends(season_query),
    db: Session = Depends(get_db)
):
    """
    개인 분석: 프로필(현재 시즌 순위, 획득 메달)은 고정, 상대 전적은 선택한 시즌(생략 = 전체 기간)
    """
    dashboard = DashboardService.get_dashboard(db, player_id, season)
    if not dashboard:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"플레이어 ID {player_id}를 찾을 수 없습니다."
        )
    return dashboard


@router.get("/players/current-season", response_model=List[PlayerStats])
def get_all_players_stats_current_season(db: Session = Depends(get_db)):
    """
    전체 플레이어 통계 조회 (현재 시즌, 한국 날짜 기준)
    팀 선택 화면에서 승률 표시용
    """
    return StatsService.get_all_player_stats(db, season_rules.current_season())


@router.get("/leaderboard", response_model=List[LeaderboardResponse])
def get_leaderboard(
    season: Optional[int] = Depends(season_query),
    db: Session = Depends(get_db)
):
    """
    개인 순위 랭킹 조회
    정렬 우선순위: 10경기 이상 > 승률 > 경기 수 > 승수 >
    티츄 성공률 > 라지티츄 성공률
    """
    stats_list = StatsService.get_leaderboard(db, season)

    # 순위 부여
    leaderboard = [
        LeaderboardResponse(rank=idx + 1, stats=stats)
        for idx, stats in enumerate(stats_list)
    ]

    return leaderboard


@router.get("/leaderboard/teams", response_model=List[TeamLeaderboardResponse])
def get_team_leaderboard(
    season: Optional[int] = Depends(season_query),
    db: Session = Depends(get_db)
):
    """
    팀 순위 랭킹 조회
    정렬 우선순위: 10경기 이상 > 승률 > 경기 수 > 승수 >
    티츄 성공률 > 라지티츄 성공률
    """
    stats_list = StatsService.get_team_leaderboard(db, season)

    # 순위 부여
    leaderboard = [
        TeamLeaderboardResponse(rank=idx + 1, stats=stats)
        for idx, stats in enumerate(stats_list)
    ]

    return leaderboard
