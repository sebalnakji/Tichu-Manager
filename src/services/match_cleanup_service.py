"""
Match Cleanup Service
미완료 게임 자동 정리 (3일 이전 데이터 삭제)
"""
from datetime import timedelta

from core import season as season_rules
from core.database import SessionLocal
from core.logging_config import get_logger
from models.models import Match, MatchStats
from services import match_trash_service as match_trash

logger = get_logger(__name__)


def cleanup_unfinished_matches():
    """
    3일 이전의 미완료(PLAYING) 매치를 삭제한다.
    MatchStats는 CASCADE로 자동 삭제된다.
    휴지통에서 7일 지난 판도 함께 영구 삭제한다 (휴지통 열기·판 삭제 때도 처리).
    매일 새벽 3시에 스케줄러에 의해 호출된다.
    """
    db = SessionLocal()
    try:
        match_trash.purge_expired(db)

        cutoff_date = season_rules.today_kst() - timedelta(days=3)

        stale_matches = db.query(Match).filter(
            Match.status == "PLAYING",
            Match.play_date < cutoff_date
        ).all()

        if not stale_matches:
            logger.info("정리할 미완료 게임이 없습니다.")
            return

        match_ids = [m.id for m in stale_matches]

        # MatchStats 먼저 삭제 (DB에 따라 CASCADE 미작동 대비)
        db.query(MatchStats).filter(MatchStats.match_id.in_(match_ids)).delete(synchronize_session=False)
        db.query(Match).filter(Match.id.in_(match_ids)).delete(synchronize_session=False)
        db.commit()

        logger.info(f"미완료 게임 {len(match_ids)}건 삭제 완료 (기준일: {cutoff_date} 이전)")

    except Exception as e:
        db.rollback()
        logger.error(f"미완료 게임 정리 실패: {e}")
    finally:
        db.close()
