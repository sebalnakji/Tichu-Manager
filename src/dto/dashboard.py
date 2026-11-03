"""
Dashboard DTO
개인 분석 화면 응답
"""
from typing import List, Optional

from pydantic import BaseModel


class DashboardPlayer(BaseModel):
    id: int
    name: str
    profile_url: str


class DashboardMedal(BaseModel):
    """끝난 시즌 개인 랭킹 최종 1~3위"""
    season: int
    season_name: str
    rank: int


class SeasonRecord(BaseModel):
    wins: int
    losses: int
    win_rate: float  # 랭킹과 같이 소수 한 자리


class PersonRecord(BaseModel):
    """사람별 내 전적 (상대로 만났을 때 / 같은 팀이었을 때)"""
    player_id: int
    name: str
    games: int
    wins: int
    losses: int
    win_rate: int  # 정수 %


class PeopleRecords(BaseModel):
    # 10판 이상 중 승패 차이(승 − 패)가 가장 큰 +(😋 맛집 / 💞 베프), 가장 작은 −(😈 천적 / 💔 악연)
    # 동점이면 승률, 그다음 판 수
    best: Optional[PersonRecord] = None
    worst: Optional[PersonRecord] = None
    people: List[PersonRecord]  # 많이 만난 순 → 승률 높은 순


class MatchMargin(BaseModel):
    """최대 점수 차 게임 (누르면 게임 기록 상세)"""
    match_id: int
    margin: int  # 우리 팀 − 상대 팀 최종 점수


class SeasonRecords(BaseModel):
    """🏅 시즌 기록 (선택한 시즌)"""
    longest_win_streak: int
    longest_lose_streak: int
    biggest_win: Optional[MatchMargin] = None
    biggest_loss: Optional[MatchMargin] = None
    # 역전승: 기회(상대 800+ & 200+ 뒤짐, 또는 마지막 라운드 라지+원투로 승리) 중 이긴 게임
    comeback_wins: int
    comeback_win_chances: int
    # 역전패: 기회(우리 800+ & 200+ 앞섬, 또는 마지막 라운드 상대 라지+원투로 패배) 중 진 게임
    comeback_losses: int
    comeback_loss_chances: int
    average_margin: int           # 이기면 +, 지면 − 의 평균 (반올림)
    tichu_calls_per_game: float   # 직접 부른 스몰 티츄 ÷ 게임 수
    grand_calls_per_game: float   # 직접 부른 라지 티츄 ÷ 게임 수


class TichuNumbers(BaseModel):
    """게임당 선언 횟수 + 성공률 (나 또는 모임 평균)"""
    tichu_per_game: float
    grand_per_game: float
    tichu_rate: float  # 성공률 %, 시도 없으면 0
    grand_rate: float
    tichu_try: int
    tichu_success: int
    grand_try: int
    grand_success: int


class TichuCompare(BaseModel):
    me: TichuNumbers
    average: TichuNumbers  # 그 시즌 경기한 모임 전체 (전체 선언 ÷ 전체 사람-게임, 전체 성공 ÷ 전체 시도)


class RankPoint(BaseModel):
    date: str   # 경기가 있었던 날
    rank: int   # 그날 경기까지 반영한 랭킹 순위 (랭킹 화면과 같은 규칙)
    ranked: int # 그날 순위에 오른 인원


class MarginBins(BaseModel):
    """최종 점수 차 분포: 구간별 승·패 수 (구간은 작은 차이 → 큰 차이 순)"""
    labels: List[str]
    wins: List[int]
    losses: List[int]


class DashboardCharts(BaseModel):
    tichu: TichuCompare
    win_rate: List[float]   # 경기 순서대로 시즌 누적 승률 % (소수 한 자리)
    ranks: List[RankPoint]
    margins: MarginBins


class PlayerDashboardResponse(BaseModel):
    player: DashboardPlayer
    # 프로필 카드 (시즌 선택과 무관하게 고정)
    current_season: int
    current_season_name: str
    current_rank: Optional[int]  # 현재 시즌 경기가 없으면 None
    current_record: Optional[SeasonRecord] = None  # 현재 시즌 승패·승률 (랭킹과 같은 숫자)
    medals: List[DashboardMedal]  # 오래된 시즌 → 최근 시즌
    # 아래 통계 (선택한 시즌, None = 전체 기간)
    season: Optional[int]
    games: int
    opponents: PeopleRecords  # ⚔️ 상대 전적
    partners: PeopleRecords   # 🤝 파트너 전적 (같은 팀 승률)
    records: Optional[SeasonRecords] = None  # 🏅 시즌 기록 (경기 없으면 None)
    charts: Optional[DashboardCharts] = None  # 그래프 (경기 없으면 None)
