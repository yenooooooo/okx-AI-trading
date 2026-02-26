import asyncio
import json
import hmac
import hashlib
import base64
import time as _time
import uvicorn
import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from okx_engine import OKXEngine
from strategy import TradingStrategy
from database import init_db, save_trade, get_trades, get_config, set_config, save_log, get_logs
from backtester import Backtester
from notifier import send_telegram_sync
from logger import get_logger

logger = get_logger(__name__)

# ── Telegram HTML 포맷터 ──────────────────────────────────────────────────────
_TG_LINE = "─" * 24

def _sym_short(symbol: str) -> str:
    return symbol.split(':')[0]

def _tg_entry(symbol: str, direction: str, price: float, amount: int, leverage: int, is_test: bool = False) -> str:
    d_emoji = "📈" if direction == "LONG" else "📉"
    test_tag = "  <b>[TEST]</b>" if is_test else ""
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  진입{test_tag}\n"
        f"{_TG_LINE}\n"
        f"{d_emoji} <b>{direction}</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"가격   │  <code>${price:,.2f}</code>\n"
        f"수량   │  <code>{amount}계약  ·  {leverage}x</code>\n"
        f"{_TG_LINE}"
    )

def _tg_exit(symbol: str, direction: str, avg_price: float, pnl_usdt: float, pnl_pct: float, reason: str) -> str:
    is_profit = pnl_pct >= 0
    result_emoji = "✅" if is_profit else "🔴"
    result_label = "익절" if is_profit else "손절"
    sign = "+" if pnl_usdt >= 0 else ""
    pnl_label = "수익금" if is_profit else "손실금"
    pct_label  = "수익률" if is_profit else "손실률"
    _reason_ko = {"STOP_LOSS": "하드 손절", "TRAILING_STOP_EXIT": "트레일링 익절"}
    reason_ko  = _reason_ko.get(reason, reason)
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  청산\n"
        f"{_TG_LINE}\n"
        f"{result_emoji} <b>{direction} {result_label}</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"청산가  │  <code>${avg_price:,.2f}</code>\n"
        f"{pnl_label}  │  <b><code>{sign}{pnl_usdt:.4f} USDT</code></b>\n"
        f"{pct_label}  │  <b><code>{sign}{pnl_pct:.2f}%</code></b>\n"
        f"사유    │  {reason_ko}\n"
        f"{_TG_LINE}"
    )

def _tg_manual_exit(symbol: str, direction: str, avg_price: float, pnl_usdt: float, pnl_pct: float) -> str:
    is_profit = pnl_pct >= 0
    sign = "+" if pnl_usdt >= 0 else ""
    pnl_label = "수익금" if is_profit else "손실금"
    pct_label  = "수익률" if is_profit else "손실률"
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  수동청산 감지\n"
        f"{_TG_LINE}\n"
        f"✋ <b>{direction} 수동청산</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"청산가  │  <code>${avg_price:,.2f}</code>\n"
        f"{pnl_label}  │  <b><code>{sign}{pnl_usdt:.4f} USDT</code></b>\n"
        f"{pct_label}  │  <b><code>{sign}{pnl_pct:.2f}%</code></b>\n"
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
    "symbols": {},  # symbol별 상태
    "logs": LogList(["[봇] 시스템 코어 초기화 완료 - API 브릿지 대기 중"]),
}

ai_brain_state = {
    "symbols": {}  # symbol별 뇌 상태
}

trade_history = []
_trading_task = None  # 중복 루프 방지용 태스크 추적
_engine: OKXEngine = None  # 싱글톤 OKX 엔진 (매 요청마다 재생성 방지)

def _generate_ws_sign(secret_key: str, timestamp: str) -> str:
    """OKX WebSocket 인증 서명 생성 (HMAC-SHA256 Base64)"""
    message = timestamp + "GET" + "/users/self/verify"
    mac = hmac.new(bytes(secret_key, 'utf-8'), bytes(message, 'utf-8'), digestmod=hashlib.sha256)
    return base64.b64encode(mac.digest()).decode('utf-8')

def _apply_position_ws_update(pos: dict):
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
            _detect_and_handle_manual_close(_engine, symbol, bot_global_state["symbols"][symbol])
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
    WS_URL = "wss://wspap.okx.com:8443/ws/v5/private"  # 데모 환경
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
                                _apply_position_ws_update(pos)
                    except Exception as parse_err:
                        logger.warning(f"Private WS 메시지 처리 오류: {parse_err}")
        except Exception as e:
            logger.warning(f"Private WS 연결 끊김, 5초 후 재연결: {e}")
            await asyncio.sleep(5)

