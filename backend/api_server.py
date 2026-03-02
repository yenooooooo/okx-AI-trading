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
from database import init_db, save_trade, get_trades, get_config, set_config, save_log, get_logs, wipe_all_trades, delete_configs, delete_symbol_configs
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

# [Phase 25] Adaptive Shield — 잔고 기반 자동 방어 티어 정의
BALANCE_TIERS = {
    'CRITICAL': {
        'max_balance': 20,
        'config': {
            'exit_only_mode': 'true', 'risk_per_trade': '0.005',
            'hard_stop_loss_rate': '0.003', 'trailing_stop_activation': '0.005',
            'trailing_stop_rate': '0.003', 'min_take_profit_rate': '0.008',
            'adx_threshold': '30.0', 'adx_max': '45.0', 'chop_threshold': '50.0',
            'volume_surge_multiplier': '2.0', 'fee_margin': '0.002',
            'cooldown_losses_trigger': '1', 'cooldown_duration_sec': '3600',
            'daily_max_loss_rate': '0.03',
        },
        'emoji': '🔴', 'description': '긴급 방어 — 신규 진입 차단, 기존 포지션만 관리',
    },
    'MICRO': {
        'max_balance': 100,
        'config': {
            'exit_only_mode': 'false', 'risk_per_trade': '0.01',
            'hard_stop_loss_rate': '0.005', 'trailing_stop_activation': '0.01',
            'trailing_stop_rate': '0.005', 'min_take_profit_rate': '0.01',
            'adx_threshold': '28.0', 'adx_max': '50.0', 'chop_threshold': '55.0',
            'volume_surge_multiplier': '1.8', 'fee_margin': '0.002',
            'cooldown_losses_trigger': '2', 'cooldown_duration_sec': '1800',
            'daily_max_loss_rate': '0.05',
        },
        'emoji': '🟡', 'description': '소액 보호 — R:R 1:2 강제, 저빈도 고확률',
    },
    'STANDARD': {
        'max_balance': 500,
        'config': {
            'exit_only_mode': 'false', 'risk_per_trade': '0.015',
            'hard_stop_loss_rate': '0.008', 'trailing_stop_activation': '0.005',
            'trailing_stop_rate': '0.003', 'min_take_profit_rate': '0.008',
            'adx_threshold': '30.0', 'adx_max': '45.0', 'chop_threshold': '55.0',
            'volume_surge_multiplier': '2.0', 'fee_margin': '0.002',
            'cooldown_losses_trigger': '2', 'cooldown_duration_sec': '1800',
            'daily_max_loss_rate': '0.05',
        },
        'emoji': '🟢', 'description': '표준 운용 — 정밀 스나이퍼 진입',
    },
    'GROWTH': {
        'max_balance': float('inf'),
        'config': {
            'exit_only_mode': 'false', 'risk_per_trade': '0.02',
            'hard_stop_loss_rate': '0.010', 'trailing_stop_activation': '0.005',
            'trailing_stop_rate': '0.004', 'min_take_profit_rate': '0.008',
            'adx_threshold': '25.0', 'adx_max': '60.0', 'chop_threshold': '58.0',
            'volume_surge_multiplier': '1.3', 'fee_margin': '0.001',
            'cooldown_losses_trigger': '4', 'cooldown_duration_sec': '600',
            'daily_max_loss_rate': '0.07',
        },
        'emoji': '🔵', 'description': '성장 추세 추종 — 넓은 트레일링, 수익 극대화',
    },
}

# 전역 상태 (다중 심볼 지원)
bot_global_state = {
    "is_running": False,
    "balance": 0.0,
    "symbols": {},
    "logs": LogList(["[봇] 시스템 코어 초기화 완료 - API 브릿지 대기 중"]),
    "stress_inject": None,
}

# ════════════ [X-Ray] 매매 진단 시스템 전역 상태 ════════════
_trade_attempt_log = []   # 매매 시도 이력 링 버퍼 (최대 50건)
_TRADE_ATTEMPT_MAX = 50
_loop_xray_state = {
    "last_scan_time": 0,
    "last_entry_attempt_time": 0,
    "last_entry_attempt_symbol": "",
    "last_entry_attempt_result": "",
    "last_entry_attempt_reason": "",
    "last_successful_entry_time": 0,
    "last_successful_entry_symbol": "",
    "loop_cycle_count": 0,
}

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
# [Phase 20.1] 동시성 충돌 방어용 절대 자물쇠 (Mutex Lock)
state_lock = asyncio.Lock()

def _reset_position_state(sym_state: dict):
    """[Phase 32] 포지션 상태 통합 초기화 — 모든 청산/취소 경로에서 일관적으로 사용
    비즈니스 로직(DB 저장, 킬스위치, 알림 등)은 각 호출부에서 별도 처리.
    이 함수는 순수 상태 필드 초기화만 담당."""
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
    # PENDING 관련 동적 필드 제거
    for _pk in ["pending_order_id", "pending_order_time", "pending_amount", "pending_price"]:
        sym_state.pop(_pk, None)

ai_brain_state = {
    "symbols": {}  # symbol별 뇌 상태
}

trade_history = []
_trading_task = None  # 중복 루프 방지용 태스크 추적
_broadcast_task = None  # /ws/dashboard 브로드캐스트 태스크 (클라이언트 연결 시 on-demand 시작)
_private_ws_task = None  # [Phase 33] Private WS 루프 태스크 추적 (헬스체크용)
_engine: OKXEngine = None  # 싱글톤 OKX 엔진 (매 요청마다 재생성 방지)
_active_strategy: TradingStrategy = None  # 활성 전략 인스턴스 레퍼런스 (wipe_db 인메모리 리셋용)


