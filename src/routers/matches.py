"""
Matches Router
게임 매치 관련 API 엔드포인트
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime, date

from core.database import get_db
from core.logging_config import get_logger
from models.models import Match, Player, MatchStats
from services.team_service import TeamService
from services.stats_service import StatsService
from services.score_service import ScoreService
from dto.match import (
    TeamAssignment,
    MatchCreate,
    MatchResponse,
    MatchDetailResponse,
    MatchStateResponse,
    RoundScoreRequest,
    RoundsReplaceRequest,
    PlayerInfo
)
from routers.admin import verify_admin

router = APIRouter(prefix="/api/matches", tags=["Matches"])
logger = get_logger(__name__)


def _build_rounds(match: Match) -> list[dict]:
    rounds = []
    for round_data in match.rounds or []:
        result = ScoreService.calculate_round_score(
            round_data.get("team_a_base", 0),
            round_data.get("team_b_base", 0),
            round_data.get("events", []),
            match.team_a_ids,
            match.team_b_ids,
        )
        rounds.append({
            "round_number": round_data.get("round"),
            "team_a_base_score": round_data.get("team_a_base", 0),
            "team_b_base_score": round_data.get("team_b_base", 0),
            "team_a_total": result["team_a_total"],
            "team_b_total": result["team_b_total"],
            "team_a_bonus": result["team_a_bonus"],
            "team_b_bonus": result["team_b_bonus"],
            "events": round_data.get("events", []),
            "direct": round_data.get("direct", False),
        })
    return rounds


def _build_match_state(match: Match) -> MatchStateResponse:
    return MatchStateResponse(
        id=match.id,
        status=match.status,
        team_a_total_score=match.score_a,
        team_b_total_score=match.score_b,
        rounds=_build_rounds(match),
        winner_team=match.winner_team,
    )


@router.post("/assign-teams")
def assign_teams(player_ids: List[int], db: Session = Depends(get_db)):
    """
    4명의 플레이어를 2:2 팀으로 랜덤 배정
    각 팀의 승률 정보도 함께 반환
    """
    if len(player_ids) != 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="정확히 4명의 플레이어를 선택해야 합니다."
        )

    # 팀 배정
    assignment = TeamService.assign_teams(player_ids, db)

    # 팀 A 승률 계산
    team_a_stats = StatsService.get_team_stats(
        assignment.team_a[0],
        assignment.team_a[1],
        db,
        year=None
    )

    # 팀 B 승률 계산
    team_b_stats = StatsService.get_team_stats(
        assignment.team_b[0],
        assignment.team_b[1],
        db,
        year=None
    )

    # 응답에 승률 정보 추가
    result = {
        "team_a": assignment.team_a,
        "team_b": assignment.team_b,
        "team_a_stats": {
            "total_games": team_a_stats.total_games if team_a_stats else 0,
            "wins": team_a_stats.wins if team_a_stats else 0,
            "win_rate": team_a_stats.win_rate if team_a_stats else 0.0
        },
        "team_b_stats": {
            "total_games": team_b_stats.total_games if team_b_stats else 0,
            "wins": team_b_stats.wins if team_b_stats else 0,
            "win_rate": team_b_stats.win_rate if team_b_stats else 0.0
        }
    }

    logger.info(f"팀 배정: Team A {assignment.team_a} vs Team B {assignment.team_b}")
    return result


@router.post("/", response_model=MatchResponse)
def create_match(match: MatchCreate, db: Session = Depends(get_db)):
    """
    새 게임 매치 생성
    """
    new_match = Match(
        play_date=date.today(),
        team_a_ids=match.team_a_ids,
        team_b_ids=match.team_b_ids,
        status="PLAYING",
        rounds=[]
    )

    db.add(new_match)
    db.commit()
    db.refresh(new_match)

    logger.info(f"매치 생성: ID {new_match.id}")
    return new_match


@router.get("/finished")
def get_finished_matches(
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db)
):
    """
    최근 완료된 매치 목록 조회 (관리자 기록 수정용)
    """
    matches = db.query(Match).filter(
        Match.status == "FINISHED"
    ).order_by(Match.id.desc()).limit(limit).all()

    player_ids = {
        player_id
        for match in matches
        for player_id in match.team_a_ids + match.team_b_ids
    }
    players = (
        db.query(Player).filter(Player.id.in_(player_ids)).all()
        if player_ids
        else []
    )
    player_names = {player.id: player.name for player in players}

    result = []
    for match in matches:
        result.append({
            "id": match.id,
            "play_date": match.play_date.isoformat(),
            "team_a_names": [
                player_names.get(player_id, "알 수 없음")
                for player_id in match.team_a_ids
            ],
            "team_b_names": [
                player_names.get(player_id, "알 수 없음")
                for player_id in match.team_b_ids
            ],
            "score_a": match.score_a,
            "score_b": match.score_b,
            "winner_team": match.winner_team
        })

    return result


@router.get("/ongoing/{player_id}")
def get_ongoing_matches(player_id: int, db: Session = Depends(get_db)):
    """
    특정 플레이어가 참여한 진행 중인 게임 조회
    - 가장 최근 매치 1개만 조회
    - 그 매치가 PLAYING이고 해당 플레이어가 참여했으면 반환
    - 그렇지 않으면 빈 배열 반환
    """
    # 가장 최근 매치 1개 조회
    latest_match = db.query(Match).order_by(Match.id.desc()).first()

    # 매치가 없거나 가장 최근 매치가 완료된 경우
    if not latest_match or latest_match.status == "FINISHED":
        return []

    # 현재 플레이어가 이 게임에 참여했는지 확인
    all_player_ids = latest_match.team_a_ids + latest_match.team_b_ids
    if player_id not in all_player_ids:
        return []  # 참여 안 한 게임이면 빈 배열

    players = db.query(Player).filter(Player.id.in_(all_player_ids)).all()
    players_by_id = {player.id: player for player in players}
    team_a_players = [
        {"id": players_by_id[pid].id, "name": players_by_id[pid].name}
        for pid in latest_match.team_a_ids
        if pid in players_by_id
    ]
    team_b_players = [
        {"id": players_by_id[pid].id, "name": players_by_id[pid].name}
        for pid in latest_match.team_b_ids
        if pid in players_by_id
    ]

    return [{
        "id": latest_match.id,
        "play_date": latest_match.play_date.isoformat(),
        "team_a_players": team_a_players,
        "team_b_players": team_b_players,
        "score_a": latest_match.score_a,
        "score_b": latest_match.score_b
    }]


@router.get("/today-record")
def get_today_record(
    team_a_ids: str,
    team_b_ids: str,
    db: Session = Depends(get_db)
):
    """
    오늘 같은 팀 조합의 전적 조회

    Args:
        team_a_ids: 팀 A 플레이어 ID (쉼표 구분, 예: "1,2")
        team_b_ids: 팀 B 플레이어 ID (쉼표 구분, 예: "3,4")
    """
    # 문자열을 리스트로 변환
    team_a = sorted([int(x) for x in team_a_ids.split(',')])
    team_b = sorted([int(x) for x in team_b_ids.split(',')])

    # 오늘 날짜의 매치만 조회
    today_matches = db.query(Match).filter(
        Match.play_date == date.today(),
        Match.status == "FINISHED"
    ).all()

    team_a_wins = 0
    team_b_wins = 0

    for match in today_matches:
        # 팀 구성이 같은지 확인 (순서 무관)
        match_team_a = sorted(match.team_a_ids)
        match_team_b = sorted(match.team_b_ids)

        if (match_team_a == team_a and match_team_b == team_b):
            if match.winner_team == "A":
                team_a_wins += 1
            else:
                team_b_wins += 1
        elif (match_team_a == team_b and match_team_b == team_a):
            # 팀 순서가 바뀐 경우
            if match.winner_team == "A":
                team_b_wins += 1
            else:
                team_a_wins += 1

    return {
        "team_a_wins": team_a_wins,
        "team_b_wins": team_b_wins,
        "total_games": team_a_wins + team_b_wins
    }


@router.get("/{match_id}", response_model=MatchDetailResponse)
def get_match_detail(match_id: int, db: Session = Depends(get_db)):
    """
    매치 상세 정보 조회 (점수판용)
    - 팀원 정보 (프로필, 개인 승률)
    - 팀 승률
    - 라운드별 점수
    """
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"매치 ID {match_id}를 찾을 수 없습니다."
        )

    (
        players_by_id,
        stats_by_player_id,
        team_a_stats,
        team_b_stats,
    ) = StatsService.get_match_context_stats(
        match.team_a_ids,
        match.team_b_ids,
        db,
        datetime.now().year,
    )

    def build_players(player_ids: list[int]) -> list[PlayerInfo]:
        result = []
        for player_id in player_ids:
            player = players_by_id.get(player_id)
            if not player:
                continue
            stats = stats_by_player_id.get(player_id)
            result.append(PlayerInfo(
                id=player.id,
                name=player.name,
                profile_url=player.profile_url or "",
                win_rate=stats.win_rate if stats else 0.0,
                recent_win_rate=stats.recent_win_rate if stats else 0.0,
            ))
        return result

    team_a_players = build_players(match.team_a_ids)
    team_b_players = build_players(match.team_b_ids)

    return MatchDetailResponse(
        id=match.id,
        play_date=match.play_date.isoformat(),
        status=match.status,
        team_a_ids=match.team_a_ids,
        team_a_players=team_a_players,
        team_a_total_score=match.score_a,
        team_a_team_win_rate=team_a_stats.win_rate if team_a_stats else 0.0,
        team_a_team_games=team_a_stats.total_games if team_a_stats else 0,
        team_b_ids=match.team_b_ids,
        team_b_players=team_b_players,
        team_b_total_score=match.score_b,
        team_b_team_win_rate=team_b_stats.win_rate if team_b_stats else 0.0,
        team_b_team_games=team_b_stats.total_games if team_b_stats else 0,
        rounds=_build_rounds(match),
        winner_team=match.winner_team
    )


@router.post("/{match_id}/rounds", response_model=MatchStateResponse)
def add_round_score(
    match_id: int,
    round_data: RoundScoreRequest,
    db: Session = Depends(get_db)
):
    """
    새 라운드 점수 추가
    - 기본 점수 + 이벤트(티츄/라티/원투) 처리
    - MatchStats에 개인 기록 저장
    - 총점 업데이트
    - 1000점 도달 시 게임 종료
    """
    match = (
        db.query(Match)
        .filter(Match.id == match_id)
        .with_for_update()
        .first()
    )
    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"매치 ID {match_id}를 찾을 수 없습니다."
        )

    logger.info(f"라운드 저장 요청: match_id={match_id}, round={round_data.round_number}")

    event_data = [event.model_dump() for event in round_data.events]
    try:
        ScoreService.validate_round(
            round_data.round_number,
            round_data.team_a_base_score,
            round_data.team_b_base_score,
            event_data,
            match.team_a_ids,
            match.team_b_ids,
            round_data.direct,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # 라운드 데이터 구성
    new_round = {
        "round": round_data.round_number,
        "team_a_base": round_data.team_a_base_score,
        "team_b_base": round_data.team_b_base_score,
        "events": event_data,
        "direct": round_data.direct
    }

    # 기존 라운드 목록 가져오기
    rounds_list = list(match.rounds or [])

    logger.info(f"기존 라운드 개수: {len(rounds_list)}")

    # 같은 라운드 번호가 있으면 교체, 없으면 추가
    existing_index = None
    for i, r in enumerate(rounds_list):
        if r.get("round") == round_data.round_number:
            existing_index = i
            break

    if existing_index is not None:
        logger.info(f"기존 라운드 {round_data.round_number} 업데이트")
        rounds_list[existing_index] = new_round
    else:
        logger.info(f"새 라운드 {round_data.round_number} 추가")
        rounds_list.append(new_round)

    match.rounds = rounds_list

    # ⭐ 중요: JSON 필드 변경을 SQLAlchemy에 알림
    flag_modified(match, "rounds")

    ScoreService.save_round_stats(
        match_id,
        round_data.round_number,
        event_data,
        db
    )

    ScoreService.update_match_total_score(match)
    db.flush()
    response = _build_match_state(match)
    db.commit()

    logger.info(f"저장 후 라운드 개수: {len(response.rounds)}")
    return response


@router.put("/{match_id}/rounds/{round_number}", response_model=MatchStateResponse)
def update_round_score(
    match_id: int,
    round_number: int,
    round_data: RoundScoreRequest,
    db: Session = Depends(get_db)
):
    """
    기존 라운드 점수 수정
    """
    # round_number를 round_data에 설정
    round_data.round_number = round_number

    # add_round_score를 재사용 (같은 로직)
    return add_round_score(match_id, round_data, db)


@router.put("/{match_id}/rounds", response_model=MatchStateResponse)
def replace_rounds(
    match_id: int,
    request: RoundsReplaceRequest,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin),
):
    """
    관리자 기록 수정: 라운드 전체를 한 번에 저장 (관리자 전용)
    - 요청 순서대로 라운드 번호를 1부터 다시 매긴다.
    - 결과가 종료 조건(1000점 이상 + 동점 아님)을 만족해야 저장한다.
    - 라운드 기록, 티츄 통계, 총점, 승자를 한 트랜잭션으로 교체한다.
    """
    match = (
        db.query(Match)
        .filter(Match.id == match_id)
        .with_for_update()
        .first()
    )
    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"매치 ID {match_id}를 찾을 수 없습니다."
        )

    new_rounds = []
    for round_number, round_data in enumerate(request.rounds, start=1):
        event_data = [event.model_dump() for event in round_data.events]
        try:
            ScoreService.validate_round(
                round_number,
                round_data.team_a_base_score,
                round_data.team_b_base_score,
                event_data,
                match.team_a_ids,
                match.team_b_ids,
                round_data.direct,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"ROUND {round_data.round_number}: {exc}",
            ) from exc

        new_rounds.append({
            "round": round_number,
            "team_a_base": round_data.team_a_base_score,
            "team_b_base": round_data.team_b_base_score,
            "events": event_data,
            "direct": round_data.direct,
        })

    # 수정 결과가 완료 경기가 아니면 저장하지 않는다
    # (진행 중으로 바뀌면 기록 목록·랭킹에서 빠지고 미완료 정리 작업에 삭제될 수 있음)
    total_a, total_b = ScoreService.calculate_totals(
        new_rounds, match.team_a_ids, match.team_b_ids
    )
    if not ScoreService.is_finished(total_a, total_b):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "1000점 이상이면서 동점이 아닌 팀이 있어야 저장할 수 있습니다. "
                f"라운드를 추가해주세요. (현재 {total_a} : {total_b})"
            ),
        )

    match.rounds = new_rounds
    flag_modified(match, "rounds")

    # 티츄 통계는 라운드 번호가 바뀔 수 있으므로 매치 단위로 다시 만든다.
    db.query(MatchStats).filter(MatchStats.match_id == match_id).delete()
    for round_data in new_rounds:
        ScoreService.save_round_stats(
            match_id,
            round_data["round"],
            round_data["events"],
            db,
        )

    ScoreService.update_match_total_score(match)
    db.flush()
    response = _build_match_state(match)
    db.commit()

    logger.info(
        f"기록 수정 저장: 매치 ID {match_id}, 라운드 {len(new_rounds)}개, "
        f"A={match.score_a}, B={match.score_b}, 상태={match.status}"
    )
    return response


@router.delete(
    "/{match_id}/rounds/{round_number}",
    response_model=MatchStateResponse,
)
def delete_round_score(match_id: int, round_number: int, db: Session = Depends(get_db)):
    """
    라운드 삭제
    """
    match = (
        db.query(Match)
        .filter(Match.id == match_id)
        .with_for_update()
        .first()
    )
    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"매치 ID {match_id}를 찾을 수 없습니다."
        )

    # 라운드 제거
    rounds_list = match.rounds if match.rounds else []
    rounds_list = [r for r in rounds_list if r.get("round") != round_number]
    match.rounds = rounds_list

    # JSON 필드 변경 알림
    flag_modified(match, "rounds")

    # MatchStats 삭제
    db.query(MatchStats).filter(
        MatchStats.match_id == match_id,
        MatchStats.round_number == round_number
    ).delete()

    ScoreService.update_match_total_score(match)
    db.flush()
    response = _build_match_state(match)
    db.commit()

    logger.info(f"라운드 {round_number} 삭제: 매치 ID {match_id}")
    return response


@router.post("/{match_id}/reset")
def reset_match(match_id: int, db: Session = Depends(get_db)):
    """
    매치 초기화
    - 모든 라운드 삭제
    - 점수 0으로 초기화
    - 팀 구성 유지
    """
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"매치 ID {match_id}를 찾을 수 없습니다."
        )

    # 모든 라운드 삭제
    match.rounds = []
    match.score_a = 0
    match.score_b = 0
    match.status = "PLAYING"
    match.winner_team = None

    # JSON 필드 변경 알림
    flag_modified(match, "rounds")

    # 해당 매치의 모든 MatchStats 삭제
    db.query(MatchStats).filter(MatchStats.match_id == match_id).delete()

    db.commit()

    logger.info(f"매치 초기화: ID {match_id}")

    return {
        "message": "게임이 초기화되었습니다.",
        "match_id": match_id
    }
