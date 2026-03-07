"""
routers/config.py — 설정 조회/변경, 타임프레임 전환, 튜닝 리셋
Routes: GET /config, POST /config, POST /timeframe/switch, POST /tuning/reset
"""
import asyncio
import json

from typing import Optional
from fastapi import APIRouter

from database import get_config, set_config, delete_configs, delete_symbol_configs, save_log
from notifier import send_telegram_sync
from logger import get_logger
from strategy import TradingStrategy
from core.state import bot_global_state, state_lock, _g, TIMEFRAME_PRESETS, ALLOWED_TIMEFRAMES
from core.helpers import _reset_position_state
from core.tg_formatters import _TG_LINE

router = APIRouter()
logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# 내부 헬퍼: 타임프레임 프리셋 적용
# ════════════════════════════════════════════════════════════════════════════
async def _apply_timeframe_presets(target_tf: str) -> bool:
    """타임프레임 전환 시 최적화된 매매 파라미터 프리셋 자동 적용"""
    preset = TIMEFRAME_PRESETS.get(target_tf)
    if not preset:
        return False
    for key, value in preset.items():
        set_config(key, str(value))
    _active_strategy = _g.get("strategy")
    if _active_strategy:
        for key, value in preset.items():
            if hasattr(_active_strategy, key):
                try:
                    if key == 'disparity_threshold':
                        setattr(_active_strategy, key, float(value) / 100.0)
                    else:
                        setattr(_active_strategy, key, float(value))
                except (ValueError, TypeError):
                    pass
    logger.info(f"[타임프레임 프리셋] {target_tf} 프리셋 적용 완료: {list(preset.keys())}")
    return True


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/config
# ════════════════════════════════════════════════════════════════════════════
@router.get("/config")
async def fetch_config(symbol: Optional[str] = None):
    """현재 봇 설정 조회. symbol 지정 시 해당 심볼 전용값 우선 반환 (GLOBAL Fallback 포함)"""
    base_config = get_config()
    if symbol and symbol != "GLOBAL":
        resolved = {}
        for key in base_config:
            sym_val = get_config(key, symbol)
            resolved[key] = sym_val if sym_val is not None else base_config[key]
        return resolved
    return base_config


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/config
# ════════════════════════════════════════════════════════════════════════════
@router.post("/config")
async def update_config(key: str, value: str, symbol: str = "GLOBAL"):
    """봇 설정 변경 (실시간 적용). symbol 지정 시 해당 심볼 전용으로 저장"""
    try:
        _engine = _g.get("engine")
        _transition_warnings = []

        # ── [Phase 29] Shadow↔Live 전환 가드 ──
        if key == "SHADOW_MODE_ENABLED":
            _old_shadow = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
            _new_shadow = str(value).lower() == 'true'

            if _old_shadow and not _new_shadow:
                _closed_papers = []
                for _sym, _sym_st in bot_global_state["symbols"].items():
                    _pos = _sym_st.get("position", "NONE")
                    _is_p = _sym_st.get("is_paper", False)
                    if _pos != "NONE" and _is_p:
                        _cp = _sym_st.get("current_price", 0)
                        _ep = _sym_st.get("entry_price", 0)
                        _amt = int(_sym_st.get("contracts", 1))
                        _lev = int(_sym_st.get("leverage", 1))
                        try:
                            _cs = float(_engine.exchange.market(_sym).get('contractSize', 0.01)) if _engine else 0.01
                        except Exception:
                            _cs = 0.01
                        _pv = _ep * _amt * _cs
                        if _pos in ("LONG", "PENDING_LONG"):
                            _gross = (_cp - _ep) * _amt * _cs
                        else:
                            _gross = (_ep - _cp) * _amt * _cs
                        _fee = -(_pv * 0.0005 * 2)
                        _pnl = _gross + _fee
                        _pnl_pct = (_pnl / (_pv / _lev) * 100) if _pv > 0 else 0.0
                        async with state_lock:
                            _reset_position_state(_sym_st)
                        _emoji = "+" if _pnl >= 0 else ""
                        _closed_papers.append(f"{_sym} {_pos} (PnL: {_emoji}{_pnl:.4f} USDT)")
                if _closed_papers:
                    _guard_msg = f"[전환 가드] Shadow→Live 전환: Paper 포지션 {len(_closed_papers)}건 자동 청산 | {', '.join(_closed_papers)}"
                    bot_global_state["logs"].append(_guard_msg)
                    logger.info(_guard_msg)
                    send_telegram_sync(f"[전환 가드] Shadow→Live 전환\nPaper 포지션 {len(_closed_papers)}건 자동 청산\n{chr(10).join(_closed_papers)}")
                    _transition_warnings.append(_guard_msg)

            elif not _old_shadow and _new_shadow:
                _live_positions = []
                for _sym, _sym_st in bot_global_state["symbols"].items():
                    _pos = _sym_st.get("position", "NONE")
                    _is_p = _sym_st.get("is_paper", False)
                    if _pos != "NONE" and not _is_p:
                        _live_positions.append(f"{_sym} {_pos}")
                if _live_positions:
                    _warn_msg = f"[전환 가드] Live→Shadow 전환 경고: 실전 포지션 {len(_live_positions)}건 유지됨 ({', '.join(_live_positions)}). 기존 실전 포지션은 그대로 동작하며, 새 진입만 Paper로 전환됩니다."
                    bot_global_state["logs"].append(_warn_msg)
                    logger.warning(_warn_msg)
                    _transition_warnings.append(_warn_msg)

        # ── [Phase 30] 심볼 변경 시 고아 설정 자동 청소 ──
        if key == "symbols":
            try:
                _old_syms = get_config('symbols') or []
                _new_syms = json.loads(value) if isinstance(value, str) else value
                if isinstance(_old_syms, list) and isinstance(_new_syms, list):
                    _removed_syms = set(_old_syms) - set(_new_syms)
                    _total_cleaned = 0
                    for _rs in _removed_syms:
                        _del_cnt = delete_symbol_configs(_rs)
                        _total_cleaned += _del_cnt
                    if _total_cleaned > 0:
                        _clean_msg = f"[Phase 30] 심볼 변경 감지: 제거된 심볼 {len(_removed_syms)}개의 고아 설정 {_total_cleaned}건 청소 완료"
                        bot_global_state["logs"].append(_clean_msg)
                        logger.info(_clean_msg)
                        _transition_warnings.append(_clean_msg)
            except (json.JSONDecodeError, TypeError):
                pass

        set_config(key, value, symbol)
        sym_tag = f"[{symbol}] " if symbol != "GLOBAL" else ""
        log_msg = f"[UI 연동 성공 🟢] {sym_tag}'{key}' 설정이 '{value}'(으)로 뇌 구조에 완벽히 적용되었습니다."
        bot_global_state["logs"].append(log_msg)
        logger.info(log_msg)
        result = {"success": True, "message": f"{key} 업데이트 완료"}
        if _transition_warnings:
            result["warnings"] = _transition_warnings
        return result
    except Exception as e:
        log_msg = f"[UI 연동 실패 🔴] '{key}' 설정 적용 중 코드 연결 오류가 발생했습니다."
        bot_global_state["logs"].append(log_msg)
        logger.error(log_msg)
        return {"success": False, "message": str(e)}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/timeframe/switch
