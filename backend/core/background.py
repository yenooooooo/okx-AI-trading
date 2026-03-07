"""
core/background.py — 백그라운드 루프 4종
  - private_ws_loop         : OKX Private WebSocket (positions 채널)
  - _margin_guard_bg_loop   : 증거금 사전 경고 (60초 주기)
  - _okx_trade_sync_loop    : OKX 수동 매매 자동 싱크 (5분 주기)
  - _heartbeat_monitor_loop : 서브시스템 장애 감지 (5분 주기)
"""
import asyncio
import json
import os
import time as _time

from database import get_config, set_config, save_trade, trade_exists_by_okx_id
from notifier import send_telegram_sync
from logger import get_logger
from core.state import bot_global_state, _g, _heartbeat_prev_status, _heartbeat_fail_streak
from core.tg_formatters import _TG_LINE, _tg_margin_guard
from core.helpers import _generate_ws_sign

logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# OKX Private WebSocket — positions 채널 실시간 수신
# ════════════════════════════════════════════════════════════════════════════
async def _apply_position_ws_update(pos: dict):
    """OKX positions 채널 데이터 → 글로벌 상태 반영 (OKX 정확 PnL)"""
    # 지연 임포트: 트레이딩 루프와 순환 임포트 방지
    from core.close_handler import _detect_and_handle_manual_close

    inst_id = pos.get('instId', '')
    parts = inst_id.split('-')
    if len(parts) == 3 and parts[2] == 'SWAP':
        symbol = f"{parts[0]}/{parts[1]}:{parts[1]}"
    else:
        return
    if symbol not in bot_global_state["symbols"]:
        return
    pos_qty = float(pos.get('pos', 0) or 0)
    if pos_qty == 0:
        if bot_global_state["symbols"][symbol].get("entry_price", 0.0) > 0:
            asyncio.create_task(_detect_and_handle_manual_close(
                _g["engine"], symbol, bot_global_state["symbols"][symbol]
            ))
    else:
        upl_ratio = float(pos.get('uplRatio', 0) or 0)
        upl = float(pos.get('upl', 0) or 0)
        mark_px = float(pos.get('markPx', 0) or 0)
        avg_px = float(pos.get('avgPx', 0) or 0)
        bot_global_state["symbols"][symbol]["unrealized_pnl_percent"] = round(upl_ratio * 100, 4)
        bot_global_state["symbols"][symbol]["unrealized_pnl"] = round(upl, 4)
        if mark_px > 0:
            bot_global_state["symbols"][symbol]["current_price"] = mark_px
            bot_global_state["symbols"][symbol]["last_price_update_time"] = _time.time()
        if avg_px > 0 and bot_global_state["symbols"][symbol].get("entry_price", 0) == 0:
            bot_global_state["symbols"][symbol]["entry_price"] = avg_px

        stored_contracts = float(bot_global_state["symbols"][symbol].get("contracts", 0))
        if stored_contracts > 0 and pos_qty > 0 and pos_qty < stored_contracts and not bot_global_state["symbols"][symbol].get("partial_tp_executed", False):
            _ws_entry_ts = bot_global_state["symbols"][symbol].get("entry_timestamp", 0)
            if _time.time() - _ws_entry_ts > 30:
                bot_global_state["symbols"][symbol]["contracts"] = int(pos_qty)
                bot_global_state["symbols"][symbol]["exchange_tp_filled"] = True
                logger.info(f"[{symbol}] 📋 거래소 TP 부분 체결 감지 | {int(stored_contracts)} → {int(pos_qty)}계약")
            else:
                logger.warning(f"[{symbol}] ⏳ [WebSocket Guard] 진입 후 30초 이내 수량 변동 무시 — 잔류 데이터 방어 ({int(stored_contracts)} → {int(pos_qty)})")
        elif stored_contracts > 0 and pos_qty == 0 and bot_global_state["symbols"][symbol].get("position", "NONE") not in ("NONE", "PENDING_LONG", "PENDING_SHORT"):
            logger.info(f"[{symbol}] 📋 [WebSocket] 수량 0 감지 — 완전 청산 신호 (exchange_tp_filled 미설정)")


