"""
core/trading_loop.py — 메인 트레이딩 루프 (다중 심볼 백그라운드)
  - async_trading_loop : 실전 매매 메인 루프
  _detect_and_handle_manual_close → core.close_handler
  execute_entry_order / _auto_tune_by_balance → core.entry_executor
"""
import asyncio
import time as _time

import pandas as pd

from database import get_config, set_config, save_trade
from okx_engine import OKXEngine
from strategy import TradingStrategy
from notifier import send_telegram_sync
from logger import get_logger
from core.state import (
    bot_global_state, ai_brain_state, _g, state_lock,
    _loop_xray_state, BALANCE_TIERS,
    PRESET_GATE_CONFIGS, PRESET_DISPLAY, _PRESET_PRIORITY, _scalp_fitness_alert_state,
)
from core.helpers import (
    _reset_position_state, _emit_thought, _is_bypass_active, _save_strategy_state,
    _log_trade_attempt, _log_decision_trail, _finalize_pipeline, _calc_preset_fitness,
    _sym_short,
)
from core.tg_formatters import (
    _TG_LINE, _tg_entry, _tg_pending, _tg_exit, _tg_manual_exit,
    _tg_scanner, _tg_volume_spike, _tg_circuit_breaker,
)
from core.close_handler import _detect_and_handle_manual_close
from core.entry_executor import execute_entry_order, _auto_tune_by_balance

logger = get_logger(__name__)
_last_valid_balance = 0.0  # [방어 필터] 마지막 정상 잔고 (API 오류 방어용)


