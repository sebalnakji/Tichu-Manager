"""
Tichu Manager - FastAPI Application
티츄 게임 관리 웹 애플리케이션
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler

from core.properties import settings, validate_required_settings
validate_required_settings()
from core.database import init_db
from core.logging_config import setup_logging, get_logger
from routers import players_router, matches_router, stats_router, auth_router, admin_router
from services.match_cleanup_service import cleanup_unfinished_matches


# 로깅 초기화
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    애플리케이션 생명주기 관리
    시작 시 DB 초기화, 종료 시 정리 작업
    """
    # Startup
    logger.info("=" * 50)
    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} 시작")
    logger.info(f"환경: {settings.ENV.value}")
    logger.info("Database connection configured")
    logger.info("=" * 50)

    # 데이터베이스 초기화
    init_db()
    logger.info("데이터베이스 초기화 완료")

    # 미완료 게임 정리 스케줄러
    scheduler = None
    if settings.ENABLE_CLEANUP_SCHEDULER:
        scheduler = BackgroundScheduler(timezone="Asia/Seoul")
        scheduler.add_job(
            cleanup_unfinished_matches,
            "cron",
            hour=3,
            minute=0,
            id="cleanup-unfinished-matches",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("미완료 게임 정리 스케줄러 등록 (매일 03:00 KST)")

        # 서버 시작 시 1회 즉시 실행
        cleanup_unfinished_matches()
    else:
        logger.info("미완료 게임 정리 스케줄러 비활성화")

    yield

    # Shutdown
    if scheduler:
        scheduler.shutdown()
    logger.info("애플리케이션 종료")


# FastAPI 앱 생성
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="친목 모임을 위한 티츄 게임 매니저",
    lifespan=lifespan,
    debug=settings.DEBUG,
)


# 정적 파일 및 템플릿 설정
app.mount("/static", StaticFiles(directory="../static"), name="static")
templates = Jinja2Templates(directory="../templates")


# API 라우터 등록
app.include_router(players_router)
app.include_router(matches_router)
app.include_router(stats_router)
app.include_router(auth_router)
app.include_router(admin_router)


# ============================================================
# HTML 페이지 라우트
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """
    메인 홈 화면 - 3개 버튼 (게임 시작, 리더보드, 설정)
    """
    return templates.TemplateResponse(
        "home.html",
        {"request": request, "title": "홈"}
    )


@app.get("/team-selection", response_class=HTMLResponse)
async def team_selection(request: Request):
    """
    팀 선택 화면 - 플레이어 선택 및 팀 배정
    """
    return templates.TemplateResponse(
        "team_selection.html",
        {"request": request, "title": "팀 선택"}
    )


@app.get("/scoreboard/{match_id}", response_class=HTMLResponse)
async def scoreboard(request: Request, match_id: int):
    """
    점수판 화면
    """
    return templates.TemplateResponse(
        "scoreboard.html",
        {"request": request, "match_id": match_id, "title": "점수판"}
    )


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(request: Request):
    """
    순위 및 승률 화면
    """
    return templates.TemplateResponse(
        "leaderboard.html",
        {"request": request, "title": "랭킹"}
    )


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    """
    게임 기록 화면 - 일별 / 개인 / 팀
    """
    return templates.TemplateResponse(
        "history.html",
        {"request": request, "title": "게임 기록"}
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """
    설정 화면
    """
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "title": "설정"}
    )


# ============================================================
# 헬스 체크
# ============================================================

@app.get("/health")
async def health_check():
    """
    서버 헬스 체크
    """
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.ENV.value,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
