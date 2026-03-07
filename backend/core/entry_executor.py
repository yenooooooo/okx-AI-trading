"""
core/entry_executor.py — 진입 주문 실행 + Adaptive Shield 잔고 기반 파라미터 튜닝
  - execute_entry_order   : 시장가/Smart Limit/Shadow 진입 주문
  - _auto_tune_by_balance : 잔고 티어별 파라미터 자동 전환
"""
import asyncio
import time as _time

from database import get_config
from notifier import send_telegram_sync
from logger import get_logger
from core.state import bot_global_state, _g, _loop_xray_state, BALANCE_TIERS
from core.helpers import _emit_thought, _log_trade_attempt
from core.tg_formatters import _tg_entry, _tg_pending

logger = get_logger(__name__)


async def execute_entry_order(engine_api, symbol: str, signal: str, trade_amount: int, order_type: str, current_price: float, ema_20_val: float = None):
    """
    [DRY] Market / Smart Limit 진입 주문 실행 헬퍼.
    Returns (executed_price, pending_order_id)
      - Market  → (current_price, None)
      - Smart Limit → (limit_price, order_id)
      - Shadow Mode → (current_price or limit_price, paper_xxx) — CCXT 바이패스
    3회 재시도(50013 방어) 포함. 실패 시 예외를 raise 한다.
    """
    import uuid
    if ema_20_val is None:
        ema_20_val = current_price

    # ── [Shadow Mode] CCXT 완전 바이패스 ──────────────────────────────────────
    is_shadow = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
    if is_shadow:
        if order_type == 'Smart Limit':
            if signal == "LONG":
                executed_price = round(max(current_price, ema_20_val), 2)
            else:
                executed_price = round(min(current_price, ema_20_val), 2)
            return executed_price, f"paper_{uuid.uuid4().hex[:8]}"
        else:
            return current_price, None

    # ── [실전 모드] 실제 거래소 API 호출 ──────────────────────────────────────
    order_success = False
    last_error = None
    executed_price = current_price
    pending_order_id = None

    for attempt in range(3):
        try:
            if order_type == 'Smart Limit':
                ob = await asyncio.to_thread(engine_api.exchange.fetch_order_book, symbol, 5)
                best_bid = ob['bids'][0][0] if ob['bids'] else current_price
                best_ask = ob['asks'][0][0] if ob['asks'] else current_price

                if signal == "LONG":
                    limit_price = round(max(best_bid, ema_20_val), 2)
                    order = await asyncio.to_thread(engine_api.exchange.create_limit_buy_order, symbol, trade_amount, limit_price)
                else:
                    limit_price = round(min(best_ask, ema_20_val), 2)
                    order = await asyncio.to_thread(engine_api.exchange.create_limit_sell_order, symbol, trade_amount, limit_price)

                pending_order_id = order.get('id')
                executed_price = limit_price
            else:
                if signal == "LONG":
                    order = await asyncio.to_thread(engine_api.exchange.create_market_buy_order, symbol, trade_amount)
                else:
                    order = await asyncio.to_thread(engine_api.exchange.create_market_sell_order, symbol, trade_amount)

                # [방어막] 시장가 실제 체결가 반영 — current_price 대신 거래소 응답 체결가 사용
                _fill_price = order.get('average') or order.get('price')
                if _fill_price and float(_fill_price) > 0:
                    executed_price = float(_fill_price)
                    # 슬리피지 경고 (0.3% 초과 시 텔레그램 알림)
                    _slip_pct = abs(executed_price - current_price) / current_price * 100
                    if _slip_pct > 0.3:
                        send_telegram_sync(
                            f"⚠️ <b>슬리피지 감지</b>\n"
                            f"코인: <code>{symbol.split(':')[0]}</code>\n"
                            f"예상가: ${current_price:.4f} → 체결가: ${executed_price:.4f}\n"
                            f"슬리피지: {_slip_pct:.3f}%"
                        )

            order_success = True
            break
        except Exception as api_err:
            last_error = api_err
            if "50013" in str(api_err):
                logger.warning(f"[{symbol}] OKX Sandbox 50013 에러(시스템 바쁨). 0.5초 후 재시도 ({attempt+1}/3)")
                await asyncio.sleep(0.5)
            else:
                raise api_err

    if not order_success:
        raise last_error

    return executed_price, pending_order_id


