"""
Admin Router
관리자 전용 API 엔드포인트
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel

from core import season as season_rules
from core.database import get_db
from core.properties import settings
from core.logging_config import get_logger
from models.models import Player, Match, MatchStats
from dto.player import PlayerResponse
from services import match_trash_service as match_trash

router = APIRouter(prefix="/api/admin", tags=["Admin"])


class ResetRequest(BaseModel):
    table: str  # 'players' | 'matches' | 'stats' | 'all'
    season: Optional[int] = None  # null이면 전체 기간
logger = get_logger(__name__)


def verify_admin(authorization: str = Header(None)):
    """
    관리자 권한 확인 미들웨어
    Authorization 헤더에 관리자 코드가 있는지 확인
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다."
        )

    # "Bearer admin123" 형식 또는 "admin123" 직접 전달
    code = authorization.replace("Bearer ", "").strip()

    if code != settings.ADMIN_CODE:
        logger.warning(f"관리자 권한 확인 실패")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 없습니다."
        )

    return True


@router.get("/users", response_model=List[PlayerResponse])
def get_all_users(
        db: Session = Depends(get_db),
        _: bool = Depends(verify_admin)
):
    """
    전체 유저 목록 조회 (관리자 전용)
    코드 포함하여 반환
    """
    players = db.query(Player).order_by(Player.created_at.desc()).all()
    logger.info(f"관리자: 전체 유저 목록 조회 ({len(players)}명)")
    return players


@router.delete("/users/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_by_admin(
        player_id: int,
        db: Session = Depends(get_db),
        _: bool = Depends(verify_admin)
):
    """
    유저 삭제 (관리자 전용)
    """
    player = db.query(Player).filter(Player.id == player_id).first()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"플레이어 ID {player_id}를 찾을 수 없습니다."
        )

    player_name = player.name
    db.delete(player)
    db.commit()

    logger.info(f"관리자: 유저 삭제 - {player_name} (ID: {player_id})")
    return None


@router.delete("/reset-all", status_code=status.HTTP_204_NO_CONTENT)
def reset_all_data(
        db: Session = Depends(get_db),
        _: bool = Depends(verify_admin)
):
    """
    전체 데이터 초기화 (관리자 전용)
    경고: 모든 플레이어, 매치, 통계 데이터 삭제
    """
    try:
        # 통계 삭제
        stats_count = db.query(MatchStats).delete()

        # 매치 삭제 (휴지통 기록 포함)
        matches_count = db.query(Match).delete()
        match_trash.delete_trash_rows_without_match(db)

        # 플레이어 삭제
        players_count = db.query(Player).delete()

        db.commit()

        logger.warning(
            f"관리자: 전체 데이터 초기화 완료 - "
            f"플레이어 {players_count}명, 매치 {matches_count}개, 통계 {stats_count}개 삭제"
        )

        return None
    except Exception as e:
        db.rollback()
        logger.error(f"데이터 초기화 실패: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="데이터 초기화 중 오류가 발생했습니다."
        )


@router.get("/matches/recent")
def get_recent_matches(
        limit: int = 10,
        db: Session = Depends(get_db),
        _: bool = Depends(verify_admin)
):
    """
    최근 매치 기록 조회 (관리자 전용)
    """
    matches = (
        db.query(Match)
        .filter(Match.status != match_trash.DELETED_STATUS)
        .order_by(Match.play_date.desc())
        .limit(limit)
        .all()
    )

    result = []
    for match in matches:
        result.append({
            "id": match.id,
            "play_date": match.play_date.isoformat(),
            "team_a_ids": match.team_a_ids,
            "team_b_ids": match.team_b_ids,
            "score_a": match.score_a,
            "score_b": match.score_b,
            "winner_team": match.winner_team,
            "status": match.status
        })

    logger.info(f"관리자: 최근 매치 {len(result)}개 조회")
    return result


@router.get("/matches/trash")
def get_trash_matches(
        db: Session = Depends(get_db),
        _: bool = Depends(verify_admin)
):
    """
    휴지통 목록 (관리자 전용): 최근 삭제 순, 카드마다 `expires_in_days`(영구 삭제까지 남은 날)
    7일 지난 판은 먼저 영구 삭제
    """
    return match_trash.list_trash(db)


