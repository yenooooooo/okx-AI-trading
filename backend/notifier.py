import os
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from logger import get_logger

logger = get_logger(__name__)

# 환경변수 로드
current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(dotenv_path=env_path, override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

_telegram_app = None

def auth_required(func):
    """지정된 CHAT_ID 통신만 허용하는 보안 필터 (인가 데코레이터)"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        if chat_id != TELEGRAM_CHAT_ID:
            logger.warning(f"Unauthorized Telegram access attempt from chat_id: {chat_id}")
            return
        return await func(update, context)
    return wrapper

@auth_required
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.state import bot_global_state
    from database import get_config
    
    is_running = bot_global_state.get("is_running", False)
    balance = bot_global_state.get("balance", 0.0)
    
    symbols_config = get_config('symbols')
    if isinstance(symbols_config, list):
        target_symbols = ", ".join(symbols_config)
    else:
        target_symbols = str(symbols_config)

    text = f"📊 *[ANTIGRAVITY 시스템 상태 요약]*\n\n"
    text += f"▪️ *동작 상태:* {'🟢 매매 탐색 중' if is_running else '🛑 일시정지 (스캐닝 전용)'}\n"
    text += f"▪️ *총 자산:* {balance:.2f} USDT\n"
    text += f"▪️ *현재 스캐너 타겟:* {target_symbols}\n\n"
    
    text += f"🎯 *[보유 포지션 & 실시간 PnL]*\n"
    has_positions = False
    for sym, state in bot_global_state.get("symbols", {}).items():
        pos = state.get("position", "NONE")
        if pos != "NONE":
            has_positions = True
            pnl = state.get("unrealized_pnl_percent", 0.0)
            entry = state.get("entry_price", 0.0)
            text += f"🔹 `{sym}` : *{pos}* (진입가: ${entry:.4f} / 수익률: {pnl:+.2f}%)\n"
    
    if not has_positions:
        text += "▫️ 현재 진입한 포지션이 없습니다.\n"
        
    keyboard = [
        [
            InlineKeyboardButton("⏸️ 봇 일시정지", callback_data="cmd_pause"),
            InlineKeyboardButton("▶️ 봇 재가동", callback_data="cmd_resume")
        ],
        [
            InlineKeyboardButton("🚨 전체 강제 청산(PANIC)", callback_data="cmd_panic")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

@auth_required
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.state import bot_global_state
    if not bot_global_state["is_running"]:
        await update.effective_message.reply_text("⚠️ 이미 매매 루프가 일시정지 상태입니다.")
        return
        
    bot_global_state["is_running"] = False
    bot_global_state["logs"].append("[봇] 텔레그램 명령으로 매매 루프가 일시중지되었습니다.")
    await update.effective_message.reply_text("🛑 *매매 루프 일시정지 완료*\n(주의: 백그라운드 스캐너는 계속 동작합니다)", parse_mode="Markdown")

@auth_required
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.state import bot_global_state, _g

    if bot_global_state["is_running"]:
        await update.effective_message.reply_text("⚠️ 시스템이 이미 가동 중입니다.")
        return

    bot_global_state["is_running"] = True
    bot_global_state["logs"].append("[봇] 텔레그램 명령으로 매매 루프 재가동")

    if _g["trading_task"] is None or _g["trading_task"].done():
        from core.trading_loop import async_trading_loop
        _g["trading_task"] = asyncio.create_task(async_trading_loop())

    await update.effective_message.reply_text("▶️ *매매 루프 재가동 완료*\n정상적으로 타점 탐색을 시작합니다.", parse_mode="Markdown")

@auth_required
async def cmd_panic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.state import bot_global_state, _g
    _engine = _g["engine"]

    bot_global_state["is_running"] = False
    bot_global_state["logs"].append("🚨 [긴급] [PANIC] 텔레그램 긴급 킬스위치 발동!")
    
    # 먼저 매매 중단 알림 발송
    await update.effective_message.reply_text("🚨 *[긴급 킬스위치 가동]*\n매매 루프를 즉시 중지하고, 모든 활성 포지션을 시장가로 청산합니다...", parse_mode="Markdown")
    
    report_msg = "🏁 *[전체 포지션 청산 및 정산 결과]*\n\n"
    closed_count = 0
    
    if _engine:
        for sym, state in bot_global_state.get("symbols", {}).items():
            pos = state.get("position", "NONE")
            if pos != "NONE":
                try:
                    amount = int(state.get("contracts", 1))
                    entry = state.get("entry_price", 0.0)
                    leverage = state.get("leverage", 1)
                    
                    # 1. 시장가 청산 주문 실행
                    order_id = _engine.close_position(sym, pos, amount)
                    closed_count += 1
                    
                    # 2. 거래소 API 체결 및 영수증 확보 대기 (최대 5초)
                    net_pnl = 0.0
                    total_gross_pnl = 0.0
                    total_fee = 0.0
                    avg_fill_price = 0.0
                    receipt_found = False
                    
                    for _attempt in range(5):
                        await asyncio.sleep(1.0)
                        try:
                            trades = _engine.get_recent_trade_receipts(sym, limit=10)
                            matching_trades = [t for t in trades if str(t.get('order')) == str(order_id)]
                            if matching_trades:
                                net_pnl, total_gross_pnl, _fee_raw, avg_fill_price = _engine.calculate_realized_pnl(matching_trades, entry)
                                total_fee = abs(_fee_raw)  # OKX fee 음수 → 양수 통일
                                receipt_found = True
                                break
                        except Exception:
                            continue
                            
                    # 3. 수익률 계산 및 보고 메시지 작성
                    if receipt_found:
                        # 물리적 원금 계산 (api_server.py 로직과 동일하게 유지)
                        try:
                            contract_size = float(_engine.exchange.market(sym).get('contractSize', 0.01))
                        except:
                            contract_size = 0.01
                        position_value = entry * amount * contract_size
                        pnl_percent = (net_pnl / (position_value / leverage) * 100) if position_value > 0 else 0.0
                        
                        report_msg += f"✅ `{sym}` ({pos})\n"
                        report_msg += f"  ▫️ 순수익: {net_pnl:+.4f} USDT ({pnl_percent:+.2f}%)\n"
                        report_msg += f"  ▫️ (항목: 총수익 {total_gross_pnl:+.4f} | 수수료 {total_fee:.4f})\n"
                        report_msg += f"  ▫️ 청산가: ${avg_fill_price:.4f}\n\n"
                    else:
                        report_msg += f"⚠️ `{sym}`: 주문 전송 완료 (정산 데이터 대기 시간 초과)\n\n"
                    
                    # 봇 내부 상태 초기화
                    state["position"] = "NONE"
                    state["entry_price"] = 0.0
                    
                except Exception as e:
                    logger.error(f"Panic close failed for {sym}: {e}")
                    report_msg += f"❌ `{sym}` 청산 실패: {e}\n\n"
    
    if closed_count == 0:
        report_msg += "▫️ 정리할 활성 포지션이 없어 시스템 정지만 수행되었습니다.\n"
    else:
        report_msg += f"🎯 총 {closed_count}개의 포지션이 정리되었습니다."
        
    await update.effective_message.reply_text(report_msg, parse_mode="Markdown")

@auth_required
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """인라인 키보드 버튼 클릭 이벤트를 라우팅 (DRY)"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data == "cmd_pause":
        await cmd_pause(update, context)
    elif data == "cmd_resume":
        await cmd_resume(update, context)
    elif data == "cmd_panic":
        await cmd_panic(update, context)

async def init_telegram_bot():
    """FastAPI lifespan / startup 에서 호출되는 비동기 초기화 함수"""
    global _telegram_app
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram 토큰 또는 CHAT_ID가 없어 봇 모듈을 비활성화합니다.")
        return

    try:
        _telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        _telegram_app.add_handler(CommandHandler("status", cmd_status))
        _telegram_app.add_handler(CommandHandler("pause", cmd_pause))
        _telegram_app.add_handler(CommandHandler("resume", cmd_resume))
        _telegram_app.add_handler(CommandHandler("panic", cmd_panic))
        _telegram_app.add_handler(CallbackQueryHandler(handle_callback))

        logger.info("텔레그램 양방향 컨트롤 타워 - 백그라운드 폴링 초기화 중...")
        await _telegram_app.initialize()
        await _telegram_app.start()
        await _telegram_app.updater.start_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"[텔레그램] 초기화 실패 — 서버는 계속 가동됩니다: {e}")
        _telegram_app = None