async def private_ws_loop():
    """OKX 프라이빗 WebSocket - positions 채널로 펀딩피 포함 정확한 PnL 실시간 수신"""
    import websockets
    _is_demo = os.getenv("OKX_DEMO", "false").strip().lower() in ("true", "1", "yes")
    WS_URL = "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999" if _is_demo else "wss://ws.okx.com:8443/ws/v5/private"
    logger.info(f"[Private WS] {'🧪 DEMO' if _is_demo else '⚡ LIVE'} 모드 — {WS_URL}")
    _ws_backoff = 5
    while True:
        try:
            if not _g["engine"] or not _g["engine"].exchange:
                await asyncio.sleep(5)
                continue
            _ws_backoff = 5
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                timestamp = str(int(_time.time()))
                sign = _generate_ws_sign(_g["engine"].secret_key, timestamp)
                await ws.send(json.dumps({
                    "op": "login",
                    "args": [{"apiKey": _g["engine"].api_key, "passphrase": _g["engine"].password,
                               "timestamp": timestamp, "sign": sign}]
                }))
                login_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                login_data = json.loads(login_resp)
                if login_data.get('event') != 'login' or login_data.get('code') != '0':
                    logger.error(f"Private WS 로그인 실패 (code: {login_data.get('code')}): {login_resp}")
                    await asyncio.sleep(5)
                    continue
                logger.info("Private WebSocket 로그인 성공 - positions 채널 구독")
                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": [{"channel": "positions", "instType": "SWAP"}]
                }))
                async for message in ws:
                    try:
                        data = json.loads(message)
                        if data.get('arg', {}).get('channel') == 'positions':
                            for pos in data.get('data', []):
                                await _apply_position_ws_update(pos)
                    except Exception as parse_err:
                        logger.warning(f"Private WS 메시지 처리 오류: {parse_err}")
        except Exception as e:
            logger.error(f"[Private WS] 연결 실패/끊김 (재연결 {_ws_backoff}초 후) — RAW 원인: {e}")
            if "auth" in str(e).lower() or "invalid" in str(e).lower():
                logger.error("[Private WS] → API 키 인증 오류 의심 — .env 키 재확인 필요")
            elif "403" in str(e) or "ip" in str(e).lower():
                logger.error("[Private WS] → IP 화이트리스트 차단 의심 — OKX API 설정 확인 필요")
            await asyncio.sleep(_ws_backoff)
            _ws_backoff = min(_ws_backoff * 2, 60)


# ════════════════════════════════════════════════════════════════════════════
# Margin Guard 백그라운드 루프
# ════════════════════════════════════════════════════════════════════════════
async def _margin_guard_bg_loop():
    """독립 백그라운드: 증거금 부족 텔레그램 사전 경고 (60초 주기, 5분 쿨다운)
    대시보드 종속성 없이 서버 가동 중이면 항상 동작."""
    import math as _mgbg_math
    await asyncio.sleep(10)
    while True:
        try:
            if _g["engine"] and _g["engine"].exchange and bot_global_state["balance"] > 0:
                _mgbg_bal = bot_global_state["balance"]
                _mgbg_safe = _mgbg_bal * 0.50
                _mgbg_sym_conf = get_config('symbols')
                _mgbg_active = _mgbg_sym_conf[0] if isinstance(_mgbg_sym_conf, list) and _mgbg_sym_conf else None
                _mgbg_check_list = [_mgbg_active] if _mgbg_active else list(bot_global_state["symbols"].keys())
                for _mgbg_sym in _mgbg_check_list:
                    try:
                        _mgbg_mkt = _g["engine"].exchange.market(_mgbg_sym)
                        _mgbg_cs = float(_mgbg_mkt.get('contractSize', 0.01))
                        _mgbg_lev = max(1, int(get_config('leverage', _mgbg_sym) or 1))
                        _mgbg_price = float(bot_global_state["symbols"][_mgbg_sym].get("current_price", 0))
                        if _mgbg_price <= 0:
                            continue

                        _mgbg_risk = float(get_config('risk_per_trade', _mgbg_sym) or 0.02)
                        if _mgbg_risk >= 1.0:
                            _mgbg_risk /= 100.0
                        _mgbg_notional = _mgbg_bal * _mgbg_risk * _mgbg_lev
                        _mgbg_estimated = max(1, round((_mgbg_notional / _mgbg_price) / _mgbg_cs))
                        _mgbg_margin_per = (_mgbg_cs * _mgbg_price) / _mgbg_lev
                        _mgbg_max = int(_mgbg_safe / _mgbg_margin_per) if _mgbg_margin_per > 0 else 0
                        if _mgbg_max >= 1:
                            _mgbg_estimated = min(_mgbg_estimated, _mgbg_max)

                        _mgbg_margin_total = (_mgbg_cs * _mgbg_price * _mgbg_estimated) / _mgbg_lev
                        _mgbg_feasible = _mgbg_safe >= _mgbg_margin_total

                        if not _mgbg_feasible:
                            _mgbg_rec = min(100, _mgbg_math.ceil((_mgbg_cs * _mgbg_price * _mgbg_estimated) / _mgbg_safe)) if _mgbg_safe > 0 else 100
                            _mgbg_alert_key = f"_margin_guard_last_alert_{_mgbg_sym}"
                            _mgbg_last_ts = float(get_config(_mgbg_alert_key) or 0)
                            if _time.time() - _mgbg_last_ts > 300:
                                set_config(_mgbg_alert_key, str(_time.time()))
                                try:
                                    send_telegram_sync(_tg_margin_guard(
                                        _mgbg_sym, _mgbg_lev, _mgbg_rec, _mgbg_bal, _mgbg_margin_total
                                    ))
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(60)