def _is_bypass_active(feature_key: str) -> bool:
    """[Phase 21.2] 스트레스 테스트 바이패스 24시간 만료 체크"""
    val = get_config(feature_key)
    if not val or str(val) == "0":
        return False
    try:
        return (_time.time() - float(val)) < 86400
    except (ValueError, TypeError):
        return False


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
    global _engine, _private_ws_task
    init_db()
    bot_global_state["logs"].append("[봇] 🔴 실전망(LIVE) 서버 시스템 가동 시작 - 실전 API 연동 완료")
    logger.info("API 서버 시작 - OKXEngine 초기화 중...")
    loop = asyncio.get_event_loop()
    _engine = await loop.run_in_executor(None, OKXEngine)
    if _engine and _engine.exchange:
        logger.info("OKXEngine 싱글톤 초기화 완료")
        # [Phase 32] 서버 재시작 시 거래소 잔류 포지션 감지 경고
        try:
            _positions = await asyncio.to_thread(_engine.exchange.fetch_positions)
            _open_pos = [p for p in _positions if float(p.get('contracts', 0)) > 0]
            if _open_pos:
                _pos_symbols = [p['symbol'] for p in _open_pos]
                _restart_warn = f"⚠️ [서버 재시작 감지] 거래소에 열린 포지션 {len(_open_pos)}건 발견: {_pos_symbols}. 봇 인메모리 상태와 동기화되지 않았습니다. 수동 확인 필요."
                bot_global_state["logs"].append(_restart_warn)
                logger.warning(_restart_warn)
                send_telegram_sync(_restart_warn)
        except Exception as _pos_err:
            logger.warning(f"[Phase 32] 시작 시 포지션 감지 실패: {_pos_err}")
        _private_ws_task = asyncio.create_task(private_ws_loop())  # OKX 프라이빗 WS 시작
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
    # [Phase 20.5] 수동청산 영수증 확보 로직 확장 (최대 6회, 약 12초 대기)
    since_ts = int((_time.time() - 120) * 1000)  # 최근 2분 이내 체결 내역 조회
    pnl = 0.0
    fee = 0.0
    found_receipt = False

    for attempt in range(6):
        try:
            trades = await asyncio.to_thread(engine_api.exchange.fetch_my_trades, symbol, since=since_ts)
            if trades:
                # 최신 체결 내역 확인
                latest_trade = trades[-1]
                pnl = float(latest_trade.get('info', {}).get('pnl', 0.0))
                fee = float(latest_trade.get('fee', {}).get('cost', 0.0))
                found_receipt = True
                logger.info(f"[{symbol}] 🧾 수동청산 영수증 확보 성공 (시도: {attempt+1}/6) | PnL: {pnl}, Fee: {fee}")
                break
        except Exception as e:
            logger.warning(f"[{symbol}] 영수증 조회 에러 (시도: {attempt+1}/6): {e}")

        await asyncio.sleep(2.0)  # 2초 간격 폴링

    # [Phase 20.5] 12초 대기 후에도 영수증이 없다면 '가상 영수증(Estimated PnL)' 직접 발급
    if not found_receipt:
        logger.warning(f"[{symbol}] ⚠️ 거래소 응답 지연으로 영수증 확보 실패. 가상 수익(Estimated PnL) 추정 계산 실행!")
        try:
            # sym_state["entry_price"]는 이미 0.0으로 초기화됨 → prev_entry 사용
            entry_p = prev_entry
            amount  = float(prev_contracts)
            _fallback_price = (sym_state.get("current_price", 0)
                               or await asyncio.to_thread(engine_api.get_current_price, symbol)
                               or prev_entry)

            if entry_p > 0:
                if prev_pos == "LONG":
                    pnl = (_fallback_price - entry_p) * amount
                else:
                    pnl = (entry_p - _fallback_price) * amount

                fee = (entry_p * amount * 0.0005) + (_fallback_price * amount * 0.0005)
                pnl = pnl - fee
                logger.info(f"[{symbol}] 🧮 가상 영수증 발급 완료 | 추정 PnL: {pnl:.4f}, 추정 Fee: {fee:.4f}")
        except Exception as calc_err:
            logger.error(f"[{symbol}] 🚨 가상 수익 추정마저 실패: {calc_err}")
            pnl = 0.0

    # [Phase 20.5] 하위 호환 변수 매핑 (기존 DB 저장 및 PnL% 계산 로직과 연결)
    pnl_amount = pnl
    total_gross = pnl + fee
    total_fee   = fee

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


