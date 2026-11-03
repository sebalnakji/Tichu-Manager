"""
Season
시즌 규칙과 한국 날짜 기준

- 시즌1 (테스트): 2026.01.01 ~ 2026.12.31
- 시즌2부터 반년씩: 2027 상반기 = 시즌2, 2027 하반기 = 시즌3, 2028 상반기 = 시즌4 ...
- 2026년 이전 경기는 시즌 없음 (전체 기간에서만 보임)

"오늘"은 서버 위치와 상관없이 한국 날짜(UTC+9, 서머타임 없음)로 센다.
다른 모듈은 `from core import season as season_rules` 후 `season_rules.today_kst()`처럼
모듈 경유로 호출한다 (테스트에서 날짜를 바꿔 끼울 수 있도록).
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

KST = timezone(timedelta(hours=9))
FIRST_SEASON_YEAR = 2026
HALF_YEAR_START_YEAR = 2027


def now_kst() -> datetime:
    """지금 한국 시각"""
    return datetime.now(KST)


def today_kst() -> date:
    """오늘 한국 날짜"""
    return now_kst().date()


def season_bounds(number: int) -> tuple[date, date]:
    """시즌 기간 [시작일, 종료 다음날)"""
    if number < 1:
        raise ValueError(f"시즌 번호는 1 이상이어야 합니다: {number}")
    if number == 1:
        return date(FIRST_SEASON_YEAR, 1, 1), date(FIRST_SEASON_YEAR + 1, 1, 1)
    offset = number - 2
    year = HALF_YEAR_START_YEAR + offset // 2
    if offset % 2 == 0:
        return date(year, 1, 1), date(year, 7, 1)
    return date(year, 7, 1), date(year + 1, 1, 1)


def season_of(day: date) -> Optional[int]:
    """날짜가 속한 시즌 번호 (2026년 이전이면 None)"""
    if day.year < FIRST_SEASON_YEAR:
        return None
    if day.year == FIRST_SEASON_YEAR:
        return 1
    half = 0 if day.month <= 6 else 1
    return 2 + (day.year - HALF_YEAR_START_YEAR) * 2 + half


def current_season() -> int:
    """오늘(한국 날짜)이 속한 시즌"""
    return season_of(today_kst()) or 1


def season_name(number: int) -> str:
    """표시 이름: '시즌1', '시즌2'"""
    season_bounds(number)  # 없는 번호면 ValueError
    return f"시즌{number}"


def season_context() -> dict:
    """페이지에 넣는 시즌 목록 (최신 시즌이 앞)"""
    current = current_season()
    return {
        "current": current,
        "list": [
            {"number": number, "name": season_name(number)}
            for number in range(current, 0, -1)
        ],
    }


def is_valid_season(number: int) -> bool:
    """시즌1 ~ 현재 시즌"""
    return 1 <= number <= current_season()


def kst_midnight_as_utc(day: date) -> datetime:
    """한국 날짜 0시를 UTC naive datetime으로 (UTC로 저장된 가입일 비교용)"""
    return (
        datetime.combine(day, time(), KST)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
