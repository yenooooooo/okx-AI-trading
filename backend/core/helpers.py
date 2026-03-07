"""
core/helpers.py — 유틸리티 함수 모음 (트레이딩 루프 + 라우터 공용)
"""
import time as _time
import hmac
import hashlib
import base64

from database import get_config, set_config
from core.state import (
    bot_global_state, ai_brain_state,
    _trade_attempt_log, _TRADE_ATTEMPT_MAX,
    _loop_xray_state,
    _decision_trail_log, _DECISION_TRAIL_MAX, _ALL_PIPELINE_STEPS,
    _thought_throttle,
    PRESET_GATE_CONFIGS, _PRESET_PRIORITY,
)


# ════════════════════════════════════════════════════════════════════════════
# 심볼 헬퍼
# ════════════════════════════════════════════════════════════════════════════
def _sym_short(symbol: str) -> str:
    return symbol.split(':')[0]


# ════════════════════════════════════════════════════════════════════════════
# [X-Ray] 매매 시도 기록
# ════════════════════════════════════════════════════════════════════════════
def _log_trade_attempt(symbol: str, signal: str, result: str, reason: str = ""):
    """[X-Ray] 매매 시도 기록 — SUCCESS / BLOCKED / FAILED"""
    import datetime as _dt
    _kst = _dt.timezone(_dt.timedelta(hours=9))
    entry = {
        "timestamp": _dt.datetime.now(_kst).isoformat(),
        "symbol": symbol,
        "signal": signal,
        "result": result,
        "reason": reason,
    }
    _trade_attempt_log.append(entry)
    if len(_trade_attempt_log) > _TRADE_ATTEMPT_MAX:
        _trade_attempt_log.pop(0)
    _loop_xray_state["last_entry_attempt_time"] = _time.time()
    _loop_xray_state["last_entry_attempt_symbol"] = symbol
    _loop_xray_state["last_entry_attempt_result"] = result
    _loop_xray_state["last_entry_attempt_reason"] = reason
    if result == "SUCCESS":
        _loop_xray_state["last_successful_entry_time"] = _time.time()
        _loop_xray_state["last_successful_entry_symbol"] = symbol


# ════════════════════════════════════════════════════════════════════════════
# [Flight Recorder] Decision Trail
# ════════════════════════════════════════════════════════════════════════════
def _log_decision_trail(symbol: str, signal: str, result: str, pipeline: list):
    """[Flight Recorder] 진입 파이프라인 스냅샷 기록"""
    import datetime as _dt
    _kst = _dt.timezone(_dt.timedelta(hours=9))
    entry = {
        "timestamp": _dt.datetime.now(_kst).isoformat(),
        "symbol": symbol,
        "signal": signal,
        "result": result,
        "pipeline": pipeline,
    }
    _decision_trail_log.append(entry)
    if len(_decision_trail_log) > _DECISION_TRAIL_MAX:
        _decision_trail_log.pop(0)


def _finalize_pipeline(pipeline: list) -> list:
    """미도달 스텝을 SKIPPED로 채우고 정렬"""
    recorded = {p["step"] for p in pipeline}
    for step in _ALL_PIPELINE_STEPS:
        if step not in recorded:
            pipeline.append({"step": step, "status": "SKIPPED", "detail": ""})
    order = {s: i for i, s in enumerate(_ALL_PIPELINE_STEPS)}
    pipeline.sort(key=lambda p: order.get(p["step"], 99))
    return pipeline