# [Phase 25] Adaptive Shield: 잔고 기반 자동 방어 티어 전환
async def _auto_tune_by_balance(curr_bal):
    """잔고 규모에 따라 전략 파라미터를 자동 전환하여 자본 보호 극대화"""
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
        _loop_xray_state["loop_cycle_count"] += 1
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
                    else:
                        try:
                            bot_global_state["logs"].append("[엔진] 다이내믹 볼륨 스캐너 가동: 24h 거래량 Top 3 탐색 중...")
                            await asyncio.sleep(0.5)  # [Phase 4] API Rate Limit 보호용 미세 비동기 지연
                            top_symbols = await engine_api.scan_top_volume_coins(limit=3)
                            if top_symbols:
                                # [Phase 30] 스캐너 심볼 로테이션 시 고아 설정 청소
                                _old_syms_scan = get_config('symbols') or []
                                if isinstance(_old_syms_scan, list):
                                    _removed_scan = set(_old_syms_scan) - set(top_symbols)
                                    for _rs_scan in _removed_scan:
                                        _del_scan = delete_symbol_configs(_rs_scan)
                                        if _del_scan > 0:
                                            logger.info(f"[Phase 30] 스캐너 로테이션: {_rs_scan} 고아 설정 {_del_scan}건 청소")
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

            # [Phase 25] Adaptive Shield: 잔고 기반 자동 방어 티어 전환
            await _auto_tune_by_balance(curr_bal)

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
                    ohlcv = await asyncio.to_thread(engine_api.exchange.fetch_ohlcv, symbol, _tf, limit=200)
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

                    # [Phase 21.2] 스트레스 바이패스: check_entry_signal 호출 전 인메모리 상태 패치
                    # strategy.py 내부(line 154, 179)에서 직접 차단하므로 호출 직전에 무력화해야 실제 적용됨
                    if _is_bypass_active('stress_bypass_cooldown_loss'):
                        strategy_instance.loss_cooldown_until = 0
                    if _is_bypass_active('stress_bypass_daily_loss'):
                        strategy_instance.kill_switch_active = False
                        strategy_instance.kill_switch_until = 0

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

                    _bypass_disp = bool(strategy_instance.bypass_disparity)
                    _bypass_ind  = bool(strategy_instance.bypass_indicator)
                    _bypass_mac  = bool(strategy_instance.bypass_macro)
                    _gates = {
                        "adx":       {"pass": bool(_adx_min <= _adx_v <= _adx_max),     "value": f"{_adx_v:.1f}",                                                   "target": f"{_adx_min:.0f}~{_adx_max:.0f}"},
                        "chop":      {"pass": bool(_chop_v < _chop_max),                "value": f"{_chop_v:.1f}",                                                  "target": f"< {_chop_max:.1f}"},
                        "volume":    {"pass": bool(_vol_ratio >= _vol_mul),             "value": f"{_vol_ratio:.2f}x",                                              "target": f"≥ {_vol_mul:.1f}x"},
                        "disparity": {"pass": bool(_bypass_disp or _disparity < 0.8),  "value": f"{_disparity:.2f}%" + (" [우회]" if _bypass_disp else ""),        "target": "< 0.8%"},
                        "macd_rsi":  {"pass": bool(_bypass_ind  or _mr_ok),            "value": f"RSI {_rsi_v:.1f}"  + (" [우회]" if _bypass_ind  else ""),        "target": "크로스+구간"},
                        "macro":     {"pass": bool(_bypass_mac  or _macro_ok),         "value": _macro_lbl             + (" [우회]" if _bypass_mac  else ""),       "target": "EMA200"},
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
                    elif (not _bypass_disp) and _disparity >= 0.8:
                        _mono = f"[{_ts}] 이격도 {_disparity:.2f}% — 이미 너무 달렸어. EMA20에 붙을 때까지 기다리는 중... ({_passed}/6)"
                    elif _vol_ratio < _vol_mul:
                        _mono = f"[{_ts}] 거래량 {_vol_ratio:.2f}x — 아직 안 터졌어. {_vol_mul:.1f}x 이상 폭발 대기 중... ({_passed}/6)"
                    elif (not _bypass_mac) and not _macro_ok:
                        _mono = f"[{_ts}] EMA200 역방향({_macro_lbl}) — 큰 흐름 거슬러 들어가면 안 돼. 추세 전환 대기 중... ({_passed}/6)"
                    elif (not _bypass_ind) and not _mr_ok:
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

                                        # [Phase 23] Shadow Hunting 체결 시 리스크 재계산
                                        if bot_global_state["symbols"][symbol].get("is_shadow_hunting", False):
                                            _sh_atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else float(executed_price * 0.01)
                                            _sh_new_sl, _sh_new_act = strategy_instance.recalculate_shadow_risk(executed_price, real_side, _sh_atr)
                                            strategy_instance.hard_stop_loss_rate = abs(_sh_new_sl - executed_price) / executed_price
                                            strategy_instance.trailing_stop_activation = abs(_sh_new_act - executed_price) / executed_price
                                            bot_global_state["symbols"][symbol]["is_shadow_hunting"] = False  # 재계산 완료, 플래그 리셋
                                            logger.info(f"[{symbol}] 🐸 Shadow Hunting 체결! 리스크 재계산 완료 | 신규 SL: {_sh_new_sl:.4f} | 트레일링 발동선: {_sh_new_act:.4f}")

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
                                                _sl_init_sl = round(_entry_p_sl * (1 - _sl_rate_init), 4) if real_side == "LONG" else round(_entry_p_sl * (1 + _sl_rate_init), 4)

                                                _params_tp_sl = {"reduceOnly": True}
                                                _params_sl_sl = {"reduceOnly": True, "stopLossPrice": _sl_init_sl}

                                                tp_order_sl = await asyncio.to_thread(
                                                    engine_api.exchange.create_order,
                                                    symbol, 'limit', _close_side_sl, _amt_sl, _sl_init_tp, _params_tp_sl
                                                )
                                                sl_order_sl = await asyncio.to_thread(
                                                    engine_api.exchange.create_order,
                                                    symbol, 'market', _close_side_sl, _amt_sl, None, _params_sl_sl
                                                )

                                                bot_global_state["symbols"][symbol]["active_tp_order_id"] = tp_order_sl['id']
                                                bot_global_state["symbols"][symbol]["active_sl_order_id"] = sl_order_sl['id']
                                                bot_global_state["symbols"][symbol]["last_placed_tp_price"] = _sl_init_tp
                                                bot_global_state["symbols"][symbol]["last_placed_sl_price"] = _sl_init_sl

                                                logger.info(f"[{symbol}] 🕸️ [Smart Limit 체결] 거래소 초기 방어막 전송 완료 | TP: {_sl_init_tp:.4f} / SL: {_sl_init_sl:.4f}")
                                            except Exception as sl_init_err:
                                                logger.error(f"[{symbol}] 🚨 [Smart Limit 체결] 초기 방어막 전송 실패. 자가 치유가 다음 사이클에서 복구 시도: {sl_init_err}")

                                    elif status in ['canceled', 'rejected'] or (time.time() - pending_time > 300):
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

                                # 마지막 가격 갱신이 3초 이상 지연되었다면 웹소켓 이상으로 간주하고 REST API 강제 호출
                                # [Phase 21.2] 스트레스 바이패스: stale_price watchdog 스킵
                                if _now - _last_update > 3.0 and not _is_bypass_active('stress_bypass_stale_price'):
                                    try:
                                        logger.warning(f"[{symbol}] ⚠️ 실시간 데이터 수신 지연 감지 (>3초). REST API 비상 우회 폴링 실행!")
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

                                            # [Phase 26] 분할 익절 후 거래소 거미줄 재생성 (계약수 + 가격 동기화)
                                            # 기존 TP/SL은 원래 계약수로 걸려있으므로 취소 후 잔여 계약수로 재배치
                                            if not _is_paper:
                                                try:
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
                                            _heal_amt = float(bot_global_state["symbols"][symbol].get("contracts", 1))
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

                                    # 이상적인 손절가(real_sl)가 존재하고, 기존에 걸어둔 손절가가 있을 경우 비교
                                    if _current_ideal_sl > 0 and _last_sl > 0 and _active_sl_id:
                                        # 오차율 계산 (절대값(목표가 - 기존가) / 기존가)
                                        _diff_ratio = abs(_current_ideal_sl - _last_sl) / _last_sl

                                        # 0.05% 이상 목표가가 변동되었을 때만 거래소 주문 갱신 (API Rate Limit 및 DDoS 오인 방어)
                                        if _diff_ratio >= 0.0005:
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
                                                    _amt_tp = float(bot_global_state["symbols"][symbol]["contracts"])
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

                                        # 4. 프론트엔드 포지션 초기화
                                        # [Phase 32] 통합 상태 초기화 헬퍼 사용
                                        async with state_lock:
                                            _reset_position_state(bot_global_state["symbols"][symbol])

                                        # [v2.1] 연패 쿨다운 카운터 업데이트 (Paper도 카운트하여 전략 검증)
                                        is_loss = (pnl_amount < 0)
                                        strategy_instance.record_trade_result(is_loss)
                                        # [Phase 21.2] 스트레스 바이패스: 연패 쿨다운 즉시 해제
                                        if _is_bypass_active('stress_bypass_cooldown_loss'):
                                            strategy_instance.loss_cooldown_until = 0
                                            strategy_instance.consecutive_loss_count = 0

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
                        # [Phase 24] 캔들 단위 진입 시그널 중복 방지 — 같은 캔들에서 재평가 스킵
                        # [Fix] LONG/SHORT 신호 발생 시에만 캔들 잠금 → HOLD 상태에서는 매 루프 재평가
                        # 기존: HOLD여도 첫 루프에서 잠금 → 이후 LONG 신호 와도 진입 불가 버그 수정
                        _cur_candle_ts = int(df['timestamp'].iloc[-1])
                        _last_signal_ts = bot_global_state["symbols"][symbol].get("last_signal_candle_ts", 0)
                        if _last_signal_ts == _cur_candle_ts:
                            continue  # 이미 이 캔들에서 LONG/SHORT 시그널 평가 완료 → 다음 캔들까지 대기
                        # 캔들 잠금은 LONG/SHORT 신호가 실제로 평가될 때만 (line 1808 진입 시점에서 설정)

                        # [Phase 19] 퇴근 모드 작동 시 신규 진입 강제 차단
                        if _exit_only:
                            _log_trade_attempt(symbol, "N/A", "BLOCKED", "exit_only_mode")
                            continue

                        # [Phase 19.3] 60초 호흡 고르기 (동일 캔들 무한 단타 방지)
                        # [Phase 21.2] 스트레스 바이패스: reentry_cd 활성 시 쿨다운 스킵
                        last_exit = bot_global_state["symbols"][symbol].get("last_exit_time", 0)
                        if time.time() - last_exit < 60 and not _is_bypass_active('stress_bypass_reentry_cd'):
                            _log_trade_attempt(symbol, "N/A", "BLOCKED", "reentry_cooldown_60s")
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
                            # [Phase 24 Fix] LONG/SHORT 신호가 실제 평가되는 시점에 캔들 잠금
                            # → 같은 캔들에서 중복 진입 방지 (원래 목적 유지)
                            # → HOLD 신호에서는 잠금 없음 → 이후 루프에서 재평가 가능
                            bot_global_state["symbols"][symbol]["last_signal_candle_ts"] = _cur_candle_ts
                            _signal_start_time = _time.time()  # [Phase 21.1] A.D.S 레이턴시 측정 시작점
                            # [Phase 18.1] 방향 모드 필터 (LONG/SHORT/AUTO) — 코인별 독립 설정
                            _direction_mode = str(get_config('direction_mode', symbol) or 'AUTO').upper()
                            if _direction_mode == 'LONG' and signal != 'LONG':
                                _log_trade_attempt(symbol, signal, "BLOCKED", f"direction_mode_{_direction_mode}")
                                continue  # LONG 전용 모드: SHORT 신호 차단
                            if _direction_mode == 'SHORT' and signal != 'SHORT':
                                _log_trade_attempt(symbol, signal, "BLOCKED", f"direction_mode_{_direction_mode}")
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
                                # [Phase 31] 증거금 사전 검증 — 거래소 거부(51008) 선제 차단
                                _margin_needed = (contract_size * current_price * trade_amount) / trade_leverage
                                if curr_bal * 0.90 < _margin_needed:
                                    _margin_msg = f"[{symbol}] 증거금 사전 검증 실패: 필요 ${_margin_needed:.2f} vs 가용 ${curr_bal * 0.90:.2f} (90% 기준)"
                                    bot_global_state["logs"].append(_margin_msg)
                                    logger.warning(_margin_msg)
                                    _log_trade_attempt(symbol, signal, "BLOCKED", "margin_insufficient")
                                    continue  # 이 심볼 진입 건너뛰기
                                # 레버리지 거래소 적용
                                try:
                                    await asyncio.to_thread(engine_api.exchange.set_leverage, trade_leverage, symbol)
                                except Exception as lev_err:
                                    logger.warning(f"[{symbol}] 레버리지 설정 실패: {lev_err}")
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
                                        _log_trade_attempt(symbol, signal, "FAILED", f"shadow_hunting: {str(_sh_err)[:80]}")
                                        continue  # 실패 시 이번 사이클 스킵

                                if not _is_shadow_hunt_order:
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
                                        bot_global_state["symbols"][symbol]["pending_order_time"] = time.time()
                                        bot_global_state["symbols"][symbol]["pending_amount"] = trade_amount
                                        bot_global_state["symbols"][symbol]["pending_price"] = executed_price
                                        bot_global_state["symbols"][symbol]["is_paper"] = _is_shadow_pending
                                        # [Phase 23] 그림자 사냥 포지션 여부 각인
                                        bot_global_state["symbols"][symbol]["is_shadow_hunting"] = _is_shadow_hunt_order
                                        # [Phase 28] 레버리지 저장 (체결 후 텔레그램 알림에서 참조)
                                        bot_global_state["symbols"][symbol]["leverage"] = trade_leverage

                                    _log_trade_attempt(symbol, signal, "SUCCESS")
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

                                    _log_trade_attempt(symbol, signal, "SUCCESS")
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
                                            _init_sl = round(_entry_p * (1 - _sl_rate), 4) if signal == "LONG" else round(_entry_p * (1 + _sl_rate), 4)

                                            # CCXT 규격에 맞춘 Reduce-Only 파라미터
                                            _params_tp = {"reduceOnly": True}
                                            _params_sl = {"reduceOnly": True, "stopLossPrice": _init_sl}

                                            # Limit TP (지정가 익절) 전송
                                            tp_order = await asyncio.to_thread(
                                                engine_api.exchange.create_order,
                                                symbol, 'limit', _close_side, _amt, _init_tp, _params_tp
                                            )

                                            # Stop-Market SL (조건부 시장가 손절) 전송
                                            sl_order = await asyncio.to_thread(
                                                engine_api.exchange.create_order,
                                                symbol, 'market', _close_side, _amt, None, _params_sl
                                            )

                                            # 뇌 구조(기억 장치)에 영수증 번호와 가격 각인
                                            bot_global_state["symbols"][symbol]["active_tp_order_id"] = tp_order['id']
                                            bot_global_state["symbols"][symbol]["active_sl_order_id"] = sl_order['id']
                                            bot_global_state["symbols"][symbol]["last_placed_tp_price"] = _init_tp
                                            bot_global_state["symbols"][symbol]["last_placed_sl_price"] = _init_sl

                                            logger.info(f"[{symbol}] 🕸️ 거래소 초기 방어막 전송 완료 | TP: {_init_tp:.4f} / SL: {_init_sl:.4f}")
                                        except Exception as limit_err:
                                            logger.error(f"[{symbol}] 🚨 초기 방어막(Limit/Stop) 전송 실패. 거래소 수동 확인 요망: {limit_err}")

                            except Exception as e:
                                _log_trade_attempt(symbol, signal, "FAILED", str(e)[:100])
                                error_msg = f"[{symbol}] 진입 실패: {str(e)}"
                                bot_global_state["logs"].append(error_msg)
                                logger.error(error_msg)

                except Exception as e:
                    logger.warning(f"[{symbol}] 루프 처리 중 오류 (다음 루프 계속): {e}")

                # [Phase 20.4] API Rate Limit 방어용 스마트 스로틀링 (코인 간 0.5초 휴식)
                # 다중 코인 감시 시 거래소 서버 폭격(HTTP 429 에러) 방지
                await asyncio.sleep(0.5)

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

            await asyncio.sleep(3)

# ===== [Phase 21.2] 스트레스 테스트 바이패스 엔드포인트 =====

@app_server.get("/api/v1/stress_bypass")
async def get_stress_bypass():
    """5개 자동 잠금 기능의 24h 바이패스 현황 조회"""
    FEATURES = {
        "kill_switch": "stress_bypass_kill_switch",
        "cooldown_loss": "stress_bypass_cooldown_loss",
        "daily_loss": "stress_bypass_daily_loss",
        "reentry_cd": "stress_bypass_reentry_cd",
        "stale_price": "stress_bypass_stale_price",
    }
    result = {}
    for name, key in FEATURES.items():
        val = get_config(key)
        try:
            activated_at = float(val) if val and str(val) != "0" else 0.0
        except (ValueError, TypeError):
            activated_at = 0.0
        elapsed = _time.time() - activated_at if activated_at else 86401
        active = elapsed < 86400
        result[name] = {
            "active": active,
            "remaining_sec": max(0.0, 86400 - elapsed) if active else 0.0,
        }
    return result


@app_server.post("/api/v1/stress_bypass")
async def set_stress_bypass(feature: str, enabled: bool):
    """특정 자동 잠금 기능을 24시간 동안 바이패스 활성화/비활성화"""
    from fastapi import HTTPException
    KEY_MAP = {
        "kill_switch": "stress_bypass_kill_switch",
        "cooldown_loss": "stress_bypass_cooldown_loss",
        "daily_loss": "stress_bypass_daily_loss",
        "reentry_cd": "stress_bypass_reentry_cd",
        "stale_price": "stress_bypass_stale_price",
    }
    if feature not in KEY_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown feature: {feature}")
    db_key = KEY_MAP[feature]
    if enabled:
        set_config(db_key, str(_time.time()))
        # 일일 손실 바이패스 활성화 시 strategy kill_switch 즉시 해제
        if feature == "daily_loss" and _active_strategy:
            _active_strategy.kill_switch_active = False
            _active_strategy.kill_switch_until = 0
        # 연패 쿨다운 바이패스 활성화 시 loss_cooldown_until 즉시 초기화
        if feature == "cooldown_loss" and _active_strategy:
            _active_strategy.loss_cooldown_until = 0
            _active_strategy.consecutive_loss_count = 0
    else:
        set_config(db_key, "0")
    return {
        "feature": feature,
        "active": enabled,
        "remaining_sec": 86400.0 if enabled else 0.0,
    }


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

        # [Phase 32] 통합 상태 초기화 헬퍼 사용
        async with state_lock:
            _reset_position_state(sym_state)

            # [Phase 19] 강제 청산 시 봇 재진입 방지를 위해 자동매매 엔진 자체를 STOP 상태로 전환
            bot_global_state["is_running"] = False
            stop_msg = "[시스템] 강제 청산(Paper) 감지: 봇 무한 재진입 방지를 위해 자동매매를 일시 중지(STOP) 합니다."
            bot_global_state["logs"].append(stop_msg)
            logger.info(stop_msg)
            send_telegram_sync(_tg_system(False))

        return {"status": "success", "message": msg}

    except Exception as e:
        return {"error": f"서버 오류: {str(e)}"}

@app_server.post("/api/v1/cancel_pending")
async def manual_cancel_pending():
    """[Phase 24] 사령관 수동 개입: 대기 중인 지정가(매복) 주문 즉시 철거"""
    try:
        _sym_conf = get_config('symbols')
        symbol = _sym_conf[0] if isinstance(_sym_conf, list) and _sym_conf else None
        if not symbol:
            return {"status": "error", "message": "활성 심볼이 없습니다."}

        sym_state = bot_global_state["symbols"].get(symbol, {})
        position = sym_state.get("position", "NONE")

        if not position.startswith("PENDING"):
            return {"status": "error", "message": "현재 대기 중인 주문이 없습니다."}

        pending_id = sym_state.get("pending_order_id")
        _is_paper = sym_state.get("is_paper", False)

        # 1. 거래소에 취소 요청 (Paper 모드가 아닐 때만 실제 API 호출)
        if pending_id and not _is_paper and _engine and _engine.exchange:
            try:
                await asyncio.to_thread(_engine.exchange.cancel_order, pending_id, symbol)
            except Exception as cancel_err:
                logger.warning(f"[{symbol}] 수동 취소 요청 실패 (이미 취소되었을 수 있음): {cancel_err}")

        # 2. [Phase 32] 통합 상태 초기화 헬퍼 사용
        async with state_lock:
            _reset_position_state(bot_global_state["symbols"][symbol])

        # 3. 가시성 보고
        _abort_msg = f"🟡 [{symbol}] 수동 철수: 사령관 명령으로 대기 주문({pending_id})을 즉시 철거했습니다."
        bot_global_state["logs"].append(_abort_msg)
        logger.warning(_abort_msg)
        save_log("WARNING", _abort_msg)
        send_telegram_sync(
            f"🟡 <b>수동 철수 명령 수신</b>\n"
            f"────────────────────────\n"
            f"사령관 개입: 대기 중인 덫을 철거하고 관망합니다.\n"
            f"• 심볼: <b>{symbol}</b>\n"
            f"• 주문 ID: <code>{pending_id}</code>"
        )

        return {"status": "success", "message": "대기 주문이 성공적으로 철거되었습니다."}
    except Exception as e:
        logger.error(f"수동 취소 실패: {e}")
        return {"status": "error", "message": str(e)}


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
        "adaptive_tier": bot_global_state.get("adaptive_tier", str(get_config('_current_adaptive_tier') or '')),
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
        # ── [Phase 29] Shadow↔Live 전환 가드 (Transition Guard) ──
        _transition_warnings = []
        if key == "SHADOW_MODE_ENABLED":
            _old_shadow = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
            _new_shadow = str(value).lower() == 'true'

            if _old_shadow and not _new_shadow:
                # ── Shadow → Live 전환: Paper 포지션 자동 강제 청산 ──
                _closed_papers = []
                for _sym, _sym_st in bot_global_state["symbols"].items():
                    _pos = _sym_st.get("position", "NONE")
                    _is_p = _sym_st.get("is_paper", False)
                    if _pos != "NONE" and _is_p:
                        # Paper 포지션 발견 → 자동 청산
                        _cp = _sym_st.get("current_price", 0)
                        _ep = _sym_st.get("entry_price", 0)
                        _amt = int(_sym_st.get("contracts", 1))
                        _lev = int(_sym_st.get("leverage", 1))
                        # PnL 시뮬레이션
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
                        # 상태 초기화
                        async with state_lock:
                            _sym_st["position"] = "NONE"
                            _sym_st["entry_price"] = 0.0
                            _sym_st["last_exit_time"] = _time.time()
                            _sym_st["take_profit_price"] = "대기중"
                            _sym_st["stop_loss_price"] = 0.0
                            _sym_st["real_sl"] = 0.0
                            _sym_st["trailing_active"] = False
                            _sym_st["trailing_target"] = 0.0
                            _sym_st["partial_tp_executed"] = False
                            _sym_st["is_paper"] = False
                            _sym_st["entry_timestamp"] = 0.0
                            _sym_st["active_tp_order_id"] = None
                            _sym_st["active_sl_order_id"] = None
                            _sym_st["last_placed_tp_price"] = 0.0
                            _sym_st["last_placed_sl_price"] = 0.0
                            # PENDING 관련 필드 제거
                            for _pk in ["pending_order_id", "pending_order_time", "pending_amount", "pending_price"]:
                                _sym_st.pop(_pk, None)
                        _emoji = "+" if _pnl >= 0 else ""
                        _closed_papers.append(f"{_sym} {_pos} (PnL: {_emoji}{_pnl:.4f} USDT)")
                if _closed_papers:
                    _guard_msg = f"[전환 가드] Shadow→Live 전환: Paper 포지션 {len(_closed_papers)}건 자동 청산 | {', '.join(_closed_papers)}"
                    bot_global_state["logs"].append(_guard_msg)
                    logger.info(_guard_msg)
                    send_telegram_sync(f"[전환 가드] Shadow→Live 전환\nPaper 포지션 {len(_closed_papers)}건 자동 청산\n{chr(10).join(_closed_papers)}")
                    _transition_warnings.append(_guard_msg)

            elif not _old_shadow and _new_shadow:
                # ── Live → Shadow 전환: 실전 포지션 존재 시 경고 ──
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

        # ── [Phase 30] 심볼 변경 시 고아 설정 자동 청소 (Config Pollution Guard) ──
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
                pass  # 파싱 실패 시 청소 건너뜀 (설정 저장은 계속 진행)

        set_config(key, value, symbol)
        sym_tag = f"[{symbol}] " if symbol != "GLOBAL" else ""
        log_msg = f"[UI 연동 성공 \U0001f7e2] {sym_tag}'{key}' 설정이 '{value}'(으)로 뇌 구조에 완벽히 적용되었습니다."
        bot_global_state["logs"].append(log_msg)
        logger.info(log_msg)
        result = {"success": True, "message": f"{key} 업데이트 완료"}
        if _transition_warnings:
            result["warnings"] = _transition_warnings
        return result
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
        "min_take_profit_rate",  # [Phase 24] 최소 익절 목표율
        # [Phase 30] 누락된 키 추가
        "direction_mode",
        "exit_only_mode",
        "shadow_hunting_enabled",
    ]
    delete_configs(keys)
    # [Phase 30] 활성 심볼의 심볼별 설정도 함께 삭제 (완전한 순정 복귀)
    _active_syms = get_config('symbols') or []
    _sym_cleaned = 0
    if isinstance(_active_syms, list):
        for _sym in _active_syms:
            _sym_cleaned += delete_symbol_configs(_sym)
    _active_strategy = TradingStrategy()
    _reset_detail = f" (심볼별 설정 {_sym_cleaned}건 추가 청소)" if _sym_cleaned > 0 else ""
    msg = f"[시스템] 사령관 명령 수신: 튜닝 데이터 삭제 및 AI 순정 모드(Tier 1) 딥 리셋 완료.{_reset_detail}"
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

        _bt_tf = str(get_config('timeframe') or '15m')
        ohlcv = await asyncio.to_thread(engine.exchange.fetch_ohlcv, symbol, _bt_tf, limit=limit)
        
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
# [Phase 27] 전체 서브시스템 자가 진단 (Full Diagnostic)
# ─────────────────────────────────────────────────────────────────────────────
@app_server.get("/api/v1/diagnostic")
async def run_full_diagnostic():
    """전체 서브시스템 자가 진단 — 10개 항목 자동 점검 (읽기 전용, 상태 변경 없음)"""
    from datetime import datetime
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
        # 매매 루프와 동일한 레버리지 결정 로직 (manual_override → manual_leverage, 아닐 시 leverage)
        _diag_manual = str(get_config('manual_override_enabled')).lower() == 'true'
        _leverage_key = 'manual_leverage' if _diag_manual else 'leverage'
        # GLOBAL 기본값 (심볼별 override가 없을 때 fallback)
        leverage_cfg_global = max(1, int(get_config(_leverage_key) or 1))
        _diag_shadow = str(get_config('SHADOW_MODE_ENABLED') or 'false').lower() == 'true'
        trade_feasibility = []
        all_feasible = True
        for sym in symbols_cfg:
            try:
                # [수정] 매매 루프와 동일하게 심볼별 leverage 우선, 없으면 GLOBAL fallback
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
        # 대표 레버리지 (메시지용): 첫 심볼 기준 또는 GLOBAL
        leverage_cfg = trade_feasibility[0].get('leverage', leverage_cfg_global) if trade_feasibility else leverage_cfg_global
        # 섀도우 모드일 때: 실제 증거금 불필요(Paper) → WARN으로 완화
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
        # 예상 티어 계산
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
        # ATR 조회 시도
        test_atr = test_price * 0.01  # 기본 fallback
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
        # TP/SL 계산 (LONG 기준)
        tp_offset = (test_price * fee_margin) + (test_atr * 0.5)
        tp_long = round(test_price + tp_offset, 4)
        sl_long = round(test_price * (1 - sl_rate), 4)
        # 유효성 검증
        import math
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
                # [수정] 매매 루프와 동일하게 심볼별 leverage 우선, 없으면 GLOBAL fallback
                sym_leverage = max(1, int(get_config(_leverage_key, sym) or leverage_cfg_global))
                mkt = _engine.exchange.market(sym) if _engine and _engine.exchange else {}
                cs = float(mkt.get('contractSize', 0.01))
                px = float(bot_global_state["symbols"].get(sym, {}).get("current_price", 0))
                if px <= 0 and _engine:
                    px = await asyncio.to_thread(_engine.get_current_price, sym) or 0
                # 매매 루프와 동일한 사이징 로직 분기
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
        # 섀도우 모드일 때: Paper 매매라 실 증거금 불필요 → WARN으로 완화
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
            # 현재 설정값 기준 통과 여부 판정
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
        import time as _t
        now_ts = _t.time()
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
            "message": f"고아 주문 없음" if not orphans else f"고아 주문 {len(orphans)}건 감지",
            "details": {"orphans": orphans}
        })
    except Exception as e:
        results.append({
            "id": "orphan_orders", "name": "고아 주문 감지",
            "status": "FAIL", "message": f"점검 실패: {str(e)[:80]}",
            "details": {}
        })

    # ── 요약 ──
    pass_count = sum(1 for r in results if r["status"] == "PASS")
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    info_count = sum(1 for r in results if r["status"] == "INFO")

    return {
        "diagnostic": results,
        "summary": {"pass": pass_count, "fail": fail_count, "warn": warn_count, "info": info_count, "total": len(results)},
        "timestamp": datetime.now().isoformat()
    }


