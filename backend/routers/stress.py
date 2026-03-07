"""
routers/stress.py — 스트레스 테스트, 바이패스, 테스트 주문, Paper 청산
Routes: GET/POST /stress_bypass, POST /test_order, /close_paper, /cancel_pending,
        /inject_stress, /reset_stress
"""
import asyncio
import time as _time

from fastapi import APIRouter, HTTPException

from database import get_config, set_config, save_log
from notifier import send_telegram_sync
from logger import get_logger
from strategy import TradingStrategy
from core.state import bot_global_state, _g, state_lock
from core.helpers import _save_strategy_state, _reset_position_state
from core.tg_formatters import _TG_LINE, _tg_entry, _tg_system

router = APIRouter()
logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/stress_bypass
# ════════════════════════════════════════════════════════════════════════════
@router.get("/stress_bypass")
async def get_stress_bypass():
    """5개 자동 잠금 기능의 24h 바이패스 현황 조회"""
    FEATURES = {
        "kill_switch": "stress_bypass_kill_switch",
        "cooldown_loss": "stress_bypass_cooldown_loss",
        "daily_loss": "stress_bypass_daily_loss",
        "reentry_cd": "stress_bypass_reentry_cd",
        "stale_price": "stress_bypass_stale_price",
    }
    result = {}
    for name, key in FEATURES.items():
        val = get_config(key)
        try:
            activated_at = float(val) if val and str(val) != "0" else 0.0
        except (ValueError, TypeError):
            activated_at = 0.0
        elapsed = _time.time() - activated_at if activated_at else 86401
        active = elapsed < 86400
        result[name] = {
            "active": active,
            "remaining_sec": max(0.0, 86400 - elapsed) if active else 0.0,
        }
    # ── 방어 상태 모니터: UI 실시간 표시용 전략 방어 상태 포함 ──
    _active_strategy = _g.get("strategy")
    _ds_now = _time.time()
    _ds = {
        "kill_switch_active": False, "kill_switch_remaining_sec": 0.0,
        "cooldown_active": False, "cooldown_remaining_sec": 0.0,
        "consecutive_losses": 0, "cd_trigger": 3,
        "daily_pnl_pct": 0.0, "daily_max_pct": 7.0,
    }
    if _active_strategy:
        _ks_until = _active_strategy.kill_switch_until
        _ks_on = _active_strategy.kill_switch_active and _ks_until > _ds_now
        _cd_until = _active_strategy.loss_cooldown_until
        _cd_on = _cd_until > _ds_now
        _ds["kill_switch_active"] = _ks_on
        _ds["kill_switch_remaining_sec"] = round(max(0.0, _ks_until - _ds_now), 1) if _ks_on else 0.0
        _ds["cooldown_active"] = _cd_on
        _ds["cooldown_remaining_sec"] = round(max(0.0, _cd_until - _ds_now), 1) if _cd_on else 0.0
        _ds["consecutive_losses"] = _active_strategy.consecutive_loss_count
        _ds["cd_trigger"] = _active_strategy.cooldown_losses_trigger
        if _active_strategy.daily_start_balance > 0:
            _ds["daily_pnl_pct"] = round(_active_strategy.daily_pnl_accumulated / _active_strategy.daily_start_balance * 100, 2)
        _ds["daily_max_pct"] = round(_active_strategy.daily_max_loss_pct * 100, 1)
    result["_defense_state"] = _ds
    return result


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/stress_bypass
# ════════════════════════════════════════════════════════════════════════════
@router.post("/stress_bypass")
async def set_stress_bypass(feature: str, enabled: bool):
    """특정 자동 잠금 기능을 24시간 동안 바이패스 활성화/비활성화"""
    _active_strategy = _g.get("strategy")
    KEY_MAP = {
        "kill_switch": "stress_bypass_kill_switch",
        "cooldown_loss": "stress_bypass_cooldown_loss",
        "daily_loss": "stress_bypass_daily_loss",
        "reentry_cd": "stress_bypass_reentry_cd",
        "stale_price": "stress_bypass_stale_price",
    }
    if feature not in KEY_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown feature: {feature}")
    db_key = KEY_MAP[feature]
    if enabled:
        set_config(db_key, str(_time.time()))
        if feature == "daily_loss" and _active_strategy:
            _active_strategy.kill_switch_active = False
            _active_strategy.kill_switch_until = 0
        if feature == "cooldown_loss" and _active_strategy:
            _active_strategy.loss_cooldown_until = 0
            _active_strategy.consecutive_loss_count = 0
    else:
        set_config(db_key, "0")
    _save_strategy_state(_active_strategy)
    _bypass_label = {
        "kill_switch": "킬스위치", "cooldown_loss": "연패 쿨다운",
        "daily_loss": "일일 손실 한도", "reentry_cd": "재진입 쿨다운",
        "stale_price": "가격 지연 감지",
    }
    _bp_name = _bypass_label.get(feature, feature)
    if enabled:
        send_telegram_sync(
            f"⚠️ <b>ANTIGRAVITY</b>  |  안전장치 바이패스\n"
            f"{_TG_LINE}\n"
            f"🔓 <b>{_bp_name}</b> 방어막 24시간 해제됨\n"
            f"⚠️ 해제 잊지 마세요! 자동 복구: 24시간 후\n"
            f"{_TG_LINE}"
        )
    else:
        send_telegram_sync(
            f"✅ <b>ANTIGRAVITY</b>  |  안전장치 복구\n"
            f"{_TG_LINE}\n"
            f"🔒 <b>{_bp_name}</b> 방어막 정상 복구\n"
            f"{_TG_LINE}"
        )
    return {
        "feature": feature,
        "active": enabled,
        "remaining_sec": 86400.0 if enabled else 0.0,
    }


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/test_order
# ════════════════════════════════════════════════════════════════════════════
@router.post("/test_order")
async def execute_test_order(direction: str = "LONG"):
    """강제 테스트 진입 (LONG/SHORT + Market/Smart Limit 지원) 엔드포인트"""
    try:
        signal = direction.upper()
        if signal not in ("LONG", "SHORT"):
            return {"error": f"잘못된 direction 값: {direction}. LONG 또는 SHORT만 허용됩니다."}

        if not bot_global_state["is_running"]:
            return {"error": "시스템이 중지되어 있습니다. 먼저 가동해 주세요."}

        _sym_conf = get_config('symbols')
        symbol = _sym_conf[0] if isinstance(_sym_conf, list) and _sym_conf else "BTC/USDT:USDT"

        if symbol not in bot_global_state["symbols"]:
            bot_global_state["symbols"][symbol] = {
                "position": "NONE", "entry_price": 0.0, "current_price": 0.0,
                "unrealized_pnl_percent": 0.0, "take_profit_price": "대기중",
                "stop_loss_price": 0.0, "highest_price": 0.0, "lowest_price": 0.0
            }

        if bot_global_state["symbols"][symbol]["position"] != "NONE":
            err_msg = "[오류] 이미 포지션을 보유 중이어서 테스트 진입을 진행할 수 없습니다."
            bot_global_state["logs"].append(err_msg)
            return {"error": "이미 포지션이 존재합니다."}

        engine_api = _g.get("engine")
        if not engine_api or not engine_api.exchange:
            return {"error": "OKX 거래소 인스턴스가 연결되지 않았습니다."}

        manual_override = str(get_config('manual_override_enabled')).lower() == 'true'
        trade_leverage = max(1, min(100, int(get_config('manual_leverage' if manual_override else 'leverage') or 1)))
        current_price_now = (await asyncio.to_thread(engine_api.get_current_price, symbol)) or 1
        try:
            contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
        except Exception:
            contract_size = 0.01
        if manual_override:
            seed_usdt = max(1.0, float(get_config('manual_amount') or 10))
            notional = seed_usdt * trade_leverage
            trade_amount = max(1, round(notional / (current_price_now * contract_size)))
        else:
            curr_bal_now = await asyncio.to_thread(engine_api.get_usdt_balance)
            strategy_tmp = TradingStrategy(initial_seed=75.0)
            _risk_rate = float(get_config('risk_per_trade') or 0.02)
            trade_amount = strategy_tmp.calculate_position_size_dynamic(
                curr_bal_now, current_price_now, trade_leverage, contract_size, _risk_rate
            )
        try:
            await asyncio.to_thread(engine_api.exchange.set_leverage, trade_leverage, symbol)
        except Exception as lev_err:
            logger.error(f"[{symbol}] 수동 진입 레버리지 설정 실패: {lev_err}")
            send_telegram_sync(f"🚨 레버리지 {trade_leverage}x 설정 실패 → 수동 진입 차단: {str(lev_err)[:80]}")
            return {"status": "error", "message": f"레버리지 설정 실패: {lev_err}"}

        order_type = str(get_config('ENTRY_ORDER_TYPE') or 'Market')
        current_price = (await asyncio.to_thread(engine_api.get_current_price, symbol)) or 0

        try:
            # 지연 임포트 (circular import 방지)
            from core.trading_loop import execute_entry_order
            executed_price, pending_order_id = await execute_entry_order(
                engine_api, symbol, signal, trade_amount, order_type, current_price
            )

            if order_type == 'Smart Limit' and pending_order_id:
                import time
                _is_shadow_pend_test = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
                _paper_tag_pt = "[👻 PAPER] " if _is_shadow_pend_test else ""
                async with state_lock:
                    bot_global_state["symbols"][symbol]["position"] = "PENDING_" + signal
                    bot_global_state["symbols"][symbol]["pending_order_id"] = pending_order_id
                    bot_global_state["symbols"][symbol]["pending_order_time"] = time.time()
                    bot_global_state["symbols"][symbol]["pending_amount"] = trade_amount
                    bot_global_state["symbols"][symbol]["pending_price"] = executed_price
                    bot_global_state["symbols"][symbol]["leverage"] = trade_leverage
                    bot_global_state["symbols"][symbol]["is_paper"] = _is_shadow_pend_test

                entry_emoji = "⏳📈" if signal == "LONG" else "⏳📉"
                test_msg = f"{_paper_tag_pt}{entry_emoji} [{symbol}] 테스트 {signal} 스마트 지정가 접수 | 목표가: ${executed_price:.2f} | {trade_amount}계약 | {trade_leverage}x"
            else:
                _is_shadow_test = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
                _paper_tag = "[👻 PAPER] " if _is_shadow_test else ""
                async with state_lock:
                    bot_global_state["symbols"][symbol]["position"] = signal
                    bot_global_state["symbols"][symbol]["entry_price"] = executed_price or current_price
                    bot_global_state["symbols"][symbol]["highest_price"] = executed_price or current_price
                    bot_global_state["symbols"][symbol]["lowest_price"] = executed_price or current_price
                    bot_global_state["symbols"][symbol]["contracts"] = trade_amount
                    bot_global_state["symbols"][symbol]["leverage"] = trade_leverage
                    bot_global_state["symbols"][symbol]["partial_tp_executed"] = False
                    bot_global_state["symbols"][symbol]["breakeven_stop_active"] = False
                    bot_global_state["symbols"][symbol]["exchange_tp_filled"] = False
                    bot_global_state["symbols"][symbol]["is_paper"] = _is_shadow_test

                entry_emoji = "📈" if signal == "LONG" else "📉"
                test_msg = f"{_paper_tag}{entry_emoji} [{symbol}] 테스트 {signal} 강제 진입 성공! (수량: {trade_amount}계약, 레버리지: {trade_leverage}x)"

            bot_global_state["logs"].append(test_msg)
            logger.info(test_msg)
            send_telegram_sync(_tg_entry(symbol, signal, executed_price or current_price, trade_amount, trade_leverage, is_test=True))

            return {"status": "success", "message": test_msg}

        except Exception as e:
            error_msg = f"[{symbol}] 테스트 {signal} 주문 실패: {str(e)}"
            bot_global_state["logs"].append(error_msg)
            logger.error(error_msg)
            return {"error": str(e)}

    except Exception as e:
        return {"error": f"서버 오류: {str(e)}"}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/close_paper
