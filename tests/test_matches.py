from datetime import date

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event

from core.database import get_db
from dto.match import RoundEvent, RoundScoreRequest
from models.models import Match, MatchStats
from routers.matches import (
    add_round_score,
    delete_round_score,
    get_match_detail,
    get_ongoing_matches,
    router as matches_router,
    update_round_score,
)
from services.score_service import ScoreService


def create_match(db, player_ids, *, status="PLAYING", play_date=None):
    match = Match(
        play_date=play_date or date.today(),
        team_a_ids=player_ids[:2],
        team_b_ids=player_ids[2:],
        status=status,
        rounds=[],
        winner_team="A" if status == "FINISHED" else None,
    )
    db.add(match)
    db.commit()
    return match.id


def round_request(
    *,
    number=1,
    team_a=100,
    team_b=0,
    events=None,
    direct=False,
):
    return RoundScoreRequest(
        round_number=number,
        team_a_base_score=team_a,
        team_b_base_score=team_b,
        events=events or [],
        direct=direct,
    )


def test_match_detail_uses_at_most_four_queries(db, player_ids):
    finished_id = create_match(db, player_ids, status="FINISHED")
    match_id = create_match(db, player_ids)
    db.add(
        MatchStats(
            match_id=finished_id,
            player_id=player_ids[0],
            round_number=1,
            is_tichu_try=True,
            is_tichu_succ=True,
        )
    )
    db.commit()

    query_count = 0

    def count_query(*_args):
        nonlocal query_count
        query_count += 1

    event.listen(db.bind, "before_cursor_execute", count_query)
    try:
        detail = get_match_detail(match_id, db)
    finally:
        event.remove(db.bind, "before_cursor_execute", count_query)

    assert detail.id == match_id
    assert len(detail.team_a_players) == 2
    assert len(detail.team_b_players) == 2
    assert detail.team_a_team_games == 1
    assert query_count <= 4


def test_ongoing_match_loads_players_in_two_queries(db, player_ids):
    match_id = create_match(db, player_ids)
    query_count = 0

    def count_query(*_args):
        nonlocal query_count
        query_count += 1

    event.listen(db.bind, "before_cursor_execute", count_query)
    try:
        result = get_ongoing_matches(player_ids[0], db)
    finally:
        event.remove(db.bind, "before_cursor_execute", count_query)

    assert result[0]["id"] == match_id
    assert query_count <= 2


def test_round_save_update_and_delete_are_consistent(db, player_ids, monkeypatch):
    match_id = create_match(db, player_ids)
    original_commit = db.commit
    commit_count = 0

    def counted_commit():
        nonlocal commit_count
        commit_count += 1
        return original_commit()

    monkeypatch.setattr(db, "commit", counted_commit)

    saved = add_round_score(
        match_id,
        round_request(
            events=[
                RoundEvent(
                    type="tichu",
                    player_id=player_ids[0],
                    success=True,
                )
            ]
        ),
        db,
    )

    assert commit_count == 1
    assert saved.team_a_total_score == 200
    assert len(saved.rounds) == 1
    assert db.query(MatchStats).filter(MatchStats.match_id == match_id).count() == 1

    updated = update_round_score(
        match_id,
        1,
        round_request(
            events=[
                RoundEvent(
                    type="tichu",
                    player_id=player_ids[0],
                    success=False,
                )
            ]
        ),
        db,
    )

    assert commit_count == 2
    assert updated.team_a_total_score == 0
    stats = db.query(MatchStats).filter(MatchStats.match_id == match_id).all()
    assert len(stats) == 1
    assert stats[0].is_tichu_succ is False

    deleted = delete_round_score(match_id, 1, db)

    assert commit_count == 3
    assert deleted.team_a_total_score == 0
    assert deleted.rounds == []
    assert db.query(MatchStats).filter(MatchStats.match_id == match_id).count() == 0


def test_round_can_finish_match(db, player_ids):
    match_id = create_match(db, player_ids)

    result = add_round_score(
        match_id,
        round_request(team_a=1000, team_b=0, direct=True),
        db,
    )

    assert result.status == "FINISHED"
    assert result.winner_team == "A"
    assert result.team_a_total_score == 1000

    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert persisted.status == "FINISHED"
    assert persisted.winner_team == "A"


def test_match_continues_when_scores_are_tied_at_1000(db, player_ids):
    match_id = create_match(db, player_ids)

    result = add_round_score(
        match_id,
        round_request(team_a=1000, team_b=1000, direct=True),
        db,
    )

    assert result.status == "PLAYING"
    assert result.winner_team is None
    assert result.team_a_total_score == 1000
    assert result.team_b_total_score == 1000

    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert persisted.status == "PLAYING"
    assert persisted.winner_team is None