# ─────────────────────────────────────────────────────────────────────────────
# [Phase 33] 연결 상태 종합 점검 (Health Check Dashboard)
# ─────────────────────────────────────────────────────────────────────────────
@app_server.get("/api/v1/health_check")
async def run_health_check():
    """[Phase 33] 프론트-백엔드-거래소 연결 상태 종합 점검 (읽기 전용, 사이드이펙트 없음)"""
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
            _ws_alive = bool(_private_ws_task and not _private_ws_task.done())
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
        # 최상위 방어막: Check 1-3 중 try-except로 잡히지 않은 예외를 여기서 처리
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

    # ── Endpoint Registry (프론트엔드 POST 검증용) ──
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

    # ── Summary ──
    _ok_cnt = sum(1 for _c in checks if _c["status"] == "OK")
    _fail_cnt = sum(1 for _c in checks if _c["status"] == "FAIL")
    _warn_cnt = sum(1 for _c in checks if _c["status"] == "WARN")

    import datetime as _hc_dt
    return {
        "checks": checks,
        "endpoints": _endpoints,
        "summary": {"ok": _ok_cnt, "fail": _fail_cnt, "warn": _warn_cnt, "total": len(checks)},
        "timestamp": _hc_dt.datetime.now().isoformat()
    }


# ════════════════════════════════════════════════════════════════════════════
# [X-Ray] 매매 진단 시스템 — 5개 엔드포인트
# ════════════════════════════════════════════════════════════════════════════