# ════════════════════════════════════════════════════════════════════════════
@router.post("/timeframe/switch")
async def switch_timeframe(target_tf: str):
    """원클릭 타임프레임 전환 (5m ↔ 15m) — 포지션 보유 시 차단, 프리셋 자동 적용"""
    try:
        if target_tf not in ALLOWED_TIMEFRAMES:
            return {"success": False, "message": f"허용된 타임프레임: {', '.join(sorted(ALLOWED_TIMEFRAMES))}"}

        current_tf = str(get_config('timeframe') or '15m')
        if current_tf == target_tf:
            return {"success": True, "message": f"이미 {target_tf} 타임프레임입니다.", "changed": False}

        blocked_symbols = []
        for sym, sym_state in bot_global_state["symbols"].items():
            pos = sym_state.get("position", "NONE")
            if pos != "NONE":
                blocked_symbols.append(f"{sym} ({pos})")
        if blocked_symbols:
            return {
                "success": False,
                "message": f"포지션 보유 중 타임프레임 변경 불가: {', '.join(blocked_symbols)}",
                "blocked_by": blocked_symbols,
            }

        set_config('timeframe', target_tf)
        presets_applied = await _apply_timeframe_presets(target_tf)

        for sym, sym_state in bot_global_state["symbols"].items():
            sym_state["_last_confirmed_candle_ts"] = 0
            sym_state["_cached_signal"] = "HOLD"
            sym_state["_cached_analysis"] = ""
            sym_state["_cached_payload"] = {}

        switch_msg = f"[타임프레임 전환] {current_tf} → {target_tf} (원클릭 전환 완료)"
        bot_global_state["logs"].append(switch_msg)
        logger.info(switch_msg)
        save_log("INFO", switch_msg)

        _tg_msg = (
            f"🔄 <b>ANTIGRAVITY</b>  |  타임프레임 전환\n"
            f"{_TG_LINE}\n"
            f"변경: <b>{current_tf} → {target_tf}</b>\n"
            f"{_TG_LINE}\n"
            f"프리셋 자동 적용 완료"
        )
        try:
            send_telegram_sync(_tg_msg)
        except Exception as tg_err:
            logger.warning(f"[타임프레임 전환] 텔레그램 알림 전송 실패: {tg_err}")

        return {
            "success": True,
            "changed": True,
            "previous": current_tf,
            "current": target_tf,
            "message": f"타임프레임 {current_tf} → {target_tf} 전환 완료",
            "presets_applied": presets_applied,
        }
    except Exception as e:
        err_msg = f"[타임프레임 전환 실패] {e}"
        bot_global_state["logs"].append(err_msg)
        logger.error(err_msg)
        return {"success": False, "message": str(e)}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/tuning/reset