# ════════════════════════════════════════════════════════════════════════════
@router.post("/close_paper")
async def close_paper_position():
    """[Shadow Mode] Paper 포지션 수동 강제 청산 엔드포인트"""
    try:
        _sym_conf = get_config('symbols')
        symbol = _sym_conf[0] if isinstance(_sym_conf, list) and _sym_conf else None
        if not symbol:
            return {"error": "활성 심볼이 없습니다."}

        sym_state = bot_global_state["symbols"].get(symbol, {})
        position_side = sym_state.get("position", "NONE")

        if position_side == "NONE":
            return {"error": "청산할 포지션이 없습니다."}

        if not sym_state.get("is_paper", False):
            return {"error": "Paper 포지션이 아닙니다. 실전 포지션은 OKX에서 직접 청산하세요."}

        engine_api = _g.get("engine")
        if engine_api and engine_api.exchange:
            current_price = (await asyncio.to_thread(engine_api.get_current_price, symbol)) or 0
        else:
            current_price = sym_state.get("current_price", 0)

        entry = sym_state.get("entry_price", 0)
        amount = int(sym_state.get("contracts", 1))
        leverage = int(sym_state.get("leverage", 1))

        try:
            contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01)) if engine_api else 0.01
        except Exception:
            contract_size = 0.01

        position_value = entry * amount * contract_size
        if position_side == "LONG":
            total_gross = (current_price - entry) * amount * contract_size
        else:
            total_gross = (entry - current_price) * amount * contract_size
        total_fee = position_value * 0.0005 * 2
        pnl_amount = total_gross - total_fee
        pnl_percent = (pnl_amount / (position_value / leverage) * 100) if position_value > 0 else 0.0

        emoji = "✅" if pnl_percent >= 0 else "🔴"
        msg = f"[👻 PAPER] {emoji} [{symbol}] {position_side} 수동 청산 | 체결가: ${current_price:.2f} | 순수익(Net): {pnl_amount:+.4f} USDT (Gross: {total_gross:+.4f}, Fee: {total_fee:.4f}) | 수익률: {pnl_percent:+.2f}%"
        bot_global_state["logs"].append(msg)
        logger.info(msg)
        send_telegram_sync(f"[👻 PAPER] {emoji} 수동 청산\n코인: {symbol} {position_side}\n체결가: ${current_price:.2f}\n순수익: {pnl_amount:+.4f} USDT ({pnl_percent:+.2f}%)")

        async with state_lock:
            _reset_position_state(sym_state)
            bot_global_state["is_running"] = False
            stop_msg = "[시스템] 강제 청산(Paper) 감지: 봇 무한 재진입 방지를 위해 자동매매를 일시 중지(STOP) 합니다."
            bot_global_state["logs"].append(stop_msg)
            logger.info(stop_msg)
            send_telegram_sync(_tg_system(False))

        return {"status": "success", "message": msg}

    except Exception as e:
        return {"error": f"서버 오류: {str(e)}"}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/cancel_pending
