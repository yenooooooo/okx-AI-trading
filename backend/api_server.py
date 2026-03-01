import asyncio
import json
import hmac
import hashlib
import base64
import os
import time as _time
import uvicorn
import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from okx_engine import OKXEngine
from strategy import TradingStrategy
from database import init_db, save_trade, get_trades, get_config, set_config, save_log, get_logs, wipe_all_trades, delete_configs
from backtester import Backtester
from notifier import send_telegram_sync
from logger import get_logger

logger = get_logger(__name__)

# ── Telegram HTML 포맷터 ──────────────────────────────────────────────────────
_TG_LINE = "─" * 24

def _sym_short(symbol: str) -> str:
    return symbol.split(':')[0]

def _tg_entry(symbol: str, direction: str, price: float, amount: int, leverage: int, payload: dict = None, is_test: bool = False) -> str:
    d_emoji = "📈" if direction == "LONG" else "📉"
    header = "👻 <b>PAPER TRADING</b>  |  가상 진입" if is_test else "⚡ <b>ANTIGRAVITY (LIVE)</b>  |  실전 진입"
    msg = (
        f"{header}\n"
        f"{_TG_LINE}\n"
        f"{d_emoji} <b>{direction}</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"가격   │  <code>${price:,.2f}</code>\n"
        f"수량   │  <code>{amount}계약  ·  {leverage}x</code>\n"
    )
    if payload:
        _ema = str(payload.get('ema_status', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
        _vol = str(payload.get('vol_multiplier', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
        _atr = str(payload.get('atr_sl_margin', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
        msg += (
            f"{_TG_LINE}\n"
            f"[진입 근거 데이터]\n"
            f"📈 1h 추세: {_ema}\n"
            f"🔥 거래량 폭발: {_vol}\n"
            f"🛡️ ATR 방어선: {_atr}\n"
        )
    msg += f"{_TG_LINE}"
    return msg

# [Phase 18.2] 스마트 지정가 대기 알림 함수 추가 (위 함수 바로 아래에 삽입)
def _tg_pending(symbol: str, direction: str, price: float, amount: int, leverage: int, is_test: bool = False) -> str:
    header = "👻 <b>PAPER TRADING</b>  |  가상 지정가" if is_test else "⚡ <b>ANTIGRAVITY (LIVE)</b>  |  실전 지정가"
    return (
        f"{header}\n"
        f"{_TG_LINE}\n"
        f"⏳ <b>PENDING {direction}</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"목표가 │  <code>${price:,.2f}</code>\n"
        f"수량   │  <code>{amount}계약  ·  {leverage}x</code>\n"
        f"상태   │  5분 내 미체결 시 자동 취소\n"
        f"{_TG_LINE}"
    )

def _tg_exit(symbol: str, direction: str, avg_price: float, gross_pnl: float, fee: float, net_pnl: float, pnl_pct: float, reason: str, is_test: bool = False) -> str:
    is_profit = pnl_pct >= 0
    result_emoji = "✅" if is_profit else "🔴"
    result_label = "익절" if is_profit else "손절"
    sign_net = "+" if net_pnl >= 0 else ""
    sign_gross = "+" if gross_pnl >= 0 else ""
    _reason_ko = {"STOP_LOSS": "하드 손절", "TRAILING_STOP_EXIT": "트레일링 익절"}
    reason_ko  = _reason_ko.get(reason, reason)
    header = "👻 <b>PAPER TRADING</b>  |  가상 청산" if is_test else "⚡ <b>ANTIGRAVITY (LIVE)</b>  |  실전 청산"
    return (
        f"{header}\n"
        f"{_TG_LINE}\n"
        f"{result_emoji} <b>{direction} {result_label}</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"청산가  │  <code>${avg_price:,.2f}</code>\n"
        f"총수익  │  <code>{sign_gross}{gross_pnl:.4f} USDT</code>\n"
        f"수수료  │  <code>{fee:.4f} USDT</code>\n"
        f"순수익  │  <b><code>{sign_net}{net_pnl:.4f} USDT</code></b>\n"
        f"수익률  │  <b><code>{sign_net}{pnl_pct:.2f}%</code></b>\n"
        f"사유    │  {reason_ko}\n"
        f"{_TG_LINE}"
    )

def _tg_manual_exit(symbol: str, direction: str, avg_price: float, gross_pnl: float, fee: float, net_pnl: float, pnl_pct: float) -> str:
    is_profit = pnl_pct >= 0
    sign_net = "+" if net_pnl >= 0 else ""
    sign_gross = "+" if gross_pnl >= 0 else ""
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  수동청산 감지\n"
        f"{_TG_LINE}\n"
        f"✋ <b>{direction} 수동청산</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"청산가  │  <code>${avg_price:,.2f}</code>\n"
        f"총수익  │  <code>{sign_gross}{gross_pnl:.4f} USDT</code>\n"
        f"수수료  │  <code>{fee:.4f} USDT</code>\n"
        f"순수익  │  <b><code>{sign_net}{net_pnl:.4f} USDT</code></b>\n"
        f"수익률  │  <b><code>{sign_net}{pnl_pct:.2f}%</code></b>\n"
        f"{_TG_LINE}"
    )

def _tg_scanner(symbols: list) -> str:
    sym_list = "  ·  ".join([s.split('/')[0] for s in symbols])
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  스캐너\n"
        f"{_TG_LINE}\n"
        f"🔍 <b>거래량 Top 3 갱신 완료</b>\n"
        f"{_TG_LINE}\n"
        f"타겟   │  <code>{sym_list}</code>\n"
        f"{_TG_LINE}"
    )

def _tg_circuit_breaker(symbol: str, balance: float) -> str:
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  경고\n"
        f"{_TG_LINE}\n"
        f"⚠️ <b>일일 손실 한도 초과</b>\n"
        f"{_TG_LINE}\n"
        f"심볼   │  <code>{_sym_short(symbol)}</code>\n"
        f"잔고   │  <code>{balance:,.2f} USDT</code>\n"
        f"{_TG_LINE}"
    )

def _tg_system(is_running: bool) -> str:
    if is_running:
        return (
            f"⚡ <b>ANTIGRAVITY</b>  |  시스템\n"
            f"{_TG_LINE}\n"
            f"🟢 <b>자동매매 가동 시작</b>\n"
            f"{_TG_LINE}"
        )
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  시스템\n"
        f"{_TG_LINE}\n"
        f"🛑 <b>자동매매 중지</b>\n"
        f"{_TG_LINE}"
    )
# ─────────────────────────────────────────────────────────────────────────────

app_server = FastAPI()

# CORS 설정
app_server.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WebSocket 관리자 ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

async def broadcast_dashboard_state():
    """1초마다 전역 상태를 연결된 모든 웹소켓 클라이언트에게 브로드캐스트"""
    while True:
        try:
            if manager.active_connections:
                # 데이터 최적화: 로그는 최신 10개만 슬라이싱
                state_to_send = {
                    "is_running": bot_global_state["is_running"],
                    "balance": bot_global_state["balance"],
                    "symbols": bot_global_state["symbols"],
                    "logs": list(bot_global_state["logs"][-10:])
                }
                await manager.broadcast_json(state_to_send)
        except Exception as e:
            logger.error(f"WebSocket Broadcast 오류: {e}")
        await asyncio.sleep(1)

class LogList(list):
    def append(self, msg):
        super().append(msg)
        if len(self) > 300:
            self.pop(0)
        lvl = "ERROR" if "[오류]" in msg or "실패" in msg else "INFO"
        # DB 저장 실패 시 1회 재시도 — 묵살 방지
        for _attempt in range(2):
            try:
                save_log(level=lvl, message=msg)
                break
            except Exception as e:
                if _attempt == 1:
                    logger.error(f"DB 저장 최종 실패 (로그 유실): {e} | msg={msg[:80]}")

# 전역 상태 (다중 심볼 지원)
bot_global_state = {
    "is_running": False,
    "balance": 0.0,
    "symbols": {},
    "logs": LogList(["[봇] 시스템 코어 초기화 완료 - API 브릿지 대기 중"]),
    "stress_inject": None,
}
# [Phase 20.1] 동시성 충돌 방어용 절대 자물쇠 (Mutex Lock)
state_lock = asyncio.Lock()

ai_brain_state = {
    "symbols": {}  # symbol별 뇌 상태
}

trade_history = []
_trading_task = None  # 중복 루프 방지용 태스크 추적
_broadcast_task = None  # /ws/dashboard 브로드캐스트 태스크 (클라이언트 연결 시 on-demand 시작)
_engine: OKXEngine = None  # 싱글톤 OKX 엔진 (매 요청마다 재생성 방지)
_active_strategy: TradingStrategy = None  # 활성 전략 인스턴스 레퍼런스 (wipe_db 인메모리 리셋용)

def _generate_ws_sign(secret_key: str, timestamp: str) -> str:
    """OKX WebSocket 인증 서명 생성 (HMAC-SHA256 Base64)"""
    message = timestamp + "GET" + "/users/self/verify"
    mac = hmac.new(bytes(secret_key, 'utf-8'), bytes(message, 'utf-8'), digestmod=hashlib.sha256)
    return base64.b64encode(mac.digest()).decode('utf-8')

async def _apply_position_ws_update(pos: dict):
    """OKX positions 채널 데이터 → 글로벌 상태 반영 (OKX 정확 PnL)"""
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
        # OKX가 포지션 종료를 알림 → 통합된 수동청산 감지 로직 호출
        if bot_global_state["symbols"][symbol].get("entry_price", 0.0) > 0:
            asyncio.create_task(_detect_and_handle_manual_close(_engine, symbol, bot_global_state["symbols"][symbol]))
    else:
        upl_ratio = float(pos.get('uplRatio', 0) or 0)
        upl = float(pos.get('upl', 0) or 0)
        mark_px = float(pos.get('markPx', 0) or 0)
        avg_px = float(pos.get('avgPx', 0) or 0)
        bot_global_state["symbols"][symbol]["unrealized_pnl_percent"] = round(upl_ratio * 100, 4)
        bot_global_state["symbols"][symbol]["unrealized_pnl"] = round(upl, 4)
        if mark_px > 0:
            bot_global_state["symbols"][symbol]["current_price"] = mark_px
        if avg_px > 0 and bot_global_state["symbols"][symbol].get("entry_price", 0) == 0:
            bot_global_state["symbols"][symbol]["entry_price"] = avg_px

async def private_ws_loop():
    """OKX 프라이빗 WebSocket - positions 채널로 펀딩피 포함 정확한 PnL 실시간 수신"""
    import websockets
    WS_URL = "wss://ws.okx.com:8443/ws/v5/private"  # 실전 환경
    while True:
        try:
            if not _engine or not _engine.exchange:
                await asyncio.sleep(5)
                continue
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                # 인증
                timestamp = str(int(_time.time()))
                sign = _generate_ws_sign(_engine.secret_key, timestamp)
                await ws.send(json.dumps({
                    "op": "login",
                    "args": [{"apiKey": _engine.api_key, "passphrase": _engine.password,
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
            logger.error(f"[Private WS] 연결 실패/끊김 — RAW 원인: {e}")
            if "auth" in str(e).lower() or "invalid" in str(e).lower():
                logger.error("[Private WS] → API 키 인증 오류 의심 — .env 키 재확인 필요")
            elif "403" in str(e) or "ip" in str(e).lower():
                logger.error("[Private WS] → IP 화이트리스트 차단 의심 — OKX API 설정 확인 필요")
            await asyncio.sleep(5)

@app_server.on_event("startup")
async def startup_event():
    """서버 시작 시 OKXEngine 1회만 초기화 + 프라이빗 WS 시작"""
    global _engine
    init_db()
    bot_global_state["logs"].append("[봇] 🔴 실전망(LIVE) 서버 시스템 가동 시작 - 실전 API 연동 완료")
    logger.info("API 서버 시작 - OKXEngine 초기화 중...")
    loop = asyncio.get_event_loop()
    _engine = await loop.run_in_executor(None, OKXEngine)
    if _engine and _engine.exchange:
        logger.info("OKXEngine 싱글톤 초기화 완료")
        asyncio.create_task(private_ws_loop())  # OKX 프라이빗 WS 시작
        logger.info("Private WebSocket 태스크 시작")
    else:
        logger.error("OKXEngine 초기화 실패 - .env 키 확인 필요")

    # 텔레그램 양방향 컨트롤 타워 구동
    from notifier import init_telegram_bot
    await init_telegram_bot()
    bot_global_state["logs"].append("[봇] 텔레그램 양방향 컨트롤 타워 비동기 가동 완료")
    logger.info("텔레그램 양방향 컨트롤 타워 비동기 시작 완료")

    # [DRY] broadcast_dashboard_state: 프론트는 OKX Public WS 직결 사용 중
    # 클라이언트 연결 시점에 on-demand 활성화 구조로 대기 (startup 공회전 제거)
    logger.info("서버 초기화 완료 - 대시보드 WS 브로드캐스트 클라이언트 대기 중")

@app_server.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 텔레그램 등 비동기 자원 회수 (Graceful Shutdown)"""
    from notifier import stop_telegram_bot
    await stop_telegram_bot()
    logger.info("API 서버 종료 - 텔레그램 자원 릴리즈 완료")

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
    else:
        prev_pos       = sym_state.get("position", "NONE")
        prev_entry     = sym_state.get("entry_price", 0.0)
        prev_contracts = int(sym_state.get("contracts", 1))
        prev_leverage  = int(sym_state.get("leverage", 1))

    if prev_pos == "NONE" or prev_entry <= 0:
        return  # 처리할 포지션 없음

    # ── 즉시 상태 초기화 (같은 사이클 중복 감지 방지) ──────────────────────
    sym_state["position"]              = "NONE"
    sym_state["entry_price"]           = 0.0
    sym_state["last_exit_time"]        = _time.time()  # [Phase 19.3] 60초 호흡 고르기 기준점
    sym_state["unrealized_pnl_percent"] = 0.0
    sym_state["unrealized_pnl"]        = 0.0
    sym_state["take_profit_price"]     = "대기중"  # [Phase 16] 문자열 통일
    sym_state["stop_loss_price"]       = 0.0
    sym_state["entry_timestamp"]       = 0.0  # [Race Condition Fix] Grace Period 리셋

    pnl_amount     = 0.0
    total_gross    = 0.0
    total_fee      = 0.0
    avg_fill_price = prev_entry  # fallback
    pnl_pct        = 0.0

    # ── OKX 체결 영수증(Trades) 조회 (최대 3회 시도) ─────────────────────────
    # positions-history는 API 반영 지연이 있으므로, 즉시 반영되는 fetch_my_trades 사용
    history_found = False
    for attempt in range(3):
        try:
            await asyncio.sleep(1.0 + attempt * 0.5)
            trades = await asyncio.to_thread(engine_api.exchange.fetch_my_trades, symbol, limit=20)
            closing_side = 'sell' if prev_pos == 'LONG' else 'buy'
            recent_closes = [t for t in trades if t.get('side') == closing_side]

            if recent_closes:
                order_id       = recent_closes[-1].get('order')
                matching       = [t for t in recent_closes if t.get('order') == order_id]
                
                pnl_amount, total_gross, total_fee, avg_fill_price = engine_api.calculate_realized_pnl(matching, prev_entry)
                
                history_found = True
                break
        except Exception as e:
            logger.error(f"[수동청산 감지] {symbol} 체결 영수증 조회 오류(시도 {attempt+1}): {e}")

    if not history_found:
        logger.warning(f"[수동청산 감지] {symbol} 체결 영수증 없음 - PnL 0 으로 기록")

    if avg_fill_price == 0:
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
        )
    except Exception as e:
        logger.error(f"[수동청산 감지] {symbol} DB 저장 오류: {e}")

    # ── 터미널 로그 + 텔레그램 알림 (요청된 정확한 포맷) ────────────────────────────────────────
    emoji = "✅" if pnl_pct >= 0 else "🔴"
    msg = f"{emoji} [수동청산 감지] {symbol} {prev_pos} 청산 | 확정 체결가: ${avg_fill_price:.2f} | 순수익(Net): {pnl_amount:+.4f} USDT (Gross: {total_gross:+.4f}, Fee: {total_fee:.4f}) | 수익률: {pnl_pct:+.2f}%"
    
    bot_global_state["logs"].append(msg)
    logger.info(msg)
    send_telegram_sync(_tg_manual_exit(symbol, prev_pos, avg_fill_price, total_gross, total_fee, pnl_amount, pnl_pct))


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


async def async_trading_loop():
    """다중 심볼 백그라운드 매매 루프"""
    global bot_global_state, ai_brain_state, _trading_task, _active_strategy

    engine_api = _engine  # 싱글톤 재사용 (load_markets 재호출 없음)
    strategy_instance = TradingStrategy(initial_seed=75.0)
    _active_strategy = strategy_instance  # wipe_db 엔드포인트가 인메모리 상태를 리셋할 수 있도록 등록
    # [v2.2] DB 설정에서 일일 최대 손실율 동기화 (UI에서 변경 가능)
    strategy_instance.daily_max_loss_pct = float(get_config('daily_max_loss_rate') or 0.07)

    if not engine_api or not engine_api.exchange:
        logger.error("OKXEngine 미초기화 상태 - 매매 루프 중단")
        return

    bot_global_state["logs"].append("[봇] OKX 거래소 연결 확인 및 자동매매 대기 중...")
    logger.info("자동매매 루프 시작")
    import time
    last_log_time = 0
    last_scan_time = 0  # 스캐너 마지막 작동 시간
    _circuit_breaker_last_warn = {}  # 서킷 브레이커 로그 쓰로틀 (심볼별 마지막 경고 시각)
    # [Phase 20.3] 조용한 에러(Silent Failure) 추적용 카운터
    consecutive_errors = 0

    while bot_global_state["is_running"]:
        try:
            current_time = time.time()

            # ── [HOTFIX: Split Brain 방지] 외부에서 뇌(전략)가 포맷되었는지 감시 ──
            if strategy_instance is not _active_strategy:
                strategy_instance = _active_strategy
                logger.info("[엔진 딥 리셋] 매매 루프: 새로운 뇌(TradingStrategy) 이식 완료.")
                bot_global_state["logs"].append("🧠 [시스템] 엔진 코어 교체 감지: 새로운 AI 뇌로 실시간 교체 완료.")

            # ── [Phase 18.1] 전역 설정 동기화 (일일 최대 손실률만 루프 단위로 유지) ──
            try:
                strategy_instance.daily_max_loss_pct = float(get_config('daily_max_loss_rate') or 0.07)
            except Exception as _sync_err:
                logger.error(f"[설정 동기화 오류] {_sync_err}")

            # ── 15분 주기 다이내믹 볼륨 스캐너 가동 ──
            if current_time - last_scan_time >= 900:
                if str(get_config('auto_scan_enabled')).lower() == 'true':
                    # 유령 포지션 방어: 보유 포지션이 있으면 타겟 변경 절대 금지
                    any_pos_open = any(
                        s.get("position", "NONE") != "NONE"
                        for s in bot_global_state["symbols"].values()
                    )
                    if any_pos_open:
                        last_scan_time = current_time  # 포지션 유지 중: 스캐너 가동 보류
                    else:
                        try:
                            bot_global_state["logs"].append("[엔진] 다이내믹 볼륨 스캐너 가동: 24h 거래량 Top 3 탐색 중...")
                            await asyncio.sleep(0.5)  # [Phase 4] API Rate Limit 보호용 미세 비동기 지연
                            top_symbols = await engine_api.scan_top_volume_coins(limit=3)
                            if top_symbols:
                                # 설정에 바로 업데이트하여 영속화 및 프론트 반영
                                set_config('symbols', top_symbols)
                                scan_msg = f"✅ [스캐너 가동] 거래량 Top 3 타겟 자동 갱신 완료: {top_symbols}"
                                bot_global_state["logs"].append(scan_msg)
                                logger.info(scan_msg)
                                send_telegram_sync(_tg_scanner(top_symbols))
                                last_scan_time = current_time
                        except Exception as scan_err:
                            err_msg = f"[오류] 스캐너 로직 실패: {scan_err}"
                            bot_global_state["logs"].append(err_msg)
                            logger.error(err_msg)
                else:
                    last_scan_time = current_time  # 비활성 상태: 타이머만 갱신, 스캔 스킵

            # 잔고 실시간 연동
            curr_bal = await asyncio.to_thread(engine_api.get_usdt_balance)
            bot_global_state["balance"] = round(curr_bal, 2)

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
                    drill_msg = f"🚨 [소방훈련: 킬스위치 강제 주입 완료] 가상 일일 손실: {fake_loss:+.2f} USDT | 발동: {'YES' if kill_triggered else 'NO'}"
                    bot_global_state["logs"].append(drill_msg)
                    logger.warning(drill_msg)
                    if kill_triggered:
                        send_telegram_sync(f"🚨 [소방훈련] 킬스위치 발동 확인\n가상 손실: {fake_loss:+.2f} USDT\n24시간 거래 중단")
                elif _stress_type == "LOSS_STREAK":
                    strategy_instance.consecutive_loss_count = strategy_instance.cooldown_losses_trigger - 1
                    strategy_instance.record_trade_result(True)
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
                    reset_msg = "✅ [소방훈련 해제 완료] 킬스위치 OFF + 쿨다운 OFF + 일일 PnL 리셋"
                    bot_global_state["logs"].append(reset_msg)
                    logger.info(reset_msg)
                    send_telegram_sync("✅ [소방훈련 해제] 킬스위치·쿨다운 전부 해제 완료")

            # 각 심볼에 대해 거래 루프 실행
            for i, symbol in enumerate(symbols):
                # 멀티 타겟팅 API Rate Limit 우회를 위한 물리적 딜레이 추가
                if i > 0:
                    await asyncio.sleep(1)
                
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
                        # [Phase 19] 퇴근 모드 로드
                        _exit_only = str(get_config('exit_only_mode', symbol)).lower() == 'true'
                    except Exception as _sym_sync_err:
                        logger.error(f"[{symbol}] 코인별 설정 동기화 오류: {_sym_sync_err}")

                    # OHLCV 데이터 수집 (5분봉, limit=200: ADX/MACD 지표 충분한 캔들 확보)
                    ohlcv = await asyncio.to_thread(engine_api.exchange.fetch_ohlcv, symbol, "5m", limit=200)
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    current_price = await asyncio.to_thread(engine_api.get_current_price, symbol)

                    bot_global_state["symbols"][symbol]["current_price"] = current_price

                    # ── 수동 청산 감지: 내부엔 포지션이 있는데 거래소엔 없으면 외부 청산 ──
                    # [Shadow Mode] Paper 포지션은 거래소에 실제 포지션이 없으므로 감지 대상에서 제외
                    _sym_is_paper = bot_global_state["symbols"][symbol].get("is_paper", False)
                    # [Race Condition Fix] 진입 직후 OKX API 전파 지연(최대 10~15초)으로 인한
                    # false manual-close 무한 알람 루프 차단 — 진입 후 15초간 수동청산 감지 비활성화
                    _entry_ts = bot_global_state["symbols"][symbol].get("entry_timestamp", 0.0)
                    _grace_period_ok = (time.time() - _entry_ts) > 15.0
                    if (exchange_open_symbols is not None
                            and not _sym_is_paper
                            and _grace_period_ok
                            and bot_global_state["symbols"][symbol]["position"] != "NONE"
                            and bot_global_state["symbols"][symbol]["entry_price"] > 0
                            and symbol not in exchange_open_symbols):
                        await _detect_and_handle_manual_close(
                            engine_api, symbol, bot_global_state["symbols"][symbol]
                        )
                        continue  # 이번 사이클은 신규 진입 시도 없이 다음 심볼로

                    # 지표 계산
                    df = strategy_instance.calculate_indicators(df)
                    latest_rsi = df['rsi'].iloc[-1]
                    latest_macd = df['macd'].iloc[-1]
                    latest_upper = df['upper_band'].iloc[-1]
                    latest_lower = df['lower_band'].iloc[-1]
                    latest_adx = df['adx'].iloc[-1] if 'adx' in df.columns else float('nan')

                    # ── [Phase 1] 거시적 추세(1h EMA200) 데이터 수집 (비동기, 캐시 적용) ──
                    macro_ema_200 = await strategy_instance.get_macro_ema_200(engine_api, symbol)

                    # 매매 시그널 및 AI 판단 상태 평가 (거시적 필터 적용)
                    signal, analysis_msg, payload = strategy_instance.check_entry_signal(df, current_price, macro_ema_200)

                    # 뇌 상태 업데이트
                    if symbol not in ai_brain_state["symbols"]:
                        ai_brain_state["symbols"][symbol] = {}

                    ai_brain_state["symbols"][symbol].update({
                        "price": current_price,
                        "rsi": round(latest_rsi, 2) if not pd.isna(latest_rsi) else 50.0,
                        "macd": round(latest_macd, 2) if not pd.isna(latest_macd) else 0.0,
                        "bb_upper": round(latest_upper, 2) if not pd.isna(latest_upper) else 0.0,
                        "bb_lower": round(latest_lower, 2) if not pd.isna(latest_lower) else 0.0,
                        "adx": round(latest_adx, 2) if not pd.isna(latest_adx) else 0.0,
                        "chop": round(float(df['chop'].iloc[-1]), 1) if 'chop' in df.columns and not pd.isna(df['chop'].iloc[-1]) else 0.0,
                        "decision": analysis_msg
                    })

                    # ── [UI A+B] 실시간 진입 관문 체크리스트 + 봇 혼잣말 생성 ──
                    import datetime as _dt
                    _row       = df.iloc[-1]
                    _rsi_v     = float(latest_rsi)  if not pd.isna(latest_rsi)  else 50.0
                    _adx_v     = float(latest_adx)  if not pd.isna(latest_adx)  else 0.0
                    _macd_v    = float(latest_macd) if not pd.isna(latest_macd) else 0.0
                    _msig_v    = float(_row['macd_signal']) if 'macd_signal' in _row.index and not pd.isna(_row['macd_signal']) else 0.0
                    _chop_v    = float(_row['chop'])     if 'chop'     in _row.index and not pd.isna(_row['chop'])     else 50.0
                    _vol_v     = float(_row['volume'])   if not pd.isna(_row['volume'])   else 0.0
                    _vsma_v    = float(_row['vol_sma_20']) if 'vol_sma_20' in _row.index and not pd.isna(_row['vol_sma_20']) else 1.0
                    _ema20_v   = float(_row['ema_20'])   if 'ema_20'   in _row.index and not pd.isna(_row['ema_20'])   else (current_price or 1)

                    _vol_ratio  = float(_vol_v / _vsma_v) if _vsma_v > 0 else 0.0
                    _disparity  = float(abs((current_price - _ema20_v) / _ema20_v) * 100) if current_price and _ema20_v else 0.0
                    _long_macd  = bool(_macd_v > _msig_v)
                    _short_macd = bool(_macd_v < _msig_v)
                    _long_rsi   = bool(30 <= _rsi_v <= 55)
                    _short_rsi  = bool(45 <= _rsi_v <= 70)
                    _mr_ok      = bool((_long_macd and _long_rsi) or (_short_macd and _short_rsi))
                    _macro_ok   = True
                    _macro_lbl  = "N/A"
                    if macro_ema_200 is not None and current_price is not None:
                        _macro_ok  = bool(current_price > float(macro_ema_200))
                        _macro_lbl = "상승추세 ↑" if _macro_ok else "하락추세 ↓"

                    # 동적 임계값 — strategy_instance 에서 직접 바인딩 (하드코딩 제거)
                    _adx_min  = strategy_instance.adx_threshold
                    _adx_max  = strategy_instance.adx_max
                    _chop_max = strategy_instance.chop_threshold
                    _vol_mul  = strategy_instance.volume_surge_multiplier

                    _gates = {
                        "adx":       {"pass": bool(_adx_min <= _adx_v <= _adx_max), "value": f"{_adx_v:.1f}",      "target": f"{_adx_min:.0f}~{_adx_max:.0f}"},
                        "chop":      {"pass": bool(_chop_v < _chop_max),             "value": f"{_chop_v:.1f}",     "target": f"< {_chop_max:.1f}"},
                        "volume":    {"pass": bool(_vol_ratio >= _vol_mul),          "value": f"{_vol_ratio:.2f}x", "target": f"≥ {_vol_mul:.1f}x"},
                        "disparity": {"pass": bool(_disparity < 0.8),               "value": f"{_disparity:.2f}%", "target": "< 0.8%"},
                        "macd_rsi":  {"pass": bool(_mr_ok),                         "value": f"RSI {_rsi_v:.1f}",  "target": "크로스+구간"},
                        "macro":     {"pass": bool(_macro_ok),                      "value": _macro_lbl,           "target": "EMA200"},
                    }
                    _passed = int(sum(1 for g in _gates.values() if g["pass"]))
                    ai_brain_state["symbols"][symbol]["gates"]        = _gates
                    ai_brain_state["symbols"][symbol]["gates_passed"] = _passed

                    # 봇 혼잣말 — 지금 무엇을 기다리는지 한 줄 생성
                    _KST = _dt.timezone(_dt.timedelta(hours=9))
                    _ts = _dt.datetime.now(_KST).strftime("%H:%M:%S")
                    
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
                    elif _disparity >= 0.8:
                        _mono = f"[{_ts}] 이격도 {_disparity:.2f}% — 이미 너무 달렸어. EMA20에 붙을 때까지 기다리는 중... ({_passed}/6)"
                    elif _vol_ratio < _vol_mul:
                        _mono = f"[{_ts}] 거래량 {_vol_ratio:.2f}x — 아직 안 터졌어. {_vol_mul:.1f}x 이상 폭발 대기 중... ({_passed}/6)"
                    elif not _macro_ok:
                        _mono = f"[{_ts}] EMA200 역방향({_macro_lbl}) — 큰 흐름 거슬러 들어가면 안 돼. 추세 전환 대기 중... ({_passed}/6)"
                    elif not _mr_ok:
                        if _long_macd and not _long_rsi:
                            _mono = f"[{_ts}] MACD 상향 ✓, RSI {_rsi_v:.1f} — LONG 진입 구간(30~55)으로 내려올 때까지 대기... ({_passed}/6)"
                        elif _short_macd and not _short_rsi:
                            _mono = f"[{_ts}] MACD 하향 ✓, RSI {_rsi_v:.1f} — SHORT 진입 구간(45~70)으로 올라올 때까지 대기... ({_passed}/6)"
                        else:
                            _mono = f"[{_ts}] RSI {_rsi_v:.1f}, MACD {_macd_v:.4f} vs Signal {_msig_v:.4f} — 크로스 신호 계산 중... ({_passed}/6)"
                    else:
                        _mono = f"[{_ts}] {_passed}/6 조건 충족 — RSI {_rsi_v:.1f} / MACD {_macd_v:.4f} 타점 탐색 중..."

                    _ml = ai_brain_state["symbols"][symbol].get("monologue", [])
                    _ml.append(_mono)
                    if len(_ml) > 30:
                        _ml = _ml[-30:]
                    ai_brain_state["symbols"][symbol]["monologue"] = _ml

                    # 포지션 상태 체크 및 리스크 관리
                    if bot_global_state["symbols"][symbol]["position"] != "NONE":
                        entry = bot_global_state["symbols"][symbol]["entry_price"]
                        position_side = bot_global_state["symbols"][symbol]["position"]

                        if entry > 0 and current_price:
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

                            bot_global_state["symbols"][symbol]["unrealized_pnl_percent"] = round(pnl, 2)

                            # --- NEW: PENDING 상태(스마트 지정가)에서 체결 여부 및 시간 초과 확인 ---
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
                                        trade_amount = bot_global_state["symbols"][symbol]["pending_amount"]
                                        trade_leverage = bot_global_state["symbols"][symbol].get("leverage", 1)
                                        
                                        bot_global_state["symbols"][symbol]["position"] = real_side
                                        bot_global_state["symbols"][symbol]["entry_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["highest_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["lowest_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["contracts"] = trade_amount  # 청산 시 재사용
                                        bot_global_state["symbols"][symbol]["partial_tp_executed"] = False  # [Partial TP] 진입 시 반드시 초기화
                                        bot_global_state["symbols"][symbol]["entry_timestamp"] = time.time()  # [Race Condition Fix]
                                        
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
                                        
                                    elif status in ['canceled', 'rejected'] or (time.time() - pending_time > 300):
                                        # 취소되었거나 5분 초과 시 -> 주문 취소 및 PENDING 해제 (고스트 오더 방지)
                                        if status not in ['canceled', 'rejected']:
                                            if not _is_paper_pending: # Paper면 cancel_order API 호출 절대 금지
                                                try:
                                                    await asyncio.to_thread(engine_api.exchange.cancel_order, pending_id, symbol)
                                                except Exception as cancel_err:
                                                    logger.warning(f"[{symbol}] 미체결 주문 취소 실패 (이미 취소되었을 수 있음): {cancel_err}")
                                                
                                        bot_global_state["symbols"][symbol]["position"] = "NONE"
                                        
                                        _paper_tag = "[👻 PAPER] " if _is_paper_pending else ""
                                        cancel_msg = f"{_paper_tag}⏱️ [{symbol}] 지정가 5분 미체결 취소 완료 → 봇이 새로운 최적의 타점을 즉시 재탐색합니다."
                                        bot_global_state["logs"].append(cancel_msg)
                                        logger.info(cancel_msg)
                                        
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

                                # 마지막 가격 갱신이 3초 이상 지연되었다면 웹소켓 이상으로 간주하고 REST API 강제 호출
                                if _now - _last_update > 3.0:
                                    try:
                                        logger.warning(f"[{symbol}] ⚠️ 실시간 데이터 수신 지연 감지 (>3초). REST API 비상 우회 폴링 실행!")
                                        _emergency_ticker = await asyncio.to_thread(engine_api.exchange.fetch_ticker, symbol)
                                        current_price = float(_emergency_ticker['last'])
                                        # 비상 갱신 후 타임스탬프 리셋
                                        bot_global_state["symbols"][symbol]["last_price_update_time"] = _now
                                    except Exception as fallback_err:
                                        logger.error(f"[{symbol}] 🚨 비상 REST API 폴링마저 실패: {fallback_err}")
                                else:
                                    # 정상 갱신 경로: 타임스탬프 업데이트
                                    bot_global_state["symbols"][symbol]["last_price_update_time"] = _now

                                # [v2.2 DRY] evaluate_risk_management Tuple 언패킹 — 이중 계산 완전 제거
                                # (action, real_sl, trailing_active, trailing_target)를 단일 소스(strategy.py)에서만 계산
                                partial_tp_executed = bot_global_state["symbols"][symbol].get("partial_tp_executed", False)
                                action, _real_sl, _trailing_active, _trailing_target = strategy_instance.evaluate_risk_management(
                                    entry, current_price, extreme_price, position_side, current_atr, symbol, partial_tp_executed
                                )

                                bot_global_state["symbols"][symbol]["real_sl"] = round(_real_sl, 4)
                                bot_global_state["symbols"][symbol]["trailing_active"] = _trailing_active
                                bot_global_state["symbols"][symbol]["trailing_target"] = round(_trailing_target, 4) if _trailing_target else 0.0

                                # [Phase 16] 다이내믹 익절가(Dynamic TP) 시각화 로직
                                _is_partial_done = bot_global_state["symbols"][symbol].get("partial_tp_executed", False)
                                if not _is_partial_done:
                                    # 1차 분할 익절 타겟 선제적 계산 (수수료 0.15% + ATR 50%)
                                    _target_offset = (entry * 0.0015) + (current_atr * 0.5)
                                    _first_target = (entry + _target_offset) if position_side == "LONG" else (entry - _target_offset)
                                    bot_global_state["symbols"][symbol]["take_profit_price"] = f"🎯 1차 타겟: ${_first_target:,.2f} (50%)"
                                else:
                                    # 1차 익절 완료 후 트레일링 스탑 상태
                                    _t_target = bot_global_state["symbols"][symbol].get("trailing_target", 0.0)
                                    if _t_target > 0:
                                        bot_global_state["symbols"][symbol]["take_profit_price"] = f"🔥 트레일링 추적: ${_t_target:,.2f}"
                                    else:
                                        bot_global_state["symbols"][symbol]["take_profit_price"] = "🚀 트레일링 대기"

                                # --- [Step 2] 50% 분할 익절 (Partial TP) 조건 체크 ---
                                if not bot_global_state["symbols"][symbol].get("partial_tp_executed", False):
                                    # [v2.1] 발동 조건: 수수료 마진 0.15% + ATR*0.5 이상 수익 구간
                                    partial_tp_threshold = (entry * 0.0015) + (current_atr * 0.5)
                                    if position_side == "LONG":
                                        partial_profit = current_price - entry
                                    else:
                                        partial_profit = entry - current_price

                                    if partial_profit >= partial_tp_threshold:
                                        try:
                                            full_contracts = int(bot_global_state["symbols"][symbol].get("contracts", 1))
                                            _is_paper = bot_global_state["symbols"][symbol].get("is_paper", False)
                                            _paper_tag = "[👻 PAPER] " if _is_paper else ""

                                            if full_contracts > 1:
                                                half_contracts = max(1, full_contracts // 2)
                                                # 시장가 절반 청산 (Reduce-Only) — Paper면 바이패스
                                                if not _is_paper:
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
                                            else:
                                                # [Phase 19.4 교정] 1계약일 경우 매도는 스킵하되, 목표가에 도달했으므로 방어선과 트레일링은 정상 발동
                                                qty_msg = "소액(1계약) 포지션으로 분할 매도 스킵, 수익 보존(본전 방어) 모드 직행"

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

                                            # 텔레그램 분리 알림
                                            _header_pt = "👻 PAPER TRADING | 가상 1차 타겟 도달" if _is_paper else "⚡ ANTIGRAVITY (LIVE) | 실전 1차 타겟 도달"
                                            partial_tg_msg = (
                                                f"{_header_pt}\n"
                                                f"{_TG_LINE}\n"
                                                f"🎯 <b>1차 타겟 도달 완료</b>  ·  <code>{_sym_short(symbol)}</code>\n"
                                                f"{_TG_LINE}\n"
                                                f"{qty_msg}\n"
                                                f"🛡️ 본전 방어선(Breakeven) 작동 시작\n"
                                                f"{_TG_LINE}"
                                            )
                                            send_telegram_sync(partial_tg_msg)

                                        except Exception as partial_err:
                                            logger.error(f"[{symbol}] 1차 타겟 도달 처리 실패: {partial_err}")
                                # --- End of Partial TP 조건 체크 ---

                                if action != "KEEP":
                                    # 1. 청산 실행 (Paper/Real 분기)
                                    try:
                                        amount = int(bot_global_state["symbols"][symbol].get("contracts", 1))
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
                                            total_fee = -(position_value * 0.0005 * 2)  # 0.05% Taker 양방향 가상 수수료
                                            pnl_amount = total_gross_pnl + total_fee
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
                                                        net_pnl, total_gross_pnl, total_fee, avg_fill_price = engine_api.calculate_realized_pnl(matching_trades, entry)
                                                        receipt_found = True
                                                        break
                                                except Exception as receipt_err:
                                                    logger.warning(f"[{symbol}] 청산 체결 영수증 파싱 오류 시도 {_attempt+1}: {receipt_err}")
                                            if not receipt_found:
                                                raise Exception("청산 주문은 들어갔으나 영수증(실현PnL) 파싱에 실패했습니다.")
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
                                                leverage=leverage
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

                                        # 4. 프론트엔드 포지션 초기화
                                        # [Phase 20.1] 상태 변경 시 자물쇠 잠금
                                        async with state_lock:
                                            bot_global_state["symbols"][symbol]["position"] = "NONE"
                                            bot_global_state["symbols"][symbol]["entry_price"] = 0.0
                                            bot_global_state["symbols"][symbol]["last_exit_time"] = time.time()  # [Phase 19.3] 60초 호흡 고르기 기준점
                                            bot_global_state["symbols"][symbol]["take_profit_price"] = "대기중"  # [Phase 16] 문자열 통일
                                            bot_global_state["symbols"][symbol]["stop_loss_price"] = 0.0
                                            bot_global_state["symbols"][symbol]["real_sl"] = 0.0
                                            bot_global_state["symbols"][symbol]["trailing_active"] = False
                                            bot_global_state["symbols"][symbol]["trailing_target"] = 0.0
                                            bot_global_state["symbols"][symbol]["partial_tp_executed"] = False
                                            bot_global_state["symbols"][symbol]["is_paper"] = False  # 플래그 리셋
                                            bot_global_state["symbols"][symbol]["entry_timestamp"] = 0.0  # [Race Condition Fix]

                                        # [v2.1] 연패 쿨다운 카운터 업데이트 (Paper도 카운트하여 전략 검증)
                                        is_loss = (pnl_amount < 0)
                                        strategy_instance.record_trade_result(is_loss)

                                        # [v2.2] 일일 누적 PnL 반영 + 킬스위치 체크 (Paper는 제외)
                                        if not _is_paper:
                                            kill_triggered = strategy_instance.record_daily_pnl(pnl_amount)
                                            if kill_triggered:
                                                kill_msg = f"🚨 [킬스위치 발동] 일일 최대 손실({strategy_instance.daily_max_loss_pct*100:.0f}%) 도달. 24시간 동안 매매 엔진 셋다운."
                                                bot_global_state["logs"].append(kill_msg)
                                                logger.warning(kill_msg)
                                                send_telegram_sync(f"🚨 킬스위치 발동\n일일 누적 손실: {strategy_instance.daily_pnl_accumulated:+.2f} USDT\n24시간 거래 중단")

                                    except Exception as e:
                                        error_msg = f"[{symbol}] 청산 실패 ({action}): {str(e)}"
                                        bot_global_state["logs"].append(error_msg)
                                        logger.error(error_msg)
                                        send_telegram_sync(_tg_exit(symbol, position_side, current_price, 0.0, 0.0, 0.0, 0.0, action, is_test=bot_global_state["symbols"][symbol].get("is_paper", False)))

                    # 포지션 없을 때 진입 신호 체크
                    if bot_global_state["symbols"][symbol]["position"] == "NONE":
                        # [Phase 19] 퇴근 모드 작동 시 신규 진입 강제 차단
                        if _exit_only:
                            continue

                        # [Phase 19.3] 60초 호흡 고르기 (동일 캔들 무한 단타 방지)
                        last_exit = bot_global_state["symbols"][symbol].get("last_exit_time", 0)
                        if time.time() - last_exit < 60:
                            continue

                        # 현재 사이클 최신 상태 기준 — 다른 심볼에 포지션이 있으면 진입 차단
                        any_other_position_open = any(
                            s.get("position", "NONE") != "NONE"
                            for k, s in bot_global_state["symbols"].items()
                            if k != symbol
                        )
                        if any_other_position_open:
                            continue

                        # [v2.2] 일일 킬스위치 단일 게이트 — 시스템 전체에서 쿨스위치 플래그 하나만 참조
                        if strategy_instance.kill_switch_active and _time.time() < strategy_instance.kill_switch_until:
                            continue

                        # signal, analysis_msg는 위에서 이미 평가됨
                        if signal in ["LONG", "SHORT"]:
                            # [Phase 18.1] 방향 모드 필터 (LONG/SHORT/AUTO) — 코인별 독립 설정
                            _direction_mode = str(get_config('direction_mode', symbol) or 'AUTO').upper()
                            if _direction_mode == 'LONG' and signal != 'LONG':
                                continue  # LONG 전용 모드: SHORT 신호 차단
                            if _direction_mode == 'SHORT' and signal != 'SHORT':
                                continue  # SHORT 전용 모드: LONG 신호 차단

                            msg = f"[{symbol}] {signal} 진입 신호 — 현재가: ${current_price}, RSI: {latest_rsi:.1f}"
                            bot_global_state["logs"].append(msg)
                            logger.info(msg)

                            try:
                                # 수동 오버라이드 or [v2.2] ATR 기반 동적 포지션 사이징
                                manual_override = str(get_config('manual_override_enabled')).lower() == 'true'
                                # [Phase 18.1] 코인별 레버리지 로드 (심볼 전용 우선, GLOBAL Fallback)
                                trade_leverage = max(1, min(100, int(get_config('manual_leverage' if manual_override else 'leverage', symbol) or 1)))
                                try:
                                    contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                                except Exception:
                                    contract_size = 0.01
                                if manual_override:
                                    # [Phase 9.1] USDT → 계약수 환산
                                    # 공식: 계약수 = floor(입력USDT * 레버리지 / (현재가 * 계약당기초자산))
                                    seed_usdt = max(1.0, float(get_config('manual_amount') or 10))
                                    notional = seed_usdt * trade_leverage
                                    trade_amount = max(1, round(notional / (current_price * contract_size)))
                                else:
                                    # [v2.3] 정률법 기반 동적 사이징 적용 (UI 연동 · 증거금 부족 패치)
                                    # [Phase 18.1] 코인별 리스크 비율 로드 (심볼 전용 우선, GLOBAL Fallback)
                                    _risk_rate = float(get_config('risk_per_trade', symbol) or 0.02)
                                    trade_amount = strategy_instance.calculate_position_size_dynamic(
                                        curr_bal, current_price, trade_leverage, contract_size, _risk_rate
                                    )
                                # 레버리지 거래소 적용
                                try:
                                    await asyncio.to_thread(engine_api.exchange.set_leverage, trade_leverage, symbol)
                                except Exception as lev_err:
                                    logger.warning(f"[{symbol}] 레버리지 설정 실패: {lev_err}")
                                # 진입 방식 (Market vs Smart Limit)
                                order_type = str(get_config('ENTRY_ORDER_TYPE') or 'Market')
                                ema_20_val = float(df['ema_20'].iloc[-1]) if 'ema_20' in df.columns and not pd.isna(df['ema_20'].iloc[-1]) else current_price

                                # [DRY] 단일 헬퍼로 주문 실행
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
                                        bot_global_state["symbols"][symbol]["pending_order_time"] = time.time()
                                        bot_global_state["symbols"][symbol]["pending_amount"] = trade_amount
                                        bot_global_state["symbols"][symbol]["pending_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["is_paper"] = _is_shadow_pending

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
                                        bot_global_state["symbols"][symbol]["entry_timestamp"] = time.time()  # [Race Condition Fix]

                                    entry_emoji = "📈" if signal == "LONG" else "📉"
                                    entry_msg = f"{_paper_tag}{entry_emoji} [{symbol}] {signal} 시장가 진입 성공! | 가격: ${executed_price:.2f} | {trade_amount}계약 | 레버리지 {trade_leverage}x"
                                    bot_global_state["logs"].append(entry_msg)
                                    logger.info(entry_msg)
                                    send_telegram_sync(_tg_entry(symbol, signal, executed_price, trade_amount, trade_leverage, payload=payload, is_test=_is_shadow_entry))

                            except Exception as e:
                                error_msg = f"[{symbol}] 진입 실패: {str(e)}"
                                bot_global_state["logs"].append(error_msg)
                                logger.error(error_msg)

                except Exception as e:
                    logger.warning(f"[{symbol}] 루프 처리 중 오류 (다음 루프 계속): {e}")

            # 5초마다 엔진 맥박(Pulse) 로그 출력
            current_time = time.time()
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

            await asyncio.sleep(3)

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

            await asyncio.sleep(3)

# ===== 기존 엔드포인트 (하위 호환) =====

@app_server.post("/api/v1/test_order")
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

        # 심볼이 아직 루프에서 초기화되지 않았으면 방어적으로 초기화
        if symbol not in bot_global_state["symbols"]:
            bot_global_state["symbols"][symbol] = {
                "position": "NONE", "entry_price": 0.0, "current_price": 0.0,
                "unrealized_pnl_percent": 0.0, "take_profit_price": "대기중",  # [Phase 16]
                "stop_loss_price": 0.0, "highest_price": 0.0, "lowest_price": 0.0
            }

        # 포지션이 이미 있을 경우 방어
        if bot_global_state["symbols"][symbol]["position"] != "NONE":
            err_msg = "[오류] 이미 포지션을 보유 중이어서 테스트 진입을 진행할 수 없습니다."
            bot_global_state["logs"].append(err_msg)
            return {"error": "이미 포지션이 존재합니다."}

        engine_api = _engine
        if not engine_api or not engine_api.exchange:
            return {"error": "OKX 거래소 인스턴스가 연결되지 않았습니다."}
            
        # 수동 오버라이드 or 동적 포지션 사이징
        manual_override = str(get_config('manual_override_enabled')).lower() == 'true'
        trade_leverage = max(1, min(100, int(get_config('manual_leverage' if manual_override else 'leverage') or 1)))
        current_price_now = (await asyncio.to_thread(engine_api.get_current_price, symbol)) or 1
        try:
            contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
        except Exception:
            contract_size = 0.01
        if manual_override:
            # [Phase 9.1] USDT → 계약수 환산 (async_trading_loop와 동일 공식 — DRY)
            # 공식: 계약수 = round(입력USDT * 레버리지 / (현재가 * 계약당기초자산))
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
        # 레버리지 거래소 적용
        try:
            await asyncio.to_thread(engine_api.exchange.set_leverage, trade_leverage, symbol)
        except Exception as lev_err:
            logger.warning(f"[{symbol}] 레버리지 설정 실패: {lev_err}")

        # 진입 방식 (Market vs Smart Limit) — 설정 존중
        order_type = str(get_config('ENTRY_ORDER_TYPE') or 'Market')
        current_price = (await asyncio.to_thread(engine_api.get_current_price, symbol)) or 0

        try:
            # [DRY] 단일 헬퍼로 주문 실행
            executed_price, pending_order_id = await execute_entry_order(
                engine_api, symbol, signal, trade_amount, order_type, current_price
            )

            # 포지션 상태 갱신 (Smart Limit → PENDING, Market → 즉시 반영)
            if order_type == 'Smart Limit' and pending_order_id:
                import time
                _is_shadow_pend_test = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
                _paper_tag_pt = "[👻 PAPER] " if _is_shadow_pend_test else ""
                # [Phase 20.1] 상태 변경 시 자물쇠 잠금
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
                # [Phase 20.1] 상태 변경 시 자물쇠 잠금
                async with state_lock:
                    bot_global_state["symbols"][symbol]["position"] = signal
                    bot_global_state["symbols"][symbol]["entry_price"] = executed_price or current_price
                    bot_global_state["symbols"][symbol]["highest_price"] = executed_price or current_price
                    bot_global_state["symbols"][symbol]["lowest_price"] = executed_price or current_price
                    bot_global_state["symbols"][symbol]["contracts"] = trade_amount
                    bot_global_state["symbols"][symbol]["leverage"] = trade_leverage
                    bot_global_state["symbols"][symbol]["partial_tp_executed"] = False
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

@app_server.post("/api/v1/close_paper")
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

        # 현재가 조회
        engine_api = _engine
        if engine_api and engine_api.exchange:
            current_price = (await asyncio.to_thread(engine_api.get_current_price, symbol)) or 0
        else:
            current_price = sym_state.get("current_price", 0)

        entry = sym_state.get("entry_price", 0)
        amount = int(sym_state.get("contracts", 1))
        leverage = int(sym_state.get("leverage", 1))

        # 가상 PnL 시뮬레이션 (매매루프 Paper 청산과 동일한 공식)
        try:
            contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01)) if engine_api else 0.01
        except Exception:
            contract_size = 0.01

        position_value = entry * amount * contract_size
        if position_side == "LONG":
            total_gross = (current_price - entry) * amount * contract_size
        else:
            total_gross = (entry - current_price) * amount * contract_size
        total_fee = -(position_value * 0.0005 * 2)
        pnl_amount = total_gross + total_fee
        pnl_percent = (pnl_amount / (position_value / leverage) * 100) if position_value > 0 else 0.0

        # DB 저장 차단 (Paper → save_trade 절대 호출 안함)

        # 로깅
        emoji = "✅" if pnl_percent >= 0 else "🔴"
        msg = f"[👻 PAPER] {emoji} [{symbol}] {position_side} 수동 청산 | 체결가: ${current_price:.2f} | 순수익(Net): {pnl_amount:+.4f} USDT (Gross: {total_gross:+.4f}, Fee: {total_fee:.4f}) | 수익률: {pnl_percent:+.2f}%"
        bot_global_state["logs"].append(msg)
        logger.info(msg)
        send_telegram_sync(f"[👻 PAPER] {emoji} 수동 청산\n코인: {symbol} {position_side}\n체결가: ${current_price:.2f}\n순수익: {pnl_amount:+.4f} USDT ({pnl_percent:+.2f}%)")

        # [Phase 20.1] 상태 변경 시 자물쇠 잠금
        async with state_lock:
            # 상태 초기화
            sym_state["position"] = "NONE"
            sym_state["entry_price"] = 0.0
            sym_state["last_exit_time"] = _time.time()  # [Phase 19.3] 60초 호흡 고르기 기준점
            sym_state["take_profit_price"] = "대기중"  # [Phase 16] 문자열 통일
            sym_state["stop_loss_price"] = 0.0
            sym_state["real_sl"] = 0.0
            sym_state["trailing_active"] = False
            sym_state["trailing_target"] = 0.0
            sym_state["partial_tp_executed"] = False
            sym_state["is_paper"] = False

            # [Phase 19] 강제 청산 시 봇 재진입 방지를 위해 자동매매 엔진 자체를 STOP 상태로 전환
            bot_global_state["is_running"] = False
            stop_msg = "[시스템] 강제 청산(Paper) 감지: 봇 무한 재진입 방지를 위해 자동매매를 일시 중지(STOP) 합니다."
            bot_global_state["logs"].append(stop_msg)
            logger.info(stop_msg)
            send_telegram_sync(_tg_system(False))

        return {"status": "success", "message": msg}

    except Exception as e:
        return {"error": f"서버 오류: {str(e)}"}

@app_server.post("/api/v1/inject_stress")
async def inject_stress(type: str):
    """[Phase 3] 스트레스 주입기 — 킬스위치/쿨다운 소방훈련"""
    stress_type = type.upper()
    if stress_type not in ("KILL_SWITCH", "LOSS_STREAK"):
        return {"error": f"잘못된 type: {type}. KILL_SWITCH 또는 LOSS_STREAK만 허용."}
    if not bot_global_state["is_running"]:
        return {"error": "시스템이 중지되어 있습니다. 먼저 가동해 주세요."}
    bot_global_state["stress_inject"] = stress_type
    return {"status": "success", "message": f"스트레스 주입 예약 완료: {stress_type} (다음 매매루프 사이클에서 발동)"}

@app_server.post("/api/v1/reset_stress")
async def reset_stress():
    """[Phase 3] 킬스위치/쿨다운 강제 해제"""
    bot_global_state["stress_inject"] = "RESET"
    reset_msg = "✅ [소방훈련 해제] 킬스위치 + 쿨다운 리셋 예약 완료 (다음 사이클에서 적용)"
    bot_global_state["logs"].append(reset_msg)
    return {"status": "success", "message": reset_msg}

@app_server.get("/api/v1/status")
async def fetch_current_status():
    """현재 봇 상태 반환 (OKX 실시간 데이터 강제 동기화)"""
    # 봇이 켜져있지 않더라도 실시간 잔고는 업데이트되어야 함
    try:
        engine = _engine
        if engine and engine.exchange:
            # 1. 잔고 무조건 갱신 (0이어도 반영 - 초기 로드 0 버그 수정)
            curr_bal = await asyncio.to_thread(engine.get_usdt_balance)
            bot_global_state["balance"] = round(curr_bal, 2)

            # 2. OKX 포지션 Hydration - CCXT ROE(percentage) 직접 바이패스
            # [DRY] 수동청산 감지는 매매루프(trading loop) 및 Private WS에서 단일 처리
            # [Race Condition Fix] position/entry_price는 매매루프가 단독 관리 — 표시 전용 필드(PnL/markPrice)만 갱신
            try:
                positions = await asyncio.to_thread(engine.exchange.fetch_positions)

                # 거래소에서 현재 활성 포지션 심볼 목록 추출
                exchange_active = {}
                for pos in positions:
                    contracts = float(pos.get('contracts', 0) or 0)
                    if contracts > 0:
                        symbol = pos.get('symbol')
                        side = pos.get('side', '').upper()
                        if symbol and side in ['LONG', 'SHORT']:
                            exchange_active[symbol] = pos

                # 봇 상태 업데이트: position/entry_price 건드리지 않고 표시 전용 필드만 갱신
                for symbol, sym_state in bot_global_state["symbols"].items():
                    if symbol in exchange_active:
                        pos = exchange_active[symbol]
                        roe = float(pos.get('percentage', 0) or 0)
                        unrealized = float(pos.get('unrealizedPnl', 0) or 0)
                        leverage = float(pos.get('leverage', 1) or 1)
                        mark = float(pos.get('markPrice', 0) or 0)
                        entry = float(pos.get('entryPrice', 0) or 0)
                        side = pos.get('side', '').upper()

                        # OKX percentage가 0이면 선물 공식으로 Fallback
                        if roe == 0.0 and entry > 0 and mark > 0:
                            diff = (mark - entry) / entry if side == 'LONG' else (entry - mark) / entry
                            roe = round(diff * 100 * leverage, 2)

                        # 표시 전용 필드만 갱신 (position/entry_price는 매매루프 전용)
                        sym_state["unrealized_pnl_percent"] = roe
                        sym_state["unrealized_pnl"] = unrealized
                        sym_state["leverage"] = leverage
                        if mark > 0:
                            sym_state["current_price"] = mark
                        # entry_price가 아직 없을 때만 최초 1회 동기화 (매매루프 진입 전 초기화용)
                        if entry > 0 and sym_state.get("entry_price", 0) == 0:
                            sym_state["entry_price"] = entry
                            sym_state["position"] = side

            except Exception as pe:
                logger.error(f"[헬스체크] OKX 포지션 API 핑 실패 — RAW 원인: {pe}")
    except Exception as e:
        logger.error(f"[헬스체크] OKX API 헬스체크 핑 실패 사유: {e}")

    # logs(300개)는 제외하고 반환 - 매초 전송 시 불필요한 대용량 페이로드 방지
    # 로그는 /api/v1/logs 엔드포인트에서 별도 조회
    _sym_conf = get_config('symbols')
    active_target = _sym_conf[0] if isinstance(_sym_conf, list) and _sym_conf else "BTC/USDT:USDT"

    # 엔진 튜닝 모드 판별 (DB 내 risk_per_trade 존재 여부 기준)
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

    return {
        "is_running": bot_global_state["is_running"],
        "balance": bot_global_state["balance"],
        "symbols": bot_global_state["symbols"],
        "active_target": active_target,
        "engine_status": {
            "mode": engine_mode,
            "risk": active_risk,
        },
    }

@app_server.get("/api/v1/brain")
async def fetch_brain_status():
    """AI 뇌 상태 반환"""
    _sym_conf = get_config('symbols')
    active_target = _sym_conf[0] if isinstance(_sym_conf, list) and _sym_conf else "BTC/USDT:USDT"
    return {**ai_brain_state, "active_target": active_target}

@app_server.get("/api/v1/trades")
async def fetch_trades_history():
    """최근 거래 내역 반환 (DB 기반)"""
    return get_trades(limit=100)

@app_server.post("/api/v1/toggle")
async def toggle_bot_action():
    """봇 시작/중지"""
    global bot_global_state, _trading_task

    # [Phase 20.1] 상태 변경 시 자물쇠 잠금
    async with state_lock:
        if bot_global_state["is_running"]:
            bot_global_state["is_running"] = False
            msg = "[봇] 명령 수신: 시스템 가동 중지 (STOP)"
            bot_global_state["logs"].append(msg)
            logger.info(msg)
            send_telegram_sync(_tg_system(False))
        else:
            bot_global_state["is_running"] = True
            msg = "[봇] 명령 수신: 시스템 가동 시작 (START)!"
            bot_global_state["logs"].append(msg)
            logger.info(msg)
            send_telegram_sync(_tg_system(True))
            # 중복 태스크 방지: 이전 태스크가 완료된 경우에만 새 태스크 생성
            if _trading_task is None or _trading_task.done():
                _trading_task = asyncio.create_task(async_trading_loop())

    return {"is_running": bot_global_state["is_running"]}

# --- 신규 WebSocket 엔드포인트 ---
@app_server.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    global _broadcast_task
    await manager.connect(websocket)
    # 첫 번째 클라이언트 연결 시 브로드캐스트 루프 on-demand 가동 (공회전 방지)
    if _broadcast_task is None or _broadcast_task.done():
        _broadcast_task = asyncio.create_task(broadcast_dashboard_state())
    try:
        while True:
            # 클라이언트로부터의 메시지 수신 대기 (연결 유지용)
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ===== 신규 엔드포인트 =====

@app_server.get("/api/v1/stats")
async def fetch_statistics():
    """성과 분석 통계 — KST 기준 오늘 일일 지표 포함"""
    from datetime import datetime, timezone, timedelta
    trades = get_trades(limit=1000)

    total_trades = len(trades)
    # None 안전 처리 (DB에 NULL 저장된 경우 TypeError 방지)
    win_trades = len([t for t in trades if (t.get('pnl_percent') or 0) > 0])
    win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0

    total_pnl_percent = sum([(t.get('pnl_percent') or 0) for t in trades])

    # Max Drawdown 계산 (시간 오름차순 정렬 후 누적 계산)
    max_drawdown = 0
    if trades:
        sorted_trades = list(reversed(trades))  # DESC → 오름차순(과거→최신)으로 정렬
        # 첫 거래의 entry_price 기반으로 초기 잔고 추정 (하드코딩 제거)
        first_pnl = sorted_trades[0].get('pnl') or 0
        initial_balance = max(1.0, abs(first_pnl) * 100 if first_pnl else 100.0)
        running_balance = initial_balance
        running_max = initial_balance
        for trade in sorted_trades:
            pnl = trade.get('pnl') or 0
            running_balance += pnl
            running_max = max(running_max, running_balance)
            drawdown = (running_max - running_balance) / running_max if running_max > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

    # Sharpe Ratio 계산
    sharpe_ratio = 0
    if total_trades > 1:
        import statistics
        pnl_percent_list = [(t.get('pnl_percent') or 0) for t in trades]
        mean_pnl = statistics.mean(pnl_percent_list)
        std_pnl = statistics.stdev(pnl_percent_list) if len(pnl_percent_list) > 1 else 1
        if std_pnl > 0:
            sharpe_ratio = mean_pnl / std_pnl

    # ── KST 기준 오늘 일일 지표 계산 ──────────────────────────────────────────
    KST = timezone(timedelta(hours=9))
    today_kst = datetime.now(KST).strftime("%Y-%m-%d")

    def _parse_kst_date(created_at_str):
        """DB created_at(UTC naive 문자열)을 KST 날짜 문자열로 변환"""
        if not created_at_str:
            return ""
        try:
            dt = datetime.fromisoformat(str(created_at_str).replace(' ', 'T'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(KST).strftime("%Y-%m-%d")
        except Exception:
            return ""

    today_trades = [t for t in trades if _parse_kst_date(t.get('created_at')) == today_kst]
    # daily_net_pnl: KST 기준 오늘 모든 거래의 순손익(fee 차감) 합산 — 양수/음수 가능
    daily_net_pnl = sum((t.get('pnl') or 0) for t in today_trades)
    # total_net_pnl: 전체 기간 모든 거래의 순손익 합산
    total_net_pnl = sum((t.get('pnl') or 0) for t in trades)
    # ─────────────────────────────────────────────────────────────────────────

    return {
        'total_trades': total_trades,
        'win_rate': round(win_rate, 2),
        'total_pnl_percent': round(total_pnl_percent, 2),
        'max_drawdown': round(max_drawdown * 100, 2),
        'sharpe_ratio': round(sharpe_ratio, 2),
        'daily_net_pnl': round(daily_net_pnl, 4),
        'total_net_pnl': round(total_net_pnl, 4),
    }


@app_server.get("/api/v1/history_stats")
async def fetch_history_stats():
    """KST 기준 일별/월별 누적 거래 통계"""
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict

    trades = get_trades(limit=99999)
    KST = timezone(timedelta(hours=9))

    daily_map = defaultdict(lambda: {'total': 0, 'wins': 0, 'gross_pnl': 0.0, 'net_pnl': 0.0})
    monthly_map = defaultdict(lambda: {'total': 0, 'wins': 0, 'gross_pnl': 0.0, 'net_pnl': 0.0})

    for t in trades:
        created_at_str = t.get('created_at')
        if not created_at_str:
            continue
        try:
            dt = datetime.fromisoformat(str(created_at_str).replace(' ', 'T'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_kst = dt.astimezone(KST)
        except Exception:
            continue

        date_key = dt_kst.strftime("%Y-%m-%d")
        month_key = dt_kst.strftime("%Y-%m")
        net_pnl = t.get('pnl') or 0
        gross_pnl = t.get('gross_pnl') or 0
        is_win = net_pnl > 0

        for key, mapping in [(date_key, daily_map), (month_key, monthly_map)]:
            mapping[key]['total'] += 1
            mapping[key]['gross_pnl'] += gross_pnl
            mapping[key]['net_pnl'] += net_pnl
            if is_win:
                mapping[key]['wins'] += 1

    def _build_sorted_list(mapping):
        result = []
        for key, data in mapping.items():
            total = data['total']
            win_rate = round(data['wins'] / total * 100, 2) if total > 0 else 0.0
            result.append({
                'date': key,
                'total_trades': total,
                'win_rate': win_rate,
                'gross_pnl': round(data['gross_pnl'], 4),
                'net_pnl': round(data['net_pnl'], 4),
            })
        return sorted(result, key=lambda x: x['date'], reverse=True)

    return {
        'daily': _build_sorted_list(daily_map),
        'monthly': _build_sorted_list(monthly_map),
    }


@app_server.post("/api/v1/wipe_db")
async def wipe_database():
    """[ADMIN] trades 테이블 전면 삭제 — 실전 투입 전 테스트 데이터 초기화"""
    global _active_strategy

    try:
        # 1. DB 거래 기록 전체 삭제
        wipe_all_trades()

        # 2. 인메모리 일일 누적 상태 리셋 (활성 전략 인스턴스가 존재할 경우)
        if _active_strategy is not None:
            _active_strategy.daily_pnl_accumulated = 0.0
            _active_strategy.daily_start_balance = 0.0
            _active_strategy.consecutive_loss_count = 0
            _active_strategy.loss_cooldown_until = 0

        # 3. 로그 초기화 및 완료 메시지 기록
        bot_global_state["logs"].clear()
        bot_global_state["logs"].append("[🚨 ADMIN] 데이터베이스 전면 초기화 완료. 실전 매매 준비 끝.")
        logger.warning("[ADMIN] wipe_db 실행: trades 테이블 전체 삭제 완료")

        return {"success": True, "message": "DB 초기화 완료. 실전 매매 준비 상태."}
    except Exception as e:
        logger.error(f"[ADMIN] wipe_db 실패: {e}")
        return {"success": False, "message": str(e)}


@app_server.get("/api/v1/export_csv")
async def export_csv():
    """전체 거래 내역 CSV 파일 다운로드"""
    import csv, io
    from fastapi.responses import Response as FastAPIResponse

    trades = get_trades(limit=99999)
    fieldnames = [
        'ID', 'Symbol', 'Position', 'Entry_Price', 'Exit_Price',
        'Amount', 'Leverage', 'Gross_PnL', 'Fee', 'Net_PnL',
        'Entry_Time', 'Exit_Time', 'Exit_Reason',
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for t in trades:
        writer.writerow({
            'ID':          t.get('id', ''),
            'Symbol':      t.get('symbol', ''),
            'Position':    t.get('position_type', ''),
            'Entry_Price': t.get('entry_price', ''),
            'Exit_Price':  t.get('exit_price', ''),
            'Amount':      t.get('amount', ''),
            'Leverage':    t.get('leverage', ''),
            'Gross_PnL':   t.get('gross_pnl', ''),
            'Fee':         t.get('fee', ''),
            'Net_PnL':     t.get('pnl', ''),
            'Entry_Time':  t.get('entry_time', ''),
            'Exit_Time':   t.get('exit_time', ''),
            'Exit_Reason': t.get('exit_reason', ''),
        })
    csv_bytes = output.getvalue().encode('utf-8-sig')  # BOM: Excel 한글 깨짐 방지
    output.close()
    return FastAPIResponse(
        content=csv_bytes,
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="antigravity_trades.csv"'},
    )


@app_server.get("/api/v1/config")
async def fetch_config(symbol: Optional[str] = None):
    """현재 봇 설정 조회. symbol 지정 시 해당 심볼 전용값 우선 반환 (GLOBAL Fallback 포함)"""
    base_config = get_config()  # GLOBAL 전체 (symbol:: 접두 키 제외)
    if symbol and symbol != "GLOBAL":
        # 심볼 전용 설정 해결: 각 키에 대해 심볼 전용값 우선, 없으면 GLOBAL값 사용
        resolved = {}
        for key in base_config:
            sym_val = get_config(key, symbol)
            resolved[key] = sym_val if sym_val is not None else base_config[key]
        return resolved
    return base_config

@app_server.post("/api/v1/config")
async def update_config(key: str, value: str, symbol: str = "GLOBAL"):
    """봇 설정 변경 (실시간 적용). symbol 지정 시 해당 심볼 전용으로 저장, 미지정 시 GLOBAL"""
    try:
        set_config(key, value, symbol)
        sym_tag = f"[{symbol}] " if symbol != "GLOBAL" else ""
        log_msg = f"[UI 연동 성공 \U0001f7e2] {sym_tag}'{key}' 설정이 '{value}'(으)로 뇌 구조에 완벽히 적용되었습니다."
        bot_global_state["logs"].append(log_msg)
        logger.info(log_msg)
        return {"success": True, "message": f"{key} 업데이트 완료"}
    except Exception as e:
        log_msg = f"[UI 연동 실패 \U0001f534] '{key}' 설정 적용 중 코드 연결 오류가 발생했습니다."
        bot_global_state["logs"].append(log_msg)
        logger.error(log_msg)
        return {"success": False, "message": str(e)}

@app_server.post("/api/v1/tuning/reset")
async def reset_tuning_to_auto():
    """튜닝 파라미터 전체 삭제 + 전략 인스턴스 재생성 (AI 순정 모드 딥 리셋)"""
    global _active_strategy
    keys = [
        "adx_threshold", "adx_max", "chop_threshold", "volume_surge_multiplier",
        "fee_margin", "hard_stop_loss_rate", "trailing_stop_activation",
        "trailing_stop_rate", "cooldown_losses_trigger", "cooldown_duration_sec",
        "risk_per_trade", "leverage",  # [누락된 핵심 파라미터 추가]
        "disparity_threshold",  # [Phase 14.2] 이격도 동적 한계치
        "bypass_macro", "bypass_disparity", "bypass_indicator",  # [Phase 14.1] Gate Bypass 플래그
    ]
    delete_configs(keys)
    _active_strategy = TradingStrategy()
    msg = "[시스템] 사령관 명령 수신: 튜닝 데이터 삭제 및 AI 순정 모드(Tier 1) 딥 리셋 완료."
    bot_global_state["logs"].append(msg)
    logger.info(msg)
    return {"success": True, "message": msg}

@app_server.get("/api/v1/ohlcv")
async def fetch_ohlcv(symbol: str = "BTC/USDT:USDT", limit: int = 100):
    """OHLCV 캔들 데이터 (차트용)"""
    try:
        engine = _engine
        if not engine or not engine.exchange:
            return {"error": "거래소 연결 실패"}

        ohlcv = await asyncio.to_thread(engine.exchange.fetch_ohlcv, symbol, "5m", limit=limit)
        
        # 샌드박스 환경 등에서 데이터가 아예 안 들어올 경우를 대비한 가상 데이터 생성 로직
        if not ohlcv or len(ohlcv) == 0:
            warn_msg = f"[차트 경고 🟡] [{symbol}] OKX 샘드박스가 OHLCV 데이터를 제공하지 않습니다. 가짜(Mock) 차트 데이터를 사용합니다."
            logger.warning(warn_msg)
            bot_global_state["logs"].append(warn_msg)
            import time
            import random
            current_time = int(time.time() * 1000)
            mock_ohlcv = []
            base_price = await asyncio.to_thread(engine.get_current_price, symbol)
            if not base_price:
                 base_price = 50000.0  # BTC/USDT 임시 기준가
            
            for i in range(limit):
                ts = current_time - ((limit - i) * 60 * 1000)
                # 캔들 시가/종가/고가/저가를 임의로 약간씩 흔듦
                open_p = base_price + random.uniform(-10, 10)
                close_p = open_p + random.uniform(-15, 15)
                high_p = max(open_p, close_p) + random.uniform(1, 15)
                low_p = min(open_p, close_p) - random.uniform(1, 15)
                volume = random.uniform(0.1, 5.0)
                
                mock_ohlcv.append({
                    'timestamp': ts,
                    'open': round(open_p, 2),
                    'high': round(high_p, 2),
                    'low': round(low_p, 2),
                    'close': round(close_p, 2),
                    'volume': round(volume, 4)
                })
                base_price = close_p # 다음 캔들 기준가 업데이트
            return mock_ohlcv

        result = []
        for candle in ohlcv:
            result.append({
                'timestamp': int(candle[0]),
                'open': candle[1],
                'high': candle[2],
                'low': candle[3],
                'close': candle[4],
                'volume': candle[5]
            })
        return result
    except Exception as e:
        logger.error(f"OHLCV 조회 실패: {e}")
        return {"error": str(e)}

@app_server.post("/api/v1/backtest")
async def run_backtest(symbol: str = "BTC/USDT:USDT", timeframe: str = "1m", limit: int = 100):
    """백테스팅 실행"""
    try:
        backtester = Backtester(initial_seed=75.0, engine=_engine)
        result = backtester.run(symbol=symbol, timeframe=timeframe, limit=limit)
        return result
    except Exception as e:
        logger.error(f"백테스팅 실패: {e}")
        return {"error": str(e)}

@app_server.get("/api/v1/symbols")
async def fetch_symbols():
    """지원 심볼 목록"""
    symbols_config = get_config('symbols')
    if isinstance(symbols_config, list):
        return {"symbols": symbols_config}
    return {"symbols": ["BTC/USDT:USDT"]}

@app_server.get("/api/v1/logs")
async def fetch_system_logs(limit: int = 50, after_id: int = 0):
    """DB 저장 로그 조회. after_id 지정 시 해당 id 이후 신규 로그만 반환 (오름차순)."""
    logs = get_logs(limit=limit, after_id=after_id)
    if not logs:
        return []

    # after_id 없는 초기 로드: DESC로 가져온 것을 오름차순으로 되돌림
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

@app_server.get("/api/v1/system_health")
async def fetch_system_health():
    """시스템 헬스 체크: OKX API, 텔레그램 실 통신, 엔진 상태 리턴"""
    # 1. OKX API 연결 상태 — fetch_balance() 실 통신
    okx_connected = False
    try:
        if _engine and _engine.exchange:
            _engine.exchange.fetch_balance()
            okx_connected = True
    except Exception:
        okx_connected = False

    # 2. 텔레그램 실제 API 핑 — bot.get_me() 로 토큰 + 네트워크 양방향 검증
    #    단순 객체 존재 여부(表面)가 아닌, Telegram 서버 응답 성공 여부(실질) 확인
    from notifier import _telegram_app, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    telegram_connected = False
    telegram_bot_name = ""
    try:
        if _telegram_app and _telegram_app.bot:
            me = await _telegram_app.bot.get_me()
            telegram_connected = True
            telegram_bot_name = f"@{me.username}" if me else ""
        elif TELEGRAM_BOT_TOKEN:
            # _telegram_app 미초기화 시 httpx로 직접 핑
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

    # 3. AI 매매 엔진 동작 상태
    strategy_running = bool(
        bot_global_state.get("is_running", False) and
        _trading_task is not None and
        not _trading_task.done()
    )

    return {
        "okx_connected": okx_connected,
        "telegram_connected": telegram_connected,
        "telegram_bot_name": telegram_bot_name,
        "strategy_engine_running": strategy_running
    }

# ─────────────────────────────────────────────────────────────────────────────
# 프론트엔드 호스팅 (백엔드 IP로 직접 접속 가능하게 설정)
# 모든 API 경로 정의 이후에 위치해야 API 요청을 가로채지 않음
# ─────────────────────────────────────────────────────────────────────────────
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(frontend_path):
    # html=True 설정으로 / 접속 시 자동으로 index.html 서빙
    app_server.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
else:
    @app_server.get("/")
    async def serve_error():
        return {"error": "Frontend directory not found", "expected_path": frontend_path}


if __name__ == "__main__":
    uvicorn.run("api_server:app_server", host="0.0.0.0", port=8000, reload=False)
