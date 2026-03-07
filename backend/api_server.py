"""
api_server.py — FastAPI 앱 진입점 (얇은 Shell)
  - FastAPI 앱 선언 + CORS 설정
  - lifespan: startup/shutdown (OKXEngine 초기화, 백그라운드 태스크 시작)
  - WebSocket /ws/dashboard (루트 레벨 — prefix 없음)
  - 라우터 include (prefix="/api/v1")
  - 프론트엔드 정적 파일 서빙

모든 비즈니스 로직은 core/ 및 routers/ 모듈에 위치한다.
"""
import asyncio
import os
import uvicorn

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import init_db
from okx_engine import OKXEngine
from notifier import send_telegram_sync, init_telegram_bot, stop_telegram_bot
from logger import get_logger
from core.state import bot_global_state, _g
from core.background import (
    private_ws_loop,
    _margin_guard_bg_loop,
    _okx_trade_sync_loop,
    _heartbeat_monitor_loop,
)
from core.ws_manager import manager, broadcast_dashboard_state

# ── 라우터 임포트 ──────────────────────────────────────────────────────────────
from routers import status, config, analytics, backtest, diagnostics, stress, admin, xray

logger = get_logger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# FastAPI 앱 + CORS
# ════════════════════════════════════════════════════════════════════════════
app_server = FastAPI()

app_server.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ════════════════════════════════════════════════════════════════════════════
# 라우터 등록 (prefix="/api/v1")
# ════════════════════════════════════════════════════════════════════════════
app_server.include_router(status.router,      prefix="/api/v1")
app_server.include_router(config.router,      prefix="/api/v1")
app_server.include_router(analytics.router,   prefix="/api/v1")
app_server.include_router(backtest.router,    prefix="/api/v1")
app_server.include_router(diagnostics.router, prefix="/api/v1")
app_server.include_router(stress.router,      prefix="/api/v1")
app_server.include_router(admin.router,       prefix="/api/v1")
app_server.include_router(xray.router,        prefix="/api/v1")

# ════════════════════════════════════════════════════════════════════════════
# WebSocket /ws/dashboard — 루트 레벨 (prefix 없음)
# ════════════════════════════════════════════════════════════════════════════
@app_server.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    # 첫 번째 클라이언트 연결 시 브로드캐스트 루프 on-demand 가동 (공회전 방지)
    if _g["broadcast_task"] is None or _g["broadcast_task"].done():
        _g["broadcast_task"] = asyncio.create_task(broadcast_dashboard_state())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ════════════════════════════════════════════════════════════════════════════
# Lifespan: Startup
# ════════════════════════════════════════════════════════════════════════════
@app_server.on_event("startup")
async def startup_event():
    """서버 시작 시 OKXEngine 1회만 초기화 + 백그라운드 태스크 시작"""
    init_db()
    bot_global_state["logs"].append("[봇] 🔴 실전망(LIVE) 서버 시스템 가동 시작 - 실전 API 연동 완료")
    logger.info("API 서버 시작 - OKXEngine 초기화 중...")

    loop = asyncio.get_event_loop()
    _g["engine"] = await loop.run_in_executor(None, OKXEngine)

    if _g["engine"] and _g["engine"].exchange:
        logger.info("OKXEngine 싱글톤 초기화 완료")

        # [Phase 32] 서버 재시작 시 거래소 잔류 포지션 감지 경고
        try:
            _positions = await asyncio.to_thread(_g["engine"].exchange.fetch_positions)
            _open_pos = [p for p in _positions if float(p.get('contracts', 0)) > 0]
            if _open_pos:
                _pos_symbols = [p['symbol'] for p in _open_pos]
                _restart_warn = (
                    f"⚠️ [서버 재시작 감지] 거래소에 열린 포지션 {len(_open_pos)}건 발견: "
                    f"{_pos_symbols}. 봇 인메모리 상태와 동기화되지 않았습니다. 수동 확인 필요."
                )
                bot_global_state["logs"].append(_restart_warn)
                logger.warning(_restart_warn)
                send_telegram_sync(_restart_warn)
        except Exception as _pos_err:
            logger.warning(f"[Phase 32] 시작 시 포지션 감지 실패: {_pos_err}")

        _g["private_ws_task"]      = asyncio.create_task(private_ws_loop())
        logger.info("Private WebSocket 태스크 시작")

        _g["margin_guard_bg_task"] = asyncio.create_task(_margin_guard_bg_loop())
        logger.info("Margin Guard 백그라운드 모니터링 태스크 시작")

        _g["trade_sync_task"]      = asyncio.create_task(_okx_trade_sync_loop())
        logger.info("OKX Trade Sync 백그라운드 태스크 시작")
    else:
        logger.error("OKXEngine 초기화 실패 - .env 키 확인 필요")

    # 텔레그램 양방향 컨트롤 타워 구동
    await init_telegram_bot()
    bot_global_state["logs"].append("[봇] 텔레그램 양방향 컨트롤 타워 비동기 가동 완료")
    logger.info("텔레그램 양방향 컨트롤 타워 비동기 시작 완료")

    # Heartbeat Monitor (5분 간격 서브시스템 장애 자동 감지)
    _g["heartbeat_task"] = asyncio.create_task(_heartbeat_monitor_loop())
    logger.info("Heartbeat Monitor 백그라운드 태스크 시작 (5분 간격)")

    logger.info("서버 초기화 완료 - 대시보드 WS 브로드캐스트 클라이언트 대기 중")


# ════════════════════════════════════════════════════════════════════════════
# Lifespan: Shutdown
# ════════════════════════════════════════════════════════════════════════════
@app_server.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 텔레그램 등 비동기 자원 회수 (Graceful Shutdown)"""
    await stop_telegram_bot()
    logger.info("API 서버 종료 - 텔레그램 자원 릴리즈 완료")


# ════════════════════════════════════════════════════════════════════════════
# 프론트엔드 정적 파일 서빙
# 모든 API 라우터 등록 이후에 위치해야 API 요청을 가로채지 않음
# ════════════════════════════════════════════════════════════════════════════
frontend_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"
)
if os.path.exists(frontend_path):
    app_server.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
else:
    @app_server.get("/")
    async def serve_error():
        return {"error": "Frontend directory not found", "expected_path": frontend_path}


if __name__ == "__main__":
    uvicorn.run("api_server:app_server", host="0.0.0.0", port=8000, reload=False)
