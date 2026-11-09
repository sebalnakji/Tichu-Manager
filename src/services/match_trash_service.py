"""
Match Trash Service
잘못된 판 휴지통: 삭제하면 7일 동안 보관(상태 DELETED), 그동안 되돌릴 수 있고 지나면 영구 삭제

- 랭킹·게임 기록·개인 분석 등은 끝난 판(FINISHED)만 읽으므로 DELETED는 자동으로 빠진다.
- 영구 삭제는 휴지통을 열거나 판을 삭제할 때 먼저 처리한다
  (운영은 매일 정리 스케줄러가 꺼져 있어 스케줄러에만 맡기지 않음).
"""
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy.orm import Session

from core import season as season_rules
from core.logging_config import get_logger
from models.models import DeletedMatch, Match, MatchStats, Player

logger = get_logger(__name__)

TRASH_DAYS = 7
DELETED_STATUS = "DELETED"
KST_OFFSET = timedelta(hours=9)


def utcnow() -> datetime:
    """현재 시각 (UTC, 테스트에서 교체)"""
    return datetime.utcnow()


def match_cards(db: Session, matches: List[Match]) -> List[Dict]:
    """기록 수정 목록·휴지통 카드용 {id, 날짜, 팀 이름, 점수, 승자}"""
    player_ids = {pid for match in matches for pid in match.team_a_ids + match.team_b_ids}
    players = db.query(Player).filter(Player.id.in_(player_ids)).all() if player_ids else []
    names = {player.id: player.name for player in players}
    return [
        {
            "id": match.id,
            "play_date": match.play_date.isoformat(),
            "team_a_names": [names.get(pid, "알 수 없음") for pid in match.team_a_ids],
            "team_b_names": [names.get(pid, "알 수 없음") for pid in match.team_b_ids],
            "score_a": match.score_a,
            "score_b": match.score_b,
            "winner_team": match.winner_team,
        }
        for match in matches
    ]


def move_to_trash(db: Session, match: Match) -> None:
    """끝난 판을 휴지통으로 (commit은 호출한 쪽)"""
    match.status = DELETED_STATUS
    db.merge(DeletedMatch(match_id=match.id, deleted_at=utcnow()))


def restore(db: Session, match: Match) -> None:
    """휴지통 판을 다시 끝난 판으로 (commit은 호출한 쪽)"""
    match.status = "FINISHED"
    db.query(DeletedMatch).filter(DeletedMatch.match_id == match.id).delete(synchronize_session=False)


def purge_expired(db: Session) -> int:
    """삭제한 지 7일이 지난 판을 티츄 기록·휴지통 기록과 함께 영구 삭제 (commit 포함)"""
    cutoff = utcnow() - timedelta(days=TRASH_DAYS)
    match_ids = [
        row.match_id
        for row in db.query(DeletedMatch.match_id).filter(DeletedMatch.deleted_at <= cutoff).all()
    ]
    if not match_ids:
        return 0
    db.query(MatchStats).filter(MatchStats.match_id.in_(match_ids)).delete(synchronize_session=False)
    db.query(Match).filter(
        Match.id.in_(match_ids), Match.status == DELETED_STATUS
    ).delete(synchronize_session=False)
    db.query(DeletedMatch).filter(DeletedMatch.match_id.in_(match_ids)).delete(synchronize_session=False)
    db.commit()
    logger.info(f"휴지통 영구 삭제 {len(match_ids)}건: {match_ids}")
    return len(match_ids)


def expires_in_days(deleted_at: datetime) -> int:
    """영구 삭제까지 남은 날 (한국 날짜 기준, 오늘 삭제 = 7, 마지막 날 = 0)"""
    expire_day = (deleted_at + KST_OFFSET + timedelta(days=TRASH_DAYS)).date()
    return max(0, (expire_day - season_rules.today_kst()).days)


def list_trash(db: Session) -> List[Dict]:
    """휴지통 목록 (7일 지난 판은 먼저 영구 삭제, 최근 삭제 순)"""
    purge_expired(db)
    rows = (
        db.query(Match, DeletedMatch.deleted_at)
        .join(DeletedMatch, DeletedMatch.match_id == Match.id)
        .filter(Match.status == DELETED_STATUS)
        .order_by(DeletedMatch.deleted_at.desc(), Match.id.desc())
        .all()
    )
    cards = match_cards(db, [match for match, _ in rows])
    for card, (_, deleted_at) in zip(cards, rows):
        card["expires_in_days"] = expires_in_days(deleted_at)
    return cards


def delete_trash_rows_without_match(db: Session) -> None:
    """데이터 초기화로 판이 사라진 휴지통 기록 정리 (commit은 호출한 쪽)"""
    db.query(DeletedMatch).filter(
        ~DeletedMatch.match_id.in_(db.query(Match.id))
    ).delete(synchronize_session=False)