@router.delete("/matches/{match_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_match_by_admin(
        match_id: int,
        db: Session = Depends(get_db),
        _: bool = Depends(verify_admin)
):
    """
    판 삭제 = 휴지통으로 (관리자 전용, 잘못된 기록 수정 목록의 [삭제])
    7일 동안 보관하며 되돌릴 수 있음. 끝난 판만 가능(진행 중 409), 없거나 이미 휴지통이면 404
    """
    match_trash.purge_expired(db)
    match = db.query(Match).filter(
        Match.id == match_id, Match.status != match_trash.DELETED_STATUS
    ).first()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"매치 ID {match_id}를 찾을 수 없습니다."
        )
    if match.status != "FINISHED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="끝난 경기만 삭제할 수 있습니다."
        )

    match_trash.move_to_trash(db, match)
    db.commit()

    logger.info(f"관리자: 매치 휴지통으로 - ID {match_id}")
    return None


@router.post("/matches/{match_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_match_by_admin(
        match_id: int,
        db: Session = Depends(get_db),
        _: bool = Depends(verify_admin)
):
    """
    휴지통 판 되돌리기 (관리자 전용): 다시 끝난 판으로. 휴지통에 없으면(7일 지나 영구 삭제 포함) 404
    """
    match_trash.purge_expired(db)
    match = db.query(Match).filter(
        Match.id == match_id, Match.status == match_trash.DELETED_STATUS
    ).first()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"휴지통에서 매치 ID {match_id}를 찾을 수 없습니다."
        )

    match_trash.restore(db, match)
    db.commit()

    logger.info(f"관리자: 매치 되돌리기 - ID {match_id}")
    return None


@router.delete("/reset")
def reset_data(
        req: ResetRequest,
        db: Session = Depends(get_db),
        _: bool = Depends(verify_admin)
):
    """
    테이블별 / 시즌별 데이터 초기화 (관리자 전용)
    - table: 'players' | 'matches' | 'stats' | 'all'
    - season: null이면 전체 기간, 숫자면 해당 시즌만 (한국 날짜 기준)
    """
    if req.season is not None and not season_rules.is_valid_season(req.season):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"없는 시즌입니다: {req.season}",
        )

    try:
        deleted = {}

        def delete_stats(y):
            q = db.query(MatchStats)
            if y:
                start_date, end_date = season_rules.season_bounds(y)
                match_ids = [
                    m.id
                    for m in db.query(Match.id).filter(
                        Match.play_date >= start_date,
                        Match.play_date < end_date,
                    ).all()
                ]
                if not match_ids:
                    return 0
                q = q.filter(MatchStats.match_id.in_(match_ids))
            return q.delete(synchronize_session=False)

        def delete_matches(y):
            q = db.query(Match)
            if y:
                start_date, end_date = season_rules.season_bounds(y)
                q = q.filter(
                    Match.play_date >= start_date,
                    Match.play_date < end_date,
                )
            return q.delete(synchronize_session=False)

        def delete_players(y):
            q = db.query(Player)
            if y:
                # 가입일은 UTC로 저장 → 시즌 경계(한국 0시)를 UTC로 바꿔 비교
                start_day, end_day = season_rules.season_bounds(y)
                start_date = season_rules.kst_midnight_as_utc(start_day)
                end_date = season_rules.kst_midnight_as_utc(end_day)
                q = q.filter(
                    Player.created_at >= start_date,
                    Player.created_at < end_date,
                )
            return q.delete(synchronize_session=False)

        table = req.table
        season = req.season

        if table in ('stats', 'all'):
            deleted['stats'] = delete_stats(season)
        if table in ('matches', 'all'):
            if table == 'matches':
                deleted['stats'] = delete_stats(season)
            deleted['matches'] = delete_matches(season)
        if table in ('players', 'all'):
            deleted['players'] = delete_players(season)
        if 'matches' in deleted:
            match_trash.delete_trash_rows_without_match(db)

        db.commit()

        season_label = season_rules.season_name(season) if season else "전체"
        table_labels = {'players': '플레이어', 'matches': '매치', 'stats': '통계', 'all': '전체'}
        parts = [f"{table_labels.get(k, k)} {v}건" for k, v in deleted.items() if v > 0]
        summary = ', '.join(parts) if parts else '삭제된 데이터 없음'

        logger.warning(f"관리자: 데이터 초기화 - {table_labels[table]} {season_label} ({summary})")

        return {"message": f"{summary} 삭제 완료"}
    except Exception as e:
        db.rollback()
        logger.error(f"데이터 초기화 실패: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="데이터 초기화 중 오류가 발생했습니다."
        )
