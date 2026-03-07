"""
routers/admin.py — 봇 토글, DB 초기화
Routes: POST /toggle, POST /wipe_db
WebSocket /ws/dashboard 는 api_server.py (루트 레벨)에서 직접 처리
"""
import asyncio

from fastapi import APIRouter

from database import wipe_all_trades
from notifier import send_telegram_sync
from logger import get_logger
from core.state import bot_global_state, state_lock, _g
from core.tg_formatters import _tg_system

router = APIRouter()
logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/toggle
# ════════════════════════════════════════════════════════════════════════════
@router.post("/toggle")
async def toggle_bot_action():
    """봇 시작/중지"""
    async with state_lock:
        if bot_global_state["is_running"]:
            bot_global_state["is_running"] = False
            msg = "[봇] 명령 수신: 시스템 가동 중지 (STOP)"
            bot_global_state["logs"].append(msg)
            logger.info(msg)
            send_telegram_sync(_tg_system(False))
        else:
            bot_global_state["is_running"] = True
            msg = "[봇] 명령 수신: 시스템 가동 시작 (START)!"
            bot_global_state["logs"].append(msg)
            logger.info(msg)
            send_telegram_sync(_tg_system(True))
            # 중복 태스크 방지: 이전 태스크가 완료된 경우에만 새 태스크 생성
            if _g["trading_task"] is None or _g["trading_task"].done():
                # 지연 임포트 (circular import 방지)
                from core.trading_loop import async_trading_loop
                _g["trading_task"] = asyncio.create_task(async_trading_loop())

    return {"is_running": bot_global_state["is_running"]}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/wipe_db
# ════════════════════════════════════════════════════════════════════════════
@router.post("/wipe_db")
async def wipe_database():
    """[ADMIN] trades 테이블 전면 삭제 — 실전 투입 전 테스트 데이터 초기화"""
    try:
        wipe_all_trades()

        _active_strategy = _g.get("strategy")
        if _active_strategy is not None:
            _active_strategy.daily_pnl_accumulated = 0.0
            _active_strategy.daily_start_balance = 0.0
            _active_strategy.consecutive_loss_count = 0
            _active_strategy.loss_cooldown_until = 0

        bot_global_state["logs"].clear()
        bot_global_state["logs"].append("[🚨 ADMIN] 데이터베이스 전면 초기화 완료. 실전 매매 준비 끝.")
        logger.warning("[ADMIN] wipe_db 실행: trades 테이블 전체 삭제 완료")

        return {"success": True, "message": "DB 초기화 완료. 실전 매매 준비 상태."}
    except Exception as e:
        logger.error(f"[ADMIN] wipe_db 실패: {e}")
        return {"success": False, "message": str(e)}