def test_valid_one_two_with_same_team_tichu_success_is_saved(db, player_ids):
    match_id = create_match(db, player_ids)

    result = add_round_score(
        match_id,
        round_request(
            team_a=0,
            team_b=0,
            events=[
                RoundEvent(type="one_two", team="A"),
                RoundEvent(
                    type="tichu",
                    player_id=player_ids[0],
                    success=True,
                ),
            ],
        ),
        db,
    )

    assert result.team_a_total_score == 300
    assert result.team_b_total_score == 0


@pytest.mark.parametrize(
    ("request_data", "message"),
    [
        (
            round_request(team_a=70, team_b=20),
            "기본 점수 합계는 100점이어야 합니다.",
        ),
        (
            round_request(
                events=[RoundEvent(type="tichu", player_id=999, success=True)]
            ),
            "해당 경기 참가자여야 합니다.",
        ),
        (
            round_request(
                events=[
                    RoundEvent(type="tichu", player_id=1, success=False),
                    RoundEvent(type="grand", player_id=1, success=False),
                ]
            ),
            "중복 기록할 수 없습니다.",
        ),
        (
            round_request(
                events=[
                    RoundEvent(type="tichu", player_id=1, success=True),
                    RoundEvent(type="grand", player_id=3, success=True),
                ]
            ),
            "라운드당 한 명만 가능합니다.",
        ),
        (
            round_request(
                team_a=0,
                team_b=0,
                events=[
                    RoundEvent(type="one_two", team="A"),
                    RoundEvent(type="tichu", player_id=3, success=True),
                ],
            ),
            "원투 상대 팀은",
        ),
        (
            round_request(
                team_a=0,
                team_b=0,
                events=[
                    RoundEvent(type="one_two", team="A"),
                    RoundEvent(type="one_two", team="B"),
                ],
            ),
            "라운드당 한 팀만",
        ),
        (
            round_request(
                events=[RoundEvent(type="one_two", team="A")],
            ),
            "양 팀 모두 0점",
        ),
        (
            round_request(
                team_a=0,
                team_b=0,
                events=[RoundEvent(type="one_two", team="invalid")],
            ),
            "A 또는 B",
        ),
        (
            round_request(
                events=[RoundEvent(type="tichu", player_id=1)],
            ),
            "성공 여부가 필요합니다.",
        ),
        (
            round_request(number=0),
            "라운드 번호는 1 이상",
        ),
        (
            round_request(
                events=[RoundEvent(type="tichu", player_id=1, success=True)],
                direct=True,
            ),
            "커스텀 메모만",
        ),
    ],
)
def test_invalid_round_is_rejected_without_saving(
    db,
    player_ids,
    request_data,
    message,
):
    match_id = create_match(db, player_ids)

    # Parametrized IDs are normalized to the fixture's actual player IDs.
    for event_data in request_data.events:
        if event_data.player_id == 1:
            event_data.player_id = player_ids[0]
        elif event_data.player_id == 3:
            event_data.player_id = player_ids[2]

    with pytest.raises(HTTPException) as exc_info:
        add_round_score(match_id, request_data, db)

    assert exc_info.value.status_code == 422
    assert message in exc_info.value.detail
    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert persisted.rounds == []
    assert persisted.score_a == 0
    assert persisted.score_b == 0
    assert db.query(MatchStats).filter(MatchStats.match_id == match_id).count() == 0