# ════════════════════════════════════════════════════════════════════════════
@router.post("/cancel_pending")
async def manual_cancel_pending():
    """[Phase 24] 사령관 수동 개입: 대기 중인 지정가(매복) 주문 즉시 철거"""
    try:
        _sym_conf = get_config('symbols')
        symbol = _sym_conf[0] if isinstance(_sym_conf, list) and _sym_conf else None
        if not symbol:
            return {"status": "error", "message": "활성 심볼이 없습니다."}

        sym_state = bot_global_state["symbols"].get(symbol, {})
        position = sym_state.get("position", "NONE")

        if not position.startswith("PENDING"):
            return {"status": "error", "message": "현재 대기 중인 주문이 없습니다."}

        pending_id = sym_state.get("pending_order_id")
        _is_paper = sym_state.get("is_paper", False)
        _engine = _g.get("engine")

        if pending_id and not _is_paper and _engine and _engine.exchange:
            try:
                await asyncio.to_thread(_engine.exchange.cancel_order, pending_id, symbol)
            except Exception as cancel_err:
                logger.warning(f"[{symbol}] 수동 취소 요청 실패 (이미 취소되었을 수 있음): {cancel_err}")

        async with state_lock:
            _reset_position_state(bot_global_state["symbols"][symbol])

        _abort_msg = f"🟡 [{symbol}] 수동 철수: 사령관 명령으로 대기 주문({pending_id})을 즉시 철거했습니다."
        bot_global_state["logs"].append(_abort_msg)
        logger.warning(_abort_msg)
        save_log("WARNING", _abort_msg)
        send_telegram_sync(
            f"🟡 <b>수동 철수 명령 수신</b>\n"
            f"────────────────────────\n"
            f"사령관 개입: 대기 중인 덫을 철거하고 관망합니다.\n"
            f"• 심볼: <b>{symbol}</b>\n"
            f"• 주문 ID: <code>{pending_id}</code>"
        )

        return {"status": "success", "message": "대기 주문이 성공적으로 철거되었습니다."}
    except Exception as e:
        logger.error(f"수동 취소 실패: {e}")
        return {"status": "error", "message": str(e)}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/inject_stress
