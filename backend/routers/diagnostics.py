"""
routers/diagnostics.py — 시스템 진단, 헬스체크, X-Ray 엔드포인트
Routes: GET /symbols, /logs, /system_health, /diagnostic, /health_check,
        /xray/loop_state, /xray/blocker_wizard, /xray/trade_attempts,
        /xray/gate_scoreboard, /xray/okx_deep_verify
"""
import asyncio
import math
import time as _time
import datetime
from datetime import datetime as _dt

import pandas as pd
from fastapi import APIRouter

from database import get_config, get_logs
from logger import get_logger
from strategy import TradingStrategy
from core.state import (
    bot_global_state, ai_brain_state, _g,
    _loop_xray_state, _trade_attempt_log, BALANCE_TIERS,
)

router = APIRouter()
logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/symbols
# ════════════════════════════════════════════════════════════════════════════
@router.get("/symbols")
async def fetch_symbols():
    """지원 심볼 목록"""
    symbols_config = get_config('symbols')
    if isinstance(symbols_config, list):
        return {"symbols": symbols_config}
    return {"symbols": ["BTC/USDT:USDT"]}


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/logs
# ════════════════════════════════════════════════════════════════════════════
@router.get("/logs")
async def fetch_system_logs(limit: int = 50, after_id: int = 0):
    """DB 저장 로그 조회. after_id 지정 시 해당 id 이후 신규 로그만 반환 (오름차순)."""
    logs = get_logs(limit=limit, after_id=after_id)
    if not logs:
        return []

    if after_id == 0:
        logs = list(reversed(logs))

    return [
        {
            "id": log.get("id"),
            "level": log.get("level", "INFO"),
            "message": log.get("message", ""),
            "created_at": log.get("created_at", "")
        }
        for log in logs
    ]


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/system_health
# ════════════════════════════════════════════════════════════════════════════
@router.get("/system_health")
async def fetch_system_health():
    """시스템 헬스 체크: OKX API, 텔레그램 실 통신, 엔진 상태 리턴"""
    _engine = _g.get("engine")
    okx_connected = False
    try:
        if _engine and _engine.exchange:
            _engine.exchange.fetch_balance()
            okx_connected = True
    except Exception:
        okx_connected = False

    from notifier import _telegram_app, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    telegram_connected = False
    telegram_bot_name = ""
    try:
        if _telegram_app and _telegram_app.bot:
            me = await _telegram_app.bot.get_me()
            telegram_connected = True
            telegram_bot_name = f"@{me.username}" if me else ""
        elif TELEGRAM_BOT_TOKEN:
            import httpx
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.json().get("ok"):
                    telegram_connected = True
                    telegram_bot_name = "@" + resp.json()["result"].get("username", "")
    except Exception as tg_err:
        logger.warning(f"Telegram 헬스 체크 실패: {tg_err}")
        telegram_connected = False

    strategy_running = bool(
        bot_global_state.get("is_running", False) and
        _g["trading_task"] is not None and
        not _g["trading_task"].done()
    )

    return {
        "okx_connected": okx_connected,
        "telegram_connected": telegram_connected,
        "telegram_bot_name": telegram_bot_name,
        "strategy_engine_running": strategy_running
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/diagnostic
# ════════════════════════════════════════════════════════════════════════════
@router.get("/diagnostic")
async def run_full_diagnostic():
    """전체 서브시스템 자가 진단 — 10개 항목 자동 점검 (읽기 전용, 상태 변경 없음)"""
    _engine = _g.get("engine")
    _active_strategy = _g.get("strategy")
    results = []

    # ── 1. OKX API 연결 ──
    try:
        if _engine and _engine.exchange:
            bal_data = await asyncio.to_thread(_engine.exchange.fetch_balance)
            usdt_total = float(bal_data.get('total', {}).get('USDT', 0))
            results.append({
                "id": "okx_connection", "name": "OKX API 연결",
                "status": "PASS", "message": f"잔고 조회 성공: ${usdt_total:.2f}",
                "details": {"usdt_total": usdt_total}
            })
        else:
            results.append({
                "id": "okx_connection", "name": "OKX API 연결",
                "status": "FAIL", "message": "OKXEngine 미초기화",
                "details": {}
            })
            usdt_total = 0
    except Exception as e:
        results.append({
            "id": "okx_connection", "name": "OKX API 연결",
            "status": "FAIL", "message": f"연결 실패: {str(e)[:80]}",
            "details": {}
        })
        usdt_total = 0

    # ── 2. 잔고 & 레버리지 → 매매 가능성 (심볼별) ──
    try:
        symbols_cfg = get_config('symbols') or ['BTC/USDT:USDT']
        _diag_manual = str(get_config('manual_override_enabled')).lower() == 'true'
        _leverage_key = 'manual_leverage' if _diag_manual else 'leverage'
        leverage_cfg_global = max(1, int(get_config(_leverage_key) or 1))
        _diag_shadow = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
        trade_feasibility = []
        all_feasible = True
        for sym in symbols_cfg:
            try:
                sym_leverage = max(1, int(get_config(_leverage_key, sym) or leverage_cfg_global))
                mkt = _engine.exchange.market(sym) if _engine and _engine.exchange else {}
                cs = float(mkt.get('contractSize', 0.01))
                px = float(bot_global_state["symbols"].get(sym, {}).get("current_price", 0))
                if px <= 0:
                    px = await asyncio.to_thread(_engine.get_current_price, sym) if _engine else 0
                margin_per = (cs * px) / sym_leverage if sym_leverage > 0 else float('inf')
                ok = usdt_total > margin_per and margin_per > 0
                if not ok:
                    all_feasible = False
                trade_feasibility.append({
                    "symbol": sym, "contractSize": cs, "price": round(px, 2),
                    "margin_per_contract": round(margin_per, 4), "feasible": ok,
                    "leverage": sym_leverage
                })
            except Exception:
                all_feasible = False
                trade_feasibility.append({"symbol": sym, "feasible": False, "error": "시장 데이터 조회 실패"})
        leverage_cfg = trade_feasibility[0].get('leverage', leverage_cfg_global) if trade_feasibility else leverage_cfg_global
        if all_feasible:
            _feas_status = "PASS"
        elif _diag_shadow:
            _feas_status = "WARN"
        else:
            _feas_status = "FAIL"
        _shadow_tag = " [👻 Shadow — Paper 모드라 실거래 미검증]" if _diag_shadow else ""
        results.append({
            "id": "trade_feasibility", "name": "매매 가능성 (잔고×레버리지)",
            "status": _feas_status,
            "message": f"전체 {len(symbols_cfg)}개 심볼 {'매매 가능' if all_feasible else '일부 증거금 부족'} ({'수동' if _diag_manual else '자동'}모드 레버리지 {leverage_cfg}x){_shadow_tag}",
            "details": {"mode": "manual" if _diag_manual else "auto", "shadow_mode": _diag_shadow, "leverage": leverage_cfg, "balance": round(usdt_total, 2), "symbols": trade_feasibility}
        })
    except Exception as e:
        results.append({
            "id": "trade_feasibility", "name": "매매 가능성 (잔고×레버리지)",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 3. Adaptive Shield 정합성 ──
    try:
        auto_preset_on = str(get_config('auto_preset_enabled') or 'false').lower() == 'true'
        db_tier = str(get_config('_current_adaptive_tier') or '')
        mem_tier = bot_global_state.get("adaptive_tier", "")
        expected_tier = ""
        if auto_preset_on and usdt_total > 0:
            for tn in ['CRITICAL', 'MICRO', 'STANDARD', 'GROWTH']:
                if usdt_total <= BALANCE_TIERS[tn]['max_balance']:
                    expected_tier = tn
                    break
        shield_ok = True
        shield_msg = ""
        if not auto_preset_on:
            shield_msg = "Adaptive Shield OFF — 수동 모드"
        elif expected_tier == db_tier == mem_tier:
            shield_msg = f"정합 OK: {expected_tier} (잔고 ${usdt_total:.2f})"
        else:
            shield_ok = False
            shield_msg = f"불일치 — 예상: {expected_tier}, DB: {db_tier}, 메모리: {mem_tier}"
        results.append({
            "id": "adaptive_shield", "name": "Adaptive Shield 정합성",
            "status": "PASS" if shield_ok or not auto_preset_on else "WARN",
            "message": shield_msg,
            "details": {"enabled": auto_preset_on, "expected": expected_tier, "db_tier": db_tier, "mem_tier": mem_tier}
        })
    except Exception as e:
        results.append({
            "id": "adaptive_shield", "name": "Adaptive Shield 정합성",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 4. TP/SL 공식 검증 ──
    try:
        test_sym = symbols_cfg[0] if symbols_cfg else 'BTC/USDT:USDT'
        test_price = float(bot_global_state["symbols"].get(test_sym, {}).get("current_price", 0))
        if test_price <= 0 and _engine:
            test_price = await asyncio.to_thread(_engine.get_current_price, test_sym) or 0
        sl_rate = float(get_config('hard_stop_loss_rate') or 0.005)
        fee_margin = float(get_config('fee_margin') or 0.0015)
        test_atr = test_price * 0.01
        try:
            tf = str(get_config('timeframe') or '15m')
            ohlcv = await asyncio.to_thread(_engine.exchange.fetch_ohlcv, test_sym, tf, None, 50)
            if ohlcv and len(ohlcv) >= 14:
                _df_diag = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                _tr = pd.concat([
                    _df_diag['high'] - _df_diag['low'],
                    (_df_diag['high'] - _df_diag['close'].shift()).abs(),
                    (_df_diag['low'] - _df_diag['close'].shift()).abs()
                ], axis=1).max(axis=1)
                test_atr = float(_tr.rolling(14).mean().iloc[-1])
                if pd.isna(test_atr):
                    test_atr = test_price * 0.01
        except Exception:
            pass
        tp_offset = (test_price * fee_margin) + (test_atr * 0.5)
        tp_long = round(test_price + tp_offset, 4)
        sl_long = round(test_price * (1 - sl_rate), 4)
        tp_valid = not math.isnan(tp_long) and not math.isinf(tp_long) and tp_long > test_price
        sl_valid = not math.isnan(sl_long) and not math.isinf(sl_long) and sl_long < test_price
        both_ok = tp_valid and sl_valid and test_price > 0
        results.append({
            "id": "tp_sl_formula", "name": "TP/SL 공식 검증",
            "status": "PASS" if both_ok else "FAIL",
            "message": f"LONG 기준 TP: ${tp_long:,.2f} / SL: ${sl_long:,.2f} (진입가 ${test_price:,.2f})" if test_price > 0 else "현재가 조회 불가",
            "details": {"symbol": test_sym, "entry": test_price, "atr": round(test_atr, 4),
                        "tp_long": tp_long, "sl_long": sl_long, "tp_valid": tp_valid, "sl_valid": sl_valid}
        })
    except Exception as e:
        results.append({
            "id": "tp_sl_formula", "name": "TP/SL 공식 검증",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 5. 포지션 사이징 시뮬레이션 ──
    try:
        risk_cfg = float(get_config('risk_per_trade') or 0.01)
        sizing_results = []
        all_sizing_ok = True
        _sizing_mode = "수동 USDT" if _diag_manual else "정률법"
        for sym in symbols_cfg:
            try:
                sym_leverage = max(1, int(get_config(_leverage_key, sym) or leverage_cfg_global))
                mkt = _engine.exchange.market(sym) if _engine and _engine.exchange else {}
                cs = float(mkt.get('contractSize', 0.01))
                px = float(bot_global_state["symbols"].get(sym, {}).get("current_price", 0))
                if px <= 0 and _engine:
                    px = await asyncio.to_thread(_engine.get_current_price, sym) or 0
                if _diag_manual:
                    seed_usdt = max(1.0, float(get_config('manual_amount') or 10))
                    notional = seed_usdt * sym_leverage
                    contracts = max(1, round(notional / (px * cs))) if px > 0 else 0
                else:
                    sim_strat = TradingStrategy(initial_seed=usdt_total)
                    contracts = sim_strat.calculate_position_size_dynamic(usdt_total, px, sym_leverage, cs, risk_cfg)
                margin_needed = (cs * px * contracts) / sym_leverage if sym_leverage > 0 else float('inf')
                ok = contracts >= 1 and margin_needed <= usdt_total * 0.95
                if not ok:
                    all_sizing_ok = False
                sizing_results.append({
                    "symbol": sym, "contracts": contracts, "contractSize": cs,
                    "margin_needed": round(margin_needed, 4), "feasible": ok,
                    "leverage": sym_leverage
                })
            except Exception:
                all_sizing_ok = False
                sizing_results.append({"symbol": sym, "contracts": 0, "feasible": False})
        if all_sizing_ok:
            _sz_status = "PASS"
        elif _diag_shadow:
            _sz_status = "WARN"
        else:
            _sz_status = "FAIL"
        _sz_shadow_tag = " [👻 Paper 모드 — 가상 체결]" if _diag_shadow else ""
        results.append({
            "id": "position_sizing", "name": "포지션 사이징 시뮬레이션",
            "status": _sz_status,
            "message": f"{_sizing_mode} | risk={risk_cfg}, leverage={leverage_cfg}x — {'전체 심볼 1계약 이상' if all_sizing_ok else '증거금 초과 (실전 시 주문 거부 가능)'}{_sz_shadow_tag}",
            "details": {"mode": _sizing_mode, "shadow_mode": _diag_shadow, "risk_per_trade": risk_cfg, "symbols": sizing_results}
        })
    except Exception as e:
        results.append({
            "id": "position_sizing", "name": "포지션 사이징 시뮬레이션",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 6. 관문 필터 현재 상태 ──
    try:
        gate_sym = symbols_cfg[0] if symbols_cfg else 'BTC/USDT:USDT'
        tf = str(get_config('timeframe') or '15m')
        ohlcv_gate = await asyncio.to_thread(_engine.exchange.fetch_ohlcv, gate_sym, tf, None, 50) if _engine and _engine.exchange else []
        if ohlcv_gate and len(ohlcv_gate) >= 20:
            _df_gate = pd.DataFrame(ohlcv_gate, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            _diag_strat = TradingStrategy()
            _df_gate = _diag_strat.calculate_indicators(_df_gate)
            _latest = _df_gate.iloc[-1]
            adx_val = float(_latest['adx']) if not pd.isna(_latest['adx']) else 0.0
            chop_val = float(_latest['chop']) if not pd.isna(_latest['chop']) else 50.0
            vol_val = float(_latest['volume'])
            vol_sma = float(_latest['vol_sma_20']) if not pd.isna(_latest['vol_sma_20']) else 0
            atr_val = float(_latest['atr']) if not pd.isna(_latest['atr']) else 0.0
            adx_th = float(get_config('adx_threshold') or 25.0)
            adx_mx = float(get_config('adx_max') or 40.0)
            chop_th = float(get_config('chop_threshold') or 61.8)
            vol_mult = float(get_config('volume_surge_multiplier') or 1.5)
            adx_pass = adx_th <= adx_val <= adx_mx
            chop_pass = chop_val < chop_th
            vol_pass = vol_val > (vol_sma * vol_mult) if vol_sma > 0 else True
            gate_count = sum([adx_pass, chop_pass, vol_pass])
            results.append({
                "id": "gate_filters", "name": "관문 필터 현재 상태",
                "status": "INFO",
                "message": f"{gate_sym} ({tf}) — 통과 {gate_count}/3 관문",
                "details": {
                    "symbol": gate_sym, "timeframe": tf,
                    "adx": {"value": round(adx_val, 1), "range": f"{adx_th}~{adx_mx}", "pass": adx_pass},
                    "chop": {"value": round(chop_val, 1), "threshold": chop_th, "pass": chop_pass},
                    "volume": {"current": round(vol_val, 1), "sma20": round(vol_sma, 1), "multiplier": vol_mult, "pass": vol_pass},
                    "atr": round(atr_val, 4)
                }
            })
        else:
            results.append({
                "id": "gate_filters", "name": "관문 필터 현재 상태",
                "status": "WARN", "message": "OHLCV 데이터 부족 (최소 20봉 필요)",
                "details": {}
            })
    except Exception as e:
        results.append({
            "id": "gate_filters", "name": "관문 필터 현재 상태",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 7. 쿨다운 & 킬스위치 상태 ──
    try:
        now_ts = _time.time()
        cooldown_active = False
        cooldown_msg = ""
        kill_msg = ""
        if _active_strategy:
            cd_until = getattr(_active_strategy, 'loss_cooldown_until', 0)
            if cd_until > now_ts:
                cooldown_active = True
                cooldown_msg = f"연패 쿨다운 활성 — {int(cd_until - now_ts)}초 남음 ({_active_strategy.consecutive_loss_count}연패)"
            ks_active = getattr(_active_strategy, 'kill_switch_active', False)
            ks_until = getattr(_active_strategy, 'kill_switch_until', 0)
            if ks_active and ks_until > now_ts:
                cooldown_active = True
                kill_msg = f"킬스위치 발동 — {(ks_until - now_ts) / 3600:.1f}시간 남음"
        status_cd = "WARN" if cooldown_active else "PASS"
        msg_cd = " | ".join(filter(None, [cooldown_msg, kill_msg])) or "비활성 (정상)"
        results.append({
            "id": "cooldown_killswitch", "name": "쿨다운 & 킬스위치",
            "status": status_cd, "message": msg_cd,
            "details": {
                "cooldown_active": bool(cooldown_msg),
                "kill_switch_active": bool(kill_msg),
                "consecutive_losses": getattr(_active_strategy, 'consecutive_loss_count', 0) if _active_strategy else 0
            }
        })
    except Exception as e:
        results.append({
            "id": "cooldown_killswitch", "name": "쿨다운 & 킬스위치",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 8. 텔레그램 연결 ──
    try:
        from notifier import _telegram_app, TELEGRAM_BOT_TOKEN
        tg_ok = False
        tg_name = ""
        if _telegram_app and _telegram_app.bot:
            me = await _telegram_app.bot.get_me()
            tg_ok = True
            tg_name = f"@{me.username}" if me else ""
        elif TELEGRAM_BOT_TOKEN:
            import httpx
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.json().get("ok"):
                    tg_ok = True
                    tg_name = "@" + resp.json()["result"].get("username", "")
        results.append({
            "id": "telegram", "name": "텔레그램 연결",
            "status": "PASS" if tg_ok else "FAIL",
            "message": f"봇 연결 성공: {tg_name}" if tg_ok else "텔레그램 미연결",
            "details": {"bot_name": tg_name}
        })
    except Exception as e:
        results.append({
            "id": "telegram", "name": "텔레그램 연결",
            "status": "FAIL", "message": f"연결 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 9. 설정 유효성 검사 ──
    try:
        validations = []
        cfg_checks = [
            ('risk_per_trade', 0.001, 0.1, "리스크 비율"),
            ('leverage', 1, 100, "레버리지"),
            ('hard_stop_loss_rate', 0.001, 0.1, "하드 손절율"),
            ('trailing_stop_activation', 0.001, 0.1, "트레일링 활성화"),
            ('trailing_stop_rate', 0.0005, 0.05, "트레일링 비율"),
            ('min_take_profit_rate', 0.001, 0.1, "최소 익절율"),
            ('daily_max_loss_rate', 0.01, 0.5, "일일 최대 손실율"),
            ('fee_margin', 0.0001, 0.01, "수수료 마진"),
        ]
        all_cfg_ok = True
        for key, lo, hi, label in cfg_checks:
            val = get_config(key)
            try:
                fval = float(val)
                ok = lo <= fval <= hi
            except (TypeError, ValueError):
                fval = None
                ok = False
            if not ok:
                all_cfg_ok = False
            validations.append({"key": key, "label": label, "value": fval, "range": f"{lo}~{hi}", "valid": ok})
        results.append({
            "id": "config_validation", "name": "설정 유효성 검사",
            "status": "PASS" if all_cfg_ok else "FAIL",
            "message": f"전체 {len(cfg_checks)}개 항목 {'정상' if all_cfg_ok else '일부 범위 이탈'}",
            "details": {"checks": validations}
        })
    except Exception as e:
        results.append({
            "id": "config_validation", "name": "설정 유효성 검사",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 10. 고아 주문 감지 ──
    try:
        orphans = []
        for sym, sym_st in bot_global_state.get("symbols", {}).items():
            pos = sym_st.get("position", "NONE")
            tp_id = sym_st.get("active_tp_order_id")
            sl_id = sym_st.get("active_sl_order_id")
            if pos == "NONE" and (tp_id or sl_id):
                orphans.append({"symbol": sym, "tp_order_id": tp_id, "sl_order_id": sl_id})
        results.append({
            "id": "orphan_orders", "name": "고아 주문 감지",
            "status": "PASS" if not orphans else "WARN",
            "message": "고아 주문 없음" if not orphans else f"고아 주문 {len(orphans)}건 감지",
            "details": {"orphans": orphans}
        })
    except Exception as e:
        results.append({
            "id": "orphan_orders", "name": "고아 주문 감지",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    pass_count = sum(1 for r in results if r["status"] == "PASS")
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    info_count = sum(1 for r in results if r["status"] == "INFO")

    return {
        "diagnostic": results,
        "summary": {"pass": pass_count, "fail": fail_count, "warn": warn_count, "info": info_count, "total": len(results)},
        "timestamp": _dt.now().isoformat()
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/health_check
# ════════════════════════════════════════════════════════════════════════════
@router.get("/health_check")
async def run_health_check():
    """[Phase 33] 프론트-백엔드-거래소 연결 상태 종합 점검 (읽기 전용)"""
    _engine = _g.get("engine")
    checks = []

    try:
        # ── Check 1: OKX REST API ──
        _t0 = _time.time()
        try:
            if _engine and _engine.exchange:
                await asyncio.to_thread(_engine.exchange.fetch_balance)
                _lat = round((_time.time() - _t0) * 1000, 1)
                checks.append({
                    "id": "okx_rest", "name": "OKX REST API",
                    "status": "OK", "latency_ms": _lat,
                    "details": f"fetch_balance 성공 ({_lat}ms)"
                })
            else:
                checks.append({
                    "id": "okx_rest", "name": "OKX REST API",
                    "status": "FAIL", "latency_ms": 0,
                    "details": "OKXEngine 미초기화"
                })
        except Exception as _e:
            _lat = round((_time.time() - _t0) * 1000, 1)
            checks.append({
                "id": "okx_rest", "name": "OKX REST API",
                "status": "FAIL", "latency_ms": _lat,
                "details": f"연결 실패: {str(_e)[:100]}"
            })

        # ── Check 2: OKX Private WebSocket ──
        try:
            _ws_alive = bool(_g["private_ws_task"] and not _g["private_ws_task"].done())
            checks.append({
                "id": "okx_private_ws", "name": "OKX Private WebSocket",
                "status": "OK" if _ws_alive else "WARN",
                "latency_ms": 0,
                "details": "positions 채널 수신 중" if _ws_alive else "Private WS 비활성 또는 재연결 대기"
            })
        except Exception as _e:
            checks.append({
                "id": "okx_private_ws", "name": "OKX Private WebSocket",
                "status": "WARN", "latency_ms": 0,
                "details": f"상태 확인 실패: {str(_e)[:100]}"
            })

        # ── Check 3: Telegram Bot ──
        _t0 = _time.time()
        try:
            from notifier import _telegram_app as _tg_app, TELEGRAM_BOT_TOKEN as _tg_token
            _tg_ok = False
            _tg_name = ""
            if _tg_app and _tg_app.bot:
                _me = await _tg_app.bot.get_me()
                _tg_ok = True
                _tg_name = f"@{_me.username}" if _me else ""
            elif _tg_token:
                import httpx
                _url = f"https://api.telegram.org/bot{_tg_token}/getMe"
                async with httpx.AsyncClient(timeout=5.0) as _client:
                    _resp = await _client.get(_url)
                    if _resp.status_code == 200 and _resp.json().get("ok"):
                        _tg_ok = True
                        _tg_name = "@" + _resp.json()["result"].get("username", "")
            _lat = round((_time.time() - _t0) * 1000, 1)
            checks.append({
                "id": "telegram", "name": "Telegram Bot",
                "status": "OK" if _tg_ok else "FAIL",
                "latency_ms": _lat,
                "details": f"{_tg_name} 응답 ({_lat}ms)" if _tg_ok else "텔레그램 미연결"
            })
        except Exception as _e:
            _lat = round((_time.time() - _t0) * 1000, 1)
            checks.append({
                "id": "telegram", "name": "Telegram Bot",
                "status": "FAIL", "latency_ms": _lat,
                "details": f"연결 실패: {str(_e)[:100]}"
            })
    except Exception as _top_err:
        logger.error(f"[Phase 33] health_check 초기화 구간 예외: {_top_err}")
        checks.append({
            "id": "init_error", "name": "초기화 오류",
            "status": "FAIL", "latency_ms": 0,
            "details": f"예외 발생: {str(_top_err)[:150]}"
        })

    # ── Check 4: SQLite Database ──
    _t0 = _time.time()
    try:
        from database import get_connection
        _conn = get_connection()
        _cur = _conn.cursor()
        _cur.execute("SELECT COUNT(*) FROM bot_config")
        _count = _cur.fetchone()[0]
        _conn.close()
        _lat = round((_time.time() - _t0) * 1000, 1)
        checks.append({
            "id": "database", "name": "SQLite Database",
            "status": "OK", "latency_ms": _lat,
            "details": f"bot_config {_count}개 키 ({_lat}ms)"
        })
    except Exception as _e:
        _lat = round((_time.time() - _t0) * 1000, 1)
        checks.append({
            "id": "database", "name": "SQLite Database",
            "status": "FAIL", "latency_ms": _lat,
            "details": f"DB 접근 실패: {str(_e)[:100]}"
        })

    # ── Check 5: Config Integrity ──
    try:
        _all_config = get_config()
        _expected_keys = {
            'symbols', 'risk_per_trade', 'hard_stop_loss_rate',
            'trailing_stop_activation', 'trailing_stop_rate', 'daily_max_loss_rate',
            'timeframe', 'leverage', 'telegram_enabled',
            'manual_override_enabled', 'manual_amount', 'manual_leverage',
            'ENTRY_ORDER_TYPE', 'adx_threshold', 'adx_max', 'chop_threshold',
            'volume_surge_multiplier', 'fee_margin',
            'cooldown_losses_trigger', 'cooldown_duration_sec',
            'auto_scan_enabled', 'direction_mode', 'exit_only_mode',
            'shadow_hunting_enabled', 'SHADOW_MODE_ENABLED',
            'min_take_profit_rate', 'auto_preset_enabled',
            '_current_adaptive_tier',
            'stress_bypass_kill_switch', 'stress_bypass_cooldown_loss',
            'stress_bypass_daily_loss', 'stress_bypass_reentry_cd',
            'stress_bypass_stale_price',
            'strategy_ks_active', 'strategy_ks_until',
            'strategy_cd_until', 'strategy_cd_count',
        }
        _actual_keys = set(_all_config.keys())
        _missing = _expected_keys - _actual_keys
        _extra = _actual_keys - _expected_keys
        if not _missing and not _extra:
            _cfg_status = "OK"
            _cfg_detail = f"전체 {len(_expected_keys)}개 키 정합"
        elif _missing:
            _cfg_status = "WARN"
            _cfg_detail = f"누락 키 {len(_missing)}개: {', '.join(sorted(_missing)[:5])}"
        else:
            _cfg_status = "OK"
            _cfg_detail = f"정합 OK (추가 키 {len(_extra)}개 존재)"
        checks.append({
            "id": "config_integrity", "name": "설정 키 정합성",
            "status": _cfg_status, "latency_ms": 0,
            "details": _cfg_detail
        })
    except Exception as _e:
        checks.append({
            "id": "config_integrity", "name": "설정 키 정합성",
            "status": "FAIL", "latency_ms": 0,
            "details": f"점검 실패: {str(_e)[:100]}"
        })

    # ── Check 6: Memory State ──
    try:
        _symbols_state = bot_global_state.get("symbols", {})
        _sym_count = len(_symbols_state)
        _zombies = []
        for _sym, _st in _symbols_state.items():
            _pos = _st.get("position", "NONE")
            _ep = _st.get("entry_price", 0)
            if _pos not in ("NONE",) and _ep == 0:
                _zombies.append(_sym)
        if _sym_count == 0:
            _mem_status = "WARN"
            _mem_detail = "심볼 미초기화 (엔진 미가동 상태)"
        elif _zombies:
            _mem_status = "WARN"
            _mem_detail = f"좀비 포지션 {len(_zombies)}건: {', '.join(_zombies)}"
        else:
            _active_count = sum(1 for _s in _symbols_state.values() if _s.get('position', 'NONE') != 'NONE')
            _mem_status = "OK"
            _mem_detail = f"{_sym_count}개 심볼 정상 (활성 포지션: {_active_count}개)"
        checks.append({
            "id": "memory_state", "name": "메모리 상태 (Global State)",
            "status": _mem_status, "latency_ms": 0,
            "details": _mem_detail
        })
    except Exception as _e:
        checks.append({
            "id": "memory_state", "name": "메모리 상태 (Global State)",
            "status": "FAIL", "latency_ms": 0,
            "details": f"점검 실패: {str(_e)[:100]}"
        })

    _endpoints = [
        {"method": "GET", "path": "/api/v1/status", "name": "봇 상태 조회"},
        {"method": "GET", "path": "/api/v1/brain", "name": "AI 뇌 상태"},
        {"method": "GET", "path": "/api/v1/config", "name": "설정 조회"},
        {"method": "GET", "path": "/api/v1/trades", "name": "거래 내역"},
        {"method": "GET", "path": "/api/v1/stats", "name": "성과 통계"},
        {"method": "GET", "path": "/api/v1/logs", "name": "시스템 로그"},
        {"method": "GET", "path": "/api/v1/symbols", "name": "심볼 목록"},
        {"method": "GET", "path": "/api/v1/system_health", "name": "시스템 헬스"},
        {"method": "GET", "path": "/api/v1/diagnostic", "name": "전체 진단"},
        {"method": "GET", "path": "/api/v1/ohlcv", "name": "차트 데이터"},
        {"method": "GET", "path": "/api/v1/stress_bypass", "name": "바이패스 현황"},
        {"method": "GET", "path": "/api/v1/history_stats", "name": "기간별 통계"},
        {"method": "GET", "path": "/api/v1/stats/advanced", "name": "심화 분석"},
        {"method": "GET", "path": "/api/v1/export_csv", "name": "CSV 내보내기"},
        {"method": "GET", "path": "/api/v1/health_check", "name": "연결 점검"},
        {"method": "POST", "path": "/api/v1/toggle", "name": "봇 시작/중지"},
        {"method": "POST", "path": "/api/v1/config", "name": "설정 변경"},
        {"method": "POST", "path": "/api/v1/test_order", "name": "테스트 주문"},
        {"method": "POST", "path": "/api/v1/close_paper", "name": "Paper 청산"},
        {"method": "POST", "path": "/api/v1/cancel_pending", "name": "매복 취소"},
        {"method": "POST", "path": "/api/v1/inject_stress", "name": "스트레스 주입"},
        {"method": "POST", "path": "/api/v1/reset_stress", "name": "스트레스 해제"},
        {"method": "POST", "path": "/api/v1/stress_bypass", "name": "바이패스 토글"},
        {"method": "POST", "path": "/api/v1/wipe_db", "name": "DB 초기화"},
        {"method": "POST", "path": "/api/v1/tuning/reset", "name": "튜닝 리셋"},
        {"method": "POST", "path": "/api/v1/backtest", "name": "백테스트"},
        {"method": "WEBSOCKET", "path": "/ws/dashboard", "name": "대시보드 WS"},
    ]

    _ok_cnt = sum(1 for _c in checks if _c["status"] == "OK")
    _fail_cnt = sum(1 for _c in checks if _c["status"] == "FAIL")
    _warn_cnt = sum(1 for _c in checks if _c["status"] == "WARN")

    return {
        "checks": checks,
        "endpoints": _endpoints,
        "summary": {"ok": _ok_cnt, "fail": _fail_cnt, "warn": _warn_cnt, "total": len(checks)},
        "timestamp": _dt.now().isoformat()
    }