@app_server.get("/api/v1/xray/loop_state")
async def xray_loop_state():
    """[X-Ray 1] 트레이딩 루프 내부 상태 실시간 스냅샷"""
    import datetime as _xdt
    _kst = _xdt.timezone(_xdt.timedelta(hours=9))

    def _fmt_ts(ts):
        if not ts or ts == 0:
            return "없음"
        return _xdt.datetime.fromtimestamp(ts, tz=_kst).strftime("%H:%M:%S KST")

    # Kill Switch 상태
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
        # Cooldown
        _cd_losses = _active_strategy.consecutive_loss_count
        _cd_trigger = _active_strategy.cooldown_losses_trigger
        _cd_active = _time.time() < _active_strategy.loss_cooldown_until
        if _cd_active:
            _rem_cd = max(0, _active_strategy.loss_cooldown_until - _time.time())
            _cd_remaining = f"{int(_rem_cd // 60)}분 {int(_rem_cd % 60)}초 남음"

    # 심볼 목록
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


@app_server.get("/api/v1/xray/blocker_wizard")
async def xray_blocker_wizard():
    """[X-Ray 2] 매매 차단 원인 마법사 — 순차 검증, 첫 실패 시 중단"""
    import datetime as _xdt
    _kst = _xdt.timezone(_xdt.timedelta(hours=9))
    steps = []
    _stopped = None

    # ── Step 1: 엔진 가동 상태 ──
    _is_on = bot_global_state["is_running"]
    steps.append({"step": 1, "name": "엔진 가동 상태", "pass": _is_on,
                  "detail": f"is_running = {_is_on}", "fix": "대시보드 상단 토글 버튼으로 봇을 시작하세요" if not _is_on else ""})
    if not _is_on:
        _stopped = 1

    # ── Step 2: 킬스위치 ──
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

    # ── Step 3: 연패 쿨다운 ──
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

    # ── Step 4: 진입 신호 존재 여부 (6/6 게이트 통과 심볼) ──
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

    # ── Step 5: 방향 모드 필터 ──
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

    # ── Step 6: 퇴근 모드 (Exit-Only) ──
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

    # ── Step 7: 다른 포지션 보유 여부 ──
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

    # ── Step 8: 재진입 쿨다운 (60초) ──
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

    # ── Step 9: 증거금 충분 여부 ──
    if _stopped is None:
        _bal = bot_global_state.get("balance", 0)
        _margin_ok = _bal > 5.0
        steps.append({"step": 9, "name": "증거금 (잔고) 충분 여부", "pass": _margin_ok,
                      "detail": f"가용 잔고: ${_bal:.2f} USDT" if _margin_ok else f"잔고 부족: ${_bal:.2f} USDT",
                      "fix": "USDT 입금 필요" if not _margin_ok else ""})
        if not _margin_ok:
            _stopped = 9

    # ── Step 10: OKX API 연결 ──
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

    # ── Step 11: 전체 정상 ──
    if _stopped is None:
        steps.append({"step": 11, "name": "전체 점검 완료", "pass": True,
                      "detail": "모든 조건 충족 — 다음 신호 대기 중", "fix": ""})

    # 결론
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


@app_server.get("/api/v1/xray/trade_attempts")
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
        # direction_mode_ 패턴
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


@app_server.get("/api/v1/xray/gate_scoreboard")
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


@app_server.get("/api/v1/xray/okx_deep_verify")
async def xray_okx_deep_verify():
    """[X-Ray 5] OKX API 딥 검증 — 실제 매매 가능 여부 확인"""
    import datetime as _xdt
    _kst = _xdt.timezone(_xdt.timedelta(hours=9))

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

            # 심볼별 매매 가능성 검증
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