# ════════════════════════════════════════════════════════════════════════════
@router.post("/inject_stress")
async def inject_stress(type: str):
    """[Phase 3] 스트레스 주입기 — 킬스위치/쿨다운 소방훈련"""
    stress_type = type.upper()
    if stress_type not in ("KILL_SWITCH", "LOSS_STREAK"):
        return {"error": f"잘못된 type: {type}. KILL_SWITCH 또는 LOSS_STREAK만 허용."}
    if not bot_global_state["is_running"]:
        return {"error": "시스템이 중지되어 있습니다. 먼저 가동해 주세요."}
    bot_global_state["stress_inject"] = stress_type
    return {"status": "success", "message": f"스트레스 주입 예약 완료: {stress_type} (다음 매매루프 사이클에서 발동)"}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/reset_stress
# ════════════════════════════════════════════════════════════════════════════
@router.post("/reset_stress")
async def reset_stress():
    """[Phase 3] 킬스위치/쿨다운 강제 해제"""
    bot_global_state["stress_inject"] = "RESET"
    reset_msg = "✅ [소방훈련 해제] 킬스위치 + 쿨다운 리셋 예약 완료 (다음 사이클에서 적용)"
    bot_global_state["logs"].append(reset_msg)
    return {"status": "success", "message": reset_msg}