# ════════════════════════════════════════════════════════════════════════════
# OKX Trade Sync — 수동 매매 기록 자동 싱크
# ════════════════════════════════════════════════════════════════════════════
async def _fetch_okx_positions_history(engine, limit=100):
    """OKX 청산 포지션 히스토리 조회 — /api/v5/account/positions-history"""
    result = await asyncio.to_thread(
        engine.exchange.private_get_account_positions_history,
        {'instType': 'SWAP', 'limit': str(limit)}
    )
    return result.get('data', [])


async def _sync_okx_trades(engine):
    """OKX 수동 매매 기록 싱크 — posId 기준 중복 방지 후 DB 저장"""
    from datetime import datetime as _sync_dt
    positions = await _fetch_okx_positions_history(engine)
    synced = 0
    for pos in positions:
        pos_id = pos.get('posId', '')
        if not pos_id or trade_exists_by_okx_id(pos_id):
            continue

        inst_id = pos.get('instId', '')
        try:
            symbol = engine.exchange.safe_symbol(inst_id)
        except Exception:
            symbol = inst_id

        direction = (pos.get('direction') or 'long').upper()
        entry_price = float(pos.get('openAvgPx') or 0)
        exit_price = float(pos.get('closeAvgPx') or 0)
        gross_pnl = float(pos.get('pnl') or 0)
        _fee_raw = float(pos.get('fee') or 0)
        fee = abs(_fee_raw)
        net_pnl = gross_pnl - fee
        leverage = int(float(pos.get('lever') or 1))
        amount = float(pos.get('closeTotalPos') or 0)

        pnl_percent = 0.0
        if entry_price > 0 and amount > 0:
            try:
                _sync_cs = float(engine.exchange.market(symbol).get('contractSize', 0.01))
            except Exception:
                _sync_cs = 0.01
            _sync_pos_val = entry_price * amount * _sync_cs
            _sync_margin = _sync_pos_val / leverage if leverage > 0 else _sync_pos_val
            pnl_percent = (net_pnl / _sync_margin * 100) if _sync_margin > 0 else 0.0

        entry_time = _sync_dt.fromtimestamp(int(pos.get('cTime', 0)) / 1000) if pos.get('cTime') else None
        exit_time = _sync_dt.fromtimestamp(int(pos.get('uTime', 0)) / 1000) if pos.get('uTime') else None

        save_trade(
            symbol=symbol, position_type=direction,
            entry_price=entry_price, amount=amount,
            exit_price=exit_price, pnl=round(net_pnl, 6),
            pnl_percent=round(pnl_percent, 4), fee=round(fee, 6),
            gross_pnl=round(gross_pnl, 6), exit_reason='OKX_MANUAL',
            leverage=leverage, entry_time=entry_time,
            exit_time=exit_time, okx_order_id=pos_id,
            source='OKX_SYNC',
            timeframe=str(get_config('timeframe') or '15m')
        )
        synced += 1
        logger.info(f"[OKX Sync] 싱크: {symbol} {direction} PnL={net_pnl:.4f} (posId={pos_id})")

    return synced


async def _okx_trade_sync_loop():
    """5분마다 OKX 수동 매매 기록 자동 싱크"""
    await asyncio.sleep(30)
    while True:
        try:
            if _g["engine"] and _g["engine"].exchange:
                count = await _sync_okx_trades(_g["engine"])
                if count > 0:
                    logger.info(f"[OKX Sync] {count}건 수동 매매 기록 싱크 완료")
        except Exception as e:
            logger.warning(f"[OKX Sync] 싱크 실패: {e}")
        await asyncio.sleep(300)