def test_round_failure_does_not_partially_commit(
    db,
    player_ids,
    monkeypatch,
):
    match_id = create_match(db, player_ids)

    def fail_to_save_stats(*_args, **_kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(
        ScoreService,
        "save_round_stats",
        fail_to_save_stats,
    )

    with pytest.raises(RuntimeError, match="forced failure"):
        add_round_score(match_id, round_request(), db)

    db.rollback()
    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert persisted.rounds == []
    assert persisted.score_a == 0
    assert persisted.score_b == 0


def test_round_api_returns_mergeable_match_state(db, player_ids):
    match_id = create_match(db, player_ids)
    app = FastAPI()
    app.include_router(matches_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        response = client.post(
            f"/api/matches/{match_id}/rounds",
            json={
                "round_number": 1,
                "team_a_base_score": 70,
                "team_b_base_score": 30,
                "events": [],
                "direct": False,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": match_id,
        "status": "PLAYING",
        "team_a_total_score": 70,
        "team_b_total_score": 30,
        "rounds": [
            {
                "round_number": 1,
                "team_a_base_score": 70,
                "team_b_base_score": 30,
                "team_a_total": 70,
                "team_b_total": 30,
                "team_a_bonus": 0,
                "team_b_bonus": 0,
                "events": [],
                "direct": False,
            }
        ],
        "winner_team": None,
    }


def _record_edit_client(db):
    app = FastAPI()
    app.include_router(matches_router)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _finished_match_with_rounds(db, player_ids, count=10):
    """매 라운드 A 100 : B 0, count=10이면 1000:0으로 종료."""
    match_id = create_match(db, player_ids)
    for number in range(1, count + 1):
        add_round_score(match_id, round_request(number=number), db)
    return match_id


def _round_json(number, team_a=100, team_b=0, events=None):
    return {
        "round_number": number,
        "team_a_base_score": team_a,
        "team_b_base_score": team_b,
        "events": events or [],
        "direct": False,
    }


def test_replace_rounds_saves_all_changes_at_once(db, player_ids):
    from core.properties import settings

    match_id = _finished_match_with_rounds(db, player_ids, count=10)
    # 기존 라운드 3개(8~10)를 지우고, 티츄 성공 라운드를 포함해 새 라운드를 추가
    rounds = [_round_json(n) for n in range(1, 8)]
    rounds += [
        _round_json(11, 70, 30, [{"type": "tichu", "player_id": player_ids[0], "success": True}]),
        _round_json(12, 60, 40),
        _round_json(13, 100, 0),
    ]

    with _record_edit_client(db) as client:
        response = client.put(
            f"/api/matches/{match_id}/rounds",
            json={"rounds": rounds},
            headers={"Authorization": settings.ADMIN_CODE},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["team_a_total_score"] == 700 + 170 + 60 + 100
    assert body["team_b_total_score"] == 70
    assert body["status"] == "FINISHED"
    assert body["winner_team"] == "A"
    # 라운드 번호는 1부터 다시 매겨진다.
    assert [r["round_number"] for r in body["rounds"]] == list(range(1, 11))

    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert persisted.score_a == 1030
    stats = db.query(MatchStats).filter(MatchStats.match_id == match_id).all()
    assert len(stats) == 1
    assert stats[0].round_number == 8
    assert stats[0].is_tichu_succ is True


def test_replace_rounds_rejects_invalid_round_without_saving(db, player_ids):
    from core.properties import settings

    match_id = _finished_match_with_rounds(db, player_ids, count=10)
    rounds = [_round_json(n) for n in range(1, 10)] + [_round_json(10, 80, 30)]

    with _record_edit_client(db) as client:
        response = client.put(
            f"/api/matches/{match_id}/rounds",
            json={"rounds": rounds},
            headers={"Authorization": settings.ADMIN_CODE},
        )

    assert response.status_code == 422
    assert "ROUND 10" in response.json()["detail"]
    db.expire_all()
    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert len(persisted.rounds) == 10
    assert persisted.score_a == 1000
    assert persisted.status == "FINISHED"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "wrong-code"}])
def test_replace_rounds_requires_admin(db, player_ids, headers):
    match_id = _finished_match_with_rounds(db, player_ids, count=10)

    with _record_edit_client(db) as client:
        response = client.put(
            f"/api/matches/{match_id}/rounds",
            json={"rounds": []},
            headers=headers,
        )

    assert response.status_code in (401, 403)
    db.expire_all()
    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert len(persisted.rounds) == 10


@pytest.mark.parametrize(
    "rounds_builder, expected_score",
    [
        # 마지막 라운드 삭제 → 900 : 0 (1000점 미만)
        (lambda: [_round_json(n) for n in range(1, 10)], "900 : 0"),
        # 1000 : 1000 동점
        (
            lambda: [_round_json(n, 50, 50) for n in range(1, 21)],
            "1000 : 1000",
        ),
    ],
)
def test_replace_rounds_rejects_unfinished_result_without_saving(
    db,
    player_ids,
    rounds_builder,
    expected_score,
):
    from core.properties import settings

    match_id = _finished_match_with_rounds(db, player_ids, count=10)

    with _record_edit_client(db) as client:
        response = client.put(
            f"/api/matches/{match_id}/rounds",
            json={"rounds": rounds_builder()},
            headers={"Authorization": settings.ADMIN_CODE},
        )

    assert response.status_code == 422
    assert "1000점 이상" in response.json()["detail"]
    assert expected_score in response.json()["detail"]
    db.expire_all()
    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert len(persisted.rounds) == 10
    assert persisted.score_a == 1000
    assert persisted.status == "FINISHED"
    assert persisted.winner_team == "A"


def test_finished_match_rejects_new_round_without_saving(db, player_ids):
    match_id = _finished_match_with_rounds(db, player_ids, count=10)

    with pytest.raises(HTTPException) as exc_info:
        add_round_score(match_id, round_request(number=11, team_a=50, team_b=50), db)

    assert exc_info.value.status_code == 409
    assert "이미 종료된 경기" in exc_info.value.detail
    db.rollback()
    persisted = db.query(Match).filter(Match.id == match_id).one()
    assert len(persisted.rounds) == 10
    assert (persisted.score_a, persisted.score_b) == (1000, 0)
    assert persisted.status == "FINISHED"


def test_finished_match_still_allows_editing_existing_round(db, player_ids):
    match_id = _finished_match_with_rounds(db, player_ids, count=10)
    tichu = RoundEvent(type="tichu", player_id=player_ids[0], success=True)

    result = update_round_score(match_id, 10, round_request(events=[tichu]), db)

    assert result.status == "FINISHED"
    assert result.team_a_total_score == 1100
    assert len(result.rounds) == 10