@app_server.on_event("startup")
async def startup_event():
    """서버 시작 시 OKXEngine 1회만 초기화 + 프라이빗 WS 시작"""
    global _engine
    init_db()
    bot_global_state["logs"].append("[봇] 서버 시스템 가동 시작 - 인프라 점검 중...")
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

    # 실시간 웹 대시보드 브로드캐스트 시작
    asyncio.create_task(broadcast_dashboard_state())
    logger.info("실시간 웹소켓 브로드캐스트 태스크 시작 완료")

@app_server.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 텔레그램 등 비동기 자원 회수 (Graceful Shutdown)"""
    from notifier import stop_telegram_bot
    await stop_telegram_bot()
    logger.info("API 서버 종료 - 텔레그램 자원 릴리즈 완료")

def _detect_and_handle_manual_close(engine_api, symbol: str, sym_state: dict, manual_prev_state: dict = None):
    """
    외부 수동 청산 감지 후 처리:
      - OKX 체결 영수증에서 실현 PnL 추출
      - DB에 MANUAL_CLOSE 기록
      - 터미널 로그 + 텔레그램 알림 발송 (요청된 포맷)
      - 봇 내부 상태를 NONE으로 초기화
    sym_state 는 bot_global_state["symbols"][symbol] 의 참조(reference).
    """
    import time as _t

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
    sym_state["unrealized_pnl_percent"] = 0.0
    sym_state["unrealized_pnl"]        = 0.0
    sym_state["take_profit_price"]     = 0.0
    sym_state["stop_loss_price"]       = 0.0

    pnl_amount     = 0.0
    avg_fill_price = prev_entry  # fallback
    pnl_pct        = 0.0

    # ── OKX 체결 영수증(Trades) 조회 (최대 3회 시도) ─────────────────────────
    # positions-history는 API 반영 지연이 있으므로, 즉시 반영되는 fetch_my_trades 사용
    history_found = False
    for attempt in range(3):
        try:
            _t.sleep(1.0 + attempt * 0.5)
            trades = engine_api.exchange.fetch_my_trades(symbol, limit=20)
            closing_side = 'sell' if prev_pos == 'LONG' else 'buy'
            recent_closes = [t for t in trades if t.get('side') == closing_side]

            if recent_closes:
                order_id       = recent_closes[-1].get('order')
                matching       = [t for t in recent_closes if t.get('order') == order_id]
                total_gross    = sum(float(t.get('info', {}).get('fillPnl', 0) or 0) for t in matching)
                total_fee      = sum(float(t.get('info', {}).get('fee',     0) or 0) for t in matching)
                total_cost     = sum(t.get('cost',   0) for t in matching)
                total_amt      = sum(t.get('amount', 0) for t in matching)
                
                # OKX 실현 수익금(Net PnL) = Gross PnL + Fee (OKX fee is usually negative)
                pnl_amount     = total_gross + total_fee
                if total_amt > 0:
                    avg_fill_price = total_cost / total_amt
                elif matching:
                    avg_fill_price = float(matching[0].get('price', prev_entry))
                
                history_found = True
                break
        except Exception as e:
            logger.error(f"[수동청산 감지] {symbol} 체결 영수증 조회 오류(시도 {attempt+1}): {e}")

    if not history_found:
        logger.warning(f"[수동청산 감지] {symbol} 체결 영수증 없음 - PnL 0 으로 기록")

    if avg_fill_price == 0:
        avg_fill_price = engine_api.get_current_price(symbol) or prev_entry

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
            amount        = prev_contracts,
            exit_reason   = "MANUAL_CLOSE",
            leverage      = prev_leverage,
        )
    except Exception as e:
        logger.error(f"[수동청산 감지] {symbol} DB 저장 오류: {e}")

    # ── 터미널 로그 + 텔레그램 알림 (요청된 정확한 포맷) ────────────────────────────────────────
    emoji = "✅" if pnl_pct >= 0 else "🔴"
    msg = f"{emoji} [수동청산 감지] {symbol} {prev_pos} 청산 | 확정 체결가: ${avg_fill_price:.2f} | 실현 수익금(PnL): {pnl_amount:+.4f} USDT | 수익률: {pnl_pct:+.2f}% (수수료/펀딩비 반영 완료)"
    
    bot_global_state["logs"].append(msg)
    logger.info(msg)
    send_telegram_sync(_tg_manual_exit(symbol, prev_pos, avg_fill_price, pnl_amount, pnl_pct))


async def async_trading_loop():
    """다중 심볼 백그라운드 매매 루프"""
    global bot_global_state, ai_brain_state, _trading_task

    engine_api = _engine  # 싱글톤 재사용 (load_markets 재호출 없음)
    strategy_instance = TradingStrategy(initial_seed=75.0)

    if not engine_api or not engine_api.exchange:
        logger.error("OKXEngine 미초기화 상태 - 매매 루프 중단")
        return

    bot_global_state["logs"].append("[봇] OKX 거래소 연결 확인 및 자동매매 대기 중...")
    logger.info("자동매매 루프 시작")
    import time
    last_log_time = 0
    last_scan_time = 0  # 스캐너 마지막 작동 시간
    _circuit_breaker_last_warn = {}  # 서킷 브레이커 로그 쓰로틀 (심볼별 마지막 경고 시각)

    while bot_global_state["is_running"]:
        try:
            current_time = time.time()
            
            # ── 15분 주기 다이내믹 볼륨 스캐너 가동 ──
            if current_time - last_scan_time >= 900:
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

            # 잔고 실시간 연동
            curr_bal = engine_api.get_usdt_balance()
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
                _exch_pos       = engine_api.get_open_positions()
                exchange_open_symbols = {p['symbol'] for p in _exch_pos}
            except Exception as _pos_err:
                logger.warning(f"거래소 포지션 조회 실패 (수동청산 감지 스킵): {_pos_err}")
                exchange_open_symbols = None

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
                            "take_profit_price": 0.0,
                            "stop_loss_price": 0.0,
                            "highest_price": 0.0,
                            "lowest_price": 0.0,
                            "real_sl": 0.0,
                            "trailing_active": False,
                            "trailing_target": 0.0,
                        }

                    # OHLCV 데이터 수집
                    ohlcv = engine_api.exchange.fetch_ohlcv(symbol, "1m", limit=100)
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    current_price = engine_api.get_current_price(symbol)

                    bot_global_state["symbols"][symbol]["current_price"] = current_price

                    # ── 수동 청산 감지: 내부엔 포지션이 있는데 거래소엔 없으면 외부 청산 ──
                    if (exchange_open_symbols is not None
                            and bot_global_state["symbols"][symbol]["position"] != "NONE"
                            and bot_global_state["symbols"][symbol]["entry_price"] > 0
                            and symbol not in exchange_open_symbols):
                        _detect_and_handle_manual_close(
                            engine_api, symbol, bot_global_state["symbols"][symbol]
                        )
                        continue  # 이번 사이클은 신규 진입 시도 없이 다음 심볼로

                    # 지표 계산
                    df = strategy_instance.calculate_indicators(df)
                    latest_rsi = df['rsi'].iloc[-1]
                    latest_macd = df['macd'].iloc[-1]
                    latest_upper = df['upper_band'].iloc[-1]
                    latest_lower = df['lower_band'].iloc[-1]

                    # ── [Phase 1] 거시적 추세(1h EMA200) 데이터 수집 (비동기, 캐시 적용) ──
                    macro_ema_200 = await strategy_instance.get_macro_ema_200(engine_api, symbol)

                    # 매매 시그널 및 AI 판단 상태 평가 (거시적 필터 적용)
                    signal, analysis_msg = strategy_instance.check_entry_signal(df, current_price, macro_ema_200)

                    # 뇌 상태 업데이트
                    if symbol not in ai_brain_state["symbols"]:
                        ai_brain_state["symbols"][symbol] = {}

                    ai_brain_state["symbols"][symbol].update({
                        "price": current_price,
                        "rsi": round(latest_rsi, 2) if not pd.isna(latest_rsi) else 50.0,
                        "macd": round(latest_macd, 2) if not pd.isna(latest_macd) else 0.0,
                        "bb_upper": round(latest_upper, 2) if not pd.isna(latest_upper) else 0.0,
                        "bb_lower": round(latest_lower, 2) if not pd.isna(latest_lower) else 0.0,
                        "decision": analysis_msg  # 프론트엔드 출력을 위해 추가
                    })

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

                            # 리스크 관리 체크
                            # LONG: highest_price(고점) 추적 / SHORT: lowest_price(저점) 추적
                            if position_side == "SHORT":
                                extreme_price = bot_global_state["symbols"][symbol].get("lowest_price", entry)
                            else:
                                extreme_price = bot_global_state["symbols"][symbol].get("highest_price", entry)

                            current_atr = df['atr'].iloc[-1] if 'atr' in df.columns else (entry * 0.01)
                            if pd.isna(current_atr) or current_atr <= 0:
                                current_atr = entry * 0.01
                                
                            # --- 동적 TP/SL 상태 계산 (프론트엔드 실시간 표시용) ---
                            # [Phase 3] ATR 기반 탄력적 쉴드 계산
                            if position_side == "LONG":
                                profit_usdt = current_price - entry
                                _real_sl = entry - (current_atr * 2.0)
                            else:
                                profit_usdt = entry - current_price
                                _real_sl = entry + (current_atr * 2.0)
                                
                            _trailing_active = profit_usdt >= (current_atr * 1.0)
                            _trailing_target = 0.0
                            
                            if _trailing_active:
                                if position_side == "LONG":
                                    _trailing_target = extreme_price - (current_atr * 0.5)
                                else:
                                    _trailing_target = extreme_price + (current_atr * 0.5)
                            bot_global_state["symbols"][symbol]["real_sl"] = round(_real_sl, 4)
                            bot_global_state["symbols"][symbol]["trailing_active"] = _trailing_active
                            bot_global_state["symbols"][symbol]["trailing_target"] = round(_trailing_target, 4) if _trailing_target else 0.0

                            risk_action = strategy_instance.evaluate_risk_management(
                                entry, current_price, extreme_price, position_side, current_atr, symbol
                            )

                            if risk_action != "KEEP":
                                # 1. 실제 거래소 청산 API 호출 (트랜잭션 무결성 방어)
                                try:
                                    # 진입 시 저장한 실제 계약수 사용 (없으면 1 fallback)
                                    amount = int(bot_global_state["symbols"][symbol].get("contracts", 1))
                                    
                                    # 1. 실제 거래소 청산 API 호출 (엔진 분리)
                                    order_id = engine_api.close_position(symbol, position_side, amount)
                                    
                                    # 2. 거래소 API 체결 완벽 성공 및 영수증 확보 대기
                                    import time as _t
                                    net_pnl = 0.0
                                    avg_fill_price = current_price
                                    receipt_found = False
                                    
                                    for _attempt in range(5):
                                        _t.sleep(1.0)
                                        try:
                                            trades = engine_api.get_recent_trade_receipts(symbol, limit=20)
                                            matching_trades = [t for t in trades if str(t.get('order')) == str(order_id)]
                                            if matching_trades:
                                                total_gross_pnl = sum(float(t.get('info', {}).get('fillPnl', 0) or 0) for t in matching_trades)
                                                total_fee = sum(float(t.get('info', {}).get('fee', 0) or 0) for t in matching_trades)
                                                total_cost = sum(t.get('cost', 0) for t in matching_trades)
                                                total_amount = sum(t.get('amount', 0) for t in matching_trades)
                                                
                                                net_pnl = total_gross_pnl + total_fee
                                                if total_amount > 0:
                                                    avg_fill_price = total_cost / total_amount
                                                else:
                                                    avg_fill_price = float(matching_trades[0].get('price', current_price))
                                                receipt_found = True
                                                break
                                        except Exception as receipt_err:
                                            logger.warning(f"[{symbol}] 청산 체결 영수증 파싱 오류 시도 {_attempt+1}: {receipt_err}")
                                            
                                    if not receipt_found:
                                        raise Exception("청산 주문은 들어갔으나 영수증(실현PnL) 파싱에 실패했습니다.")
                                        
                                    pnl_amount = net_pnl
                                    
                                    # 물리적 원금 = (진입가 * 계약수 * 계약단위) / 레버리지
                                    try:
                                        contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                                    except:
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
                                        amount=amount,
                                        exit_reason=risk_action,
                                        leverage=leverage
                                    )

                                    # 3. 청산 알림 (사유 한글 변환) - 영수증 기반 확정 로깅만 사용
                                    _exit_reason_ko = {
                                        "STOP_LOSS": "🛑 손절",
                                        "TRAILING_STOP_EXIT": "✅ 트레일링 익절",
                                    }
                                    reason_ko = _exit_reason_ko.get(risk_action, risk_action)
                                    emoji = "✅" if pnl_percent >= 0 else "🔴"
                                    
                                    msg = f"{emoji} [{symbol}] {position_side} 청산 | 확정 체결가: ${avg_fill_price:.2f} | 실현 수익금(PnL): {pnl_amount:+.4f} USDT | 수익률: {pnl_percent:+.2f}% (수수료/펀딩비 반영 완료) | {reason_ko}"
                                        
                                    bot_global_state["logs"].append(msg)
                                    logger.info(msg)
                                    send_telegram_sync(_tg_exit(symbol, position_side, avg_fill_price, pnl_amount, pnl_percent, risk_action))

                                    # 4. 프론트엔드 포지션 초기화
                                    bot_global_state["symbols"][symbol]["position"] = "NONE"
                                    bot_global_state["symbols"][symbol]["entry_price"] = 0.0
                                    bot_global_state["symbols"][symbol]["take_profit_price"] = 0.0
                                    bot_global_state["symbols"][symbol]["stop_loss_price"] = 0.0
                                    bot_global_state["symbols"][symbol]["real_sl"] = 0.0
                                    bot_global_state["symbols"][symbol]["trailing_active"] = False
                                    bot_global_state["symbols"][symbol]["trailing_target"] = 0.0

                                except Exception as e:
                                    # API 호출 실패 시 에러만 기록하고 포지션을 유지 (DB 업데이트 안함)
                                    err_msg = f"[{symbol}] 청산 체결 실패 (망 오류 등): {str(e)} - 다음 루프 재시도"
                                    bot_global_state["logs"].append(err_msg)
                                    logger.error(err_msg)

                    # 포지션 없을 때 진입 신호 체크
                    if bot_global_state["symbols"][symbol]["position"] == "NONE":
                        # 현재 사이클 최신 상태 기준 — 다른 심볼에 포지션이 있으면 진입 차단
                        any_other_position_open = any(
                            s.get("position", "NONE") != "NONE"
                            for k, s in bot_global_state["symbols"].items()
                            if k != symbol
                        )
                        if any_other_position_open:
                            continue
                            
                        # 서킷 브레이커: 일일 손실 한도 초과 시 신규 진입 차단 (60초에 1회만 로그)
                        if strategy_instance.is_daily_drawdown_exceeded(curr_bal):
                            now = time.time()
                            if now - _circuit_breaker_last_warn.get(symbol, 0) >= 60:
                                cb_msg = f"⚠️ [{symbol}] 일일 손실 한도 초과 - 신규 진입 차단 (잔고: {curr_bal:.2f} USDT)"
                                bot_global_state["logs"].append(cb_msg)
                                logger.warning(cb_msg)
                                send_telegram_sync(_tg_circuit_breaker(symbol, curr_bal))
                                _circuit_breaker_last_warn[symbol] = now
                            continue

                        # signal, analysis_msg는 위에서 이미 평가됨
                        if signal in ["LONG", "SHORT"]:
                            msg = f"[{symbol}] {signal} 진입 신호 — 현재가: ${current_price}, RSI: {latest_rsi:.1f}"
                            bot_global_state["logs"].append(msg)
                            logger.info(msg)

                            try:
                                # 수동 오버라이드 or 동적 포지션 사이징
                                manual_override = str(get_config('manual_override_enabled')).lower() == 'true'
                                if manual_override:
                                    trade_amount = max(1, int(float(get_config('manual_amount') or 1)))
                                    trade_leverage = max(1, min(100, int(get_config('manual_leverage') or 1)))
                                else:
                                    risk_rate = float(get_config('risk_per_trade') or 0.01)
                                    trade_leverage = max(1, min(100, int(get_config('leverage') or 1)))
                                    size_btc = strategy_instance.calculate_position_size(curr_bal, risk_rate, current_price, trade_leverage)
                                    try:
                                        contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
                                    except Exception:
                                        contract_size = 0.01
                                    trade_amount = max(1, round(size_btc / contract_size))
                                # 레버리지 거래소 적용
                                try:
                                    engine_api.exchange.set_leverage(trade_leverage, symbol)
                                except Exception as lev_err:
                                    logger.warning(f"[{symbol}] 레버리지 설정 실패: {lev_err}")
                                if signal == "LONG":
                                    engine_api.exchange.create_market_buy_order(symbol, trade_amount)
                                else:
                                    engine_api.exchange.create_market_sell_order(symbol, trade_amount)

                                # 포지션 상태 업데이트
                                bot_global_state["symbols"][symbol]["position"] = signal
                                bot_global_state["symbols"][symbol]["entry_price"] = current_price
                                bot_global_state["symbols"][symbol]["highest_price"] = current_price
                                bot_global_state["symbols"][symbol]["lowest_price"] = current_price
                                bot_global_state["symbols"][symbol]["leverage"] = trade_leverage
                                bot_global_state["symbols"][symbol]["contracts"] = trade_amount  # 청산 시 재사용

                                entry_emoji = "📈" if signal == "LONG" else "📉"
                                entry_msg = f"{entry_emoji} [{symbol}] {signal} 진입 성공! | 가격: ${current_price:.2f} | {trade_amount}계약 | 레버리지 {trade_leverage}x"
                                bot_global_state["logs"].append(entry_msg)
                                logger.info(entry_msg)
                                send_telegram_sync(_tg_entry(symbol, signal, current_price, trade_amount, trade_leverage))

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

            await asyncio.sleep(3)

        except Exception as e:
            # 어떤 예외가 발생해도 루프를 절대 종료하지 않음 (Crash 방어)
            err_msg = f"[오류] 매매 루프 예외 발생 - 3초 후 재시작: {str(e)}"
            bot_global_state["logs"].append(err_msg)
            logger.error(err_msg)
            await asyncio.sleep(3)
            continue

# ===== 기존 엔드포인트 (하위 호환) =====

@app_server.post("/api/v1/test_order")
async def execute_test_order():
    """강제 테스트 매수 (Market Buy) 실행 엔드포인트"""
    try:
        if not bot_global_state["is_running"]:
            return {"error": "시스템이 중지되어 있습니다. 먼저 가동해 주세요."}
            
        symbol = list(bot_global_state["symbols"].keys())[0] if bot_global_state["symbols"] else "BTC/USDT:USDT"

        # 심볼이 아직 루프에서 초기화되지 않았으면 방어적으로 초기화
        if symbol not in bot_global_state["symbols"]:
            bot_global_state["symbols"][symbol] = {
                "position": "NONE", "entry_price": 0.0, "current_price": 0.0,
                "unrealized_pnl_percent": 0.0, "take_profit_price": 0.0,
                "stop_loss_price": 0.0, "highest_price": 0.0, "lowest_price": 0.0
            }

        # 포지션이 이미 있을 경우 방어
        if bot_global_state["symbols"][symbol]["position"] != "NONE":
            err_msg = "[오류] 이미 포지션을 보유 중이어서 테스트 매수를 진행할 수 없습니다."
            bot_global_state["logs"].append(err_msg)
            return {"error": "이미 포지션이 존재합니다."}

        engine_api = _engine
        if not engine_api or not engine_api.exchange:
            return {"error": "OKX 거래소 인스턴스가 연결되지 않았습니다."}
            
        # 수동 오버라이드 or 동적 포지션 사이징
        manual_override = str(get_config('manual_override_enabled')).lower() == 'true'
        if manual_override:
            trade_amount = max(1, int(float(get_config('manual_amount') or 1)))
            trade_leverage = max(1, min(100, int(get_config('manual_leverage') or 1)))
        else:
            risk_rate = float(get_config('risk_per_trade') or 0.01)
            trade_leverage = max(1, min(100, int(get_config('leverage') or 1)))
            current_price_now = engine_api.get_current_price(symbol) or 1
            curr_bal_now = engine_api.get_usdt_balance()
            strategy_tmp = TradingStrategy(initial_seed=75.0)
            size_btc = strategy_tmp.calculate_position_size(curr_bal_now, risk_rate, current_price_now, trade_leverage)
            try:
                contract_size = float(engine_api.exchange.market(symbol).get('contractSize', 0.01))
            except Exception:
                contract_size = 0.01
            trade_amount = max(1, round(size_btc / contract_size))
        # 레버리지 거래소 적용
        try:
            engine_api.exchange.set_leverage(trade_leverage, symbol)
        except Exception as lev_err:
            logger.warning(f"[{symbol}] 레버리지 설정 실패: {lev_err}")

        try:
            # 시장가 매수
            engine_api.exchange.create_market_buy_order(symbol, trade_amount)
            
            # 포지션 상태 억지로 반영 (다음 루프에서 동기화될 임시값)
            ticker = engine_api.exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            
            # 테스트 진입 로그 기록
            test_msg = f"📈 [{symbol}] 테스트 매수(LONG) 강제 진입 성공! (수량: {trade_amount}계약, 레버리지: {trade_leverage}x)"
            bot_global_state["logs"].append(test_msg)
            logger.info(test_msg)
            send_telegram_sync(_tg_entry(symbol, "LONG", current_price, trade_amount, trade_leverage, is_test=True))
            
            bot_global_state["symbols"][symbol]["position"] = "LONG"
            bot_global_state["symbols"][symbol]["entry_price"] = current_price
            bot_global_state["symbols"][symbol]["highest_price"] = current_price
            bot_global_state["symbols"][symbol]["lowest_price"] = current_price
            bot_global_state["symbols"][symbol]["contracts"] = trade_amount  # 청산 시 정확한 수량 사용
            # TP/SL 가격 자동 계산 (LONG 기준: +3% TP, -2% SL)
            bot_global_state["symbols"][symbol]["take_profit_price"] = round(float(current_price) * 1.03, 2)
            bot_global_state["symbols"][symbol]["stop_loss_price"] = round(float(current_price) * 0.98, 2)

            return {"status": "success", "message": test_msg}

        except Exception as e:
            error_msg = f"[{symbol}] 테스트 매수 주문 자체 실패: {str(e)}"
            bot_global_state["logs"].append(error_msg)
            logger.error(error_msg)
            return {"error": str(e)}

    except Exception as e:
         return {"error": f"서버 오류: {str(e)}"}

@app_server.get("/api/v1/status")
async def fetch_current_status():
    """현재 봇 상태 반환 (OKX 실시간 데이터 강제 동기화)"""
    # 봇이 켜져있지 않더라도 실시간 잔고는 업데이트되어야 함
    try:
        engine = _engine
        if engine and engine.exchange:
            # 1. 잔고 무조건 갱신 (0이어도 반영 - 초기 로드 0 버그 수정)
            curr_bal = engine.get_usdt_balance()
            bot_global_state["balance"] = round(curr_bal, 2)

            # 2. OKX 포지션 Hydration - CCXT ROE(percentage) 직접 바이패스
            try:
                # 리셋 전 스냅샷: 포지션이 열려있던 심볼 저장 (수동청산 감지용)
                prev_open = {}
                for sym in bot_global_state["symbols"]:
                    s = bot_global_state["symbols"][sym]
                    if s.get("position", "NONE") not in ("NONE", "") and s.get("entry_price", 0.0) > 0:
                        prev_open[sym] = {
                            "position": s["position"],
                            "entry_price": s["entry_price"],
                            "contracts": s.get("contracts", 1),
                            "leverage": int(s.get("leverage", 1)),
                            "current_price": s.get("current_price", 0.0),
                        }

                positions = engine.exchange.fetch_positions()
                # fetch_positions() 성공한 경우에만 NONE 리셋 (실패 시 기존 포지션 유지)
                for sym in bot_global_state["symbols"]:
                    bot_global_state["symbols"][sym]["position"] = "NONE"
                    bot_global_state["symbols"][sym]["unrealized_pnl_percent"] = 0.0

                for pos in positions:
                    contracts = float(pos.get('contracts', 0) or 0)
                    if contracts > 0:
                        symbol = pos.get('symbol')
                        if symbol in bot_global_state["symbols"]:
                            side = pos.get('side', '').upper()
                            if side in ['LONG', 'SHORT']:
                                # OKX가 계산한 ROE(%) 직접 사용
                                roe = float(pos.get('percentage', 0) or 0)
                                unrealized = float(pos.get('unrealizedPnl', 0) or 0)
                                leverage = float(pos.get('leverage', 1) or 1)
                                mark = float(pos.get('markPrice', 0) or 0)
                                entry = float(pos.get('entryPrice', 0) or 0)

                                # OKX percentage가 0이면 선물 공식으로 Fallback
                                # 공식: ((Mark - Entry) / Entry) * 100 * Leverage
                                if roe == 0.0 and entry > 0 and mark > 0:
                                    diff = (mark - entry) / entry if side == 'LONG' else (entry - mark) / entry
                                    roe = round(diff * 100 * leverage, 2)

                                bot_global_state["symbols"][symbol]["position"] = side
                                bot_global_state["symbols"][symbol]["entry_price"] = entry
                                bot_global_state["symbols"][symbol]["current_price"] = mark
                                bot_global_state["symbols"][symbol]["unrealized_pnl_percent"] = roe
                                bot_global_state["symbols"][symbol]["unrealized_pnl"] = unrealized
                                bot_global_state["symbols"][symbol]["leverage"] = leverage
                                bot_global_state["symbols"][symbol]["contracts"] = contracts
                                # TP/SL 표시용 복원 (서버 재시작 후 0.00 방지)
                                if entry > 0 and bot_global_state["symbols"][symbol].get("take_profit_price", 0) == 0:
                                    if side == 'LONG':
                                        bot_global_state["symbols"][symbol]["take_profit_price"] = round(entry * 1.03, 2)
                                        bot_global_state["symbols"][symbol]["stop_loss_price"] = round(entry * 0.98, 2)
                                    else:
                                        bot_global_state["symbols"][symbol]["take_profit_price"] = round(entry * 0.97, 2)
                                        bot_global_state["symbols"][symbol]["stop_loss_price"] = round(entry * 1.02, 2)
                # 수동청산 감지: 직전에 열려있었으나 OKX REST 조회 후 NONE으로 바뀐 심볼
                for sym, prev in prev_open.items():
                    curr_pos = bot_global_state["symbols"][sym].get("position", "NONE")
                    curr_entry = bot_global_state["symbols"][sym].get("entry_price", 0.0)
                    # curr_entry > 0: 봇 자체 청산(entry_price=0으로 초기화)이 아님을 확인
                    if curr_pos == "NONE" and curr_entry > 0:
                        # 봇 자체 청산이 아닌 외부 수동 청산 감지
                        _detect_and_handle_manual_close(engine, sym, bot_global_state["symbols"][sym], manual_prev_state=prev)

            except Exception as pe:
                logger.warning(f"포지션 데이터 스캔 실패: {pe}")
    except Exception as e:
        logger.warning(f"실시간 잔고/포지션 갱신 실패: {e}")

    # logs(300개)는 제외하고 반환 - 매초 전송 시 불필요한 대용량 페이로드 방지
    # 로그는 /api/v1/logs 엔드포인트에서 별도 조회
    return {
        "is_running": bot_global_state["is_running"],
        "balance": bot_global_state["balance"],
        "symbols": bot_global_state["symbols"],
    }

@app_server.get("/api/v1/brain")
async def fetch_brain_status():
    """AI 뇌 상태 반환"""
    return ai_brain_state

@app_server.get("/api/v1/trades")
async def fetch_trades_history():
    """최근 거래 내역 반환 (DB 기반)"""
    return get_trades(limit=100)

@app_server.post("/api/v1/toggle")
async def toggle_bot_action():
    """봇 시작/중지"""
    global bot_global_state, _trading_task

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
    await manager.connect(websocket)
    try:
        while True:
            # 클라이언트로부터의 메시지 수신 대기 (연결 유지용)
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ===== 신규 엔드포인트 =====

@app_server.get("/api/v1/stats")
async def fetch_statistics():
    """성과 분석 통계"""
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

    return {
        'total_trades': total_trades,
        'win_rate': round(win_rate, 2),
        'total_pnl_percent': round(total_pnl_percent, 2),
        'max_drawdown': round(max_drawdown * 100, 2),
        'sharpe_ratio': round(sharpe_ratio, 2)
    }

@app_server.get("/api/v1/config")
async def fetch_config():
    """현재 봇 설정 조회"""
    config = get_config()
    return config

@app_server.post("/api/v1/config")
async def update_config(key: str, value: str):
    """봇 설정 변경 (실시간 적용)"""
    try:
        set_config(key, value)
        logger.info(f"설정 변경: {key} = {value}")
        return {"success": True, "message": f"{key} 업데이트 완료"}
    except Exception as e:
        logger.error(f"설정 변경 실패: {e}")
        return {"success": False, "message": str(e)}

@app_server.get("/api/v1/ohlcv")
async def fetch_ohlcv(symbol: str = "BTC/USDT:USDT", limit: int = 100):
    """OHLCV 캔들 데이터 (차트용)"""
    try:
        engine = _engine
        if not engine or not engine.exchange:
            return {"error": "거래소 연결 실패"}

        ohlcv = engine.exchange.fetch_ohlcv(symbol, "1m", limit=limit)
        
        # 샌드박스 환경 등에서 데이터가 아예 안 들어올 경우를 대비한 가상 데이터 생성 로직
        if not ohlcv or len(ohlcv) == 0:
            logger.warning(f"[{symbol}] OHLCV 데이터가 비어 있습니다. 임시 차트 데이터를 생성합니다.")
            import time
            import random
            current_time = int(time.time() * 1000)
            mock_ohlcv = []
            base_price = engine.get_current_price(symbol)
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

if __name__ == "__main__":
    uvicorn.run("api_server:app_server", host="0.0.0.0", port=8000, reload=False)