# ════════════════════════════════════════════════════════════════════════════
# Heartbeat Monitor — 서브시스템 장애 자동 감지
# ════════════════════════════════════════════════════════════════════════════
async def _heartbeat_monitor_loop():
    """5분 간격 핵심 서브시스템 상태 점검 + 장애/복구 텔레그램 알림
    [오탐 방지] 2회 연속 FAIL 시에만 장애 알림"""
    await asyncio.sleep(120)

    while True:
        try:
            checks = {}

            # 1) OKX REST API
            try:
                if _g["engine"] and _g["engine"].exchange:
                    await asyncio.to_thread(_g["engine"].exchange.fetch_balance)
                    checks['okx_rest'] = 'OK'
                else:
                    checks['okx_rest'] = 'FAIL'
            except Exception:
                checks['okx_rest'] = 'FAIL'

            # 2) Private WebSocket
            try:
                _pvt = _g["private_ws_task"]
                if _pvt and not _pvt.done():
                    checks['okx_ws'] = 'OK'
                elif _pvt and _pvt.done():
                    _ws_exc = _pvt.exception() if not _pvt.cancelled() else None
                    if _ws_exc:
                        logger.error(f"[Heartbeat] Private WS 태스크 사망 원인: {_ws_exc}")
                    checks['okx_ws'] = 'FAIL'
                else:
                    checks['okx_ws'] = 'WARN'
            except Exception:
                checks['okx_ws'] = 'WARN'

            # 3) Trading Loop
            try:
                _tt = _g["trading_task"]
                if _tt and not _tt.done():
                    checks['trading_loop'] = 'OK'
                else:
                    checks['trading_loop'] = 'WARN'
            except Exception:
                checks['trading_loop'] = 'WARN'

            # 4) Telegram Bot
            try:
                from notifier import _telegram_app as _hb_tg
                checks['telegram'] = 'OK' if (_hb_tg and _hb_tg.bot) else 'WARN'
            except Exception:
                checks['telegram'] = 'WARN'

            # [오탐 방지] 연속 FAIL 카운터 업데이트
            for key, status in checks.items():
                if status == 'FAIL':
                    _heartbeat_fail_streak[key] = _heartbeat_fail_streak.get(key, 0) + 1
                else:
                    _heartbeat_fail_streak[key] = 0

            alerts = []
            recoveries = []
            _check_labels = {
                'okx_rest': 'OKX REST API',
                'okx_ws': 'OKX Private WebSocket',
                'trading_loop': '매매 루프',
                'telegram': '텔레그램 봇',
            }

            for key, status in checks.items():
                prev = _heartbeat_prev_status.get(key, 'OK')
                streak = _heartbeat_fail_streak.get(key, 0)
                if status == 'FAIL' and streak >= 2 and prev != 'FAIL':
                    alerts.append(_check_labels.get(key, key))
                elif status == 'OK' and prev == 'FAIL':
                    recoveries.append(_check_labels.get(key, key))

            for key, status in checks.items():
                streak = _heartbeat_fail_streak.get(key, 0)
                if status == 'FAIL' and streak >= 2:
                    _heartbeat_prev_status[key] = 'FAIL'
                elif status == 'OK':
                    _heartbeat_prev_status[key] = 'OK'

            if alerts:
                _fail_list = ' / '.join(alerts)
                _fail_msg = (
                    f"🚨 <b>ANTIGRAVITY</b>  |  시스템 경고\n"
                    f"{_TG_LINE}\n"
                    f"⚠️ <b>서브시스템 장애 감지 (2회 연속 확인)</b>\n"
                    f"{_TG_LINE}\n"
                    f"장애 항목 │  <code>{_fail_list}</code>\n"
                    f"조치 필요 │  AWS 서버 상태 확인\n"
                    f"{_TG_LINE}"
                )
                send_telegram_sync(_fail_msg)
                bot_global_state["logs"].append(f"🚨 [Heartbeat] 서브시스템 장애 감지: {_fail_list}")
                logger.error(f"[Heartbeat] 서브시스템 장애: {_fail_list}")

            if recoveries:
                _rec_list = ' / '.join(recoveries)
                _rec_msg = (
                    f"✅ <b>ANTIGRAVITY</b>  |  시스템 복구\n"
                    f"{_TG_LINE}\n"
                    f"🟢 <b>서브시스템 복구 확인</b>\n"
                    f"{_TG_LINE}\n"
                    f"복구 항목 │  <code>{_rec_list}</code>\n"
                    f"{_TG_LINE}"
                )
                send_telegram_sync(_rec_msg)
                bot_global_state["logs"].append(f"✅ [Heartbeat] 서브시스템 복구 확인: {_rec_list}")
                logger.info(f"[Heartbeat] 서브시스템 복구: {_rec_list}")

        except Exception as e:
            logger.error(f"[Heartbeat] 모니터링 루프 예외: {e}")

        await asyncio.sleep(300)