# ════════════════════════════════════════════════════════════════════════════
@router.post("/tuning/reset")
async def reset_tuning_to_auto():
    """튜닝 파라미터 전체 삭제 + 전략 인스턴스 재생성 (AI 순정 모드 딥 리셋)"""
    keys = [
        "adx_threshold", "adx_max", "chop_threshold", "volume_surge_multiplier",
        "fee_margin", "hard_stop_loss_rate", "trailing_stop_activation",
        "trailing_stop_rate", "cooldown_losses_trigger", "cooldown_duration_sec",
        "risk_per_trade", "leverage",
        "disparity_threshold",
        "bypass_macro", "bypass_disparity", "bypass_indicator",
        "min_take_profit_rate",
        "direction_mode",
        "exit_only_mode",
        "shadow_hunting_enabled",
    ]
    delete_configs(keys)
    _active_syms = get_config('symbols') or []
    _sym_cleaned = 0
    if isinstance(_active_syms, list):
        for _sym in _active_syms:
            _sym_cleaned += delete_symbol_configs(_sym)
    _g["strategy"] = TradingStrategy()
    _current_tf = str(get_config('timeframe') or '15m')
    _tf_reapplied = False
    if _current_tf in ALLOWED_TIMEFRAMES:
        await _apply_timeframe_presets(_current_tf)
        _tf_reapplied = True
    _reset_detail = f" (심볼별 설정 {_sym_cleaned}건 추가 청소)" if _sym_cleaned > 0 else ""
    _tf_detail = f" | ⏱️ {_current_tf} 프리셋 재적용" if _tf_reapplied else ""
    msg = f"[시스템] 사령관 명령 수신: 튜닝 데이터 삭제 및 AI 순정 모드(Tier 1) 딥 리셋 완료.{_reset_detail}{_tf_detail}"
    bot_global_state["logs"].append(msg)
    logger.info(msg)
    return {"success": True, "message": msg}