# ════════════════════════════════════════════════════════════════════════════
# 포지션 상태 초기화
# ════════════════════════════════════════════════════════════════════════════
def _reset_position_state(sym_state: dict):
    """[Phase 32] 포지션 상태 통합 초기화 — 순수 상태 필드 초기화만 담당"""
    sym_state["position"] = "NONE"
    sym_state["entry_price"] = 0.0
    sym_state["last_exit_time"] = _time.time()
    sym_state["take_profit_price"] = "대기중"
    sym_state["stop_loss_price"] = 0.0
    sym_state["real_sl"] = 0.0
    sym_state["trailing_active"] = False
    sym_state["trailing_target"] = 0.0
    sym_state["partial_tp_executed"] = False
    sym_state["is_paper"] = False
    sym_state["entry_timestamp"] = 0.0
    sym_state["active_tp_order_id"] = None
    sym_state["active_sl_order_id"] = None
    sym_state["last_placed_tp_price"] = 0.0
    sym_state["last_placed_sl_price"] = 0.0
    sym_state["highest_price"] = 0.0
    sym_state["lowest_price"] = 0.0
    sym_state["unrealized_pnl_percent"] = 0.0
    sym_state["is_shadow_hunting"] = False
    sym_state["contracts"] = 0
    sym_state["leverage"] = 1
    sym_state["exchange_tp_filled"] = False
    sym_state["tp_order_amount"] = 0
    sym_state["breakeven_stop_active"] = False
    sym_state["_candle_lock_set_time"] = 0
    for _pk in ["pending_order_id", "pending_order_time", "pending_amount", "pending_price"]:
        sym_state.pop(_pk, None)


# ════════════════════════════════════════════════════════════════════════════
# [Consciousness Stream] 봇 사고 기록
# ════════════════════════════════════════════════════════════════════════════
def _emit_thought(symbol: str, msg: str, throttle_key: str = None, throttle_sec: float = 10.0):
    """봇 의식의 흐름 — ai_brain_state monologue에 실시간 사고 추가"""
    try:
        if throttle_key:
            _now = _time.time()
            _last = _thought_throttle.get(throttle_key, 0)
            if _now - _last < throttle_sec:
                return
            _thought_throttle[throttle_key] = _now
        _ml = ai_brain_state.get("symbols", {}).get(symbol, {}).get("monologue", [])
        _ml.append(msg)
        if len(_ml) > 50:
            _ml = _ml[-50:]
        if symbol in ai_brain_state.get("symbols", {}):
            ai_brain_state["symbols"][symbol]["monologue"] = _ml
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# [Phase 21.2] 스트레스 테스트 바이패스 24시간 만료 체크
# ════════════════════════════════════════════════════════════════════════════
def _is_bypass_active(feature_key: str) -> bool:
    val = get_config(feature_key)
    if not val or str(val) == "0":
        return False
    try:
        return (_time.time() - float(val)) < 86400
    except (ValueError, TypeError):
        return False


# ════════════════════════════════════════════════════════════════════════════
# 전략 방어 상태 영속화
# ════════════════════════════════════════════════════════════════════════════
def _save_strategy_state(s):
    """TradingStrategy 방어 상태(킬스위치+쿨다운)를 SQLite에 즉시 저장"""
    if s is None:
        return
    set_config("strategy_ks_active", "1" if s.kill_switch_active else "0")
    set_config("strategy_ks_until", str(s.kill_switch_until))
    set_config("strategy_cd_until", str(s.loss_cooldown_until))
    set_config("strategy_cd_count", str(s.consecutive_loss_count))


# ════════════════════════════════════════════════════════════════════════════
# OKX WebSocket 인증 서명 생성
# ════════════════════════════════════════════════════════════════════════════
def _generate_ws_sign(secret_key: str, timestamp: str) -> str:
    """HMAC-SHA256 Base64"""
    message = timestamp + "GET" + "/users/self/verify"
    mac = hmac.new(bytes(secret_key, 'utf-8'), bytes(message, 'utf-8'), digestmod=hashlib.sha256)
    return base64.b64encode(mac.digest()).decode('utf-8')


# ════════════════════════════════════════════════════════════════════════════
# [프리셋 추천] 적합도 채점
# ════════════════════════════════════════════════════════════════════════════
def _calc_preset_fitness(adx: float, chop: float, vol_ratio: float, macro_ok: bool, rsi: float, cfg: dict) -> int:
    """프리셋 1개에 대한 적합도 점수 계산 (max 8)"""
    score = 0
    if cfg['adx_min'] <= adx <= cfg['adx_max']:
        score += 2
    if chop < cfg['chop_max']:
        score += 2
    if vol_ratio >= cfg['vol_min']:
        score += 2
    if macro_ok or cfg.get('bypass_macro', False):
        score += 1
    if (30 <= rsi <= 70) or cfg.get('bypass_indicator', False):
        score += 1
    return score
