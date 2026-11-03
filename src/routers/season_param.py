"""
시즌 쿼리 파라미터 (?season=1)
생략하면 전체 기간, 시즌1 ~ 현재 시즌이 아니면 422
"""
from typing import Optional

from fastapi import HTTPException, Query, status

from core import season as season_rules


def season_query(
    season: Optional[int] = Query(None, description="시즌 번호 (생략 시 전체 기간)"),
) -> Optional[int]:
    if season is not None and not season_rules.is_valid_season(season):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"없는 시즌입니다: {season}",
        )
    return season
