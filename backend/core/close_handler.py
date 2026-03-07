"""
core/close_handler.py — 수동 청산 감지 및 PnL 정산 처리
  - _detect_and_handle_manual_close : OKX 체결 영수증 기반 수동청산 감지/정산/알림
"""
import asyncio
import time as _time

from database import get_config, save_trade
from notifier import send_telegram_sync
from logger import get_logger
from core.state import bot_global_state, _g
from core.helpers import _reset_position_state
from core.tg_formatters import _TG_LINE, _tg_manual_exit

logger = get_logger(__name__)


async def _detect_and_handle_manual_close(engine_api, symbol: str, sym_state: dict, manual_prev_state: dict = None):
    """
    외부 수동 청산 감지 후 처리:
      - OKX 체결 영수증에서 실현 PnL 추출
      - DB에 MANUAL_CLOSE 기록
      - 터미널 로그 + 텔레그램 알림 발송 (요청된 포맷)
      - 봇 내부 상태를 NONE으로 초기화
    sym_state 는 bot_global_state["symbols"][symbol] 의 참조(reference).
    """
    if manual_prev_state:
        prev_pos       = manual_prev_state.get("position", "NONE")
        prev_entry     = manual_prev_state.get("entry_price", 0.0)
        prev_contracts = int(manual_prev_state.get("contracts", 1))
        prev_leverage  = int(manual_prev_state.get("leverage", 1))
        prev_entry_ts  = float(manual_prev_state.get("entry_timestamp", 0))
    else:
        prev_pos       = sym_state.get("position", "NONE")
        prev_entry     = sym_state.get("entry_price", 0.0)
        prev_contracts = int(sym_state.get("contracts", 1))
        prev_leverage  = int(sym_state.get("leverage", 1))
        prev_entry_ts  = float(sym_state.get("entry_timestamp", 0))

    if prev_pos == "NONE" or prev_entry <= 0:
        return  # 처리할 포지션 없음

    # [Phase 22.4] 고아 주문(Orphan Orders) 청소: 포지션 종료 시 허공에 남은 잔여 거미줄 일괄 취소
    _tp_id = sym_state.get("active_tp_order_id")
    _sl_id = sym_state.get("active_sl_order_id")

    if _tp_id or _sl_id:
        logger.info(f"[{symbol}] 🧹 포지션 종료 감지. 잔여 거미줄(고아 주문) 청소 시작...")
        for _oid in [_tp_id, _sl_id]:
            if _oid:
                try:
                    await asyncio.to_thread(engine_api.exchange.cancel_order, _oid, symbol)
                    logger.info(f"[{symbol}] 🗑️ 잔여 거미줄 찢기 완료 (ID: {_oid})")
                except Exception as clear_err:
                    # 이미 체결되었거나 취소된 경우 자연스러운 현상이므로 패스
                    logger.warning(f"[{symbol}] 잔여 거미줄 취소 실패(이미 소멸됨): {clear_err}")

    # ── 즉시 상태 초기화 (같은 사이클 중복 감지 방지) ──────────────────────
    # [Phase 32] 통합 상태 초기화 헬퍼 사용
    _reset_position_state(sym_state)

    pnl_amount     = 0.0
    total_gross    = 0.0
    total_fee      = 0.0
    avg_fill_price = prev_entry  # fallback
    pnl_pct        = 0.0

    # ── OKX 체결 영수증(Trades) 조회 (최대 6회, 약 12초 대기) ─────────────────────────
    # [Bug Fix] 진입 시각 기준 조회 + 청산 방향/수량 필터로 이전 포지션 체결 혼입 방지
    _entry_based_ts = int(prev_entry_ts * 1000) if prev_entry_ts > 0 else int((_time.time() - 120) * 1000)
    since_ts = max(_entry_based_ts, int((_time.time() - 300) * 1000))  # 진입 시각 또는 최대 5분 전
    found_receipt = False

    for attempt in range(6):
        try:
            trades = await asyncio.to_thread(engine_api.exchange.fetch_my_trades, symbol, since=since_ts)
            if trades:
                # 청산 방향 필터: LONG 청산 = sell, SHORT 청산 = buy
                close_side = 'sell' if prev_pos == 'LONG' else 'buy'
                closing_trades = [t for t in trades if t.get('side') == close_side]

                # [방어막] 체결 수량 검증 — 보유 계약수와 일치하는 체결만 허용
                # 이전 포지션의 분할 익절/손절 체결이 혼입되는 것을 방지
                if len(closing_trades) > 1:
                    _total_matched = sum(float(t.get('amount', 0)) for t in closing_trades)
                    if _total_matched > prev_contracts * 1.5:
                        # 체결 수량이 보유 수량의 1.5배 초과 → 최신 체결만 사용 (이전 포지션 혼입 의심)
                        # 타임스탬프 역순 정렬 후 보유 수량에 맞는 최신 체결만 추출
                        closing_trades.sort(key=lambda t: t.get('timestamp', 0), reverse=True)
                        _trimmed = []
                        _acc = 0.0
                        for t in closing_trades:
                            _acc += float(t.get('amount', 0))
                            _trimmed.append(t)
                            if _acc >= prev_contracts:
                                break
                        closing_trades = _trimmed
                        logger.warning(f"[{symbol}] ⚠️ 체결 수량 초과 감지 (합계: {_total_matched} > 보유: {prev_contracts}) → 최신 {len(_trimmed)}건으로 트리밍")

                if not closing_trades:
                    closing_trades = [trades[-1]]  # fallback: 마지막 체결 사용

                # [DRY] 자동청산과 동일한 PnL 계산 함수 사용 (info.fillPnl + 양방향 수수료 역산)
                pnl_amount, total_gross, _fee_raw, avg_fill_price = engine_api.calculate_realized_pnl(closing_trades, prev_entry)
                total_fee = abs(_fee_raw)  # 디스플레이용 양수 변환
                found_receipt = True
                logger.info(f"[{symbol}] 🧾 수동청산 영수증 확보 성공 (시도: {attempt+1}/6, 체결 {len(closing_trades)}건) | Net: {pnl_amount:.4f}, Gross: {total_gross:.4f}, Fee: {total_fee:.4f}")
                break
        except Exception as e:
            logger.warning(f"[{symbol}] 영수증 조회 에러 (시도: {attempt+1}/6): {e}")

        await asyncio.sleep(2.0)  # 2초 간격 폴링

    # 12초 대기 후에도 영수증이 없다면 '가상 영수증(Estimated PnL)' 직접 발급
    if not found_receipt:
        logger.warning(f"[{symbol}] ⚠️ 거래소 응답 지연으로 영수증 확보 실패. 가상 수익(Estimated PnL) 추정 계산 실행!")
        pnl_amount = 0.0
        total_gross = 0.0
        total_fee = 0.0
        try:
            entry_p = prev_entry
            amount  = float(prev_contracts)
            _fallback_price = (sym_state.get("current_price", 0)
                               or await asyncio.to_thread(engine_api.get_current_price, symbol)
                               or prev_entry)
            avg_fill_price = _fallback_price

            if entry_p > 0:
                try:
                    _cs = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                except Exception:
                    _cs = 0.01

                if prev_pos == "LONG":
                    total_gross = (_fallback_price - entry_p) * amount * _cs
                else:
                    total_gross = (entry_p - _fallback_price) * amount * _cs

                total_fee = (entry_p * amount * _cs * 0.0005) + (_fallback_price * amount * _cs * 0.0005)
                pnl_amount = total_gross - total_fee
                logger.info(f"[{symbol}] 🧮 가상 영수증 발급 완료 | 추정 Net: {pnl_amount:.4f}, Gross: {total_gross:.4f}, Fee: {total_fee:.4f}")
        except Exception as calc_err:
            logger.error(f"[{symbol}] 🚨 가상 수익 추정마저 실패: {calc_err}")
        # 가상 영수증 사용 경고 알림
        send_telegram_sync(
            f"⚠️ <b>수동청산 영수증 미확보</b>\n{_TG_LINE}\n"
            f"<code>{_sym_short(symbol)}</code> │ 거래소 응답 지연으로 추정 PnL 사용\n"
            f"📌 OKX 앱에서 실제 체결 내역을 확인하세요."
        )

    # [방어] avg_fill_price가 0이면 현재가 또는 진입가로 대체
    if avg_fill_price <= 0:
        avg_fill_price = await asyncio.to_thread(engine_api.get_current_price, symbol) or prev_entry

    # ── PnL% 계산 (공식 수익금 기반) ──────────────────────────────────────────────────────────
    try:
        contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
    except Exception:
        contract_size = 0.01
    
    position_value = prev_entry * prev_contracts * contract_size
    
    # 공식 수익금이 0이 아니라면 공식 수익률을 계산
    pnl_pct = (
        (pnl_amount / (position_value / prev_leverage) * 100)
        if position_value > 0 and prev_leverage > 0 else 0.0
    )

    # ── DB 저장 ───────────────────────────────────────────────────────────
    try:
        save_trade(
            symbol        = symbol,
            position_type = prev_pos,
            entry_price   = prev_entry,
            exit_price    = round(avg_fill_price, 2),
            pnl           = round(pnl_amount, 4),
            pnl_percent   = round(pnl_pct, 4),
            fee           = round(total_fee, 4),
            gross_pnl     = round(total_gross, 4),
            amount        = prev_contracts,
            exit_reason   = "MANUAL_CLOSE",
            leverage      = prev_leverage,
            timeframe     = str(get_config('timeframe') or '15m'),
        )
    except Exception as e:
        logger.error(f"[수동청산 감지] {symbol} DB 저장 오류: {e}")

    # ── 터미널 로그 + 텔레그램 알림 (요청된 정확한 포맷) ────────────────────────────────────────
    emoji = "✅" if pnl_pct >= 0 else "🔴"
    msg = f"{emoji} [수동청산 감지] {symbol} {prev_pos} 청산 | 확정 체결가: ${avg_fill_price:.2f} | 순수익(Net): {pnl_amount:+.4f} USDT (Gross: {total_gross:+.4f}, Fee: {total_fee:.4f}) | 수익률: {pnl_pct:+.2f}%"
    
    bot_global_state["logs"].append(msg)
    logger.info(msg)
    send_telegram_sync(_tg_manual_exit(symbol, prev_pos, avg_fill_price, total_gross, total_fee, pnl_amount, pnl_pct))


