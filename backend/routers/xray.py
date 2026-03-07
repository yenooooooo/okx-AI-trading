"""
routers/xray.py — X-Ray 실시간 진단 엔드포인트
Routes: GET /xray/loop_state, /xray/blocker_wizard, /xray/trade_attempts,
        /xray/gate_scoreboard, /xray/okx_deep_verify
"""
import asyncio
import math
import time as _time
import datetime
from datetime import datetime as _dt

import pandas as pd
from fastapi import APIRouter

from database import get_config
from logger import get_logger
from strategy import TradingStrategy
from core.state import (
    bot_global_state, ai_brain_state, _g,
    _loop_xray_state, _trade_attempt_log, BALANCE_TIERS,
)

router = APIRouter()
logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/xray/loop_state
# ════════════════════════════════════════════════════════════════════════════
@router.get("/xray/loop_state")
async def xray_loop_state():
    """[X-Ray 1] 트레이딩 루프 내부 상태 실시간 스냅샷"""
    import datetime as _xdt
    _kst = _xdt.timezone(_xdt.timedelta(hours=9))
    _active_strategy = _g.get("strategy")

    def _fmt_ts(ts):
        if not ts or ts == 0:
            return "없음"
        return _xdt.datetime.fromtimestamp(ts, tz=_kst).strftime("%H:%M:%S KST")

    _ks_active = False
    _ks_remaining = ""
    _ks_daily_pnl = 0.0
    _ks_daily_pnl_pct = 0.0
    _ks_max_pct = 7.0
    _cd_active = False
    _cd_losses = 0
    _cd_trigger = 3
    _cd_remaining = ""
    if _active_strategy:
        _ks_max_pct = _active_strategy.daily_max_loss_pct * 100
        _ks_daily_pnl = _active_strategy.daily_pnl_accumulated
        if _active_strategy.daily_start_balance > 0:
            _ks_daily_pnl_pct = (_ks_daily_pnl / _active_strategy.daily_start_balance) * 100
        _ks_active = _active_strategy.kill_switch_active and _time.time() < _active_strategy.kill_switch_until
        if _ks_active:
            _rem_sec = max(0, _active_strategy.kill_switch_until - _time.time())
            _h, _m = int(_rem_sec // 3600), int((_rem_sec % 3600) // 60)
            _ks_remaining = f"{_h}시간 {_m}분 남음"
        _cd_losses = _active_strategy.consecutive_loss_count
        _cd_trigger = _active_strategy.cooldown_losses_trigger
        _cd_active = _time.time() < _active_strategy.loss_cooldown_until
        if _cd_active:
            _rem_cd = max(0, _active_strategy.loss_cooldown_until - _time.time())
            _cd_remaining = f"{int(_rem_cd // 60)}분 {int(_rem_cd % 60)}초 남음"

    _syms = []
    for _s, _sd in bot_global_state["symbols"].items():
        _brain = ai_brain_state.get("symbols", {}).get(_s, {})
        _syms.append({
            "symbol": _s,
            "symbol_short": _s.split("/")[0] if "/" in _s else _s,
            "direction_mode": str(get_config('direction_mode', _s) or 'AUTO').upper(),
            "exit_only": str(get_config('exit_only_mode', _s) or 'false').lower() == 'true',
            "position": _sd.get("position", "NONE"),
            "gates_passed": _brain.get("gates_passed", 0),
        })

    _trading_task = _g.get("trading_task")
    return {
        "is_running": bot_global_state["is_running"],
        "trading_task_alive": _trading_task is not None and not _trading_task.done() if _trading_task else False,
        "loop_cycle_count": _loop_xray_state["loop_cycle_count"],
        "kill_switch": {
            "active": _ks_active,
            "remaining_text": _ks_remaining,
            "daily_pnl": round(_ks_daily_pnl, 2),
            "daily_pnl_pct": round(_ks_daily_pnl_pct, 2),
            "daily_max_pct": round(_ks_max_pct, 1),
        },
        "cooldown": {
            "active": _cd_active,
            "consecutive_losses": _cd_losses,
            "trigger_threshold": _cd_trigger,
            "remaining_text": _cd_remaining,
        },
        "symbols": _syms,
        "active_symbols_count": len(_syms),
        "last_scan_time_text": _fmt_ts(_loop_xray_state["last_scan_time"]),
        "last_entry_attempt": {
            "time_text": _fmt_ts(_loop_xray_state["last_entry_attempt_time"]),
            "symbol": _loop_xray_state["last_entry_attempt_symbol"],
            "result": _loop_xray_state["last_entry_attempt_result"],
            "reason": _loop_xray_state["last_entry_attempt_reason"],
        },
        "last_successful_entry": {
            "time_text": _fmt_ts(_loop_xray_state["last_successful_entry_time"]),
            "symbol": _loop_xray_state["last_successful_entry_symbol"],
        },
        "timestamp": _xdt.datetime.now(_kst).isoformat(),
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/xray/blocker_wizard
# ════════════════════════════════════════════════════════════════════════════
@router.get("/xray/blocker_wizard")
async def xray_blocker_wizard():
    """[X-Ray 2] 매매 차단 원인 마법사 — 순차 검증, 첫 실패 시 중단"""
    import datetime as _xdt
    _kst = _xdt.timezone(_xdt.timedelta(hours=9))
    _active_strategy = _g.get("strategy")
    _engine = _g.get("engine")
    steps = []
    _stopped = None

    _is_on = bot_global_state["is_running"]
    steps.append({"step": 1, "name": "엔진 가동 상태", "pass": _is_on,
                  "detail": f"is_running = {_is_on}", "fix": "대시보드 상단 토글 버튼으로 봇을 시작하세요" if not _is_on else ""})
    if not _is_on:
        _stopped = 1

    if _stopped is None:
        _ks = False
        _ks_detail = "비활성"
        if _active_strategy and _active_strategy.kill_switch_active and _time.time() < _active_strategy.kill_switch_until:
            _ks = True
            _rem = max(0, _active_strategy.kill_switch_until - _time.time())
            _pnl_pct = 0
            if _active_strategy.daily_start_balance > 0:
                _pnl_pct = (_active_strategy.daily_pnl_accumulated / _active_strategy.daily_start_balance) * 100
            _ks_detail = f"발동 중 | 일일 손익: {_pnl_pct:.1f}% | {int(_rem//3600)}h {int((_rem%3600)//60)}m 후 해제"
        steps.append({"step": 2, "name": "킬스위치 (일일 손실 한도)", "pass": not _ks,
                      "detail": _ks_detail, "fix": "자정에 자동 해제됩니다. 또는 스트레스 바이패스로 임시 해제 가능" if _ks else ""})
        if _ks:
            _stopped = 2

    if _stopped is None:
        _cd = False
        _cd_detail = f"{_active_strategy.consecutive_loss_count if _active_strategy else 0}연패 / 트리거 {_active_strategy.cooldown_losses_trigger if _active_strategy else 3}"
        if _active_strategy and _time.time() < _active_strategy.loss_cooldown_until:
            _cd = True
            _rem_cd = max(0, _active_strategy.loss_cooldown_until - _time.time())
            _cd_detail = f"{_active_strategy.consecutive_loss_count}연패 쿨다운 중 | {int(_rem_cd//60)}분 {int(_rem_cd%60)}초 남음"
        steps.append({"step": 3, "name": "연패 쿨다운 (3연패 시 15분)", "pass": not _cd,
                      "detail": _cd_detail, "fix": "쿨다운 시간이 지나면 자동 해제됩니다" if _cd else ""})
        if _cd:
            _stopped = 3

    if _stopped is None:
        _brain_syms = ai_brain_state.get("symbols", {})
        _signal_syms = []
        _gate_summary = []
        for _bs, _bd in _brain_syms.items():
            _gp = _bd.get("gates_passed", 0)
            _gate_summary.append(f"{_bs.split('/')[0]}: {_gp}/6")
            if _gp >= 6:
                _signal_syms.append(_bs)
        _has_signal = len(_signal_syms) > 0
        _detail_txt = ", ".join(_gate_summary) if _gate_summary else "아직 분석된 심볼 없음"
        if _has_signal:
            _detail_txt = f"통과: {', '.join(s.split('/')[0] for s in _signal_syms)} | " + _detail_txt
        steps.append({"step": 4, "name": "진입 신호 존재 (6/6 게이트)", "pass": _has_signal,
                      "detail": _detail_txt, "fix": "모든 관문을 통과하는 시장 조건을 기다리세요" if not _has_signal else ""})
        if not _has_signal:
            _stopped = 4

    if _stopped is None:
        _dir_blocks = []
        for _ds in bot_global_state["symbols"]:
            _dm = str(get_config('direction_mode', _ds) or 'AUTO').upper()
            if _dm != 'AUTO':
                _dir_blocks.append(f"{_ds.split('/')[0]}: {_dm} 전용")
        _dir_ok = len(_dir_blocks) == 0
        steps.append({"step": 5, "name": "방향 모드 필터", "pass": True,
                      "detail": "전체 AUTO" if _dir_ok else "제한 설정: " + ", ".join(_dir_blocks),
                      "fix": "" if _dir_ok else "반대 방향 신호가 차단될 수 있습니다. 튜닝 패널에서 AUTO로 변경 가능"})

    if _stopped is None:
        _exit_syms = []
        for _es in bot_global_state["symbols"]:
            if str(get_config('exit_only_mode', _es) or 'false').lower() == 'true':
                _exit_syms.append(_es.split('/')[0])
        _exit_ok = len(_exit_syms) == 0
        steps.append({"step": 6, "name": "퇴근 모드 (Exit-Only)", "pass": _exit_ok,
                      "detail": "비활성" if _exit_ok else f"활성: {', '.join(_exit_syms)}",
                      "fix": "퇴근 모드 해제 후 신규 진입이 가능합니다" if not _exit_ok else ""})
        if not _exit_ok:
            _stopped = 6

    if _stopped is None:
        _open_pos = []
        for _ps, _pd in bot_global_state["symbols"].items():
            if _pd.get("position", "NONE") != "NONE":
                _open_pos.append(f"{_ps.split('/')[0]}: {_pd['position']}")
        _pos_ok = len(_open_pos) == 0
        steps.append({"step": 7, "name": "기존 포지션 보유 여부", "pass": _pos_ok,
                      "detail": "보유 포지션 없음" if _pos_ok else "보유 중: " + ", ".join(_open_pos),
                      "fix": "기존 포지션 청산 후 신규 진입 가능" if not _pos_ok else ""})
        if not _pos_ok:
            _stopped = 7

    if _stopped is None:
        _reentry_blocks = []
        for _rs, _rd in bot_global_state["symbols"].items():
            _le = _rd.get("last_exit_time", 0)
            if _le and _time.time() - _le < 60:
                _rem_r = int(60 - (_time.time() - _le))
                _reentry_blocks.append(f"{_rs.split('/')[0]}: {_rem_r}초 남음")
        _re_ok = len(_reentry_blocks) == 0
        steps.append({"step": 8, "name": "재진입 쿨다운 (60초)", "pass": _re_ok,
                      "detail": "대기 없음" if _re_ok else ", ".join(_reentry_blocks),
                      "fix": "60초 후 자동 해제" if not _re_ok else ""})
        if not _re_ok:
            _stopped = 8

    if _stopped is None:
        _bal = bot_global_state.get("balance", 0)
        _margin_ok = _bal > 5.0
        steps.append({"step": 9, "name": "증거금 (잔고) 충분 여부", "pass": _margin_ok,
                      "detail": f"가용 잔고: ${_bal:.2f} USDT" if _margin_ok else f"잔고 부족: ${_bal:.2f} USDT",
                      "fix": "USDT 입금 필요" if not _margin_ok else ""})
        if not _margin_ok:
            _stopped = 9

    if _stopped is None:
        _okx_ok = False
        _okx_detail = "엔진 미초기화"
        if _engine:
            try:
                _t0 = _time.time()
                _test_bal = await asyncio.to_thread(_engine.get_usdt_balance)
                _lat = int((_time.time() - _t0) * 1000)
                _okx_ok = True
                _okx_detail = f"연결 정상 (${_test_bal:.2f}, {_lat}ms)"
            except Exception as _okx_err:
                _okx_detail = f"연결 실패: {str(_okx_err)[:60]}"
        steps.append({"step": 10, "name": "OKX API 연결", "pass": _okx_ok,
                      "detail": _okx_detail, "fix": "API 키 확인 또는 네트워크 점검 필요" if not _okx_ok else ""})
        if not _okx_ok:
            _stopped = 10

    if _stopped is None:
        steps.append({"step": 11, "name": "전체 점검 완료", "pass": True,
                      "detail": "모든 조건 충족 — 다음 신호 대기 중", "fix": ""})

    _verdict_map = {
        1: "봇이 중지 상태입니다. 상단 토글로 시작하세요.",
        2: "킬스위치가 발동 중입니다. 일일 손실 한도를 초과했습니다.",
        3: "연패 쿨다운 중입니다. 잠시 후 자동 해제됩니다.",
        4: "진입 신호가 없습니다. 7게이트를 모두 통과하는 시장 조건을 기다리세요.",
        5: "방향 모드 제한으로 신호가 차단될 수 있습니다.",
        6: "퇴근 모드(Exit-Only)가 활성화되어 신규 진입이 차단됩니다.",
        7: "다른 심볼에 이미 포지션이 열려있어 신규 진입이 차단됩니다.",
        8: "최근 청산 후 60초 재진입 쿨다운 대기 중입니다.",
        9: "잔고가 부족하여 최소 1계약도 진입할 수 없습니다.",
        10: "OKX API에 연결할 수 없습니다.",
    }

    return {
        "steps": steps,
        "verdict": _verdict_map.get(_stopped, "모든 조건 충족 — 정상 대기 중"),
        "stopped_at_step": _stopped,
        "total_steps": 11,
        "all_clear": _stopped is None,
        "timestamp": _xdt.datetime.now(_kst).isoformat(),
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/xray/trade_attempts
# ════════════════════════════════════════════════════════════════════════════
@router.get("/xray/trade_attempts")
async def xray_trade_attempts():
    """[X-Ray 3] 매매 시도 이력 피드"""
    import datetime as _xdt
    _kst = _xdt.timezone(_xdt.timedelta(hours=9))

    _reason_kr = {
        "exit_only_mode": "퇴근 모드 (신규 진입 차단)",
        "reentry_cooldown_60s": "재진입 쿨다운 (60초 대기)",
        "other_position_open": "다른 심볼 포지션 보유 중",
        "kill_switch": "킬스위치 발동 (일일 손실 한도)",
        "margin_insufficient": "증거금 부족",
        "same_candle": "동일 캔들 중복 평가",
    }
    _color_map = {"SUCCESS": "emerald", "BLOCKED": "yellow", "FAILED": "red"}

    attempts = []
    for _a in reversed(_trade_attempt_log):
        _reason = _a.get("reason", "")
        _reason_text = _reason
        if _reason.startswith("direction_mode_"):
            _dm = _reason.replace("direction_mode_", "")
            _reason_text = f"방향 모드 차단 ({_dm} 전용)"
        elif _reason.startswith("shadow_hunting:"):
            _reason_text = f"그림자 사냥 실패: {_reason.replace('shadow_hunting: ', '')}"
        elif _reason in _reason_kr:
            _reason_text = _reason_kr[_reason]
        elif _a["result"] == "FAILED" and _reason:
            _reason_text = f"주문 실패: {_reason}"

        attempts.append({
            "timestamp": _a["timestamp"],
            "symbol": _a["symbol"],
            "symbol_short": _a["symbol"].split("/")[0] if "/" in _a["symbol"] else _a["symbol"],
            "signal": _a["signal"],
            "result": _a["result"],
            "reason": _a["reason"],
            "result_text": _reason_text if _a["result"] != "SUCCESS" else "진입 성공",
            "result_color": _color_map.get(_a["result"], "gray"),
        })

    _total = len(attempts)
    _success = sum(1 for a in attempts if a["result"] == "SUCCESS")
    _blocked = sum(1 for a in attempts if a["result"] == "BLOCKED")
    _failed = sum(1 for a in attempts if a["result"] == "FAILED")

    return {
        "attempts": attempts,
        "summary": {"total": _total, "success": _success, "blocked": _blocked, "failed": _failed},
        "timestamp": _xdt.datetime.now(_kst).isoformat(),
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/xray/gate_scoreboard
# ════════════════════════════════════════════════════════════════════════════
@router.get("/xray/gate_scoreboard")
async def xray_gate_scoreboard():
    """[X-Ray 4] 7게이트 라이브 스코어보드"""
    import datetime as _xdt
    _kst = _xdt.timezone(_xdt.timedelta(hours=9))

    _gate_labels = {
        "adx": "ADX 추세",
        "chop": "CHOP 횡보",
        "volume": "거래량",
        "disparity": "이격도",
        "macd_rsi": "MACD+RSI",
        "macro": "거시추세",
    }

    symbols = []
    for _s, _bd in ai_brain_state.get("symbols", {}).items():
        _gates_raw = _bd.get("gates", {})
        _gates = {}
        for _gk, _gv in _gates_raw.items():
            _gates[_gk] = {
                "pass": _gv.get("pass", False),
                "value": _gv.get("value", "N/A"),
                "target": _gv.get("target", ""),
                "label": _gate_labels.get(_gk, _gk),
            }
        symbols.append({
            "symbol": _s,
            "symbol_short": _s.split("/")[0] if "/" in _s else _s,
            "gates_passed": _bd.get("gates_passed", 0),
            "gates_total": 6,
            "gates": _gates,
            "decision": _bd.get("decision", "분석 대기 중"),
            "price": _bd.get("price", 0),
        })

    return {
        "symbols": symbols,
        "timestamp": _xdt.datetime.now(_kst).isoformat(),
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/xray/okx_deep_verify
# ════════════════════════════════════════════════════════════════════════════
@router.get("/xray/okx_deep_verify")
async def xray_okx_deep_verify():
    """[X-Ray 5] OKX API 딥 검증 — 실제 매매 가능 여부 확인"""
    import datetime as _xdt
    _kst = _xdt.timezone(_xdt.timedelta(hours=9))
    _engine = _g.get("engine")

    _api_status = {"connected": False, "latency_ms": 0, "balance": 0.0, "balance_text": "연결 불가"}
    _sym_results = []

    if _engine:
        try:
            _t0 = _time.time()
            _bal = await asyncio.to_thread(_engine.get_usdt_balance)
            _lat = int((_time.time() - _t0) * 1000)
            _api_status = {
                "connected": True,
                "latency_ms": _lat,
                "balance": round(_bal, 2),
                "balance_text": f"${_bal:.2f} USDT",
            }

            for _s in bot_global_state["symbols"]:
                try:
                    _mkt = _engine.exchange.market(_s)
                    _cs = float(_mkt.get('contractSize', 0.01))
                    _lev = max(1, int(get_config('leverage', _s) or 1))
                    _price = float(bot_global_state["symbols"][_s].get("current_price", 0))
                    if _price <= 0:
                        try:
                            _price = float(await asyncio.to_thread(_engine.get_current_price, _s))
                        except Exception:
                            _price = 0

                    _safe_bal = _bal * 0.95
                    _margin_per = (_cs * _price) / _lev if _price > 0 and _lev > 0 else 0
                    _max_contracts = int(_safe_bal / _margin_per) if _margin_per > 0 else 0
                    _feasible = _max_contracts >= 1

                    _sym_results.append({
                        "symbol": _s,
                        "symbol_short": _s.split("/")[0] if "/" in _s else _s,
                        "contract_size": _cs,
                        "current_price": round(_price, 2),
                        "leverage": _lev,
                        "margin_per_contract": round(_margin_per, 2),
                        "max_contracts": _max_contracts,
                        "min_contracts": 1,
                        "feasible": _feasible,
                        "feasible_text": f"매매 가능 (최대 {_max_contracts}계약)" if _feasible else "매매 불가 (증거금 부족)",
                    })
                except Exception as _sym_err:
                    _sym_results.append({
                        "symbol": _s,
                        "symbol_short": _s.split("/")[0] if "/" in _s else _s,
                        "contract_size": 0, "current_price": 0, "leverage": 0,
                        "margin_per_contract": 0, "max_contracts": 0, "min_contracts": 1,
                        "feasible": False, "feasible_text": f"조회 실패: {str(_sym_err)[:50]}",
                    })
        except Exception as _api_err:
            _api_status["balance_text"] = f"연결 실패: {str(_api_err)[:50]}"

    return {
        "api_status": _api_status,
        "symbols": _sym_results,
        "overall_feasible": all(s["feasible"] for s in _sym_results) if _sym_results else False,
        "timestamp": _xdt.datetime.now(_kst).isoformat(),
    }