# [Phase 25] Adaptive Shield: 잔고 기반 자동 방어 티어 전환
_last_valid_balance = 0.0  # [방어 필터] 마지막 정상 잔고 (API 오류 방어용)

async def _auto_tune_by_balance(curr_bal):
    """잔고 규모에 따라 전략 파라미터를 자동 전환하여 자본 보호 극대화"""
    global _last_valid_balance

    # 0. [방어 필터] API 오류로 잔고가 0이거나 이전 대비 70% 이상 급감 시 무시
    if curr_bal <= 0:
        logger.debug(f"[Adaptive Shield] 잔고 0 감지 — API 오류 의심, 티어 전환 스킵")
        return
    if _last_valid_balance > 0 and curr_bal < _last_valid_balance * 0.3:
        logger.warning(f"[Adaptive Shield] 잔고 급감 감지 (${_last_valid_balance:.2f} → ${curr_bal:.2f}) — API 오류 의심, 티어 전환 스킵")
        return
    _last_valid_balance = curr_bal

    # 0-1. [부팅 복구] 서버 재시작 시 DB에 남아있는 이전 티어를 메모리에 로드
    #       메모리 티어가 비어있으면 DB에서 복원하여 정상 재평가 유도
    if not bot_global_state.get("adaptive_tier"):
        _db_tier = str(get_config('_current_adaptive_tier') or '')
        if _db_tier:
            bot_global_state["adaptive_tier"] = _db_tier

    # 1. 기능 활성화 여부 체크
    if str(get_config('auto_preset_enabled') or 'false').lower() != 'true':
        # OFF 전환 시 티어 상태 클리어 (배지 잔류 방지)
        if bot_global_state.get("adaptive_tier", ""):
            bot_global_state["adaptive_tier"] = ""
            set_config('_current_adaptive_tier', '')
        return

    # 2. 현재 티어 판정 (잔고 구간별)
    new_tier = None
    for tier_name in ['CRITICAL', 'MICRO', 'STANDARD', 'GROWTH']:
        if curr_bal <= BALANCE_TIERS[tier_name]['max_balance']:
            new_tier = tier_name
            break

    current_tier = bot_global_state.get("adaptive_tier", "")

    # 3. 히스테리시스 5%: 경계값 근처 진동 방지 (예: $100에서 $98↔$102 반복 전환 차단)
    if current_tier and current_tier != new_tier:
        threshold = BALANCE_TIERS[current_tier]['max_balance']
        if threshold != float('inf') and threshold > 0:
            if abs(curr_bal - threshold) / threshold < 0.05:
                return  # 경계값 5% 이내 → 전환 보류

    # 4. 티어 변경 없으면 스킵
    if new_tier == current_tier:
        return

    # 5. 포지션 보유 중이면 전환 보류 (mid-trade 파라미터 변경 방지)
    any_position = any(
        s.get("position", "NONE") != "NONE"
        for s in bot_global_state["symbols"].values()
    )
    if any_position:
        return

    # 6. 새 티어 적용: DB에 일괄 저장
    tier_config = BALANCE_TIERS[new_tier]['config']
    for key, value in tier_config.items():
        set_config(key, str(value))

    # 7. 상태 기록
    bot_global_state["adaptive_tier"] = new_tier
    set_config('_current_adaptive_tier', new_tier)

    tier_info = BALANCE_TIERS[new_tier]
    msg = f"{tier_info['emoji']} [Adaptive Shield] 방어 등급 전환: {current_tier or 'INIT'} → {new_tier} | 잔고: ${curr_bal:.2f} | {tier_info['description']}"
    bot_global_state["logs"].append(msg)
    logger.info(msg)

    # 텔레그램 알림
    _tg_adaptive = (
        f"{tier_info['emoji']} <b>Adaptive Shield 방어 등급 전환</b>\n"
        f"{_TG_LINE}\n"
        f"전환: <b>{current_tier or 'INIT'} → {new_tier}</b>\n"
        f"잔고: <b>${curr_bal:.2f}</b>\n"
        f"{_TG_LINE}\n"
        f"{tier_info['description']}\n"
        f"risk: {tier_config['risk_per_trade']} | SL: {tier_config['hard_stop_loss_rate']} | daily_max: {tier_config['daily_max_loss_rate']}"
    )
    send_telegram_sync(_tg_adaptive)


