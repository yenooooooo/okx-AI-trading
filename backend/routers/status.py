"""
routers/status.py — 봇 상태, AI 뇌, 거래 내역
Routes: GET /status, /brain, /trades
"""
import asyncio
import math as _mg_math
import os

from fastapi import APIRouter

from database import get_config, get_trades
from logger import get_logger
from core.state import bot_global_state, ai_brain_state, state_lock, _g

router = APIRouter()
logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/status
# ════════════════════════════════════════════════════════════════════════════
@router.get("/status")
async def fetch_current_status():
    """현재 봇 상태 반환 (OKX 실시간 데이터 강제 동기화)"""
    try:
        engine = _g.get("engine")
        if engine and engine.exchange:
            curr_bal = await asyncio.to_thread(engine.get_usdt_balance)
            bot_global_state["balance"] = round(curr_bal, 2)

            try:
                positions = await asyncio.to_thread(engine.exchange.fetch_positions)

                exchange_active = {}
                for pos in positions:
                    contracts = float(pos.get('contracts', 0) or 0)
                    if contracts > 0:
                        symbol = pos.get('symbol')
                        side = pos.get('side', '').upper()
                        if symbol and side in ['LONG', 'SHORT']:
                            exchange_active[symbol] = pos

                for symbol, sym_state in bot_global_state["symbols"].items():
                    if symbol in exchange_active:
                        pos = exchange_active[symbol]
                        roe = float(pos.get('percentage', 0) or 0)
                        unrealized = float(pos.get('unrealizedPnl', 0) or 0)
                        leverage = float(pos.get('leverage', 1) or 1)
                        mark = float(pos.get('markPrice', 0) or 0)
                        entry = float(pos.get('entryPrice', 0) or 0)
                        side = pos.get('side', '').upper()

                        if roe == 0.0 and entry > 0 and mark > 0:
                            diff = (mark - entry) / entry if side == 'LONG' else (entry - mark) / entry
                            roe = round(diff * 100 * leverage, 2)

                        sym_state["unrealized_pnl_percent"] = roe
                        sym_state["unrealized_pnl"] = unrealized
                        sym_state["leverage"] = leverage
                        if mark > 0:
                            sym_state["current_price"] = mark
                        if entry > 0 and sym_state.get("entry_price", 0) == 0:
                            sym_state["entry_price"] = entry
                            sym_state["position"] = side
                            sym_state["partial_tp_executed"] = False
                            sym_state["breakeven_stop_active"] = False
                            sym_state["exchange_tp_filled"] = False

            except Exception as pe:
                logger.error(f"[헬스체크] OKX 포지션 API 핑 실패 — RAW 원인: {pe}")
    except Exception as e:
        logger.error(f"[헬스체크] OKX API 헬스체크 핑 실패 사유: {e}")

    _sym_conf = get_config('symbols')
    active_target = _sym_conf[0] if isinstance(_sym_conf, list) and _sym_conf else "BTC/USDT:USDT"

    try:
        _risk = get_config('risk_per_trade')
        if _risk:
            engine_mode = "TUNED"
            active_risk = round(float(_risk) * 100, 1)
        else:
            engine_mode = "AUTO"
            active_risk = "AI Dynamic"
    except Exception:
        engine_mode = "AUTO"
        active_risk = "AI Dynamic"

    # ── Margin Guard: 심볼별 증거금 사전 검증 ──
    _margin_guard = {}
    _mg_bal = bot_global_state["balance"]
    _engine = _g.get("engine")
    if _engine and _engine.exchange and _mg_bal > 0:
        for _mg_sym in bot_global_state["symbols"]:
            try:
                _mg_mkt = _engine.exchange.market(_mg_sym)
                _mg_cs = float(_mg_mkt.get('contractSize', 0.01))
                _mg_lev = max(1, int(get_config('leverage', _mg_sym) or 1))
                _mg_price = float(bot_global_state["symbols"][_mg_sym].get("current_price", 0))

                if _mg_price > 0:
                    _mg_safe = _mg_bal * 0.50
                    _mg_margin_per = (_mg_cs * _mg_price) / _mg_lev
                    _mg_max = int(_mg_safe / _mg_margin_per) if _mg_margin_per > 0 else 0

                    _mg_risk = float(get_config('risk_per_trade', _mg_sym) or 0.02)
                    if _mg_risk >= 1.0:
                        _mg_risk /= 100.0
                    _mg_notional = _mg_bal * _mg_risk * _mg_lev
                    _mg_estimated = max(1, round((_mg_notional / _mg_price) / _mg_cs))
                    if _mg_max >= 1:
                        _mg_estimated = min(_mg_estimated, _mg_max)

                    _mg_margin_total = (_mg_cs * _mg_price * _mg_estimated) / _mg_lev
                    _mg_feasible = _mg_safe >= _mg_margin_total

                    _mg_rec_lev = _mg_lev
                    if not _mg_feasible and _mg_safe > 0:
                        _mg_rec_lev = min(100, _mg_math.ceil((_mg_cs * _mg_price * _mg_estimated) / _mg_safe))

                    _margin_guard[_mg_sym] = {
                        "feasible": _mg_feasible,
                        "current_leverage": _mg_lev,
                        "recommended_leverage": _mg_rec_lev,
                        "max_contracts": _mg_max,
                        "estimated_contracts": _mg_estimated,
                        "margin_per_contract": round(_mg_margin_per, 2),
                        "available_margin": round(_mg_safe, 2),
                        "needs_change": not _mg_feasible,
                    }
                else:
                    _margin_guard[_mg_sym] = {"feasible": True, "needs_change": False}
            except Exception:
                _margin_guard[_mg_sym] = {"feasible": True, "needs_change": False}

    _is_demo_mode = os.getenv("OKX_DEMO", "false").strip().lower() in ("true", "1", "yes")

    return {
        "is_running": bot_global_state["is_running"],
        "balance": bot_global_state["balance"],
        "symbols": bot_global_state["symbols"],
        "active_target": active_target,
        "adaptive_tier": bot_global_state.get("adaptive_tier", str(get_config('_current_adaptive_tier') or '')),
        "engine_status": {
            "mode": engine_mode,
            "risk": active_risk,
        },
        "margin_guard": _margin_guard,
        "is_demo": _is_demo_mode,
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/brain
# ════════════════════════════════════════════════════════════════════════════
@router.get("/brain")
async def fetch_brain_status():
    """AI 뇌 상태 반환"""
    _sym_conf = get_config('symbols')
    active_target = _sym_conf[0] if isinstance(_sym_conf, list) and _sym_conf else "BTC/USDT:USDT"
    return {**ai_brain_state, "active_target": active_target}


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/trades
# ════════════════════════════════════════════════════════════════════════════
@router.get("/trades")
async def fetch_trades_history():
    """최근 거래 내역 반환 (DB 기반)"""
    return get_trades(limit=100)