async def stop_telegram_bot():
    """FastAPI shutdown 단계에서 안전한 자원 해제 트리거"""
    global _telegram_app
    if _telegram_app:
        logger.info("텔레그램 봇 폴링 Graceful Shutdown 진행...")
        try:
            await _telegram_app.updater.stop()
            await _telegram_app.stop()
            await _telegram_app.shutdown()
        except Exception as e:
            logger.warning(f"[텔레그램] Shutdown 중 오류 (무시): {e}")


async def send_telegram(message: str):
    """단방향 알림용 비동기 전송 함수 (체결/오류 등 시스템 알림)"""
    if not TELEGRAM_CHAT_ID or not TELEGRAM_BOT_TOKEN:
        return
        
    global _telegram_app
    try:
        if _telegram_app and _telegram_app.bot:
            await _telegram_app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="HTML"
            )
        else:
            # Fallback: _telegram_app이 아직 초기화되기 전 또는 실패 시 HTTP 직통
            import httpx
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload_data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload_data)
                if resp.status_code != 200:
                    logger.warning(f"Telegram HTTP fallback 응답 오류: {resp.status_code} | {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Telegram 메시지 발송 실패: {e}")


def send_telegram_sync(message: str):
    """
    동기 환경(FastAPI async 루프 내부 포함)에서 안전하게 텔레그램을 발송하는 래퍼
    [Python 3.10+ 안전 패치]: asyncio.get_running_loop() 기반으로 태스크 생성
    """
    if not TELEGRAM_CHAT_ID or not TELEGRAM_BOT_TOKEN:
        return
    try:
        try:
            loop = asyncio.get_running_loop()
            # 이미 실행 중인 루프가 있음 (정상 FastAPI 시나리오)
            loop.create_task(send_telegram(message))
        except RuntimeError:
            # 실행 중인 루프가 없음 (결제 단계 외부 호출)
            asyncio.run(send_telegram(message))
    except Exception as e:
        logger.error(f"Telegram 동기 전송 실패: {e}")