async def async_trading_loop():
    """다중 심볼 백그라운드 매매 루프"""
    # [_g] strategy_instance 은 _g dict를 통해 wipe_db 와 공유됨

    engine_api = _g["engine"]  # 싱글톤 재사용 (load_markets 재호출 없음)
    strategy_instance = TradingStrategy(initial_seed=75.0)
    _g["strategy"] = strategy_instance  # wipe_db 엔드포인트가 인메모리 상태를 리셋할 수 있도록 등록
    # [v2.2] DB 설정에서 일일 최대 손실율 동기화 (UI에서 변경 가능)
    strategy_instance.daily_max_loss_pct = float(get_config('daily_max_loss_rate') or 0.07)
    # [방어 상태 복원] 서버 재시작 후 킬스위치·쿨다운 상태 복원 (만료된 상태는 자동 무시)
    strategy_instance.load_state(get_config)
    logger.info(f"[방어 상태 복원] KS={strategy_instance.kill_switch_active} | CD_until={strategy_instance.loss_cooldown_until:.0f} | losses={strategy_instance.consecutive_loss_count}")

    if not engine_api or not engine_api.exchange:
        logger.error("OKXEngine 미초기화 상태 - 매매 루프 중단")
        return

    bot_global_state["logs"].append("[봇] OKX 거래소 연결 확인 및 자동매매 대기 중...")
    logger.info("자동매매 루프 시작")
    last_log_time = 0
    last_scan_time = 0  # 스캐너 마지막 작동 시간
    last_spike_scan_time = 0  # [Volume Spike] 스파이크 감지 마지막 작동 시간 (3분 주기)
    _circuit_breaker_last_warn = {}  # 서킷 브레이커 로그 쓰로틀 (심볼별 마지막 경고 시각)
    # [Phase 20.3] 조용한 에러(Silent Failure) 추적용 카운터
    consecutive_errors = 0

    while bot_global_state["is_running"]:
        _loop_xray_state["loop_cycle_count"] += 1
        try:
            current_time = _time.time()

            # ── [HOTFIX: Split Brain 방지] 외부에서 뇌(전략)가 포맷되었는지 감시 ──
            if strategy_instance is not _g.get("strategy"):
                strategy_instance = _g["strategy"]
                logger.info("[엔진 딥 리셋] 매매 루프: 새로운 뇌(TradingStrategy) 이식 완료.")
                bot_global_state["logs"].append("🧠 [시스템] 엔진 코어 교체 감지: 새로운 AI 뇌로 실시간 교체 완료.")

            # ── [Phase 18.1] 전역 설정 동기화 (일일 최대 손실률만 루프 단위로 유지) ──
            try:
                strategy_instance.daily_max_loss_pct = float(get_config('daily_max_loss_rate') or 0.07)
            except Exception as _sync_err:
                logger.error(f"[설정 동기화 오류] {_sync_err}")

            # ── 15분 주기 다이내믹 볼륨 스캐너 가동 ──
            _loop_xray_state["last_scan_time"] = current_time
            if current_time - last_scan_time >= 900:
                if str(get_config('auto_scan_enabled')).lower() == 'true':
                    # 유령 포지션 방어: 보유 포지션이 있으면 타겟 변경 절대 금지
                    any_pos_open = any(
                        s.get("position", "NONE") != "NONE"
                        for s in bot_global_state["symbols"].values()
                    )
                    if any_pos_open:
                        last_scan_time = current_time  # 포지션 유지 중: 스캐너 가동 보류
                        # [Consciousness] 스캐너 보류 알림
                        for _cs_s in bot_global_state["symbols"]:
                            _emit_thought(_cs_s, "🔍 볼륨 스캐너: 포지션 보유 중 → 타겟 변경 보류", throttle_key=f"scan_hold_{_cs_s}", throttle_sec=60.0)
                    else:
                        try:
                            bot_global_state["logs"].append("[엔진] 다이내믹 볼륨 스캐너 가동: 24h 거래량 Top 3 탐색 중...")
                            # [Consciousness] 스캐너 가동 시작
                            for _cs_s in (get_config('symbols') or ['BTC/USDT:USDT']):
                                _emit_thought(_cs_s, "🔍 볼륨 스캐너 가동 중... 전체 OKX 마켓 24h 거래량 Top 3 탐색")
                            await asyncio.sleep(0.5)  # [Phase 4] API Rate Limit 보호용 미세 비동기 지연
                            top_symbols = await engine_api.scan_top_volume_coins(limit=3)
                            if top_symbols:
                                _cs_short_syms = [s.split('/')[0] for s in top_symbols]
                                _old_syms_scan = get_config('symbols') or []
                                if isinstance(_old_syms_scan, list):
                                    _is_same = set(_old_syms_scan) == set(top_symbols)
                                else:
                                    _is_same = False

                                if _is_same:
                                    # 동일 심볼 → 변경 불필요, 결과만 피드백
                                    last_scan_time = current_time
                                    for _cs_s in top_symbols:
                                        _emit_thought(_cs_s, f"🔍 스캐너 완료: {_cs_short_syms} (현재와 동일 → 유지)")
                                else:
                                    # 심볼 변경 발생 → 로테이션 + 고아 청소
                                    if isinstance(_old_syms_scan, list):
                                        _removed_scan = set(_old_syms_scan) - set(top_symbols)
                                        for _rs_scan in _removed_scan:
                                            _del_scan = delete_symbol_configs(_rs_scan)
                                            if _del_scan > 0:
                                                logger.info(f"[Phase 30] 스캐너 로테이션: {_rs_scan} 고아 설정 {_del_scan}건 청소")
                                    # 설정에 바로 업데이트하여 영속화 및 프론트 반영
                                    set_config('symbols', top_symbols)
                                    _old_short = [s.split('/')[0] for s in (_old_syms_scan if isinstance(_old_syms_scan, list) else [])]
                                    scan_msg = f"✅ [스캐너 가동] 거래량 Top 3 타겟 변경: {_old_short} → {_cs_short_syms}"
                                    bot_global_state["logs"].append(scan_msg)
                                    logger.info(scan_msg)
                                    send_telegram_sync(_tg_scanner(top_symbols))
                                    last_scan_time = current_time
                                    # [Consciousness] 스캐너 결과 방출
                                    for _cs_s in top_symbols:
                                        _emit_thought(_cs_s, f"🔍 스캐너 결과: {_cs_short_syms} → 타겟 변경 완료!")

                                # [Margin Guard] 스캐너 전환 직후 즉시 레버리지/증거금 검증
                                # _margin_guard_bg_loop과 동일한 쿨다운 키 공유 → 중복 알림 방지
                                import math as _scan_mg_math
                                _scan_mg_bal = bot_global_state.get("balance", 0)
                                _scan_mg_safe = _scan_mg_bal * 0.50
                                for _scan_sym in top_symbols:
                                    try:
                                        _scan_mkt = engine_api.exchange.market(_scan_sym)
                                        _scan_cs = float(_scan_mkt.get('contractSize', 0.01))
                                        _scan_lev = max(1, int(get_config('leverage', _scan_sym) or 1))
                                        _scan_price = await asyncio.to_thread(engine_api.get_current_price, _scan_sym)
                                        if not _scan_price or _scan_price <= 0:
                                            continue
                                        # 추정 계약수 계산
                                        _scan_risk = float(get_config('risk_per_trade', _scan_sym) or 0.02)
                                        if _scan_risk >= 1.0:
                                            _scan_risk /= 100.0
                                        _scan_notional = _scan_mg_bal * _scan_risk * _scan_lev
                                        _scan_estimated = max(1, round((_scan_notional / _scan_price) / _scan_cs))
                                        _scan_margin_per = (_scan_cs * _scan_price) / _scan_lev
                                        _scan_max = int(_scan_mg_safe / _scan_margin_per) if _scan_margin_per > 0 else 0
                                        if _scan_max >= 1:
                                            _scan_estimated = min(_scan_estimated, _scan_max)
                                        _scan_margin_total = (_scan_cs * _scan_price * _scan_estimated) / _scan_lev
                                        _scan_feasible = _scan_mg_safe >= _scan_margin_total

                                        if not _scan_feasible:
                                            _scan_rec = min(100, _scan_mg_math.ceil((_scan_cs * _scan_price * _scan_estimated) / _scan_mg_safe)) if _scan_mg_safe > 0 else 100
                                            # [Consciousness] Margin Guard 경고
                                            _emit_thought(_scan_sym, f"⚠️ Margin Guard: {_scan_sym.split('/')[0]} 레버리지 {_scan_lev}x 부적합! 추천: {_scan_rec}x")
                                            # 쿨다운 키 공유: _margin_guard_bg_loop과 동일 → 60초 내 중복 알림 방지
                                            _scan_alert_key = f"_margin_guard_last_alert_{_scan_sym}"
                                            set_config(_scan_alert_key, str(_time.time()))
                                            try:
                                                send_telegram_sync(_tg_margin_guard(
                                                    _scan_sym, _scan_lev, _scan_rec, _scan_mg_bal, _scan_margin_total
                                                ))
                                            except Exception:
                                                pass
                                    except Exception as _scan_mg_err:
                                        logger.debug(f"[Scanner Margin Guard] {_scan_sym} 검증 스킵: {_scan_mg_err}")
                            else:
                                # top_symbols 빈 배열 → API 응답 이상
                                last_scan_time = current_time
                                for _cs_s in (get_config('symbols') or ['BTC/USDT:USDT']):
                                    _emit_thought(_cs_s, "🔍 스캐너 완료: OKX API 응답 없음 → 현재 타겟 유지")
                                logger.warning("[Scanner] scan_top_volume_coins 빈 결과 반환")
                        except Exception as scan_err:
                            err_msg = f"[오류] 스캐너 로직 실패: {scan_err}"
                            bot_global_state["logs"].append(err_msg)
                            logger.error(err_msg)
                else:
                    last_scan_time = current_time  # 비활성 상태: 타이머만 갱신, 스캔 스킵

            # ── [Volume Spike Detector] 거래량 폭발 감지 (3분 독립 주기) ──
            if current_time - last_spike_scan_time >= 180:
                last_spike_scan_time = current_time
                try:
                    _spike_threshold = float(get_config('spike_threshold') or 2.0)
                    _spike_min_vol = float(get_config('spike_min_volume') or 15_000_000)
                    spikes = await engine_api.detect_volume_spikes(
                        min_quote_volume=_spike_min_vol,
                        spike_multiplier=_spike_threshold
                    )
                    if spikes:
                        # [A] 항상: 텔레그램 알림 + 의식의 흐름
                        _spike_names = [s['base'] for s in spikes[:3]]
                        for _cs_s in (get_config('symbols') or ['BTC/USDT:USDT']):
                            _emit_thought(_cs_s, f"🔥 거래량 폭발 감지! {_spike_names} — 평소 대비 급등")
                        send_telegram_sync(_tg_volume_spike(spikes))
                        bot_global_state["logs"].append(f"🔥 [스파이크 감지] 거래량 폭발: {_spike_names}")

                        # [B] 자동 전환 ON 시: 1위 스파이크 코인으로 타겟 전환
                        if str(get_config('spike_auto_switch')).lower() == 'true':
                            # 포지션 보유 중이면 전환 금지 (기존 스캐너와 동일 방어)
                            _spike_any_pos = any(
                                s.get("position", "NONE") != "NONE"
                                for s in bot_global_state["symbols"].values()
                            )
                            if not _spike_any_pos:
                                best_spike = spikes[0]
                                current_symbols = get_config('symbols') or []
                                if not isinstance(current_symbols, list):
                                    current_symbols = ['BTC/USDT:USDT']
                                if best_spike['symbol'] not in current_symbols:
                                    new_symbols = list(current_symbols)
                                    if len(new_symbols) >= 3:
                                        new_symbols[2] = best_spike['symbol']  # 3번째 슬롯 교체
                                    else:
                                        new_symbols.append(best_spike['symbol'])
                                    set_config('symbols', new_symbols)
                                    _spike_msg = f"🔥 [스파이크 자동 전환] {best_spike['base']} ({best_spike['price_change_pct']:+.1f}%) → 타겟 슬롯 교체"
                                    bot_global_state["logs"].append(_spike_msg)
                                    send_telegram_sync(
                                        f"🔥 <b>ANTIGRAVITY</b>  |  스파이크 자동 전환\n"
                                        f"{_TG_LINE}\n"
                                        f"<b>{best_spike['base']}</b> → 타겟 자동 교체 ({best_spike['price_change_pct']:+.1f}%)\n"
                                        f"{_TG_LINE}"
                                    )
                                    for _cs_s in new_symbols:
                                        _emit_thought(_cs_s, f"🔥 스파이크 자동 전환! {best_spike['base']} ({best_spike['price_change_pct']:+.1f}%) → 3번 슬롯 교체")
                    else:
                        # 스파이크 없음 → 의식의 흐름에 정상 상태 표시
                        for _cs_s in (get_config('symbols') or ['BTC/USDT:USDT']):
                            _emit_thought(_cs_s, "🔥 스파이크 스캔 완료 — 현재 급등 코인 없음", throttle_key=f"spike_none_{_cs_s}", throttle_sec=180.0)
                except Exception as _spike_err:
                    logger.warning(f"[Spike Detector] 감지 실패: {_spike_err}")
                    bot_global_state["logs"].append(f"⚠️ [스파이크] 감지 오류: {str(_spike_err)[:80]}")

            # 잔고 실시간 연동
            curr_bal = await asyncio.to_thread(engine_api.get_usdt_balance)
            bot_global_state["balance"] = round(curr_bal, 2)

            # [Phase 25] Adaptive Shield: 잔고 기반 자동 방어 티어 전환
            await _auto_tune_by_balance(curr_bal)
            # [Consciousness] 잔고 + 티어 상태 방출
            _cs_tier = get_config('_current_adaptive_tier') or '—'
            for _cs_sym in (get_config('symbols') or ['BTC/USDT:USDT']):
                _emit_thought(_cs_sym, f"💰 잔고: ${curr_bal:.2f} USDT | 🛡️ 방어 티어: {_cs_tier}", throttle_key=f"bal_{_cs_sym}", throttle_sec=15.0)

            # 설정된 심볼 목록 로드
            symbols_config = get_config('symbols')
            if isinstance(symbols_config, list):
                symbols = symbols_config
            else:
                symbols = ['BTC/USDT:USDT']

            # any_position_open은 루프 내에서 심볼별로 재계산 (동적 플래그 제거)

            # ── 매 사이클: 거래소 실제 포지션 조회 (수동 청산 감지용) ────────
            try:
                _exch_pos       = await asyncio.to_thread(engine_api.get_open_positions)
                exchange_open_symbols = {p['symbol'] for p in _exch_pos}
            except Exception as _pos_err:
                logger.warning(f"거래소 포지션 조회 실패 (수동청산 감지 스킵): {_pos_err}")
                exchange_open_symbols = None

            # [v2.2] 일일 리셋 체크 (UTC 자정 기준)
            strategy_instance.check_daily_reset(curr_bal)

            # ── [Phase 3] 스트레스 주입기 수신 (Fire Drill) ──────────────────
            _stress_type = bot_global_state.get("stress_inject")
            if _stress_type:
                bot_global_state["stress_inject"] = None
                if _stress_type == "KILL_SWITCH":
                    fake_loss = -(curr_bal * 0.10)
                    strategy_instance.daily_pnl_accumulated = fake_loss
                    kill_triggered = strategy_instance.record_daily_pnl(0)
                    _save_strategy_state(strategy_instance)
                    drill_msg = f"🚨 [소방훈련: 킬스위치 강제 주입 완료] 가상 일일 손실: {fake_loss:+.2f} USDT | 발동: {'YES' if kill_triggered else 'NO'}"
                    bot_global_state["logs"].append(drill_msg)
                    logger.warning(drill_msg)
                    if kill_triggered:
                        send_telegram_sync(f"🚨 [소방훈련] 킬스위치 발동 확인\n가상 손실: {fake_loss:+.2f} USDT\n24시간 거래 중단")
                elif _stress_type == "LOSS_STREAK":
                    strategy_instance.consecutive_loss_count = strategy_instance.cooldown_losses_trigger - 1
                    strategy_instance.record_trade_result(True)
                    _save_strategy_state(strategy_instance)
                    import datetime as _dt_s
                    _cd_end = _dt_s.datetime.fromtimestamp(
                        strategy_instance.loss_cooldown_until,
                        tz=_dt_s.timezone(_dt_s.timedelta(hours=9))
                    ).strftime("%H:%M:%S")
                    drill_msg = f"🚨 [소방훈련: {strategy_instance.cooldown_losses_trigger}연패 쿨다운 강제 주입 완료] 15분간 진입 차단 | 해제: {_cd_end} KST"
                    bot_global_state["logs"].append(drill_msg)
                    logger.warning(drill_msg)
                    send_telegram_sync(f"🚨 [소방훈련] {strategy_instance.cooldown_losses_trigger}연패 쿨다운 발동\n15분 진입 차단 | 해제: {_cd_end} KST")
                elif _stress_type == "RESET":
                    strategy_instance.kill_switch_active = False
                    strategy_instance.kill_switch_until = 0
                    strategy_instance.daily_pnl_accumulated = 0.0
                    strategy_instance.consecutive_loss_count = 0
                    strategy_instance.loss_cooldown_until = 0
                    _save_strategy_state(strategy_instance)
                    reset_msg = "✅ [소방훈련 해제 완료] 킬스위치 OFF + 쿨다운 OFF + 일일 PnL 리셋"
                    bot_global_state["logs"].append(reset_msg)
                    logger.info(reset_msg)
                    send_telegram_sync("✅ [소방훈련 해제] 킬스위치·쿨다운 전부 해제 완료")

            # 각 심볼에 대해 거래 루프 실행
            for i, symbol in enumerate(symbols):
                # 멀티 타겟팅 API Rate Limit 우회를 위한 물리적 딜레이 추가
                if i > 0:
                    await asyncio.sleep(1)
                    # [Phase 31] 멀티심볼: 두 번째 심볼부터 잔고 재갱신 (stale balance 방지)
                    try:
                        curr_bal = await asyncio.to_thread(engine_api.get_usdt_balance)
                        bot_global_state["balance"] = round(curr_bal, 2)
                    except Exception:
                        pass  # 갱신 실패 시 이전 값 유지

                try:
                    # 심볼 상태 초기화
                    if symbol not in bot_global_state["symbols"]:
                        bot_global_state["symbols"][symbol] = {
                            "position": "NONE",
                            "entry_price": 0.0,
                            "current_price": 0.0,
                            "unrealized_pnl_percent": 0.0,
                            "take_profit_price": "대기중",  # [Phase 16] 문자열 통일
                            "stop_loss_price": 0.0,
                            "highest_price": 0.0,
                            "lowest_price": 0.0,
                            "real_sl": 0.0,
                            "trailing_active": False,
                            "trailing_target": 0.0,
                            "entry_timestamp": 0.0,  # [Race Condition Fix] 진입 시각 기록 — Grace Period 계산용
                            "last_price_update_time": _time.time(),  # [Phase 20.2] 데이터 신선도 추적용 타임스탬프
                            "last_exit_time": 0,
                            # [Phase 21.4] A.D.S 전략 영양실조 감시용 상태
                            "starvation_start_time": _time.time(),
                            "starvation_reasons": {},
                            "last_starvation_report": _time.time(),
                            "last_analyzed_candle_ts": 0,
                            "last_signal_candle_ts": 0,  # [Phase 24] 캔들 단위 진입 시그널 중복 방지
                            # [재진입 로직] 본전/소익 트레일링 후 같은 방향 1회 재진입 허용
                            "_reentry_eligible": False,
                            "_reentry_direction": "",
                            "_reentry_count": 0,
                            # [Phase 22.1] 동적 지정가 방어막(Dynamic Limit TP/SL) 기억 장치
                            "active_tp_order_id": None,
                            "active_sl_order_id": None,
                            "last_placed_tp_price": 0.0,
                            "last_placed_sl_price": 0.0,
                            # [Phase 23] 그림자 사냥 포지션 여부 추적
                            "is_shadow_hunting": False,
                            # [Phase 29] Shadow/Live 모드 플래그 초기값 명시
                            "is_paper": False,
                            # [Phase 32] 동적 생성 필드 초기값 명시 (상태 드리프트 방지)
                            "contracts": 0,
                            "leverage": 1,
                            "unrealized_pnl_percent": 0.0,
                            "partial_tp_executed": False,
                            "breakeven_stop_active": False,  # [Breakeven Stop] 래칫 플래그
                        }

                    # [Phase 18.1] 코인별 뇌 구조 독립 동기화 (심볼 전용 설정 우선, 없으면 GLOBAL Fallback)
                    try:
                        strategy_instance.adx_threshold = float(get_config('adx_threshold', symbol) or 25.0)
                        strategy_instance.adx_max = float(get_config('adx_max', symbol) or 40.0)
                        strategy_instance.chop_threshold = float(get_config('chop_threshold', symbol) or 61.8)
                        strategy_instance.volume_surge_multiplier = float(get_config('volume_surge_multiplier', symbol) or 1.5)
                        strategy_instance.fee_margin = float(get_config('fee_margin', symbol) or 0.0015)
                        strategy_instance.hard_stop_loss_rate = float(get_config('hard_stop_loss_rate', symbol) or 0.005)
                        strategy_instance.trailing_stop_activation = float(get_config('trailing_stop_activation', symbol) or 0.003)
                        strategy_instance.trailing_stop_rate = float(get_config('trailing_stop_rate', symbol) or 0.002)
                        strategy_instance.cooldown_losses_trigger = int(get_config('cooldown_losses_trigger', symbol) or 3)
                        strategy_instance.cooldown_duration_sec = int(get_config('cooldown_duration_sec', symbol) or 900)
                        _disp_th = get_config('disparity_threshold', symbol)
                        if _disp_th is not None: strategy_instance.disparity_threshold = float(_disp_th) / 100.0
                        _b_macro = get_config('bypass_macro', symbol)
                        if _b_macro is not None: strategy_instance.bypass_macro = (str(_b_macro).lower() == 'true')
                        _b_disp = get_config('bypass_disparity', symbol)
                        if _b_disp is not None: strategy_instance.bypass_disparity = (str(_b_disp).lower() == 'true')
                        _b_ind = get_config('bypass_indicator', symbol)
                        if _b_ind is not None: strategy_instance.bypass_indicator = (str(_b_ind).lower() == 'true')
                        # [Phase 24] 최소 익절 목표율 로드 (R:R 강제)
                        strategy_instance.min_take_profit_rate = float(get_config('min_take_profit_rate', symbol) or 0.01)
                        # [Phase 19] 퇴근 모드 로드
                        _exit_only = str(get_config('exit_only_mode', symbol)).lower() == 'true'
                    except Exception as _sym_sync_err:
                        logger.error(f"[{symbol}] 코인별 설정 동기화 오류: {_sym_sync_err}")

                    # [Phase 24] OHLCV 데이터 수집 — 타임프레임 DB 설정 가능화 (기본 15m)
                    _tf = str(get_config('timeframe', symbol) or '15m')
                    ohlcv = None
                    for _ohlcv_retry in range(3):
                        try:
                            ohlcv = await asyncio.to_thread(engine_api.exchange.fetch_ohlcv, symbol, _tf, limit=200)
                            break
                        except Exception as _ohlcv_err:
                            logger.warning(f"[{symbol}] OHLCV 수집 실패 ({_ohlcv_retry+1}/3): {_ohlcv_err}")
                            if _ohlcv_retry < 2:
                                await asyncio.sleep(2)
                    if not ohlcv:
                        _emit_thought(symbol, f"🚨 OHLCV 데이터 수집 3회 실패! 이번 사이클 스킵")
                        send_telegram_sync(f"⚠️ <b>{_sym_short(symbol)}</b> OHLCV 수집 3회 실패 — 사이클 스킵")
                        continue
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    current_price = await asyncio.to_thread(engine_api.get_current_price, symbol)

                    # [방어] current_price None/0 가드 — None이면 이번 사이클 스킵 (연쇄 TypeError 방지)
                    if not current_price or current_price <= 0:
                        _emit_thought(symbol, f"⚠️ 현재가 조회 실패 (None/0) — 이번 사이클 스킵", throttle_key=f"price_null_{symbol}", throttle_sec=30.0)
                        logger.warning(f"[{symbol}] get_current_price 반환값 None/0 — 사이클 스킵")
                        continue

                    bot_global_state["symbols"][symbol]["current_price"] = current_price
                    # [Consciousness] 데이터 수집 완료
                    _emit_thought(symbol, f"📊 {symbol.split('/')[0]} {_tf} 캔들 {len(df)}개 수집 | 현재가 ${current_price:,.2f}", throttle_key=f"ohlcv_{symbol}", throttle_sec=15.0)

                    # ── 수동 청산 감지: 내부엔 포지션이 있는데 거래소엔 없으면 외부 청산 ──
                    # [Shadow Mode] Paper 포지션은 거래소에 실제 포지션이 없으므로 감지 대상에서 제외
                    _sym_is_paper = bot_global_state["symbols"][symbol].get("is_paper", False)
                    # [Race Condition Fix] 진입 직후 OKX API 전파 지연(최대 10~15초)으로 인한
                    # false manual-close 무한 알람 루프 차단 — 진입 후 15초간 수동청산 감지 비활성화
                    _entry_ts = bot_global_state["symbols"][symbol].get("entry_timestamp", 0.0)
                    _grace_period_ok = (_time.time() - _entry_ts) > 15.0
                    if (exchange_open_symbols is not None
                            and not _sym_is_paper
                            and _grace_period_ok
                            and bot_global_state["symbols"][symbol]["position"] != "NONE"
                            and bot_global_state["symbols"][symbol]["entry_price"] > 0
                            and symbol not in exchange_open_symbols):
                        # [Consciousness] 수동 청산 감지
                        _emit_thought(symbol, f"👤 외부 수동 청산 감지! {symbol.split('/')[0]} 포지션이 거래소에서 사라짐 — 상태 동기화 중...")
                        await _detect_and_handle_manual_close(
                            engine_api, symbol, bot_global_state["symbols"][symbol]
                        )
                        continue  # 이번 사이클은 신규 진입 시도 없이 다음 심볼로

                    # 지표 계산
                    df = strategy_instance.calculate_indicators(df)

                    # ── [확정봉 기반 평가] 미완성 봉(마지막 행) 제외 → 봇 판단 = 화면 100% 일치 ──
                    df_confirmed = df.iloc[:-1]
                    if len(df_confirmed) < 50:
                        continue  # 지표 계산 최소치 미달
                    _confirmed_row = df_confirmed.iloc[-1]
                    _confirmed_ts = int(_confirmed_row['timestamp'])
                    candle_close = float(_confirmed_row['close'])

                    # 확정봉 기준 지표 읽기
                    latest_rsi = df_confirmed['rsi'].iloc[-1]
                    latest_macd = df_confirmed['macd'].iloc[-1]
                    latest_upper = df_confirmed['upper_band'].iloc[-1]
                    latest_lower = df_confirmed['lower_band'].iloc[-1]
                    latest_adx = df_confirmed['adx'].iloc[-1] if 'adx' in df_confirmed.columns else float('nan')

                    # ── [Phase 1] 거시적 추세(1h EMA200) 데이터 수집 (비동기, 캐시 적용) ──
                    macro_ema_200 = await strategy_instance.get_macro_ema_200(engine_api, symbol)

                    # [Phase 21.2] 스트레스 바이패스: check_entry_signal 호출 전 인메모리 상태 패치
                    # strategy.py 내부(line 154, 179)에서 직접 차단하므로 호출 직전에 무력화해야 실제 적용됨
                    if _is_bypass_active('stress_bypass_cooldown_loss'):
                        strategy_instance.loss_cooldown_until = 0
                    if _is_bypass_active('stress_bypass_daily_loss'):
                        strategy_instance.kill_switch_active = False
                        strategy_instance.kill_switch_until = 0

                    # ── 캔들 변화 감지: 새 봉 완성 시만 시그널 재평가 (봉 간 캐시 재사용) ──
                    _prev_confirmed = bot_global_state["symbols"][symbol].get("_last_confirmed_candle_ts", 0)
                    _new_candle = (_confirmed_ts != _prev_confirmed)

                    if _new_candle:
                        bot_global_state["symbols"][symbol]["_last_confirmed_candle_ts"] = _confirmed_ts
                        # [재진입 로직] 새 캔들 → 재진입 카운터/자격 초기화
                        bot_global_state["symbols"][symbol]["_reentry_count"] = 0
                        bot_global_state["symbols"][symbol]["_reentry_eligible"] = False
                        bot_global_state["symbols"][symbol]["_reentry_direction"] = ""
                        # [Consciousness] 새 캔들 확정
                        import datetime as _cs_dt
                        _cs_kst = _cs_dt.timezone(_cs_dt.timedelta(hours=9))
                        _cs_candle_t = _cs_dt.datetime.fromtimestamp(_confirmed_ts / 1000, tz=_cs_kst).strftime("%H:%M")
                        _emit_thought(symbol, f"🕯️ 새 {_tf} 캔들 확정! {_cs_candle_t}봉 종가 ${candle_close:,.2f} → 시그널 재평가 시작")
                        # 확정봉 기준 매매 시그널 평가 (candle_close = 확정봉 종가)
                        signal, analysis_msg, payload = strategy_instance.check_entry_signal(df_confirmed, candle_close, macro_ema_200)
                        # 캐시 저장 (봉 간 재사용)
                        bot_global_state["symbols"][symbol]["_cached_signal"] = signal
                        bot_global_state["symbols"][symbol]["_cached_analysis"] = analysis_msg
                        bot_global_state["symbols"][symbol]["_cached_payload"] = payload
                    else:
                        # 동일 봉: 캐시된 시그널 사용 (게이트 결과 불변)
                        signal = bot_global_state["symbols"][symbol].get("_cached_signal", "HOLD")
                        analysis_msg = bot_global_state["symbols"][symbol].get("_cached_analysis", "")
                        payload = bot_global_state["symbols"][symbol].get("_cached_payload", {})
                        # [Consciousness] 동일 봉 대기
                        _emit_thought(symbol, f"🔎 동일 봉 대기 중 — 캐시된 시그널({signal}) 재사용", throttle_key=f"same_candle_{symbol}", throttle_sec=15.0)

                    # ── [하이브리드 진입] 확정봉 HOLD → 라이브봉 실시간 재평가 ──
                    # ADX/CHOP = 14기간 평균이라 라이브봉에서도 확정봉과 거의 동일
                    # 거래량/RSI/MACD = 실시간 폭발을 즉시 포착
                    if not _new_candle and signal == "HOLD" and len(df) >= 50:
                        try:
                            # [거래량 시간비례 보정] 미완성 라이브봉 → 봉 전체 기간으로 프로젝션
                            _hybrid_df = df.copy()
                            _tf_sec_map = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
                            _tf_seconds = _tf_sec_map.get(_tf, 900)
                            _live_ts_ms = float(_hybrid_df['timestamp'].iloc[-1])
                            _elapsed_sec = (_time.time() * 1000 - _live_ts_ms) / 1000
                            _elapsed_sec = max(_elapsed_sec, 30.0)  # 최소 30초 (극초반 과대추정 방지)
                            _vol_proj_ratio = min(_tf_seconds / _elapsed_sec, 10.0)  # 최대 10배 캡
                            if _vol_proj_ratio > 1.0:
                                _vol_col_idx = _hybrid_df.columns.get_loc('volume')
                                _hybrid_df.iloc[-1, _vol_col_idx] = _hybrid_df['volume'].iloc[-1] * _vol_proj_ratio

                            _hybrid_signal, _hybrid_msg, _hybrid_payload = strategy_instance.check_entry_signal(_hybrid_df, current_price, macro_ema_200)
                            if _hybrid_signal in ("LONG", "SHORT"):
                                signal = _hybrid_signal
                                analysis_msg = f"[실시간] {_hybrid_msg}"
                                payload = _hybrid_payload
                                # 텔레그램 + 의식 로그: 동일 라이브봉에서 1회만 발송
                                _hybrid_live_ts = int(df['timestamp'].iloc[-1])
                                if bot_global_state["symbols"][symbol].get("_last_hybrid_alert_ts", 0) != _hybrid_live_ts:
                                    bot_global_state["symbols"][symbol]["_last_hybrid_alert_ts"] = _hybrid_live_ts
                                    _emit_thought(symbol, f"🎯 하이브리드 진입! 라이브봉 {_hybrid_signal} — 거래량 보정 {_vol_proj_ratio:.1f}x 적용")
                                    send_telegram_sync(
                                        f"🎯 <b>하이브리드 진입 감지</b>\n{_TG_LINE}\n"
                                        f"코인 │ <code>{_sym_short(symbol)}</code>\n"
                                        f"방향 │ <b>{_hybrid_signal}</b> (라이브봉 실시간)\n"
                                        f"사유 │ 확정봉 HOLD → 라이브봉 관문 충족\n"
                                        f"보정 │ 거래량 {_vol_proj_ratio:.1f}x 프로젝션 ({_elapsed_sec:.0f}초/{_tf_seconds}초)\n{_TG_LINE}\n"
                                        f"📌 확정봉 대기 없이 실시간 진입 시도"
                                    )
                        except Exception as _hybrid_err:
                            logger.debug(f"[Hybrid Entry] 라이브봉 재평가 오류 (메인 루프 보호): {_hybrid_err}")

                    # 뇌 상태 업데이트
                    if symbol not in ai_brain_state["symbols"]:
                        ai_brain_state["symbols"][symbol] = {}

                    ai_brain_state["symbols"][symbol].update({
                        "price": current_price,
                        "candle_close": candle_close,
                        "confirmed_candle_ts": _confirmed_ts,
                        "timeframe": _tf,
                        "rsi": round(latest_rsi, 2) if not pd.isna(latest_rsi) else 50.0,
                        "macd": round(latest_macd, 2) if not pd.isna(latest_macd) else 0.0,
                        "bb_upper": round(latest_upper, 2) if not pd.isna(latest_upper) else 0.0,
                        "bb_lower": round(latest_lower, 2) if not pd.isna(latest_lower) else 0.0,
                        "adx": round(latest_adx, 2) if not pd.isna(latest_adx) else 0.0,
                        "chop": round(float(_confirmed_row['chop']), 1) if 'chop' in _confirmed_row.index and not pd.isna(_confirmed_row['chop']) else 0.0,
                        "decision": analysis_msg,
                        "macro_ema_200": float(macro_ema_200) if macro_ema_200 is not None else None,
                    })

                    # ── [라이브 지표] 현재 캔들(미완성) 기준 실시간 수치 (표시 전용, 봇 판정에 사용 안 함) ──
                    _live_row = df.iloc[-1]
                    _live_adx   = round(float(_live_row['adx']),  1) if 'adx'  in _live_row.index and not pd.isna(_live_row['adx'])  else 0.0
                    _live_chop  = round(float(_live_row['chop']), 1) if 'chop' in _live_row.index and not pd.isna(_live_row['chop']) else 50.0
                    _live_rsi   = round(float(_live_row['rsi']),  1) if 'rsi'  in _live_row.index and not pd.isna(_live_row['rsi'])  else 50.0
                    _live_macd  = round(float(_live_row['macd']), 2) if 'macd' in _live_row.index and not pd.isna(_live_row['macd']) else 0.0
                    _live_msig  = round(float(_live_row['macd_signal']), 2) if 'macd_signal' in _live_row.index and not pd.isna(_live_row['macd_signal']) else 0.0
                    _live_vol   = float(_live_row['volume'])      if not pd.isna(_live_row['volume'])      else 0.0
                    _live_vsma  = float(_live_row['vol_sma_20'])  if 'vol_sma_20' in _live_row.index and not pd.isna(_live_row['vol_sma_20']) else 1.0
                    _live_ema20 = float(_live_row['ema_20'])      if 'ema_20' in _live_row.index and not pd.isna(_live_row['ema_20']) else (current_price or 1)
                    _live_vol_ratio = round(_live_vol / _live_vsma, 2) if _live_vsma > 0 else 0.0
                    _live_disparity = round(abs((current_price - _live_ema20) / _live_ema20) * 100, 2) if current_price and _live_ema20 else 0.0

                    # 게이지 % 계산 — 임계값 근접도 (0=경계, 100=안전)
                    _lg_adx_min = strategy_instance.adx_threshold
                    _lg_adx_max = strategy_instance.adx_max
                    _lg_chop_max = strategy_instance.chop_threshold
                    _lg_vol_mul = strategy_instance.volume_surge_multiplier
                    _g_adx  = max(0, min(100, int((_live_adx - _lg_adx_min) / max(1, _lg_adx_max - _lg_adx_min) * 100))) if _live_adx >= _lg_adx_min else max(0, int(_live_adx / max(1, _lg_adx_min) * 50))
                    _g_chop = max(0, min(100, int((_lg_chop_max - _live_chop) / max(1, _lg_chop_max) * 100)))
                    _g_vol  = max(0, min(100, int(_live_vol_ratio / max(0.01, _lg_vol_mul) * 100)))
                    _g_disp = max(0, min(100, int((0.8 - _live_disparity) / 0.8 * 100))) if _live_disparity < 0.8 else 0
                    _g_rsi  = 100 if 30 <= _live_rsi <= 70 else max(0, 100 - int(abs(_live_rsi - 50) * 2))
                    _g_macro = 0
                    if macro_ema_200 is not None and current_price:
                        _ema_diff_pct = (current_price - float(macro_ema_200)) / float(macro_ema_200) * 100
                        _g_macro = max(0, min(100, int(50 + _ema_diff_pct * 10)))

                    ai_brain_state["symbols"][symbol]["live_gates"] = {
                        "adx":       {"value": _live_adx,        "gauge": _g_adx},
                        "chop":      {"value": _live_chop,        "gauge": _g_chop},
                        "volume":    {"value": _live_vol_ratio,   "gauge": _g_vol},
                        "disparity": {"value": _live_disparity,   "gauge": _g_disp},
                        "macd_rsi":  {"value": _live_rsi,         "gauge": _g_rsi,  "macd": _live_macd, "macd_signal": _live_msig},
                        "macro":     {"value": round((current_price - float(macro_ema_200)) / float(macro_ema_200) * 100, 2) if macro_ema_200 and current_price else 0.0, "gauge": _g_macro},
                    }

                    # ── [확정봉 기반] 진입 관문 체크리스트 (새 봉: 지표 캐시 갱신 / 매 루프: 설정 반영 즉시 재판정) ──
                    if _new_candle:
                        import datetime as _dt
                        _row       = _confirmed_row
                        # 확정봉 지표 캐시 — 봉이 바뀔 때만 갱신 (지표값 불변)
                        # [v4.0] 이전 확정봉 MACD 값 — 히스토그램 모멘텀 계산용
                        _prev_confirmed = df_confirmed.iloc[-2] if len(df_confirmed) >= 2 else _row
                        _gate_cache = {
                            "rsi_v":     float(latest_rsi)  if not pd.isna(latest_rsi)  else 50.0,
                            "adx_v":     float(latest_adx)  if not pd.isna(latest_adx)  else 0.0,
                            "macd_v":    float(latest_macd) if not pd.isna(latest_macd) else 0.0,
                            "msig_v":    float(_row['macd_signal']) if 'macd_signal' in _row.index and not pd.isna(_row['macd_signal']) else 0.0,
                            "prev_macd_v": float(_prev_confirmed['macd']) if 'macd' in _prev_confirmed.index and not pd.isna(_prev_confirmed['macd']) else 0.0,
                            "prev_msig_v": float(_prev_confirmed['macd_signal']) if 'macd_signal' in _prev_confirmed.index and not pd.isna(_prev_confirmed['macd_signal']) else 0.0,
                            "chop_v":    float(_row['chop'])     if 'chop'     in _row.index and not pd.isna(_row['chop'])     else 50.0,
                            "vol_v":     float(_row['volume'])   if not pd.isna(_row['volume'])   else 0.0,
                            "vsma_v":    float(_row['vol_sma_20']) if 'vol_sma_20' in _row.index and not pd.isna(_row['vol_sma_20']) else 1.0,
                            "ema20_v":   float(_row['ema_20'])   if 'ema_20'   in _row.index and not pd.isna(_row['ema_20'])   else (candle_close or 1),
                            "candle_close": candle_close,
                            "macro_ema_200": float(macro_ema_200) if macro_ema_200 is not None else None,
                        }
                        bot_global_state["symbols"][symbol]["_gate_cache"] = _gate_cache

                    # [Fix] 매 루프: 캐시된 지표 + 최신 설정(bypass/threshold)으로 gates 즉시 재판정
                    # → bypass 변경 시 다음 봉 대기 없이 Entry Readiness 패널 즉시 갱신
                    _gc = bot_global_state["symbols"][symbol].get("_gate_cache")
                    if _gc:
                        _rsi_v    = _gc["rsi_v"]
                        _adx_v    = _gc["adx_v"]
                        _macd_v   = _gc["macd_v"]
                        _msig_v   = _gc["msig_v"]
                        _chop_v   = _gc["chop_v"]
                        _vol_v    = _gc["vol_v"]
                        _vsma_v   = _gc["vsma_v"]
                        _ema20_v  = _gc["ema20_v"]
                        _gc_close = _gc["candle_close"]
                        _gc_macro = _gc["macro_ema_200"]

                        _vol_ratio  = float(_vol_v / _vsma_v) if _vsma_v > 0 else 0.0
                        _disparity  = float(abs((_gc_close - _ema20_v) / _ema20_v) * 100) if _gc_close and _ema20_v else 0.0
                        # [v4.0] MACD 히스토그램 모멘텀 기반 M+R 판정 (strategy.py와 100% 동기화)
                        _hist_now   = _macd_v - _msig_v
                        _hist_prev  = _gc.get("prev_macd_v", 0.0) - _gc.get("prev_msig_v", 0.0)
                        _long_macd  = bool(_hist_now > _hist_prev)   # 히스토그램 상승 = 반등 모멘텀
                        _short_macd = bool(_hist_now < _hist_prev)   # 히스토그램 하락 = 하락 모멘텀
                        _long_rsi   = bool(30 <= _rsi_v <= 55)
                        _short_rsi  = bool(45 <= _rsi_v <= 70)
                        _mr_ok      = bool((_long_macd and _long_rsi) or (_short_macd and _short_rsi))
                        _macro_ok   = True
                        _macro_lbl  = "N/A"
                        if _gc_macro is not None and _gc_close is not None:
                            _macro_ok  = bool(_gc_close > _gc_macro)
                            _macro_lbl = "상승추세 ↑" if _macro_ok else "하락추세 ↓"

                        # 동적 임계값 — strategy_instance 에서 직접 바인딩 (최신 설정 반영)
                        _adx_min  = strategy_instance.adx_threshold
                        _adx_max  = strategy_instance.adx_max
                        _chop_max = strategy_instance.chop_threshold
                        _vol_mul  = strategy_instance.volume_surge_multiplier
                        _disp_th  = strategy_instance.disparity_threshold * 100  # 비율→% 변환

                        _bypass_disp = bool(strategy_instance.bypass_disparity)
                        _bypass_ind  = bool(strategy_instance.bypass_indicator)
                        _bypass_mac  = bool(strategy_instance.bypass_macro)
                        _gates = {
                            "adx":       {"pass": bool(_adx_min <= _adx_v <= _adx_max),           "value": f"{_adx_v:.1f}",                                                   "target": f"{_adx_min:.0f}~{_adx_max:.0f}"},
                            "chop":      {"pass": bool(_chop_v < _chop_max),                      "value": f"{_chop_v:.1f}",                                                  "target": f"< {_chop_max:.1f}"},
                            "volume":    {"pass": bool(_vol_ratio >= _vol_mul),                   "value": f"{_vol_ratio:.2f}x",                                              "target": f"≥ {_vol_mul:.1f}x"},
                            "disparity": {"pass": bool(_bypass_disp or _disparity < _disp_th),   "value": f"{_disparity:.2f}%" + (" [우회]" if _bypass_disp else ""),        "target": f"< {_disp_th:.1f}%"},
                            "macd_rsi":  {"pass": bool(_bypass_ind  or _mr_ok),                  "value": f"RSI {_rsi_v:.1f}"  + (" [우회]" if _bypass_ind  else ""),        "target": "모멘텀+구간"},
                            "macro":     {"pass": bool(_bypass_mac  or _macro_ok),               "value": _macro_lbl             + (" [우회]" if _bypass_mac  else ""),       "target": "EMA200"},
                        }
                        _passed = int(sum(1 for g in _gates.values() if g["pass"]))
                        ai_brain_state["symbols"][symbol]["gates"]        = _gates
                        ai_brain_state["symbols"][symbol]["gates_passed"] = _passed

                        # ── [Preset Fitness] 전체 프리셋 적합도 채점 및 최적 추천 ──
                        try:
                            _pf_scores = {}
                            for _pf_name, _pf_cfg in PRESET_GATE_CONFIGS.items():
                                _pf_scores[_pf_name] = _calc_preset_fitness(_adx_v, _chop_v, _vol_ratio, _macro_ok, _rsi_v, _pf_cfg)

                            # 최적 프리셋: 점수 내림차순 → 동점 시 우선순위(보수적) 순서
                            _pf_best_name = max(_pf_scores, key=lambda k: (_pf_scores[k], -_PRESET_PRIORITY.index(k)))
                            _pf_best_score = _pf_scores[_pf_best_name]
                            _pf_icon, _pf_label = PRESET_DISPLAY.get(_pf_best_name, ('❓', _pf_best_name))

                            # ai_brain_state에 저장 (프론트엔드 전달용)
                            ai_brain_state["symbols"][symbol]["preset_fitness"] = _pf_scores
                            ai_brain_state["symbols"][symbol]["recommended_preset"] = _pf_best_name
                            ai_brain_state["symbols"][symbol]["recommended_preset_score"] = _pf_best_score
                            ai_brain_state["symbols"][symbol]["recommended_preset_icon"] = _pf_icon
                            ai_brain_state["symbols"][symbol]["recommended_preset_label"] = _pf_label

                            # 하위호환: 기존 scalp_fitness 키 유지
                            ai_brain_state["symbols"][symbol]["scalp_fitness"] = _pf_scores.get('scalp_context', 0)
                            ai_brain_state["symbols"][symbol]["scalp_fitness_label"] = "스캘핑 적합" if _pf_scores.get('scalp_context', 0) >= 6 else "대기"

                            # ── TG 알림: 최적 프리셋 변경 시 (5분 쿨다운) ──
                            _sf_now = _time.time()
                            if symbol not in _scalp_fitness_alert_state:
                                _scalp_fitness_alert_state[symbol] = {"last_alert_time": 0, "last_preset": None}
                            _sf_st = _scalp_fitness_alert_state[symbol]

                            if _pf_best_score >= 6 and (
                                _sf_st.get("last_preset") != _pf_best_name
                                or (_sf_now - _sf_st["last_alert_time"] >= 300)
                            ):
                                _pf_sorted = sorted(_pf_scores.items(), key=lambda x: x[1], reverse=True)[:3]
                                _pf_detail = "\n".join(
                                    f"  {PRESET_DISPLAY.get(n, ('', n))[0]} {PRESET_DISPLAY.get(n, ('', n))[1]} │ <b>{s}/8</b>"
                                    for n, s in _pf_sorted
                                )
                                send_telegram_sync(
                                    f"📊 <b>프리셋 추천</b>\n{_TG_LINE}\n"
                                    f"코인 │ <code>{_sym_short(symbol)}</code>\n"
                                    f"최적 │ {_pf_icon} <b>{_pf_label}</b> ({_pf_best_score}/8)\n{_TG_LINE}\n"
                                    f"{_pf_detail}\n{_TG_LINE}\n"
                                    f"ADX {_adx_v:.1f} · CHOP {_chop_v:.1f} · VOL {_vol_ratio:.2f}x\n"
                                    f"RSI {_rsi_v:.1f} · 거시추세 {'일치' if _macro_ok else '불일치'}\n"
                                    f"📌 튜닝 패널에서 {_pf_icon} {_pf_label} 프리셋 적용 권장"
                                )
                                _sf_st["last_alert_time"] = _sf_now
                            _sf_st["last_preset"] = _pf_best_name if _pf_best_score >= 6 else None
                        except Exception as _pf_err:
                            logger.debug(f"[Preset Fitness] 채점 오류 (메인 루프 보호): {_pf_err}")

                    # 봇 혼잣말 — 확정봉 시각 기준 1회 생성 (새 봉에서만)
                    if _new_candle and _gc:
                        import datetime as _dt
                        _KST = _dt.timezone(_dt.timedelta(hours=9))
                        _candle_dt = _dt.datetime.fromtimestamp(_confirmed_ts / 1000, tz=_KST)
                        _ts = _candle_dt.strftime("%H:%M") + "봉"

                        if _exit_only and bot_global_state["symbols"][symbol]["position"] == "NONE":
                            _mono = f"[{_ts}] 🛏️ 퇴근 모드(Exit-Only) 가동 중 — 기존 포지션 관리만 수행하며 신규 진입을 차단합니다."
                        elif signal == "LONG":
                            _mono = f"[{_ts}] 🟢 LONG 진입 신호 포착! 6/6 관문 통과 — 주문 실행!"
                        elif signal == "SHORT":
                            _mono = f"[{_ts}] 🔴 SHORT 진입 신호 포착! 6/6 관문 통과 — 주문 실행!"
                        elif not (_adx_min <= _adx_v <= _adx_max):
                            if _adx_v < _adx_min:
                                _mono = f"[{_ts}] ADX {_adx_v:.1f} — 방향성 없는 시장이야. {_adx_min:.0f} 이상으로 올라올 때까지 기다리는 중... ({_passed}/6)"
                            else:
                                _mono = f"[{_ts}] ADX {_adx_v:.1f} — 추세 끝물이야. {_adx_max:.0f} 아래로 식을 때까지 관망 중... ({_passed}/6)"
                        elif _chop_v >= _chop_max:
                            _mono = f"[{_ts}] CHOP {_chop_v:.1f} — 횡보장이야, 톱니바퀴 구간. {_chop_max:.1f} 아래로 떨어질 때까지 쉬는 중... ({_passed}/6)"
                        elif (not _bypass_disp) and _disparity >= _disp_th:
                            _mono = f"[{_ts}] 이격도 {_disparity:.2f}% — 이미 너무 달렸어. EMA20에 붙을 때까지 기다리는 중... ({_passed}/6)"
                        elif _vol_ratio < _vol_mul:
                            _mono = f"[{_ts}] 거래량 {_vol_ratio:.2f}x — 아직 안 터졌어. {_vol_mul:.1f}x 이상 폭발 대기 중... ({_passed}/6)"
                        elif (not _bypass_mac) and not _macro_ok:
                            _mono = f"[{_ts}] EMA200 역방향({_macro_lbl}) — 큰 흐름 거슬러 들어가면 안 돼. 추세 전환 대기 중... ({_passed}/6)"
                        elif (not _bypass_ind) and not _mr_ok:
                            if _long_macd and not _long_rsi:
                                _mono = f"[{_ts}] 모멘텀↑ ✓, RSI {_rsi_v:.1f} — LONG 진입 구간(30~55)으로 내려올 때까지 대기... ({_passed}/6)"
                            elif _short_macd and not _short_rsi:
                                _mono = f"[{_ts}] 모멘텀↓ ✓, RSI {_rsi_v:.1f} — SHORT 진입 구간(45~70)으로 올라올 때까지 대기... ({_passed}/6)"
                            else:
                                _mono = f"[{_ts}] RSI {_rsi_v:.1f}, Hist {_hist_now:.4f}(prev {_hist_prev:.4f}) — 모멘텀 반전 대기 중... ({_passed}/6)"
                        else:
                            _mono = f"[{_ts}] {_passed}/6 조건 충족 — RSI {_rsi_v:.1f} / MACD {_macd_v:.4f} 타점 탐색 중..."

                        _ml = ai_brain_state["symbols"][symbol].get("monologue", [])
                        _ml.append(_mono)
                        if len(_ml) > 50:
                            _ml = _ml[-50:]
                        ai_brain_state["symbols"][symbol]["monologue"] = _ml

                    # ══════════ [Flight Recorder] Guard Wall — 진입 방벽 실시간 상태 ══════════
                    # 매 루프마다 10개 가드의 CLEAR/BLOCKING 상태를 ai_brain_state에 주입
                    # → /api/v1/brain 응답에 자동 포함, 프론트엔드 Guard Wall 패널에서 3초 갱신
                    _gw = {}
                    _gw_sym_state = bot_global_state["symbols"][symbol]

                    # 1. 캔들 잠금 (시간 기반 자동 해제 반영)
                    _gw_candle_ts = int(df['timestamp'].iloc[-1])
                    _gw_last_sig = _gw_sym_state.get("last_signal_candle_ts", 0)
                    _gw_lock_time = _gw_sym_state.get("_candle_lock_set_time", 0)
                    _gw_lock_elapsed = _time.time() - _gw_lock_time if _gw_lock_time > 0 else 999
                    _gw_lock_max = 300  # 5분
                    _gw_candle_locked = (_gw_last_sig == _gw_candle_ts) and (_gw_lock_elapsed < _gw_lock_max)
                    if _gw_candle_locked:
                        _gw_remain = int(_gw_lock_max - _gw_lock_elapsed)
                        _gw_candle_detail = f"잠금 중 (해제까지 {_gw_remain}초)"
                    elif _gw_last_sig == _gw_candle_ts:
                        _gw_candle_detail = "시간 만료 → 자동 해제"
                    else:
                        _gw_candle_detail = "대기 중"
                    # 재진입 자격이면 캔들 잠금 상태를 "재진입 대기"로 표시
                    _gw_reentry_eligible = _gw_sym_state.get("_reentry_eligible", False)
                    if _gw_candle_locked and _gw_reentry_eligible:
                        _gw_candle_detail = f"🔄 재진입 대기 (캔들 잠금 면제)"
                        _gw_candle_locked = False  # Guard Wall에서는 CLEAR로 표시
                    _gw["candle_lock"] = {
                        "status": "BLOCKING" if _gw_candle_locked else "CLEAR",
                        "detail": _gw_candle_detail
                    }

                    # 재진입 상태
                    _gw_reentry_dir = _gw_sym_state.get("_reentry_direction", "")
                    _gw["reentry"] = {
                        "status": "ACTIVE" if _gw_reentry_eligible else "CLEAR",
                        "detail": f"{_gw_reentry_dir} 재진입 대기 중" if _gw_reentry_eligible else "비활성"
                    }

                    # 2. 퇴근 모드
                    _gw["exit_only"] = {
                        "status": "BLOCKING" if _exit_only else "CLEAR",
                        "detail": "퇴근 모드 활성화" if _exit_only else "정상"
                    }

                    # 3. 재진입 쿨다운 (재진입: 30초 / 일반: 60초)
                    _gw_last_exit = _gw_sym_state.get("last_exit_time", 0)
                    _gw_cd_max = 30 if _gw_reentry_eligible else 60
                    _gw_cd_remain = max(0, _gw_cd_max - (_time.time() - _gw_last_exit)) if _gw_last_exit > 0 else 0
                    _gw_cd_bypass = _is_bypass_active('stress_bypass_reentry_cd')
                    _gw["reentry_cd"] = {
                        "status": "BLOCKING" if _gw_cd_remain > 0 and not _gw_cd_bypass else "CLEAR",
                        "detail": f"남은 {int(_gw_cd_remain)}초 ({'재진입' if _gw_reentry_eligible else '일반'})" if _gw_cd_remain > 0 and not _gw_cd_bypass else ("바이패스" if _gw_cd_bypass and _gw_cd_remain > 0 else "정상")
                    }

                    # 4. 타 포지션 보유
                    _gw_other = [(k, s.get("position", "NONE")) for k, s in bot_global_state["symbols"].items() if k != symbol and s.get("position", "NONE") != "NONE"]
                    _gw["other_position"] = {
                        "status": "BLOCKING" if _gw_other else "CLEAR",
                        "detail": f"{_gw_other[0][0].split('/')[0]} {_gw_other[0][1]}" if _gw_other else "없음"
                    }

                    # 5. 킬스위치 (일일 손실 한도)
                    _gw_ks = strategy_instance.kill_switch_active and _time.time() < strategy_instance.kill_switch_until
                    _gw_ks_bypass = _is_bypass_active('stress_bypass_daily_loss')
                    _gw["kill_switch"] = {
                        "status": "BLOCKING" if _gw_ks and not _gw_ks_bypass else "CLEAR",
                        "detail": "일일 손실 한도 발동" if _gw_ks and not _gw_ks_bypass else ("바이패스" if _gw_ks_bypass and _gw_ks else "정상")
                    }

                    # 6. 연패 쿨다운
                    _gw_lcd = _time.time() < strategy_instance.loss_cooldown_until
                    _gw_lcd_bypass = _is_bypass_active('stress_bypass_cooldown_loss')
                    _gw_lcd_remain = max(0, strategy_instance.loss_cooldown_until - _time.time())
                    _gw["loss_cooldown"] = {
                        "status": "BLOCKING" if _gw_lcd and not _gw_lcd_bypass else "CLEAR",
                        "detail": f"{strategy_instance.consecutive_loss_count}연패 ({int(_gw_lcd_remain)}초)" if _gw_lcd and not _gw_lcd_bypass else f"{strategy_instance.consecutive_loss_count}/{strategy_instance.cooldown_losses_trigger}연패"
                    }

                    # 7. 활성 타겟
                    _gw_sym_conf = get_config('symbols')
                    _gw_active_t = _gw_sym_conf[0] if isinstance(_gw_sym_conf, list) and _gw_sym_conf else None
                    _gw["active_target"] = {
                        "status": "BLOCKING" if _gw_active_t and symbol != _gw_active_t else "CLEAR",
                        "detail": f"타겟: {_gw_active_t}" if _gw_active_t and symbol != _gw_active_t else "본 심볼 활성"
                    }

                    # 8. 방향 모드
                    _gw_dir = str(get_config('direction_mode', symbol) or 'AUTO').upper()
                    _gw["direction_mode"] = {
                        "status": "N/A" if _gw_dir == "AUTO" else "CLEAR",
                        "detail": f"{_gw_dir} 전용" if _gw_dir != "AUTO" else "양방향"
                    }

                    # 9. 소액 계좌 방어 (micro_account_protection)
                    _gw_lev = max(1, int(get_config('leverage', symbol) or 1))
                    try:
                        _gw_cs = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                    except Exception:
                        _gw_cs = 0.01
                    _gw_min_margin = (_gw_cs * current_price) / _gw_lev if current_price > 0 and _gw_lev > 0 else 0
                    _gw["micro_account"] = {
                        "status": "BLOCKING" if curr_bal > 0 and _gw_min_margin > curr_bal * 0.50 else "CLEAR",
                        "detail": f"1계약 ${_gw_min_margin:.2f} vs 50% ${curr_bal * 0.50:.2f}" if curr_bal > 0 and _gw_min_margin > 0 else "검증 대기"
                    }

                    # 10. 증거금 검증
                    _gw_risk = float(get_config('risk_per_trade', symbol) or 0.02)
                    if _gw_risk >= 1.0:
                        _gw_risk /= 100.0
                    _gw_notional = curr_bal * _gw_risk * _gw_lev
                    _gw_est_amt = max(1, round((_gw_notional / current_price) / _gw_cs)) if current_price > 0 and _gw_cs > 0 else 1
                    _gw_margin_need = (_gw_cs * current_price * _gw_est_amt) / _gw_lev if _gw_lev > 0 else 0
                    _gw["margin_check"] = {
                        "status": "BLOCKING" if curr_bal > 0 and curr_bal * 0.90 < _gw_margin_need else "CLEAR",
                        "detail": f"필요 ${_gw_margin_need:.2f} vs 가용 ${curr_bal * 0.90:.2f}" if curr_bal > 0 else "검증 대기"
                    }

                    ai_brain_state["symbols"][symbol]["entry_guards"] = _gw

                    # ══════════ [Flight Recorder] Config Snapshot — 백엔드 실제 사용 설정값 ══════════
                    ai_brain_state["symbols"][symbol]["active_config"] = {
                        "leverage": _gw_lev,
                        "risk_per_trade": round(_gw_risk * 100, 1),
                        "hard_stop_loss_rate": round(strategy_instance.hard_stop_loss_rate * 100, 2),
                        "trailing_stop_activation": round(strategy_instance.trailing_stop_activation * 100, 2),
                        "trailing_stop_rate": round(strategy_instance.trailing_stop_rate * 100, 2),
                        "adx_threshold": strategy_instance.adx_threshold,
                        "chop_threshold": strategy_instance.chop_threshold,
                        "volume_surge_multiplier": strategy_instance.volume_surge_multiplier,
                        "disparity_threshold": round(strategy_instance.disparity_threshold * 100, 1),
                        "direction_mode": _gw_dir,
                        "timeframe": _tf,
                    }

                    # [Phase 21.4] A.D.S 자가 면역 체계: 전략 영양실조(Starvation) 감시
                    if bot_global_state["symbols"][symbol]["position"] == "NONE":
                        _sym_st = bot_global_state["symbols"][symbol]
                        _cur_ts = int(df['timestamp'].iloc[-1])

                        # 1. 캔들(5분)당 1회만 거절 사유 수집 (동일 캔들 중복 카운트 방지)
                        if _sym_st.get("last_analyzed_candle_ts") != _cur_ts:
                            _sym_st["last_analyzed_candle_ts"] = _cur_ts
                            if signal == "HOLD" and "차단" in analysis_msg:
                                # "이격도 초과 차단 — EMA20..." -> "이격도 초과 차단" 추출
                                _reason_key = analysis_msg.split("차단")[0].strip() + " 차단"
                                _sym_st["starvation_reasons"] = _sym_st.get("starvation_reasons", {})
                                _sym_st["starvation_reasons"][_reason_key] = _sym_st["starvation_reasons"].get(_reason_key, 0) + 1

                        # 2. 12시간 주기 진단 리포트 발송
                        _now_s = _time.time()
                        if _now_s - _sym_st.get("last_starvation_report", _now_s) >= 43200:  # 12시간 = 43200초
                            _sym_st["last_starvation_report"] = _now_s

                            # 마지막 청산(또는 봇 시작)으로부터 경과된 시간 계산
                            _base_time = _sym_st["last_exit_time"] if _sym_st.get("last_exit_time", 0) > 0 else _sym_st.get("starvation_start_time", _now_s)
                            _hours_starved = (_now_s - _base_time) / 3600

                            if _hours_starved >= 11.5:  # 포지션 없이 대략 12시간 경과 시
                                _reasons_dict = _sym_st.get("starvation_reasons", {})
                                if _reasons_dict:
                                    _top_reason = max(_reasons_dict, key=_reasons_dict.get)
                                    _total_blocks = sum(_reasons_dict.values())
                                    _starv_msg = (
                                        f"🩻 [A.D.S 진단] [{symbol}] ⚠️ 전략 영양실조 상태 감지 | "
                                        f"최근 {_hours_starved:.1f}시간 동안 진입 0건 | "
                                        f"총 {_total_blocks}번의 유효 신호가 방어막에 막힘 (주요 원인: '{_top_reason}') | "
                                        f"💡 [AI 권장]: 튜닝 패널에서 '{_top_reason}' 관련 임계값을 완화해 보세요."
                                    )
                                    bot_global_state["logs"].append(_starv_msg)
                                    logger.warning(_starv_msg)

                                    _tg_starv = (
                                        f"🩻 <b>A.D.S 진단 보고서</b>\n"
                                        f"{_TG_LINE}\n"
                                        f"⚠️ <b>전략 영양실조 감지</b>  ·  <code>{_sym_short(symbol)}</code>\n"
                                        f"{_TG_LINE}\n"
                                        f"경과 시간 │  <code>{_hours_starved:.1f} 시간째 관망 중</code>\n"
                                        f"놓친 타점 │  <code>{_total_blocks} 회</code>\n"
                                        f"주요 원인 │  <b>{_top_reason}</b>\n"
                                        f"{_TG_LINE}\n"
                                        f"💡 <b>AI 권장 조치</b>\n"
                                        f"대시보드 튜닝 패널에서 해당 조건의 임계값을 약간 완화하여 진입 확률을 높이십시오."
                                    )
                                    send_telegram_sync(_tg_starv)

                                # 리포트 발송 후 통계 리셋 (다음 12시간을 위해)
                                _sym_st["starvation_reasons"] = {}

                    # 포지션 상태 체크 및 리스크 관리
                    if bot_global_state["symbols"][symbol]["position"] != "NONE":
                        entry = bot_global_state["symbols"][symbol]["entry_price"]
                        position_side = bot_global_state["symbols"][symbol]["position"]
                        # [Consciousness] 포지션 모니터링
                        if position_side in ("LONG", "SHORT") and entry > 0:
                            _cs_pnl = ((current_price - entry) / entry * 100) if position_side == "LONG" else ((entry - current_price) / entry * 100)
                            _cs_lev = bot_global_state["symbols"][symbol].get("leverage", 1)
                            _cs_pnl_lev = _cs_pnl * _cs_lev
                            _emit_thought(symbol, f"👁️ {position_side} 감시 중 | 진입 ${entry:,.2f} → 현재 ${current_price:,.2f} | PnL {_cs_pnl_lev:+.2f}% ({_cs_lev}x)", throttle_key=f"pos_mon_{symbol}", throttle_sec=10.0)

                        # [BUGFIX] PENDING 상태에서는 entry_price=0이므로 entry > 0 가드를 우회하여 타임아웃 체크 도달 보장
                        if (entry > 0 or position_side in ["PENDING_LONG", "PENDING_SHORT"]) and current_price:
                            # 레버리지 적용 (기본값 1)
                            leverage = bot_global_state["symbols"][symbol].get("leverage", 1)

                            if position_side == "LONG":
                                pnl = ((current_price - entry) / entry) * 100 * leverage
                                bot_global_state["symbols"][symbol]["highest_price"] = max(
                                    bot_global_state["symbols"][symbol].get("highest_price", current_price),
                                    current_price
                                )
                            elif position_side == "SHORT":
                                pnl = ((entry - current_price) / entry) * 100 * leverage
                                bot_global_state["symbols"][symbol]["lowest_price"] = min(
                                    bot_global_state["symbols"][symbol].get("lowest_price", current_price),
                                    current_price
                                )
                            else:
                                pnl = 0.0  # PENDING 상태: 미체결이므로 PnL 0

                            bot_global_state["symbols"][symbol]["unrealized_pnl_percent"] = round(pnl, 2)

                            # --- PENDING 상태(스마트 지정가)에서 체결 여부 및 시간 초과 확인 ---
                            if position_side in ["PENDING_LONG", "PENDING_SHORT"]:
                                pending_time = bot_global_state["symbols"][symbol].get("pending_order_time", 0)
                                pending_id = bot_global_state["symbols"][symbol].get("pending_order_id")
                                _is_paper_pending = bot_global_state["symbols"][symbol].get("is_paper", False)

                                order_status = {}

                                try:
                                    if _is_paper_pending:
                                        pending_target = bot_global_state["symbols"][symbol].get("pending_price", current_price)
                                        # [Phase 19.3] 3초 즉시 체결 버그 수정 -> 가격 도달 시에만 현실적 체결
                                        is_filled = False
                                        if position_side == "PENDING_LONG" and current_price <= pending_target:
                                            is_filled = True
                                        elif position_side == "PENDING_SHORT" and current_price >= pending_target:
                                            is_filled = True

                                        if is_filled:
                                            order_status = {'status': 'closed', 'average': pending_target, 'filled': 1}
                                        else:
                                            order_status = {'status': 'open', 'filled': 0}
                                    else:
                                        # [실전 모드] OKX에서 실제 주문 상태 조회
                                        order_status = await asyncio.to_thread(engine_api.exchange.fetch_order, pending_id, symbol)
                                        
                                    status = order_status.get('status')
                                    filled = order_status.get('filled', 0)
                                    
                                    if status == 'closed' or filled > 0:
                                        # 체결 성공 -> 실제 포지션으로 전환
                                        real_side = "LONG" if position_side == "PENDING_LONG" else "SHORT"
                                        executed_price = order_status.get('average') or order_status.get('price') or bot_global_state["symbols"][symbol]["pending_price"]
                                        # [Consciousness] 지정가 체결 완료
                                        _emit_thought(symbol, f"✅ 스마트 지정가 체결 완료! {real_side} 체결가 ${executed_price:,.2f}")
                                        trade_amount = bot_global_state["symbols"][symbol]["pending_amount"]
                                        trade_leverage = bot_global_state["symbols"][symbol].get("leverage", 1)
                                        
                                        bot_global_state["symbols"][symbol]["position"] = real_side
                                        bot_global_state["symbols"][symbol]["entry_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["highest_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["lowest_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["contracts"] = trade_amount  # 청산 시 재사용
                                        bot_global_state["symbols"][symbol]["partial_tp_executed"] = False  # [Partial TP] 진입 시 반드시 초기화
                                        bot_global_state["symbols"][symbol]["breakeven_stop_active"] = False  # [방어막] 이전 포지션 잔류 플래그 강제 초기화
                                        bot_global_state["symbols"][symbol]["entry_timestamp"] = _time.time()  # [Race Condition Fix]

                                        del bot_global_state["symbols"][symbol]["pending_order_id"]
                                        del bot_global_state["symbols"][symbol]["pending_order_time"]
                                        del bot_global_state["symbols"][symbol]["pending_amount"]
                                        del bot_global_state["symbols"][symbol]["pending_price"]
                                        
                                        _paper_tag = "[👻 PAPER] " if _is_paper_pending else ""
                                        entry_emoji = "🎯📈" if real_side == "LONG" else "🎯📉"
                                        entry_msg = f"{_paper_tag}{entry_emoji} [{symbol}] {real_side} 스마트 지정가 체결 완료! | 체결가: ${executed_price:.2f} | {trade_amount}계약"
                                        bot_global_state["logs"].append(entry_msg)
                                        logger.info(entry_msg)
                                        send_telegram_sync(_tg_entry(symbol, real_side, executed_price, trade_amount, trade_leverage, payload=None, is_test=_is_paper_pending))

                                        # [Phase 23] Shadow Hunting 체결 시 리스크 재계산
                                        if bot_global_state["symbols"][symbol].get("is_shadow_hunting", False):
                                            _sh_atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else float(executed_price * 0.01)
                                            _sh_new_sl, _sh_new_act = strategy_instance.recalculate_shadow_risk(executed_price, real_side, _sh_atr)
                                            strategy_instance.hard_stop_loss_rate = abs(_sh_new_sl - executed_price) / executed_price
                                            strategy_instance.trailing_stop_activation = abs(_sh_new_act - executed_price) / executed_price
                                            bot_global_state["symbols"][symbol]["is_shadow_hunting"] = False  # 재계산 완료, 플래그 리셋
                                            logger.info(f"[{symbol}] 🐸 Shadow Hunting 체결! 리스크 재계산 완료 | 신규 SL: {_sh_new_sl:.4f} | 트레일링 발동선: {_sh_new_act:.4f}")

                                        # ── [재진입 로직] Smart Limit 체결 시 자격 소멸 + 카운터 ──
                                        if bot_global_state["symbols"][symbol].get("_reentry_eligible", False):
                                            bot_global_state["symbols"][symbol]["_reentry_count"] = bot_global_state["symbols"][symbol].get("_reentry_count", 0) + 1
                                            bot_global_state["symbols"][symbol]["_reentry_eligible"] = False
                                            bot_global_state["symbols"][symbol]["_reentry_direction"] = ""
                                            logger.info(f"[{symbol}] 🔄 재진입(Smart Limit) 실행 완료! {real_side}")
                                            send_telegram_sync(
                                                f"🔄 <b>재진입 실행</b>\n{_TG_LINE}\n"
                                                f"코인 │ <code>{_sym_short(symbol)}</code>\n"
                                                f"방향 │ <b>{real_side}</b> (같은 방향 재진입)\n"
                                                f"체결가 │ ${executed_price:,.2f}\n"
                                                f"사유 │ 본전/소익 트레일링 후 추세 지속\n{_TG_LINE}\n"
                                                f"📌 재진입 1/1회 사용 (추가 재진입 차단)"
                                            )

                                        # [Phase 28] Smart Limit 체결 직후 거래소 초기 TP/SL 배치
                                        # Market 진입(Line 1820-1860)과 100% 동일한 방어막 로직
                                        if not _is_paper_pending:
                                            try:
                                                _entry_p_sl = float(executed_price)
                                                _amt_sl = float(trade_amount)
                                                _close_side_sl = "sell" if real_side == "LONG" else "buy"

                                                # TP: 수수료 0.15% + ATR * 50%
                                                _sl_atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else float(_entry_p_sl * 0.01)
                                                _sl_offset = (_entry_p_sl * 0.0015) + (_sl_atr * 0.5)
                                                _sl_init_tp = round(_entry_p_sl + _sl_offset, 4) if real_side == "LONG" else round(_entry_p_sl - _sl_offset, 4)

                                                # SL: hard_stop_loss_rate 직결
                                                _sl_rate_init = strategy_instance.hard_stop_loss_rate
                                                # [방어막] SL rate 최소 0.2% 보장
                                                if _sl_rate_init < 0.002:
                                                    logger.warning(f"[{symbol}] ⚠️ Smart Limit SL rate 이상({_sl_rate_init}) → 0.002로 교정")
                                                    _sl_rate_init = 0.002
                                                _sl_init_sl = round(_entry_p_sl * (1 - _sl_rate_init), 4) if real_side == "LONG" else round(_entry_p_sl * (1 + _sl_rate_init), 4)

                                                # [TP Split] 멀티계약: TP 50% / 1계약: TP 100%
                                                _full_int_sl = int(_amt_sl)
                                                if _full_int_sl > 1:
                                                    _tp_amt_sl = max(1, _full_int_sl // 2)
                                                else:
                                                    _tp_amt_sl = _full_int_sl  # 1계약 전량

                                                _params_tp_sl = {"reduceOnly": True}
                                                _params_sl_sl = {"reduceOnly": True, "stopLossPrice": _sl_init_sl}

                                                tp_order_sl = await asyncio.to_thread(
                                                    engine_api.exchange.create_order,
                                                    symbol, 'limit', _close_side_sl, _tp_amt_sl, _sl_init_tp, _params_tp_sl
                                                )
                                                sl_order_sl = await asyncio.to_thread(
                                                    engine_api.exchange.create_order,
                                                    symbol, 'market', _close_side_sl, _amt_sl, None, _params_sl_sl
                                                )

                                                bot_global_state["symbols"][symbol]["active_tp_order_id"] = tp_order_sl['id']
                                                bot_global_state["symbols"][symbol]["active_sl_order_id"] = sl_order_sl['id']
                                                bot_global_state["symbols"][symbol]["last_placed_tp_price"] = _sl_init_tp
                                                bot_global_state["symbols"][symbol]["last_placed_sl_price"] = _sl_init_sl
                                                bot_global_state["symbols"][symbol]["tp_order_amount"] = _tp_amt_sl

                                                _tp_pct_sl = "50%" if _full_int_sl > 1 else "100%"
                                                logger.info(f"[{symbol}] 🕸️ [Smart Limit 체결] 거래소 초기 방어막 전송 완료 | TP: {_sl_init_tp:.4f} ({_tp_pct_sl}, {_tp_amt_sl}계약) / SL: {_sl_init_sl:.4f}")
                                            except Exception as sl_init_err:
                                                logger.error(f"[{symbol}] 🚨 [Smart Limit 체결] 초기 방어막 전송 실패. 자가 치유가 다음 사이클에서 복구 시도: {sl_init_err}")

                                    elif status in ['canceled', 'rejected'] or (_time.time() - pending_time > 300):
                                        # [Consciousness] 미체결 취소
                                        _emit_thought(symbol, f"⏱️ 지정가 5분 미체결 → 주문 취소, 새 타점 재탐색 시작")
                                        # 취소되었거나 5분 초과 시 -> 주문 취소 및 PENDING 해제 (고스트 오더 방지)
                                        if status not in ['canceled', 'rejected']:
                                            if not _is_paper_pending: # Paper면 cancel_order API 호출 절대 금지
                                                try:
                                                    await asyncio.to_thread(engine_api.exchange.cancel_order, pending_id, symbol)
                                                except Exception as cancel_err:
                                                    logger.warning(f"[{symbol}] 미체결 주문 취소 실패 (이미 취소되었을 수 있음): {cancel_err}")
                                                
                                        # [Phase 23.5] Shadow Hunting 철수 전용 알림 (상태 초기화 전에 체크)
                                        _was_shadow_hunting = bot_global_state["symbols"][symbol].get("is_shadow_hunting", False)

                                        # [Phase 32] 통합 상태 초기화 헬퍼 사용
                                        _reset_position_state(bot_global_state["symbols"][symbol])

                                        _paper_tag = "[👻 PAPER] " if _is_paper_pending else ""
                                        cancel_msg = f"{_paper_tag}⏱️ [{symbol}] 지정가 5분 미체결 취소 완료 → 봇이 새로운 최적의 타점을 즉시 재탐색합니다."
                                        bot_global_state["logs"].append(cancel_msg)
                                        logger.info(cancel_msg)
                                        if _was_shadow_hunting:
                                            _sh_fail_msg = f"🐸 [{symbol}] 그림자 사냥 실패 — 꼬리가 잡히지 않아 작전 철수합니다."
                                            bot_global_state["logs"].append(_sh_fail_msg)
                                            save_log("WARNING", _sh_fail_msg)
                                            send_telegram_sync(
                                                f"🐸 <b>그림자 사냥 철수</b>\n"
                                                f"────────────────────────\n"
                                                f"5분 내 휩쏘 꼬리가 잡히지 않았습니다.\n"
                                                f"• 심볼: <b>{symbol}</b>\n"
                                                f"✅ 주문 취소 완료. 봇이 재탐색합니다."
                                            )

                                except Exception as order_err:
                                    logger.error(f"[{symbol}] 스마트 지정가 체결 상태 조회 실패: {order_err}")
                            # --- End of PENDING 상태 체크 ---

                            # 익절/손절 체크 (PENDING이 아닐 때만)
                            if position_side in ["LONG", "SHORT"]:
                                # 리스크 관리 체크
                                # LONG: highest_price(고점) 추적 / SHORT: lowest_price(저점) 추적
                                if position_side == "SHORT":
                                    extreme_price = bot_global_state["symbols"][symbol].get("lowest_price", entry)
                                else:
                                    extreme_price = bot_global_state["symbols"][symbol].get("highest_price", entry)

                                current_atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else float(entry * 0.01)
                                if pd.isna(current_atr) or current_atr <= 0:
                                    current_atr = float(entry * 0.01)

                                # [Phase 20.2] Stale Price Watchdog (웹소켓 뇌사 방어막)
                                _now = _time.time()
                                _last_update = bot_global_state["symbols"][symbol].get("last_price_update_time", _now)

                                # 마지막 가격 갱신이 10초 이상 지연되었다면 웹소켓 이상으로 간주하고 REST API 강제 호출
                                # [Phase 20.2 Fix] 임계값 3s→10s: 멀티심볼 순차 루프 + fetch_ohlcv 지연으로 3초는 false alarm 발생
                                # [Phase 21.2] 스트레스 바이패스: stale_price watchdog 스킵
                                if _now - _last_update > 10.0 and not _is_bypass_active('stress_bypass_stale_price'):
                                    # [Consciousness] Stale Price 감지
                                    _emit_thought(symbol, f"⚠️ 실시간 데이터 수신 지연 감지({_now - _last_update:.1f}초)! REST API 비상 폴링 실행")
                                    try:
                                        logger.warning(f"[{symbol}] ⚠️ 실시간 데이터 수신 지연 감지 (>10초). REST API 비상 우회 폴링 실행!")
                                        _emergency_ticker = await asyncio.to_thread(engine_api.exchange.fetch_ticker, symbol)
                                        current_price = float(_emergency_ticker['last'])
                                        # 비상 갱신 후 타임스탬프 리셋
                                        bot_global_state["symbols"][symbol]["last_price_update_time"] = _now
                                    except Exception as fallback_err:
                                        logger.error(f"[{symbol}] 🚨 비상 REST API 폴링마저 실패: {fallback_err}")
                                else:
                                    # 정상 갱신 경로 또는 바이패스 활성 시: 타임스탬프 강제 업데이트 (누적 지연 방지)
                                    bot_global_state["symbols"][symbol]["last_price_update_time"] = _now

                                # [v2.2 DRY] evaluate_risk_management Tuple 언패킹 — 이중 계산 완전 제거
                                # (action, real_sl, trailing_active, trailing_target)를 단일 소스(strategy.py)에서만 계산
                                partial_tp_executed = bot_global_state["symbols"][symbol].get("partial_tp_executed", False)
                                _pos_contracts = int(bot_global_state["symbols"][symbol].get("contracts", 1))
                                _breakeven_active = bot_global_state["symbols"][symbol].get("breakeven_stop_active", False)
                                action, _real_sl, _trailing_active, _trailing_target = strategy_instance.evaluate_risk_management(
                                    entry, current_price, extreme_price, position_side, current_atr, symbol, partial_tp_executed, _pos_contracts, _breakeven_active
                                )
                                # [Consciousness] 리스크 관리 결과
                                _cs_trail_tag = "ON" if _trailing_active else "OFF"
                                _emit_thought(symbol, f"🔧 리스크 체크: {action} | SL ${_real_sl:,.2f} | 트레일링 {_cs_trail_tag}", throttle_key=f"risk_{symbol}", throttle_sec=10.0)

                                # [Breakeven Stop] 래칫(Ratchet) — 1계약 trailing 최초 활성화 시 SL → 진입가 업그레이드
                                # 한번 True가 되면 포지션 종료까지 절대 False로 돌아가지 않음
                                if _trailing_active and _pos_contracts <= 1 and not partial_tp_executed and not _breakeven_active:
                                    bot_global_state["symbols"][symbol]["breakeven_stop_active"] = True
                                    _breakeven_active = True
                                    # [수수료 포함 본전 방어] SL을 진입가+수수료로 설정 → "본전 청산 = 순손실" 원천 차단
                                    _fee_margin_rate = strategy_instance.fee_margin  # 0.0015 (0.15%) — 양방향 수수료 커버
                                    if position_side == "LONG":
                                        _be_floor_with_fee = entry * (1.0 + _fee_margin_rate)
                                    else:
                                        _be_floor_with_fee = entry * (1.0 - _fee_margin_rate)
                                    bot_global_state["symbols"][symbol]["breakeven_floor_price"] = round(_be_floor_with_fee, 4)
                                    # [Consciousness] 트레일링 + 본전 방어 활성화
                                    _emit_thought(symbol, f"🔥 트레일링 스탑 활성화! 추적가 ${_trailing_target:,.2f}")
                                    _emit_thought(symbol, f"🛡️ 본전 방어(Breakeven) 활성화 — SL이 진입가+수수료 ${_be_floor_with_fee:,.2f}로 업그레이드!")
                                    # [즉시 반영] 방금 활성화 → strategy.py는 아직 모르므로 직접 SL 바닥 적용
                                    if position_side == "LONG":
                                        _real_sl = max(_real_sl, _be_floor_with_fee)
                                    else:
                                        _real_sl = min(_real_sl, _be_floor_with_fee)
                                    # 텔레그램 알림
                                    _tf_label = str(get_config('timeframe') or '15m')
                                    _be_msg = (
                                        f"🛡️ <b>본전 방어 활성화 (수수료 포함)</b>\n"
                                        f"{_TG_LINE}\n"
                                        f"<code>{_sym_short(symbol)}</code>  ·  ⏱️ <code>{_tf_label}</code>\n"
                                        f"진입가: ${entry:,.2f}\n"
                                        f"SL: ${_be_floor_with_fee:,.2f} (수수료 {_fee_margin_rate*100:.2f}% 포함)\n"
                                        f"이후 순이익 보장 청산"
                                    )
                                    send_telegram_sync(_be_msg)
                                    bot_global_state["logs"].append(f"🛡️ [{symbol}] SL → ${_be_floor_with_fee:,.2f} 업그레이드 (수수료 포함 Breakeven)")

                                # [래칫 방어] 트레일링/본전방어 활성 후 SL은 이익 방향으로만 이동 (절대 후퇴 금지)
                                _prev_real_sl = float(bot_global_state["symbols"][symbol].get("real_sl", 0.0))
                                if _prev_real_sl > 0 and (_trailing_active or _breakeven_active):
                                    if position_side == "LONG" and _real_sl < _prev_real_sl:
                                        _emit_thought(symbol, f"🔒 래칫 방어: SL 후퇴 차단 ${_real_sl:,.2f} → ${_prev_real_sl:,.2f} 유지", throttle_key=f"ratchet_{symbol}", throttle_sec=30.0)
                                        _real_sl = _prev_real_sl
                                    elif position_side == "SHORT" and _real_sl > _prev_real_sl:
                                        _emit_thought(symbol, f"🔒 래칫 방어: SL 후퇴 차단 ${_real_sl:,.2f} → ${_prev_real_sl:,.2f} 유지", throttle_key=f"ratchet_{symbol}", throttle_sec=30.0)
                                        _real_sl = _prev_real_sl
                                bot_global_state["symbols"][symbol]["real_sl"] = round(_real_sl, 4)
                                bot_global_state["symbols"][symbol]["trailing_active"] = _trailing_active
                                bot_global_state["symbols"][symbol]["trailing_target"] = round(_trailing_target, 4) if _trailing_target else 0.0

                                # [Phase 16] 다이내믹 익절가(Dynamic TP) 시각화 로직
                                _is_partial_done = bot_global_state["symbols"][symbol].get("partial_tp_executed", False)
                                if not _is_partial_done:
                                    # 1차 분할 익절 타겟 선제적 계산 (수수료 0.15% + ATR 50%)
                                    _target_offset = (entry * 0.0015) + (current_atr * 0.5)
                                    _first_target = (entry + _target_offset) if position_side == "LONG" else (entry - _target_offset)
                                    # [Consciousness] 다이내믹 TP 상태
                                    _cs_dist = abs(current_price - _first_target)
                                    _cs_dist_pct = (_cs_dist / entry * 100) if entry > 0 else 0
                                    _emit_thought(symbol, f"📐 1차 타겟 ${_first_target:,.2f} | 현재가까지 거리: ${_cs_dist:,.2f} ({_cs_dist_pct:.2f}%)", throttle_key=f"tp_dyn_{symbol}", throttle_sec=10.0)
                                    # [TP Split] 멀티계약 vs 1계약 구분 표시
                                    _cur_contracts = int(bot_global_state["symbols"][symbol].get("contracts", 1))
                                    if _cur_contracts > 1:
                                        bot_global_state["symbols"][symbol]["take_profit_price"] = f"🎯 1차 타겟: ${_first_target:,.2f} (50% 거래소 지정가)"
                                    else:
                                        # [1계약 트레일링 전용] TP 미등록 → 트레일링 스탑 대기 표시
                                        if _trailing_active:
                                            bot_global_state["symbols"][symbol]["take_profit_price"] = f"🔥 트레일링 추적 중: ${_trailing_target:,.2f}"
                                        else:
                                            bot_global_state["symbols"][symbol]["take_profit_price"] = f"⏳ 트레일링 대기 | 활성화 목표: ${_first_target:,.2f}"
                                else:
                                    # 1차 익절 완료 후 트레일링 스탑 상태
                                    _t_target = bot_global_state["symbols"][symbol].get("trailing_target", 0.0)
                                    if _t_target > 0:
                                        bot_global_state["symbols"][symbol]["take_profit_price"] = f"🔥 트레일링 추적: ${_t_target:,.2f}"
                                    else:
                                        bot_global_state["symbols"][symbol]["take_profit_price"] = "🚀 트레일링 대기"

                                # --- [Step 2] 50% 분할 익절 (Partial TP) 조건 체크 ---
                                if not bot_global_state["symbols"][symbol].get("partial_tp_executed", False):
                                    # [TP Split] 거래소 TP 부분 체결 플래그 우선 확인
                                    _exchange_tp_filled = bot_global_state["symbols"][symbol].get("exchange_tp_filled", False)

                                    # [v2.1] 발동 조건: 수수료 마진 0.15% + ATR*0.5 이상 수익 구간
                                    partial_tp_threshold = (entry * 0.0015) + (current_atr * 0.5)
                                    if position_side == "LONG":
                                        partial_profit = current_price - entry
                                    else:
                                        partial_profit = entry - current_price

                                    # [방어막] exchange_tp_filled 오탐 차단: 수익이 임계값 50% 미만이면 오탐으로 간주
                                    if _exchange_tp_filled:
                                        _tp_entry_ts = bot_global_state["symbols"][symbol].get("entry_timestamp", 0)
                                        _tp_age = _time.time() - _tp_entry_ts
                                        _tp_min_profit = partial_tp_threshold * 0.5
                                        if partial_profit < _tp_min_profit or _tp_age < 30:
                                            logger.warning(f"[{symbol}] 🚨 [TP Guard] 거래소 TP 신호 오탐 차단 — 수익 ${partial_profit:.2f} < 최소 ${_tp_min_profit:.2f} / 진입경과 {_tp_age:.1f}초")
                                            bot_global_state["symbols"][symbol]["exchange_tp_filled"] = False
                                            _exchange_tp_filled = False

                                    if _exchange_tp_filled or partial_profit >= partial_tp_threshold:
                                        # [Consciousness] 1차 타겟 도달
                                        _emit_thought(symbol, f"🎯 1차 타겟 도달! 수익 ${partial_profit:,.2f} >= 임계값 ${partial_tp_threshold:,.2f} → 분할 익절 실행!")
                                        try:
                                            full_contracts = int(bot_global_state["symbols"][symbol].get("contracts", 1))
                                            _is_paper = bot_global_state["symbols"][symbol].get("is_paper", False)
                                            _paper_tag = "[👻 PAPER] " if _is_paper else ""

                                            if _exchange_tp_filled:
                                                # ── [TP Split] 거래소 TP가 이미 체결됨 → 시장가 스킵, 상태관리만 ──
                                                bot_global_state["symbols"][symbol]["exchange_tp_filled"] = False
                                                qty_msg = f"📋 거래소 지정가 체결 완료 | 잔여: {full_contracts}계약"
                                                _tp_fill_source = "exchange"
                                            elif full_contracts > 1:
                                                half_contracts = max(1, full_contracts // 2)
                                                # 시장가 절반 청산 (Reduce-Only) — Paper면 바이패스
                                                if not _is_paper:
                                                    # [TP Split] 먼저 거래소 TP 주문 취소 시도 (아직 체결되지 않은 경우)
                                                    _pending_tp_id = bot_global_state["symbols"][symbol].get("active_tp_order_id")
                                                    if _pending_tp_id:
                                                        try:
                                                            await asyncio.to_thread(engine_api.exchange.cancel_order, _pending_tp_id, symbol)
                                                        except Exception:
                                                            pass  # 이미 체결/취소된 경우 무시
                                                    if position_side == "LONG":
                                                        partial_order = await asyncio.to_thread(
                                                            engine_api.exchange.create_market_sell_order,
                                                            symbol, half_contracts,
                                                            {"reduceOnly": True}
                                                        )
                                                    else:
                                                        partial_order = await asyncio.to_thread(
                                                            engine_api.exchange.create_market_buy_order,
                                                            symbol, half_contracts,
                                                            {"reduceOnly": True}
                                                        )
                                                bot_global_state["symbols"][symbol]["contracts"] = full_contracts - half_contracts
                                                qty_msg = f"물량 50% ({half_contracts}계약) 수익 실현 완료 | 잔여: {bot_global_state['symbols'][symbol]['contracts']}계약"
                                                _tp_fill_source = "software"
                                            else:
                                                # [Phase 19.4 교정] 1계약일 경우 매도는 스킵하되, 목표가에 도달했으므로 방어선과 트레일링은 정상 발동
                                                qty_msg = "소액(1계약) 포지션으로 분할 매도 스킵, 수익 보존(본전 방어) 모드 직행"
                                                _tp_fill_source = "software"

                                            # 상태 업데이트 (공통)
                                            bot_global_state["symbols"][symbol]["partial_tp_executed"] = True

                                            # 본전 방어선 갱신 (프론트엔드 표시용)
                                            if position_side == "LONG":
                                                breakeven_sl = round(entry + (entry * 0.001), 4)
                                            else:
                                                breakeven_sl = round(entry - (entry * 0.001), 4)
                                            bot_global_state["symbols"][symbol]["real_sl"] = breakeven_sl

                                            partial_msg = (
                                                f"{_paper_tag}🎯 [{symbol}] {position_side} 1차 타겟 도달 완료 💰\n"
                                                f"{qty_msg}\n"
                                                f"🛡️ 본전 방어선(Breakeven) 시작: ${breakeven_sl}"
                                            )
                                            bot_global_state["logs"].append(partial_msg)
                                            logger.info(partial_msg)

                                            # 텔레그램 분리 알림 — [TP Split] 체결 소스 태그 추가
                                            _fill_tag = "📋 거래소 지정가 체결" if _tp_fill_source == "exchange" else "⚡ 소프트웨어 감지"
                                            _header_pt = "👻 PAPER TRADING | 가상 1차 타겟 도달" if _is_paper else "⚡ ANTIGRAVITY (LIVE) | 실전 1차 타겟 도달"
                                            partial_tg_msg = (
                                                f"{_header_pt}\n"
                                                f"{_TG_LINE}\n"
                                                f"🎯 <b>1차 타겟 도달 완료</b>  ·  <code>{_sym_short(symbol)}</code>\n"
                                                f"{_fill_tag}\n"
                                                f"{_TG_LINE}\n"
                                                f"{qty_msg}\n"
                                                f"🛡️ 본전 방어선(Breakeven) 작동 시작\n"
                                                f"{_TG_LINE}"
                                            )
                                            send_telegram_sync(partial_tg_msg)

                                            # [Phase 26] 분할 익절 후 거래소 거미줄 재생성 (계약수 + 가격 동기화)
                                            # 기존 TP/SL은 원래 계약수로 걸려있으므로 취소 후 잔여 계약수로 재배치
                                            if not _is_paper:
                                                try:
                                                    # [TP Split] exchange_tp_filled 경로에서는 TP 이미 체결됨 → SL만 취소/재배치
                                                    _old_tp_id = bot_global_state["symbols"][symbol].get("active_tp_order_id")
                                                    _old_sl_id = bot_global_state["symbols"][symbol].get("active_sl_order_id")
                                                    for _oid in [_old_tp_id, _old_sl_id]:
                                                        if _oid:
                                                            try:
                                                                await asyncio.to_thread(engine_api.exchange.cancel_order, _oid, symbol)
                                                            except Exception:
                                                                pass

                                                    # SL 재생성 (잔여 계약수 + 본전 방어가)
                                                    _remaining = float(bot_global_state["symbols"][symbol]["contracts"])
                                                    _close_side_re = "sell" if position_side == "LONG" else "buy"
                                                    _params_sl_re = {"reduceOnly": True, "stopLossPrice": breakeven_sl}
                                                    new_sl_re = await asyncio.to_thread(
                                                        engine_api.exchange.create_order,
                                                        symbol, 'market', _close_side_re, _remaining, None, _params_sl_re
                                                    )
                                                    bot_global_state["symbols"][symbol]["active_sl_order_id"] = new_sl_re['id']
                                                    bot_global_state["symbols"][symbol]["last_placed_sl_price"] = breakeven_sl

                                                    # TP 제거 (트레일링 모드에서는 SL이 익절 역할을 겸함)
                                                    bot_global_state["symbols"][symbol]["active_tp_order_id"] = None
                                                    bot_global_state["symbols"][symbol]["last_placed_tp_price"] = 0.0

                                                    logger.info(f"[{symbol}] 🕸️ 분할 익절 후 거미줄 재배치 완료 | 잔여: {_remaining}계약 | SL: {breakeven_sl}")
                                                except Exception as reweb_err:
                                                    logger.warning(f"[{symbol}] ⚠️ 분할 익절 후 거미줄 재배치 예외: {reweb_err}")

                                        except Exception as partial_err:
                                            logger.error(f"[{symbol}] 1차 타겟 도달 처리 실패: {partial_err}")
                                # --- End of Partial TP 조건 체크 ---

                                # [Phase 28] TP/SL 자가 치유 안전망 (Self-Healing Safety Net)
                                # 실전 포지션인데 거래소 SL이 없는 경우 → 즉시 생성 (크래시 복구, 예외 복구 등)
                                if not bot_global_state["symbols"][symbol].get("is_paper", False):
                                    _heal_sl_id = bot_global_state["symbols"][symbol].get("active_sl_order_id")
                                    _heal_real_sl = float(bot_global_state["symbols"][symbol].get("real_sl", 0.0))
                                    if not _heal_sl_id and entry > 0:
                                        try:
                                            _heal_close_side = "sell" if position_side == "LONG" else "buy"
                                            _heal_amt = float(bot_global_state["symbols"][symbol].get("contracts", 0))
                                            if _heal_amt <= 0:
                                                try:
                                                    _heal_amt = await asyncio.to_thread(engine_api.get_position_contracts, symbol)
                                                except Exception:
                                                    _heal_amt = 1.0
                                            _heal_amt = max(1.0, _heal_amt)
                                            # SL 가격: real_sl이 있으면 사용, 없으면 hard_stop_loss_rate 기반 계산
                                            if _heal_real_sl > 0:
                                                _heal_sl_price = _heal_real_sl
                                            else:
                                                _heal_sl_rate = strategy_instance.hard_stop_loss_rate
                                                _heal_sl_price = round(entry * (1 - _heal_sl_rate), 4) if position_side == "LONG" else round(entry * (1 + _heal_sl_rate), 4)
                                            _heal_params = {"reduceOnly": True, "stopLossPrice": _heal_sl_price}
                                            _heal_order = await asyncio.to_thread(
                                                engine_api.exchange.create_order,
                                                symbol, 'market', _heal_close_side, _heal_amt, None, _heal_params
                                            )
                                            bot_global_state["symbols"][symbol]["active_sl_order_id"] = _heal_order['id']
                                            bot_global_state["symbols"][symbol]["last_placed_sl_price"] = _heal_sl_price
                                            logger.warning(f"[{symbol}] 🩹 [Self-Healing] 거래소 SL 누락 감지 → 자동 복구 완료 | SL: {_heal_sl_price:.4f}")
                                        except Exception as heal_err:
                                            logger.error(f"[{symbol}] 🚨 [Self-Healing] SL 자동 복구 실패: {heal_err}")

                                # [Phase 22.3] 0.05% 스마트 갱신 (Smart Amend) 엔진
                                # 실시간으로 변동된 본전 방어선 및 트레일링 스탑(real_sl)을 추적하여 거래소 거미줄 위치를 수정함
                                if not bot_global_state["symbols"][symbol].get("is_paper", False):
                                    _current_ideal_sl = float(bot_global_state["symbols"][symbol].get("real_sl", 0.0))
                                    _last_sl = float(bot_global_state["symbols"][symbol].get("last_placed_sl_price", 0.0))
                                    _active_sl_id = bot_global_state["symbols"][symbol].get("active_sl_order_id")

                                    # [래칫 방어] 거래소 SL은 이익 방향으로만 이동 (후퇴 원천 차단)
                                    if _current_ideal_sl > 0 and _last_sl > 0:
                                        if position_side == "LONG" and _current_ideal_sl < _last_sl:
                                            logger.info(f"[{symbol}] 🔒 [Smart Amend 래칫] SL 후퇴 차단: {_current_ideal_sl:.4f} → {_last_sl:.4f} 유지")
                                            _current_ideal_sl = _last_sl
                                        elif position_side == "SHORT" and _current_ideal_sl > _last_sl:
                                            logger.info(f"[{symbol}] 🔒 [Smart Amend 래칫] SL 후퇴 차단: {_current_ideal_sl:.4f} → {_last_sl:.4f} 유지")
                                            _current_ideal_sl = _last_sl

                                    # [본전 보장 + 수수료] breakeven 활성 시 거래소 SL 최소 진입가+수수료 강제
                                    _be_floor_active = bot_global_state["symbols"][symbol].get("breakeven_stop_active", False)
                                    if _be_floor_active and _current_ideal_sl > 0:
                                        # breakeven_floor_price가 있으면 수수료 포함 가격, 없으면 진입가 폴백
                                        _entry_floor = float(bot_global_state["symbols"][symbol].get("breakeven_floor_price",
                                                             bot_global_state["symbols"][symbol].get("entry_price", 0.0)))
                                        if _entry_floor > 0:
                                            if position_side == "LONG" and _current_ideal_sl < _entry_floor:
                                                logger.warning(f"[{symbol}] 🛡️ [본전 보장] SL({_current_ideal_sl:.4f})이 수수료포함본전({_entry_floor:.4f}) 미만 → 강제 교정")
                                                _current_ideal_sl = _entry_floor
                                            elif position_side == "SHORT" and _current_ideal_sl > _entry_floor:
                                                logger.warning(f"[{symbol}] 🛡️ [본전 보장] SL({_current_ideal_sl:.4f})이 수수료포함본전({_entry_floor:.4f}) 초과 → 강제 교정")
                                                _current_ideal_sl = _entry_floor

                                    # 이상적인 손절가(real_sl)가 존재하고, 기존에 걸어둔 손절가가 있을 경우 비교
                                    if _current_ideal_sl > 0 and _last_sl > 0 and _active_sl_id:
                                        # [방어막] SL이 진입가 방향으로 비정상적으로 이동하는 것 차단
                                        # breakeven_stop_active 없이 SL이 entry 근처로 이동 = 플래그 오염 의심
                                        _amend_entry = float(bot_global_state["symbols"][symbol].get("entry_price", 0.0))
                                        _amend_be_active = bot_global_state["symbols"][symbol].get("breakeven_stop_active", False)
                                        _amend_pt_done = bot_global_state["symbols"][symbol].get("partial_tp_executed", False)
                                        if _amend_entry > 0 and not _amend_be_active and not _amend_pt_done:
                                            # 본전 방어/분할 익절 미발동 상태에서 SL이 진입가 대비 0.1% 이내로 이동 시도 → 차단
                                            _sl_to_entry_dist = abs(_current_ideal_sl - _amend_entry) / _amend_entry
                                            if _sl_to_entry_dist < 0.001:
                                                logger.warning(f"[{symbol}] 🚨 [Smart Amend 차단] SL({_current_ideal_sl:.4f})이 진입가({_amend_entry:.4f}) 대비 {_sl_to_entry_dist*100:.3f}%로 비정상 접근 — 갱신 스킵")
                                                _current_ideal_sl = 0  # 갱신 방지

                                        # 오차율 계산 (절대값(목표가 - 기존가) / 기존가)
                                        _diff_ratio = abs(_current_ideal_sl - _last_sl) / _last_sl if _current_ideal_sl > 0 and _last_sl > 0 else 0

                                        # [트레일링 공격적 추적] 트레일링 활성 시 갱신 임계값 5배 하향 (0.05% → 0.01%)
                                        _trailing_active_amend = bot_global_state["symbols"][symbol].get("trailing_active", False)
                                        _amend_threshold = 0.0001 if _trailing_active_amend else 0.0005
                                        if _diff_ratio >= _amend_threshold:
                                            try:
                                                # 1. 기존 거미줄(주문) 취소
                                                await asyncio.to_thread(engine_api.exchange.cancel_order, _active_sl_id, symbol)

                                                # 2. 새로운 위치에 거미줄(주문) 생성 (CCXT Stop-Market 규격)
                                                _close_side = "sell" if position_side == "LONG" else "buy"
                                                _amt = float(bot_global_state["symbols"][symbol]["contracts"])
                                                _params_sl = {"reduceOnly": True, "stopLossPrice": _current_ideal_sl}

                                                new_sl_order = await asyncio.to_thread(
                                                    engine_api.exchange.create_order,
                                                    symbol, 'market', _close_side, _amt, None, _params_sl
                                                )

                                                # 3. 뇌 구조(기억 장치) 업데이트
                                                bot_global_state["symbols"][symbol]["active_sl_order_id"] = new_sl_order['id']
                                                bot_global_state["symbols"][symbol]["last_placed_sl_price"] = _current_ideal_sl

                                                logger.info(f"[{symbol}] 🕸️ 방어막 위치 스마트 갱신 완료 | 기존: {_last_sl} ➔ 변경: {_current_ideal_sl} (오차율: {_diff_ratio*100:.3f}%)")

                                            except Exception as amend_err:
                                                # 이미 거래소에서 체결되었거나 취소된 경우 무시하고 다음 사이클에서 동기화되도록 예외 처리
                                                logger.warning(f"[{symbol}] ⚠️ 방어막 갱신 중 예외 발생 (이미 체결되었을 가능성): {amend_err}")

                                # [Phase 26] TP 스마트 갱신 — SL과 동일한 Cancel+Recreate 패턴
                                # 1차 타겟 미도달 상태에서만 작동 (분할 익절 후에는 TP 주문 없음, SL이 겸임)
                                if not bot_global_state["symbols"][symbol].get("is_paper", False):
                                    _is_partial_done_tp = bot_global_state["symbols"][symbol].get("partial_tp_executed", False)
                                    _active_tp_id = bot_global_state["symbols"][symbol].get("active_tp_order_id")

                                    if not _is_partial_done_tp and _active_tp_id:
                                        _target_offset_tp = (entry * 0.0015) + (current_atr * 0.5)
                                        _ideal_tp = (entry + _target_offset_tp) if position_side == "LONG" else (entry - _target_offset_tp)
                                        _last_tp = float(bot_global_state["symbols"][symbol].get("last_placed_tp_price", 0.0))

                                        if _last_tp > 0:
                                            _diff_ratio_tp = abs(_ideal_tp - _last_tp) / _last_tp
                                            if _diff_ratio_tp >= 0.0005:
                                                try:
                                                    await asyncio.to_thread(engine_api.exchange.cancel_order, _active_tp_id, symbol)
                                                    _close_side_tp = "sell" if position_side == "LONG" else "buy"
                                                    _amt_tp = float(bot_global_state["symbols"][symbol].get("tp_order_amount", bot_global_state["symbols"][symbol]["contracts"]))
                                                    _params_tp_amend = {"reduceOnly": True}
                                                    new_tp_order = await asyncio.to_thread(
                                                        engine_api.exchange.create_order,
                                                        symbol, 'limit', _close_side_tp, _amt_tp, round(_ideal_tp, 4), _params_tp_amend
                                                    )
                                                    bot_global_state["symbols"][symbol]["active_tp_order_id"] = new_tp_order['id']
                                                    bot_global_state["symbols"][symbol]["last_placed_tp_price"] = round(_ideal_tp, 4)
                                                    logger.info(f"[{symbol}] 🕸️ 익절 거미줄 스마트 갱신 | {_last_tp:.4f} ➔ {_ideal_tp:.4f} (오차율: {_diff_ratio_tp*100:.3f}%)")
                                                except Exception as tp_amend_err:
                                                    logger.warning(f"[{symbol}] ⚠️ 익절 거미줄 갱신 예외: {tp_amend_err}")

                                if action != "KEEP":
                                    # [Consciousness] 청산 사유 방출
                                    if action == "STOP_LOSS":
                                        _emit_thought(symbol, f"🚨 하드 손절 발동! ${current_price:,.2f} → SL ${_real_sl:,.2f} 돌파 — 즉시 청산 실행!")
                                    elif action == "TRAILING_STOP_EXIT":
                                        _emit_thought(symbol, f"💰 트레일링 익절 발동! 고점 대비 하락 → 추적가 ${_trailing_target:,.2f} 돌파 — 수익 확정!")
                                    else:
                                        _emit_thought(symbol, f"⚡ 청산 실행: {action} | 현재가 ${current_price:,.2f}")
                                    # 1. 청산 실행 (Paper/Real 분기)
                                    try:
                                        amount = int(bot_global_state["symbols"][symbol].get("contracts", 0))
                                        # [Bug Fix] 내부 contracts가 0이면 거래소 실제 수량으로 복구
                                        if amount <= 0:
                                            try:
                                                _actual_contracts = await asyncio.to_thread(engine_api.get_position_contracts, symbol)
                                                amount = int(_actual_contracts) if _actual_contracts > 0 else 1
                                                logger.warning(f"[{symbol}] ⚠️ 내부 contracts=0 → 거래소 실제 수량 {amount}으로 복구")
                                            except Exception:
                                                amount = 1  # 최후 방어: 최소 1계약
                                        _is_paper = bot_global_state["symbols"][symbol].get("is_paper", False)
                                        _paper_tag = "[👻 PAPER] " if _is_paper else ""

                                        if _is_paper:
                                            # ── [Shadow Mode] 가상 PnL 시뮬레이션 ──
                                            avg_fill_price = current_price
                                            try:
                                                contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                                            except Exception:
                                                contract_size = 0.01
                                            position_value = entry * amount * contract_size
                                            if position_side == "LONG":
                                                total_gross_pnl = (current_price - entry) * amount * contract_size
                                            else:
                                                total_gross_pnl = (entry - current_price) * amount * contract_size
                                            total_fee = position_value * 0.0005 * 2  # 0.05% Taker 양방향 가상 수수료 (양수)
                                            pnl_amount = total_gross_pnl - total_fee
                                            pnl_percent = (pnl_amount / (position_value / leverage) * 100) if position_value > 0 else 0.0
                                            # DB 저장 차단 (is_paper == True)
                                        else:
                                            # ── [실전 모드] 거래소 청산 + 영수증 파싱 ──
                                            order_id = await asyncio.to_thread(engine_api.close_position, symbol, position_side, amount)
                                            net_pnl = 0.0
                                            total_gross_pnl = 0.0
                                            total_fee = 0.0
                                            avg_fill_price = current_price
                                            receipt_found = False
                                            for _attempt in range(5):
                                                await asyncio.sleep(1.0)
                                                try:
                                                    trades = await asyncio.to_thread(engine_api.get_recent_trade_receipts, symbol, limit=20)
                                                    matching_trades = [t for t in trades if str(t.get('order')) == str(order_id)]
                                                    if matching_trades:
                                                        net_pnl, total_gross_pnl, _fee_raw, avg_fill_price = engine_api.calculate_realized_pnl(matching_trades, entry)
                                                        total_fee = abs(_fee_raw)  # OKX fee는 음수 → 표시/DB용 양수 변환
                                                        receipt_found = True
                                                        break
                                                except Exception as receipt_err:
                                                    logger.warning(f"[{symbol}] 청산 체결 영수증 파싱 오류 시도 {_attempt+1}: {receipt_err}")
                                            if not receipt_found:
                                                # [방어] 청산은 이미 체결됨 — raise 대신 추정값으로 계속 진행 (상태 불일치 방지)
                                                logger.warning(f"[{symbol}] 영수증 파싱 5회 실패 — 추정값으로 대체 처리")
                                                _emit_thought(symbol, f"⚠️ 영수증 파싱 실패! 청산은 완료됐으나 정확한 PnL 미확인 — 추정값 대체")
                                                send_telegram_sync(
                                                    f"⚠️ <b>ANTIGRAVITY</b>  |  영수증 파싱 실패\n"
                                                    f"{_TG_LINE}\n"
                                                    f"심볼: <code>{symbol}</code>\n"
                                                    f"청산 주문은 체결됐으나 PnL 영수증 조회 5회 실패\n"
                                                    f"추정값으로 DB 저장됩니다. OKX에서 실제 PnL 확인 필요\n"
                                                    f"{_TG_LINE}"
                                                )
                                                # 추정 PnL 계산 (영수증 없이)
                                                try:
                                                    contract_size_est = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                                                except Exception:
                                                    contract_size_est = 0.01
                                                if position_side == "LONG":
                                                    total_gross_pnl = (current_price - entry) * amount * contract_size_est
                                                else:
                                                    total_gross_pnl = (entry - current_price) * amount * contract_size_est
                                                total_fee = entry * amount * contract_size_est * 0.0005 * 2  # 양수 통일
                                                net_pnl = total_gross_pnl - total_fee
                                                avg_fill_price = current_price
                                            pnl_amount = net_pnl
                                            try:
                                                contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                                            except Exception:
                                                contract_size = 0.01
                                            position_value = entry * amount * contract_size
                                            pnl_percent = (pnl_amount / (position_value / leverage) * 100) if position_value > 0 else 0.0

                                            save_trade(
                                                symbol=symbol,
                                                position_type=position_side,
                                                entry_price=entry,
                                                exit_price=avg_fill_price,
                                                pnl=round(pnl_amount, 4),
                                                pnl_percent=round(pnl_percent, 4),
                                                fee=round(total_fee, 4),
                                                gross_pnl=round(total_gross_pnl, 4),
                                                amount=amount,
                                                exit_reason=action,
                                                leverage=leverage,
                                                timeframe=str(get_config('timeframe') or '15m')
                                            )

                                        # 3. 청산 알림 (Paper/Real 공통 — 태그만 다름)
                                        _exit_reason_ko = {
                                            "STOP_LOSS": "🛑 손절",
                                            "TRAILING_STOP_EXIT": "✅ 트레일링 익절",
                                        }
                                        reason_ko = _exit_reason_ko.get(action, action)
                                        emoji = "✅" if pnl_percent >= 0 else "🔴"
                                        
                                        msg = f"{_paper_tag}{emoji} [{symbol}] {position_side} 청산 | 확정 체결가: ${avg_fill_price:.2f} | 순수익(Net): {pnl_amount:+.4f} USDT (Gross: {total_gross_pnl:+.4f}, Fee: {total_fee:.4f}) | 수익률: {pnl_percent:+.2f}% | {reason_ko}"
                                            
                                        bot_global_state["logs"].append(msg)
                                        logger.info(msg)
                                        send_telegram_sync(_tg_exit(symbol, position_side, avg_fill_price, total_gross_pnl, total_fee, pnl_amount, pnl_percent, action, is_test=_is_paper))

                                        # [Phase 22.4] 메인 엔진 자체 청산 시 잔여 거미줄(고아 주문) 일괄 청소
                                        _act_tp = bot_global_state["symbols"][symbol].get("active_tp_order_id")
                                        _act_sl = bot_global_state["symbols"][symbol].get("active_sl_order_id")
                                        if _act_tp or _act_sl:
                                            logger.info(f"[{symbol}] 🧹 메인 로직 청산 발동. 잔여 거미줄 청소 시작...")
                                            for _oid in [_act_tp, _act_sl]:
                                                if _oid:
                                                    try:
                                                        await asyncio.to_thread(engine_api.exchange.cancel_order, _oid, symbol)
                                                    except Exception:
                                                        pass  # 에러 무시 (안전 종료)

                                        # ── [재진입 로직] 청산 직전 자격 판정 ──
                                        # 트레일링 본전/소익(±0.3%) 청산만 자격 부여, 손절은 제외
                                        _prev_reentry_count = bot_global_state["symbols"][symbol].get("_reentry_count", 0)
                                        if action == "TRAILING_STOP_EXIT" and abs(pnl_percent) <= 0.3 and _prev_reentry_count < 1:
                                            bot_global_state["symbols"][symbol]["_reentry_eligible"] = True
                                            bot_global_state["symbols"][symbol]["_reentry_direction"] = position_side
                                            logger.info(f"[{symbol}] 🔄 재진입 자격 부여: {position_side} 방향 | PnL: {pnl_percent:+.2f}%")
                                            _emit_thought(symbol, f"🔄 재진입 자격 부여! {position_side} 방향 — 본전/소익 트레일링 청산 (PnL {pnl_percent:+.2f}%)")
                                        else:
                                            bot_global_state["symbols"][symbol]["_reentry_eligible"] = False
                                            bot_global_state["symbols"][symbol]["_reentry_direction"] = ""

                                        # 4. 프론트엔드 포지션 초기화
                                        # [Phase 32] 통합 상태 초기화 헬퍼 사용
                                        async with state_lock:
                                            _reset_position_state(bot_global_state["symbols"][symbol])

                                        # [v2.1] 연패 쿨다운 카운터 업데이트 (Paper도 카운트하여 전략 검증)
                                        is_loss = (pnl_amount < 0)
                                        strategy_instance.record_trade_result(is_loss)
                                        # [Consciousness] 쿨다운 진입 감지
                                        if strategy_instance.loss_cooldown_until > _time.time():
                                            import datetime as _cs_cd_dt
                                            _cs_cd_end = _cs_cd_dt.datetime.fromtimestamp(strategy_instance.loss_cooldown_until, tz=_cs_cd_dt.timezone(_cs_cd_dt.timedelta(hours=9))).strftime("%H:%M:%S")
                                            _emit_thought(symbol, f"❄️ {strategy_instance.consecutive_loss_count}연패 쿨다운 진입 — {_cs_cd_end} KST까지 진입 차단")
                                            _cd_dur = int(get_config('cooldown_duration_sec') or 900)
                                            send_telegram_sync(
                                                f"❄️ <b>ANTIGRAVITY</b>  |  연패 쿨다운\n"
                                                f"{_TG_LINE}\n"
                                                f"<b>{strategy_instance.consecutive_loss_count}연패</b> 감지 → {_cd_dur // 60}분 진입 차단\n"
                                                f"해제 예정: <code>{_cs_cd_end} KST</code>\n"
                                                f"{_TG_LINE}"
                                            )
                                        # [Phase 21.2] 스트레스 바이패스: 연패 쿨다운 즉시 해제
                                        if _is_bypass_active('stress_bypass_cooldown_loss'):
                                            strategy_instance.loss_cooldown_until = 0
                                            strategy_instance.consecutive_loss_count = 0
                                        _save_strategy_state(strategy_instance)

                                        # [Phase 21.3] A.D.S 자가 면역 체계: 손절 자동 부검 리포트 (Post-Mortem)
                                        if is_loss:
                                            _reasons = []
                                            _row_pm = df.iloc[-1]
                                            _cur_macd = float(_row_pm['macd']) if not pd.isna(_row_pm['macd']) else 0.0
                                            _cur_sig = float(_row_pm['macd_signal']) if 'macd_signal' in _row_pm and not pd.isna(_row_pm['macd_signal']) else 0.0
                                            _cur_chop = float(_row_pm['chop']) if 'chop' in _row_pm and not pd.isna(_row_pm['chop']) else 50.0
                                            _cur_vol = float(_row_pm['volume']) if not pd.isna(_row_pm['volume']) else 0.0
                                            _cur_vsma = float(_row_pm['vol_sma_20']) if 'vol_sma_20' in _row_pm and not pd.isna(_row_pm['vol_sma_20']) else 1.0

                                            # 1. 횡보장 판단
                                            if _cur_chop > 60:
                                                _reasons.append(f"톱니바퀴 장세 돌입(CHOP {_cur_chop:.1f})")
                                            # 2. 세력 이탈 판단
                                            if _cur_vol < _cur_vsma:
                                                _reasons.append("매수/매도세 실종(거래량 급감)")
                                            # 3. 방향성 역전 및 거시 추세 이탈 판단
                                            if position_side == "LONG":
                                                if _cur_macd < _cur_sig:
                                                    _reasons.append("MACD 데드크로스(모멘텀 역전)")
                                                if macro_ema_200 is not None and current_price < macro_ema_200:
                                                    _reasons.append("1h EMA200 하방 돌파 당함")
                                            else:
                                                if _cur_macd > _cur_sig:
                                                    _reasons.append("MACD 골든크로스(모멘텀 역전)")
                                                if macro_ema_200 is not None and current_price > macro_ema_200:
                                                    _reasons.append("1h EMA200 상방 돌파 당함")

                                            _reason_txt = " · ".join(_reasons) if _reasons else "복합적 시장 변동성(스파이크/휩쏘)"
                                            _pm_msg = (
                                                f"🩻 [A.D.S 부검] [{symbol}] {position_side} 손절 원인 분석 완료 | "
                                                f"사망 원인: {_reason_txt} | "
                                                f"💡 [AI 권장]: 튜닝 패널에서 보수적 프리셋(스나이퍼/아이언돔) 전환 검토"
                                            )
                                            bot_global_state["logs"].append(_pm_msg)
                                            logger.info(_pm_msg)

                                        # [v2.2] 일일 누적 PnL 반영 + 킬스위치 체크 (Paper는 제외)
                                        if not _is_paper:
                                            kill_triggered = strategy_instance.record_daily_pnl(pnl_amount)
                                            _save_strategy_state(strategy_instance)
                                            if kill_triggered:
                                                kill_msg = f"🚨 [킬스위치 발동] 일일 최대 손실({strategy_instance.daily_max_loss_pct*100:.0f}%) 도달. 24시간 동안 매매 엔진 셧다운."
                                                bot_global_state["logs"].append(kill_msg)
                                                logger.warning(kill_msg)
                                                send_telegram_sync(f"🚨 킬스위치 발동\n일일 누적 손실: {strategy_instance.daily_pnl_accumulated:+.2f} USDT\n24시간 거래 중단")
                                                # [Consciousness] 킬스위치 발동
                                                _emit_thought(symbol, f"🚨 킬스위치 발동! 일일 손실 한도({strategy_instance.daily_max_loss_pct*100:.0f}%) 초과 — 24시간 거래 중단")

                                    except Exception as e:
                                        error_msg = f"[{symbol}] 청산 실패 ({action}): {str(e)}"
                                        bot_global_state["logs"].append(error_msg)
                                        logger.error(error_msg)
                                        # [Bug Fix] 청산 실패 텔레그램 스팸 방지 — 5분 쿨다운
                                        _close_fail_key = f"_close_fail_tg_ts_{symbol}"
                                        _close_fail_last = float(bot_global_state["symbols"][symbol].get(_close_fail_key, 0))
                                        if _time.time() - _close_fail_last > 300:  # 5분
                                            bot_global_state["symbols"][symbol][_close_fail_key] = _time.time()
                                            send_telegram_sync(
                                                f"🚨 <b>청산 실패 반복 경고</b>\n"
                                                f"코인: <code>{symbol.split(':')[0]}</code>\n"
                                                f"포지션: {position_side} | 사유: {action}\n"
                                                f"오류: {str(e)[:100]}\n"
                                                f"⚠️ OKX에서 수동 청산 필요"
                                            )

                    # 포지션 없을 때 진입 신호 체크
                    if bot_global_state["symbols"][symbol]["position"] == "NONE":
                        # [Phase 24] 캔들 단위 진입 시그널 중복 방지 — 같은 캔들에서 재평가 스킵
                        # [Fix] LONG/SHORT 신호 발생 시에만 캔들 잠금 → HOLD 상태에서는 매 루프 재평가
                        # [Phase TF+] 시간 기반 자동 해제: 15분봉에서 최대 5분만 대기 (5분봉은 기존과 동일)
                        _cur_candle_ts = int(df['timestamp'].iloc[-1])
                        _last_signal_ts = bot_global_state["symbols"][symbol].get("last_signal_candle_ts", 0)
                        _CANDLE_LOCK_MAX_SEC = 300  # 캔들 잠금 최대 지속시간: 5분
                        if _last_signal_ts == _cur_candle_ts:
                            # 시간 기반 자동 해제 체크
                            _lock_set_time = bot_global_state["symbols"][symbol].get("_candle_lock_set_time", 0)
                            _lock_elapsed = _time.time() - _lock_set_time if _lock_set_time > 0 else 0
                            _is_reentry_eligible = bot_global_state["symbols"][symbol].get("_reentry_eligible", False)
                            if _lock_elapsed < _CANDLE_LOCK_MAX_SEC and not _is_reentry_eligible:
                                continue  # 아직 쿨다운 중 + 재진입 자격 없음 → 대기
                            elif _is_reentry_eligible:
                                _reentry_dir = bot_global_state["symbols"][symbol].get("_reentry_direction", "")
                                _emit_thought(symbol, f"🔄 재진입 자격 → 캔들 잠금 면제 ({_reentry_dir} 방향)", throttle_key=f"reentry_unlock_{symbol}", throttle_sec=15.0)
                            else:
                                # 5분 경과 → 자동 해제
                                _emit_thought(symbol, f"🔓 캔들 잠금 시간 만료({_lock_elapsed:.0f}s ≥ {_CANDLE_LOCK_MAX_SEC}s) → 재진입 평가 허용", throttle_key=f"candle_unlock_{symbol}", throttle_sec=60.0)
                        # 캔들 잠금은 LONG/SHORT 신호가 실제로 평가될 때만 설정됨

                        # [Phase 19] 퇴근 모드 작동 시 신규 진입 강제 차단
                        if _exit_only:
                            _emit_thought(symbol, "🛏️ 퇴근 모드(Exit-Only) 활성 — 신규 진입 차단 중", throttle_key=f"exit_only_{symbol}", throttle_sec=30.0)
                            _log_trade_attempt(symbol, "N/A", "BLOCKED", "exit_only_mode")
                            continue

                        # [Phase 19.3] 호흡 고르기 (재진입: 30초 / 일반: 60초)
                        # [Phase 21.2] 스트레스 바이패스: reentry_cd 활성 시 쿨다운 스킵
                        last_exit = bot_global_state["symbols"][symbol].get("last_exit_time", 0)
                        _reentry_cd_sec = 30 if bot_global_state["symbols"][symbol].get("_reentry_eligible", False) else 60
                        if _time.time() - last_exit < _reentry_cd_sec and not _is_bypass_active('stress_bypass_reentry_cd'):
                            _log_trade_attempt(symbol, "N/A", "BLOCKED", f"reentry_cooldown_{_reentry_cd_sec}s")
                            continue

                        # 현재 사이클 최신 상태 기준 — 다른 심볼에 포지션이 있으면 진입 차단
                        any_other_position_open = any(
                            s.get("position", "NONE") != "NONE"
                            for k, s in bot_global_state["symbols"].items()
                            if k != symbol
                        )
                        if any_other_position_open:
                            _log_trade_attempt(symbol, "N/A", "BLOCKED", "other_position_open")
                            continue

                        # [v2.2] 일일 킬스위치 단일 게이트 — 시스템 전체에서 쿨스위치 플래그 하나만 참조
                        # [Phase 21.2] 스트레스 바이패스: daily_loss 활성 시 킬스위치 무시
                        if strategy_instance.kill_switch_active and _time.time() < strategy_instance.kill_switch_until:
                            if _is_bypass_active('stress_bypass_daily_loss'):
                                logger.warning(f"[{symbol}] ⚠️ [STRESS BYPASS] 일일 손실 킬스위치 → 바이패스 활성 중, 무시")
                            else:
                                _log_trade_attempt(symbol, signal if signal in ["LONG", "SHORT"] else "N/A", "BLOCKED", "kill_switch")
                                continue

                        # signal, analysis_msg는 위에서 이미 평가됨
                        if signal in ["LONG", "SHORT"]:
                            # ── [재진입 로직] 방향 검증 ──
                            _is_reentry_trade = bot_global_state["symbols"][symbol].get("_reentry_eligible", False)
                            if _is_reentry_trade:
                                _reentry_dir = bot_global_state["symbols"][symbol].get("_reentry_direction", "")
                                if signal != _reentry_dir:
                                    # 반대 방향 → 재진입 자격 소멸, 일반 진입으로 전환
                                    bot_global_state["symbols"][symbol]["_reentry_eligible"] = False
                                    bot_global_state["symbols"][symbol]["_reentry_direction"] = ""
                                    _is_reentry_trade = False
                                    _emit_thought(symbol, f"🔄❌ 재진입 방향 불일치 ({_reentry_dir} ≠ {signal}) → 일반 진입으로 전환")

                            # [Consciousness] 진입 신호 발견
                            _cs_sig_emoji = "🟢" if signal == "LONG" else "🔴"
                            _reentry_tag = " (🔄 재진입)" if _is_reentry_trade else ""
                            _emit_thought(symbol, f"{_cs_sig_emoji} {signal} 진입 신호 포착!{_reentry_tag} 진입 파이프라인 검증 시작...")
                            # [Flight Recorder] Decision Trail 파이프라인 추적 시작
                            _pipeline = []

                            # [Bug Fix] active_target 외 심볼 신규 진입 차단
                            # 봇은 모든 심볼을 감시하지만, 신규 진입은 active_target에만 허용
                            # 기존 포지션 관리(TP/SL/트레일링)는 모든 심볼에서 계속 동작
                            _active_symbols = get_config('symbols')
                            _active_target = _active_symbols[0] if isinstance(_active_symbols, list) and _active_symbols else None
                            if _active_target and symbol != _active_target:
                                _block_msg = f"[{symbol}] ⛔ 비활성 타겟 진입 차단 — 현재 활성 타겟: {_active_target}"
                                logger.info(_block_msg)
                                _log_trade_attempt(symbol, signal, "BLOCKED", "not_active_target")
                                _pipeline.append({"step": "active_target", "status": "BLOCKED", "detail": f"타겟: {_active_target}"})
                                _log_decision_trail(symbol, signal, "BLOCKED", _finalize_pipeline(_pipeline))
                                ai_brain_state["symbols"][symbol]["latest_decision_trail"] = _decision_trail_log[-1] if _decision_trail_log else None
                                continue

                            # [Phase 24 Fix] 캔들 잠금은 주문 성공 후로 지연 (아래 _log_trade_attempt SUCCESS 직전)
                            # → micro_account_protection / margin_insufficient 차단 시 같은 캔들에서 재시도 가능
                            # → 유저가 레버리지 변경 후 즉시 재진입 허용
                            _pipeline.append({"step": "active_target", "status": "PASS", "detail": "본 심볼 활성"})
                            _signal_start_time = _time.time()  # [Phase 21.1] A.D.S 레이턴시 측정 시작점
                            # [Phase 18.1] 방향 모드 필터 (LONG/SHORT/AUTO) — 코인별 독립 설정
                            _direction_mode = str(get_config('direction_mode', symbol) or 'AUTO').upper()
                            if _direction_mode == 'LONG' and signal != 'LONG':
                                _log_trade_attempt(symbol, signal, "BLOCKED", f"direction_mode_{_direction_mode}")
                                _pipeline.append({"step": "direction_mode", "status": "BLOCKED", "detail": f"LONG 전용: {signal} 차단"})
                                _log_decision_trail(symbol, signal, "BLOCKED", _finalize_pipeline(_pipeline))
                                ai_brain_state["symbols"][symbol]["latest_decision_trail"] = _decision_trail_log[-1] if _decision_trail_log else None
                                continue  # LONG 전용 모드: SHORT 신호 차단
                            if _direction_mode == 'SHORT' and signal != 'SHORT':
                                _log_trade_attempt(symbol, signal, "BLOCKED", f"direction_mode_{_direction_mode}")
                                _pipeline.append({"step": "direction_mode", "status": "BLOCKED", "detail": f"SHORT 전용: {signal} 차단"})
                                _log_decision_trail(symbol, signal, "BLOCKED", _finalize_pipeline(_pipeline))
                                ai_brain_state["symbols"][symbol]["latest_decision_trail"] = _decision_trail_log[-1] if _decision_trail_log else None
                                continue  # SHORT 전용 모드: LONG 신호 차단

                            _pipeline.append({"step": "direction_mode", "status": "PASS", "detail": f"{_direction_mode}"})
                            msg = f"[{symbol}] {signal} 진입 신호 — 현재가: ${current_price}, RSI: {latest_rsi:.1f}"
                            bot_global_state["logs"].append(msg)
                            logger.info(msg)

                            try:
                                # 수동 오버라이드 or [v2.2] ATR 기반 동적 포지션 사이징
                                manual_override = str(get_config('manual_override_enabled')).lower() == 'true'
                                # [Bug Fix] 레버리지 심볼별 완전 격리
                                # get_config(key, symbol)은 GLOBAL fallback 내장 → 직접 심볼 키 조회로 격리
                                if manual_override:
                                    trade_leverage = max(1, min(100, int(get_config('manual_leverage', symbol) or get_config('manual_leverage') or 1)))
                                else:
                                    # 1차: 심볼 전용 키 직접 조회 (GLOBAL fallback 우회)
                                    _sym_lev_direct = get_config(f"{symbol}::leverage")
                                    if _sym_lev_direct is not None:
                                        trade_leverage = max(1, min(100, int(_sym_lev_direct)))
                                    else:
                                        # 2차: GLOBAL leverage (마스터 튜닝 패널 설정값)
                                        _global_lev = get_config('leverage')
                                        trade_leverage = max(1, min(100, int(_global_lev or 1)))
                                        # [Safety] 소액 계좌 + GLOBAL fallback + 고레버리지 = 위험 경고
                                        if trade_leverage > 5 and curr_bal < 100:
                                            logger.warning(f"[{symbol}] ⚠️ 심볼 전용 레버리지 없음 → GLOBAL {trade_leverage}x 사용 (소액 계좌 ${curr_bal:.2f})")
                                try:
                                    contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                                except Exception:
                                    contract_size = 0.01
                                if manual_override:
                                    # [Phase 9.1] USDT → 계약수 환산
                                    # 공식: 계약수 = floor(입력USDT * 레버리지 / (현재가 * 계약당기초자산))
                                    seed_usdt = max(1.0, float(get_config('manual_amount') or 10))
                                    # [Phase 31] 수수료 여유분 확보 (dynamic 모드와 동일한 95% 안전 버퍼)
                                    safe_seed = seed_usdt * 0.95
                                    notional = safe_seed * trade_leverage
                                    trade_amount = max(1, round(notional / (current_price * contract_size)))
                                else:
                                    # [v2.3] 정률법 기반 동적 사이징 적용 (UI 연동 · 증거금 부족 패치)
                                    # [Phase 18.1] 코인별 리스크 비율 로드 (심볼 전용 우선, GLOBAL Fallback)
                                    _risk_rate = float(get_config('risk_per_trade', symbol) or 0.02)
                                    trade_amount = strategy_instance.calculate_position_size_dynamic(
                                        curr_bal, current_price, trade_leverage, contract_size, _risk_rate
                                    )
                                _pipeline.append({"step": "position_sizing", "status": "PASS", "detail": f"{trade_amount}계약 {trade_leverage}x"})
                                # [Bug Fix] 소액 계좌 방어: 1계약 증거금이 잔고의 50%를 초과하면 진입 차단
                                _min_1_contract_margin = (contract_size * current_price) / trade_leverage
                                if _min_1_contract_margin > curr_bal * 0.50:
                                    _micro_msg = (
                                        f"[{symbol}] ⛔ 소액 계좌 방어 발동: 1계약 증거금 ${_min_1_contract_margin:.2f} > "
                                        f"잔고 50% ${curr_bal * 0.50:.2f} — 이 코인은 현재 자본으로 거래 불가"
                                    )
                                    bot_global_state["logs"].append(_micro_msg)
                                    logger.warning(_micro_msg)
                                    _log_trade_attempt(symbol, signal, "BLOCKED", "micro_account_protection")
                                    # [Margin Guard] 소액 방어에서도 추천 레버리지 역산 + 텔레그램 알림 (5분 쿨다운)
                                    import math as _mg_math_micro
                                    _mg_micro_safe_bal = curr_bal * 0.50
                                    _mg_micro_1x_margin = contract_size * current_price
                                    _mg_micro_rec = min(100, _mg_math_micro.ceil(_mg_micro_1x_margin / _mg_micro_safe_bal)) if _mg_micro_safe_bal > 0 else 100
                                    if _mg_micro_rec > trade_leverage:
                                        _mg_micro_alert_key = f"_margin_guard_last_alert_{symbol}"
                                        _mg_micro_last = float(get_config(_mg_micro_alert_key) or 0)
                                        if _time.time() - _mg_micro_last > 300:  # 5분 쿨다운
                                            set_config(_mg_micro_alert_key, str(_time.time()))
                                            try:
                                                _mg_micro_tg = _tg_margin_guard(symbol, trade_leverage, _mg_micro_rec, curr_bal, _min_1_contract_margin)
                                                send_telegram_sync(_mg_micro_tg)
                                            except Exception:
                                                pass  # 텔레그램 실패 시 매매 흐름 보호
                                    _pipeline.append({"step": "micro_account", "status": "BLOCKED", "detail": f"1계약 ${_min_1_contract_margin:.2f} > 50% ${curr_bal * 0.50:.2f}"})
                                    _log_decision_trail(symbol, signal, "BLOCKED", _finalize_pipeline(_pipeline))
                                    ai_brain_state["symbols"][symbol]["latest_decision_trail"] = _decision_trail_log[-1] if _decision_trail_log else None
                                    continue  # 이 심볼 진입 건너뛰기

                                _pipeline.append({"step": "micro_account", "status": "PASS", "detail": f"1계약 ${_min_1_contract_margin:.2f}"})
                                # [Phase 31] 증거금 사전 검증 — 거래소 거부(51008) 선제 차단
                                _margin_needed = (contract_size * current_price * trade_amount) / trade_leverage
                                if curr_bal * 0.90 < _margin_needed:
                                    _margin_msg = f"[{symbol}] 증거금 사전 검증 실패: 필요 ${_margin_needed:.2f} vs 가용 ${curr_bal * 0.90:.2f} (90% 기준)"
                                    bot_global_state["logs"].append(_margin_msg)
                                    logger.warning(_margin_msg)
                                    _log_trade_attempt(symbol, signal, "BLOCKED", "margin_insufficient")
                                    # [Margin Guard] 추천 레버리지 역산 + 텔레그램 알림 (5분 쿨다운)
                                    import math as _mg_math2
                                    _mg_safe_bal = curr_bal * 0.50  # [Fix] micro_account_protection(50%) 기준과 통일
                                    _mg_1x_margin = contract_size * current_price
                                    _mg_rec = min(100, _mg_math2.ceil(_mg_1x_margin / _mg_safe_bal)) if _mg_safe_bal > 0 else 100
                                    if _mg_rec > trade_leverage:
                                        _mg_alert_key = f"_margin_guard_last_alert_{symbol}"
                                        _mg_last = float(get_config(_mg_alert_key) or 0)
                                        if _time.time() - _mg_last > 300:  # 5분 쿨다운
                                            set_config(_mg_alert_key, str(_time.time()))
                                            try:
                                                _mg_tg = _tg_margin_guard(symbol, trade_leverage, _mg_rec, curr_bal, _margin_needed)
                                                send_telegram_sync(_mg_tg)
                                            except Exception:
                                                pass  # 텔레그램 실패 시 매매 흐름 보호
                                    _pipeline.append({"step": "margin_check", "status": "BLOCKED", "detail": f"필요 ${_margin_needed:.2f} > 가용 ${curr_bal * 0.90:.2f}"})
                                    _log_decision_trail(symbol, signal, "BLOCKED", _finalize_pipeline(_pipeline))
                                    ai_brain_state["symbols"][symbol]["latest_decision_trail"] = _decision_trail_log[-1] if _decision_trail_log else None
                                    continue  # 이 심볼 진입 건너뛰기
                                _pipeline.append({"step": "margin_check", "status": "PASS", "detail": f"증거금 ${_margin_needed:.2f} OK"})
                                # 레버리지 거래소 적용 (실패 시 진입 차단 — 잘못된 레버리지로 주문 방지)
                                # [59669 방어] 레버리지 변경 전 잔여 주문(TP/SL·트레일링) 선취소
                                try:
                                    _open_orders = await asyncio.to_thread(engine_api.exchange.fetch_open_orders, symbol)
                                    if _open_orders:
                                        logger.info(f"[{symbol}] 레버리지 변경 전 잔여 주문 {len(_open_orders)}건 취소 시작")
                                        for _ord in _open_orders:
                                            try:
                                                await asyncio.to_thread(engine_api.exchange.cancel_order, _ord['id'], symbol)
                                            except Exception:
                                                pass
                                except Exception as _cancel_err:
                                    logger.warning(f"[{symbol}] 잔여 주문 취소 중 오류 (계속 진행): {_cancel_err}")
                                try:
                                    await asyncio.to_thread(engine_api.exchange.set_leverage, trade_leverage, symbol)
                                except Exception as lev_err:
                                    logger.error(f"[{symbol}] 레버리지 설정 실패 → 진입 차단: {lev_err}")
                                    _emit_thought(symbol, f"🚨 레버리지 {trade_leverage}x 설정 실패! 진입 차단됨 — {lev_err}")
                                    send_telegram_sync(
                                        f"🚨 <b>ANTIGRAVITY</b>  |  레버리지 설정 실패\n"
                                        f"{_TG_LINE}\n"
                                        f"심볼: <code>{_sym_short(symbol)}</code>  |  레버리지: {trade_leverage}x\n"
                                        f"원인: {str(lev_err)[:80]}\n"
                                        f"⛔ 진입이 차단되었습니다.\n"
                                        f"{_TG_LINE}"
                                    )
                                    continue  # 이 심볼 진입 건너뛰기
                                # 진입 방식 (Market vs Smart Limit)
                                order_type = str(get_config('ENTRY_ORDER_TYPE') or 'Market')
                                ema_20_val = float(df['ema_20'].iloc[-1]) if 'ema_20' in df.columns and not pd.isna(df['ema_20'].iloc[-1]) else current_price

                                # [Phase 23] Shadow Hunting 인터셉트 — execute_entry_order 호출 전 시그널 가로채기
                                _shadow_hunting = str(get_config('shadow_hunting_enabled') or 'false').lower() == 'true'
                                _is_shadow_hunt_order = False
                                if _shadow_hunting:
                                    logger.info(f"🐸 [Shadow Hunting] 청개구리 모드 가동 중. 시그널을 역이용합니다.")
                                    _original_direction = signal
                                    _hard_sl_rate = strategy_instance.hard_stop_loss_rate
                                    # [Phase 23 원복] SL 가격에 역방향 지정가 투척 — OKX 선물에서 즉시 체결됨
                                    # postOnly 제거: OKX 선물은 SL 레벨 지정가를 정상 체결 처리함
                                    if _original_direction == "LONG":
                                        # 원래 LONG → SHORT로 역전: 현재가 아래 SL 지점에 매도 지정가
                                        _shadow_limit_price = round(current_price * (1 - _hard_sl_rate), 4)
                                        signal = "SHORT"
                                    else:
                                        # 원래 SHORT → LONG으로 역전: 현재가 위 SL 지점에 매수 지정가
                                        _shadow_limit_price = round(current_price * (1 + _hard_sl_rate), 4)
                                        signal = "LONG"
                                    logger.info(f"🎯 [Shadow Hunting] 원래 방향: {_original_direction} -> 역방향: {signal}")
                                    logger.info(f"🕸️ [Shadow Hunting] 역방향 타점: ${_shadow_limit_price:,.4f}")
                                    # ── [Shadow Mode] Paper 모드 시 CCXT 완전 바이패스 (실거래 절대 금지) ──
                                    _sh_is_paper = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
                                    try:
                                        if _sh_is_paper:
                                            # Paper 모드: 가상 주문 ID 생성, 실거래소 API 호출 없음
                                            _sh_order = {'id': f"paper_sh_{int(_time.time() * 1000)}"}
                                        elif signal == "LONG":
                                            _sh_order = await asyncio.to_thread(
                                                engine_api.exchange.create_limit_buy_order,
                                                symbol, trade_amount, _shadow_limit_price, {}
                                            )
                                        else:
                                            _sh_order = await asyncio.to_thread(
                                                engine_api.exchange.create_limit_sell_order,
                                                symbol, trade_amount, _shadow_limit_price, {}
                                            )
                                        executed_price = _shadow_limit_price
                                        pending_order_id = _sh_order.get('id')
                                        order_type = 'Smart Limit'  # PENDING 분기 강제 진입
                                        _is_shadow_hunt_order = True
                                        logger.info(f"[{symbol}] 🕸️ Shadow Hunting 지정가 투척 완료 | 방향: {signal} | 가격: {_shadow_limit_price} | ID: {pending_order_id}")
                                        # [Phase 23.5] 가시성 확보 — UI 터미널 + 텔레그램 즉시 보고
                                        _sh_ui_msg = f"🐸 [그림자 사냥] 매복 지정가 투척 | {symbol} | {signal} | 타겟: ${_shadow_limit_price:,.4f} | 5분 대기"
                                        bot_global_state["logs"].append(_sh_ui_msg)
                                        save_log("INFO", _sh_ui_msg)
                                        _sh_tg_msg = (
                                            f"🐸 <b>그림자 사냥 (Shadow Hunting)</b>\n"
                                            f"────────────────────────\n"
                                            f"전략이 휩쏘 꼬리 사냥을 시작합니다.\n"
                                            f"• 원래 신호: <b>{_original_direction}</b> 반전 → <b>{signal}</b>\n"
                                            f"• 매복 가격: <b>${_shadow_limit_price:,.4f}</b>\n"
                                            f"⏳ 5분 내 미체결 시 자동 철수합니다."
                                        )
                                        send_telegram_sync(_sh_tg_msg)
                                    except Exception as _sh_err:
                                        logger.error(f"[{symbol}] 🐸 Shadow Hunting 주문 실패: {_sh_err}")
                                        bot_global_state["symbols"][symbol]["last_signal_candle_ts"] = _cur_candle_ts  # 실패 시 캔들 잠금
                                        bot_global_state["symbols"][symbol]["_candle_lock_set_time"] = _time.time()
                                        _log_trade_attempt(symbol, signal, "FAILED", f"shadow_hunting: {str(_sh_err)[:80]}")
                                        _pipeline.append({"step": "order_execution", "status": "FAILED", "detail": f"Shadow: {str(_sh_err)[:50]}"})
                                        _log_decision_trail(symbol, signal, "FAILED", _finalize_pipeline(_pipeline))
                                        ai_brain_state["symbols"][symbol]["latest_decision_trail"] = _decision_trail_log[-1] if _decision_trail_log else None
                                        continue  # 실패 시 이번 사이클 스킵

                                if not _is_shadow_hunt_order:
                                    # [Consciousness] 진입 주문 실행
                                    _cs_emoji = "🟢" if signal == "LONG" else "🔴"
                                    if order_type == 'Market':
                                        _emit_thought(symbol, f"{_cs_emoji} {signal} 시장가 진입 실행! ${current_price:,.2f} × {trade_amount}계약 ({trade_leverage}x)")
                                    else:
                                        _emit_thought(symbol, f"{_cs_emoji} {signal} 스마트 지정가 주문 제출 중... EMA20 ${ema_20_val:,.2f} 기준")
                                    # [DRY] 단일 헬퍼로 주문 실행 (Shadow Hunting이 아닐 때만)
                                    executed_price, pending_order_id = await execute_entry_order(
                                        engine_api, symbol, signal, trade_amount, order_type, current_price, ema_20_val
                                    )

                                # 포지션 상태 업데이트 (Smart Limit인 경우 PENDING 상태로 대기)
                                if order_type == 'Smart Limit':
                                    _is_shadow_pending = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
                                    _paper_tag_p = "[👻 PAPER] " if _is_shadow_pending else ""
                                    # [Phase 20.1] 상태 변경 시 자물쇠 잠금
                                    async with state_lock:
                                        bot_global_state["symbols"][symbol]["position"] = "PENDING_" + signal
                                        bot_global_state["symbols"][symbol]["pending_order_id"] = pending_order_id
                                        bot_global_state["symbols"][symbol]["pending_order_time"] = _time.time()
                                        bot_global_state["symbols"][symbol]["pending_amount"] = trade_amount
                                        bot_global_state["symbols"][symbol]["pending_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["is_paper"] = _is_shadow_pending
                                        # [Phase 23] 그림자 사냥 포지션 여부 각인
                                        bot_global_state["symbols"][symbol]["is_shadow_hunting"] = _is_shadow_hunt_order
                                        # [Phase 28] 레버리지 저장 (체결 후 텔레그램 알림에서 참조)
                                        bot_global_state["symbols"][symbol]["leverage"] = trade_leverage

                                    # [Phase 24 Fix] 주문 성공 후 캔들 잠금 — 차단 시 재시도 허용
                                    bot_global_state["symbols"][symbol]["last_signal_candle_ts"] = _cur_candle_ts
                                    bot_global_state["symbols"][symbol]["_candle_lock_set_time"] = _time.time()
                                    _log_trade_attempt(symbol, signal, "SUCCESS")
                                    _pipeline.append({"step": "order_execution", "status": "PASS", "detail": f"Smart Limit ${executed_price:.2f}"})
                                    _log_decision_trail(symbol, signal, "SUCCESS", _finalize_pipeline(_pipeline))
                                    ai_brain_state["symbols"][symbol]["latest_decision_trail"] = _decision_trail_log[-1] if _decision_trail_log else None
                                    entry_emoji = "⏳"
                                    entry_msg = f"{_paper_tag_p}{entry_emoji} [{symbol}] {signal} 스마트 지정가 주문 접수 | 목표가: ${executed_price:.2f} | {trade_amount}계약 (5분 내 미체결 시 취소)"
                                    bot_global_state["logs"].append(entry_msg)
                                    logger.info(entry_msg)

                                    # [Phase 18.2] 스마트 지정가 접수 시 텔레그램 알림 즉시 발송
                                    send_telegram_sync(_tg_pending(symbol, signal, executed_price, trade_amount, trade_leverage, is_test=_is_shadow_pending))
                                else:
                                    _is_shadow_entry = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
                                    _paper_tag = "[👻 PAPER] " if _is_shadow_entry else ""
                                    # [Phase 20.1] 상태 변경 시 자물쇠 잠금
                                    async with state_lock:
                                        bot_global_state["symbols"][symbol]["position"] = signal
                                        bot_global_state["symbols"][symbol]["entry_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["highest_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["lowest_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["leverage"] = trade_leverage
                                        bot_global_state["symbols"][symbol]["contracts"] = trade_amount
                                        bot_global_state["symbols"][symbol]["is_paper"] = _is_shadow_entry
                                        bot_global_state["symbols"][symbol]["entry_timestamp"] = _time.time()  # [Race Condition Fix]
                                        # [방어막] 이전 포지션 잔류 플래그 강제 초기화 — SL 진입가 이동 방지
                                        bot_global_state["symbols"][symbol]["partial_tp_executed"] = False
                                        bot_global_state["symbols"][symbol]["breakeven_stop_active"] = False
                                        bot_global_state["symbols"][symbol]["exchange_tp_filled"] = False

                                    # [Phase 24 Fix] 주문 성공 후 캔들 잠금 — 차단 시 재시도 허용
                                    bot_global_state["symbols"][symbol]["last_signal_candle_ts"] = _cur_candle_ts
                                    bot_global_state["symbols"][symbol]["_candle_lock_set_time"] = _time.time()
                                    _log_trade_attempt(symbol, signal, "SUCCESS")
                                    _pipeline.append({"step": "order_execution", "status": "PASS", "detail": f"Market ${executed_price:.2f}"})
                                    _log_decision_trail(symbol, signal, "SUCCESS", _finalize_pipeline(_pipeline))
                                    ai_brain_state["symbols"][symbol]["latest_decision_trail"] = _decision_trail_log[-1] if _decision_trail_log else None
                                    # [Phase 21.1] A.D.S 자가 진단: 레이턴시 & 슬리피지 측정
                                    _latency_ms = (_time.time() - _signal_start_time) * 1000
                                    _ref_price = payload.get('close', executed_price) if payload else executed_price
                                    _slippage_pct = abs(executed_price - _ref_price) / _ref_price * 100 if _ref_price else 0.0
                                    _diag_msg = (f"🩻 [A.D.S DIAG] [{symbol}] {signal} 진입 진단 | "
                                                 f"레이턴시: {_latency_ms:.0f}ms | "
                                                 f"슬리피지: {_slippage_pct:.4f}% | "
                                                 f"기준가: ${_ref_price:.4f} → 체결가: ${executed_price:.4f}")
                                    bot_global_state["logs"].append(_diag_msg)
                                    logger.info(_diag_msg)

                                    entry_emoji = "📈" if signal == "LONG" else "📉"
                                    entry_msg = f"{_paper_tag}{entry_emoji} [{symbol}] {signal} 시장가 진입 성공! | 가격: ${executed_price:.2f} | {trade_amount}계약 | 레버리지 {trade_leverage}x"
                                    bot_global_state["logs"].append(entry_msg)
                                    logger.info(entry_msg)
                                    send_telegram_sync(_tg_entry(symbol, signal, executed_price, trade_amount, trade_leverage, payload=payload, is_test=_is_shadow_entry))

                                    # ── [재진입 로직] 재진입 성공 시 자격 소멸 + 카운터 + 알림 ──
                                    if bot_global_state["symbols"][symbol].get("_reentry_eligible", False):
                                        bot_global_state["symbols"][symbol]["_reentry_count"] = bot_global_state["symbols"][symbol].get("_reentry_count", 0) + 1
                                        bot_global_state["symbols"][symbol]["_reentry_eligible"] = False
                                        bot_global_state["symbols"][symbol]["_reentry_direction"] = ""
                                        logger.info(f"[{symbol}] 🔄 재진입 실행 완료! {signal} | 카운터: {bot_global_state['symbols'][symbol]['_reentry_count']}/1")
                                        send_telegram_sync(
                                            f"🔄 <b>재진입 실행</b>\n{_TG_LINE}\n"
                                            f"코인 │ <code>{_sym_short(symbol)}</code>\n"
                                            f"방향 │ <b>{signal}</b> (같은 방향 재진입)\n"
                                            f"체결가 │ ${executed_price:,.2f}\n"
                                            f"사유 │ 본전/소익 트레일링 후 추세 지속\n{_TG_LINE}\n"
                                            f"📌 재진입 1/1회 사용 (추가 재진입 차단)"
                                        )

                                    # [Phase 22.2] 진입 직후 거래소 서버에 초기 거미줄(Limit TP / Stop SL) 투척
                                    if not _is_shadow_entry:  # 페이퍼 모드가 아닐 때만 실제 주문 전송
                                        try:
                                            _entry_p = float(bot_global_state["symbols"][symbol]["entry_price"])
                                            _amt = float(bot_global_state["symbols"][symbol]["contracts"])
                                            _close_side = "sell" if signal == "LONG" else "buy"

                                            # [Phase 26] 초기 익절가: UI 표시 공식과 100% 동일 (수수료 0.15% + ATR * 50%)
                                            _entry_atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else float(_entry_p * 0.01)
                                            _target_offset = (_entry_p * 0.0015) + (_entry_atr * 0.5)
                                            _init_tp = round(_entry_p + _target_offset, 4) if signal == "LONG" else round(_entry_p - _target_offset, 4)
                                            # [Phase 26] 초기 손절가: 튜닝 패널 hard_stop_loss_rate 설정 직결
                                            _sl_rate = strategy_instance.hard_stop_loss_rate
                                            # [방어막] SL rate가 0이거나 비정상적으로 작으면 최소 0.2% 보장
                                            if _sl_rate < 0.002:
                                                logger.warning(f"[{symbol}] ⚠️ hard_stop_loss_rate 이상({_sl_rate}) → 최소 0.002(0.2%)로 교정")
                                                _sl_rate = 0.002
                                            _init_sl = round(_entry_p * (1 - _sl_rate), 4) if signal == "LONG" else round(_entry_p * (1 + _sl_rate), 4)

                                            # [TP Split] 멀티계약: TP 50% / 1계약: TP 미등록(트레일링 전용)
                                            _full_int = int(_amt)

                                            # CCXT 규격에 맞춘 Reduce-Only 파라미터
                                            _params_sl = {"reduceOnly": True, "stopLossPrice": _init_sl}

                                            # Stop-Market SL (조건부 시장가 손절) 전송 — 전체 수량 (1계약/다계약 공통)
                                            sl_order = await asyncio.to_thread(
                                                engine_api.exchange.create_order,
                                                symbol, 'market', _close_side, _amt, None, _params_sl
                                            )
                                            bot_global_state["symbols"][symbol]["active_sl_order_id"] = sl_order['id']
                                            bot_global_state["symbols"][symbol]["last_placed_sl_price"] = _init_sl

                                            if _full_int > 1:
                                                # [멀티계약] TP 50% 지정가 등록 — 기존 방식 유지
                                                _tp_amt = max(1, _full_int // 2)
                                                _params_tp = {"reduceOnly": True}
                                                tp_order = await asyncio.to_thread(
                                                    engine_api.exchange.create_order,
                                                    symbol, 'limit', _close_side, _tp_amt, _init_tp, _params_tp
                                                )
                                                bot_global_state["symbols"][symbol]["active_tp_order_id"] = tp_order['id']
                                                bot_global_state["symbols"][symbol]["last_placed_tp_price"] = _init_tp
                                                bot_global_state["symbols"][symbol]["tp_order_amount"] = _tp_amt
                                                logger.info(f"[{symbol}] 🕸️ 거래소 초기 방어막 전송 완료 | TP: {_init_tp:.4f} (50%, {_tp_amt}계약) / SL: {_init_sl:.4f}")
                                            else:
                                                # [1계약 전용] TP 지정가 미등록 → 트레일링 스탑으로만 익절
                                                # R:R 0.7:1 문제 해결: SL만 거래소에 걸고, 수익은 트레일링이 극대화
                                                bot_global_state["symbols"][symbol]["active_tp_order_id"] = None
                                                bot_global_state["symbols"][symbol]["last_placed_tp_price"] = 0.0
                                                bot_global_state["symbols"][symbol]["tp_order_amount"] = 0
                                                logger.info(f"[{symbol}] 🕸️ 1계약 트레일링 전용 모드 | TP 미등록 (트레일링 스탑으로 익절) / SL: {_init_sl:.4f}")
                                        except Exception as limit_err:
                                            logger.error(f"[{symbol}] 🚨 초기 방어막(Limit/Stop) 전송 실패. 거래소 수동 확인 요망: {limit_err}")

                            except Exception as e:
                                bot_global_state["symbols"][symbol]["last_signal_candle_ts"] = _cur_candle_ts  # 실패 시 캔들 잠금
                                bot_global_state["symbols"][symbol]["_candle_lock_set_time"] = _time.time()
                                _log_trade_attempt(symbol, signal, "FAILED", str(e)[:100])
                                error_msg = f"[{symbol}] 진입 실패: {str(e)}"
                                bot_global_state["logs"].append(error_msg)
                                logger.error(error_msg)
                                _emit_thought(symbol, f"🚨 진입 실패! {signal} — {str(e)[:60]}")
                                send_telegram_sync(
                                    f"🚨 <b>ANTIGRAVITY</b>  |  진입 실패\n"
                                    f"{_TG_LINE}\n"
                                    f"심볼: <code>{_sym_short(symbol)}</code>  |  방향: {signal}\n"
                                    f"원인: {str(e)[:100]}\n"
                                    f"{_TG_LINE}"
                                )

                except Exception as e:
                    logger.warning(f"[{symbol}] 루프 처리 중 오류 (다음 루프 계속): {e}")

                # [Phase 20.4] API Rate Limit 방어용 스마트 스로틀링 (코인 간 0.5초 휴식)
                # 다중 코인 감시 시 거래소 서버 폭격(HTTP 429 에러) 방지
                await asyncio.sleep(0.5)

            # 5초마다 엔진 맥박(Pulse) 로그 출력
            current_time = _time.time()
            if current_time - last_log_time >= 5:
                for sym, stat in ai_brain_state["symbols"].items():
                    price = stat.get('price', 0)
                    rsi = stat.get('rsi', 0)
                    macd = stat.get('macd', 0)
                    sym_state = bot_global_state["symbols"].get(sym, {})
                    position = sym_state.get("position", "NONE")

                    if position != "NONE":
                        entry_price = sym_state.get("entry_price", 0)
                        pnl_pct = sym_state.get("unrealized_pnl_percent", 0)
                        pos_emoji = "📈" if position == "LONG" else "📉"
                        pnl_sign = "+" if pnl_pct >= 0 else ""
                        engine_msg = f"{pos_emoji} [{sym}] {position} 포지션 유지 중 | 진입가: ${entry_price:.2f} | 현재가: ${price} | 수익률: {pnl_sign}{pnl_pct:.2f}%"
                    else:
                        engine_msg = f"[감시] {sym} 현재가: ${price} | RSI: {rsi:.1f} | MACD: {macd:.2f} | 타점 탐색 중..."

                    bot_global_state["logs"].append(engine_msg)
                    logger.info(engine_msg)
                last_log_time = current_time

            # [Phase 20.3] 1회 사이클 무사 통과 시 에러 카운터 초기화
            consecutive_errors = 0

            # [Phase 20.4] 코인 간 0.5초씩 이미 쉬었으므로 메인 루프 휴식은 1초로 유지
            await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("⚠️ 매매 엔진 루프가 강제 취소되었습니다.")
            break

        except Exception as e:
            # [Phase 20.3] 연속 에러 감지 및 킬스위치 (뇌사 방어)
            consecutive_errors += 1
            logger.error(f"🚨 매매 루프 치명적 에러 발생 (누적 {consecutive_errors}회): {e}", exc_info=True)
            err_msg = f"[오류] 매매 루프 예외 발생 (누적 {consecutive_errors}회) - 3초 후 재시작: {str(e)}"
            bot_global_state["logs"].append(err_msg)

            if consecutive_errors >= 3:
                # [Phase 21.2] 스트레스 바이패스: kill_switch 활성 시 연속 에러 킬스위치 무시
                if _is_bypass_active('stress_bypass_kill_switch'):
                    logger.warning(f"⚠️ [STRESS BYPASS] 킬 스위치 조건 충족({consecutive_errors}회) → 바이패스 활성 중, 무시")
                    consecutive_errors = 0
                else:
                    _sos_msg = (
                        f"🚨 <b>[CRITICAL FATAL ERROR]</b> 🚨\n"
                        f"{_TG_LINE}\n"
                        f"봇의 뇌 구조에 치명적인 연속 에러가 3회 감지되었습니다.\n"
                        f"추가적인 자산 손실(슬리피지/오작동)을 막기 위해\n"
                        f"<b>매매 엔진을 강제 셧다운(Kill-Switch) 합니다.</b>\n"
                        f"{_TG_LINE}\n"
                        f"오류: <code>{str(e)[:100]}...</code>\n"
                        f"조치: 서버 로그 확인 및 시스템 재가동 요망"
                    )
                    logger.critical("💀 [KILL-SWITCH] 메인 루프 뇌사 상태 감지. 봇 강제 종료!")
                    send_telegram_sync(_sos_msg)
                    bot_global_state["is_running"] = False
                    break

            # [Consciousness] 루프 대기
            for _cs_idle_sym in (symbols if 'symbols' in dir() else []):
                _emit_thought(_cs_idle_sym, "💤 다음 사이클 대기 중... (3초 후 재분석)", throttle_key=f"idle_{_cs_idle_sym}", throttle_sec=30.0)
            await asyncio.sleep(3)

# ===== [Phase 21.2] 스트레스 테스트 바이패스 엔드포인트 =====

