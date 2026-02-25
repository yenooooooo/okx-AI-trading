import asyncio
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import pandas as pd
from okx_engine import OKXEngine
from strategy import TradingStrategy
from database import init_db, save_trade, get_trades, get_config, set_config, save_log, get_logs
from backtester import Backtester
from notifier import send_telegram_sync
from logger import get_logger

logger = get_logger(__name__)

app_server = FastAPI()

# CORS 설정
app_server.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 전역 상태 (다중 심볼 지원)
bot_global_state = {
    "is_running": False,
    "balance": 0.0,
    "symbols": {},  # symbol별 상태
    "logs": ["[시스템] API 통신 브릿지가 준비되었습니다."],
}

ai_brain_state = {
    "symbols": {}  # symbol별 뇌 상태
}

trade_history = []

async def async_trading_loop():
    """다중 심볼 백그라운드 매매 루프"""
    global bot_global_state, ai_brain_state

    engine_api = OKXEngine()
    strategy_instance = TradingStrategy(initial_seed=75.0)

    bot_global_state["logs"].append("[봇] OKX 거래소 연결 확인 및 자동매매 대기 중...")
    logger.info("자동매매 루프 시작")

    while bot_global_state["is_running"]:
        try:
            # 잔고 실시간 연동
            curr_bal = engine_api.get_usdt_balance()
            bot_global_state["balance"] = round(curr_bal, 2)

            # 설정된 심볼 목록 로드
            symbols_config = get_config('symbols')
            if isinstance(symbols_config, list):
                symbols = symbols_config
            else:
                symbols = ['BTC/USDT:USDT']

            # 각 심볼에 대해 거래 루프 실행
            for symbol in symbols:
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
                            "lowest_price": 0.0
                        }

                    # OHLCV 데이터 수집
                    ohlcv = engine_api.exchange.fetch_ohlcv(symbol, "1m", limit=30)
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    current_price = engine_api.get_current_price(symbol)

                    bot_global_state["symbols"][symbol]["current_price"] = current_price

                    # 지표 계산
                    df = strategy_instance.calculate_indicators(df)
                    latest_rsi = df['rsi'].iloc[-1]
                    latest_macd = df['macd'].iloc[-1]
                    latest_upper = df['upper_band'].iloc[-1]
                    latest_lower = df['lower_band'].iloc[-1]

                    # 뇌 상태 업데이트
                    if symbol not in ai_brain_state["symbols"]:
                        ai_brain_state["symbols"][symbol] = {}

                    ai_brain_state["symbols"][symbol].update({
                        "price": current_price,
                        "rsi": round(latest_rsi, 2) if not pd.isna(latest_rsi) else 50.0,
                        "macd": round(latest_macd, 2) if not pd.isna(latest_macd) else 0.0,
                        "bb_upper": round(latest_upper, 2) if not pd.isna(latest_upper) else 0.0,
                        "bb_lower": round(latest_lower, 2) if not pd.isna(latest_lower) else 0.0,
                    })

                    # 포지션 상태 체크 및 리스크 관리
                    if bot_global_state["symbols"][symbol]["position"] != "NONE":
                        entry = bot_global_state["symbols"][symbol]["entry_price"]
                        position_side = bot_global_state["symbols"][symbol]["position"]

                        if entry > 0 and current_price:
                            if position_side == "LONG":
                                pnl = ((current_price - entry) / entry) * 100
                                bot_global_state["symbols"][symbol]["highest_price"] = max(
                                    bot_global_state["symbols"][symbol].get("highest_price", current_price),
                                    current_price
                                )
                            elif position_side == "SHORT":
                                pnl = ((entry - current_price) / entry) * 100
                                bot_global_state["symbols"][symbol]["lowest_price"] = min(
                                    bot_global_state["symbols"][symbol].get("lowest_price", current_price),
                                    current_price
                                )

                            bot_global_state["symbols"][symbol]["unrealized_pnl_percent"] = round(pnl, 2)

                            # 리스크 관리 체크
                            highest = bot_global_state["symbols"][symbol].get("highest_price", entry)
                            risk_action = strategy_instance.evaluate_risk_management(
                                entry, current_price, highest, position_side
                            )

                            if risk_action != "KEEP":
                                # 포지션 청산
                                pnl_percent = pnl
                                pnl_amount = (curr_bal / 100) * pnl_percent if pnl_percent > 0 else 0

                                # DB에 거래 기록 저장
                                save_trade(
                                    symbol=symbol,
                                    position_type=position_side,
                                    entry_price=entry,
                                    exit_price=current_price,
                                    pnl=pnl_amount,
                                    pnl_percent=pnl_percent,
                                    amount=1.0,  # 임시값
                                    exit_reason=risk_action,
                                    leverage=1
                                )

                                # 알림
                                msg = f"[{symbol}] {position_side} 포지션 청산 - 사유: {risk_action}, 수익률: {pnl_percent:.2f}%"
                                bot_global_state["logs"].append(msg)
                                logger.info(msg)
                                send_telegram_sync(msg)

                                # 포지션 초기화
                                bot_global_state["symbols"][symbol]["position"] = "NONE"
                                bot_global_state["symbols"][symbol]["entry_price"] = 0.0
                                bot_global_state["symbols"][symbol]["take_profit_price"] = 0.0
                                bot_global_state["symbols"][symbol]["stop_loss_price"] = 0.0

                    # 포지션 없을 때 진입 신호 체크
                    if bot_global_state["symbols"][symbol]["position"] == "NONE":
                        signal = strategy_instance.check_entry_signal(df)

                        if signal in ["LONG", "SHORT"]:
                            msg = f"[{symbol}] {signal} 진입 신호 - 현재가: ${current_price}, RSI: {latest_rsi:.1f}"
                            bot_global_state["logs"].append(msg)
                            logger.info(msg)

                            try:
                                # 시장가 주문 (임시 1계약)
                                amount = 1
                                if signal == "LONG":
                                    engine_api.exchange.create_market_buy_order(symbol, amount)
                                else:
                                    engine_api.exchange.create_market_sell_order(symbol, amount)

                                # 포지션 상태 업데이트
                                bot_global_state["symbols"][symbol]["position"] = signal
                                bot_global_state["symbols"][symbol]["entry_price"] = current_price
                                bot_global_state["symbols"][symbol]["highest_price"] = current_price
                                bot_global_state["symbols"][symbol]["lowest_price"] = current_price

                                entry_msg = f"[{symbol}] {signal} 진입 성공! (${current_price})"
                                bot_global_state["logs"].append(entry_msg)
                                logger.info(entry_msg)
                                send_telegram_sync(entry_msg)

                            except Exception as e:
                                error_msg = f"[{symbol}] 진입 실패: {str(e)}"
                                bot_global_state["logs"].append(error_msg)
                                logger.error(error_msg)

                except Exception as e:
                    pass  # 일시적 API 에러 무시

            await asyncio.sleep(3)

        except Exception as e:
            err_msg = f"[오류] 매매 루프: {str(e)}"
            bot_global_state["logs"].append(err_msg)
            logger.error(err_msg)
            await asyncio.sleep(5)

# ===== 기존 엔드포인트 (하위 호환) =====

@app_server.get("/api/v1/status")
async def fetch_current_status():
    """현재 봇 상태 반환"""
    return bot_global_state

@app_server.get("/api/v1/brain")
async def fetch_brain_status():
    """AI 뇌 상태 반환"""
    return ai_brain_state

@app_server.get("/api/v1/trades")
async def fetch_trades_history():
    """최근 거래 내역 반환"""
    return trade_history

@app_server.post("/api/v1/toggle")
async def toggle_bot_action():
    """봇 시작/중지"""
    global bot_global_state

    if bot_global_state["is_running"]:
        bot_global_state["is_running"] = False
        msg = "[명령] 봇이 정지되었습니다."
        bot_global_state["logs"].append(msg)
        logger.info(msg)
        send_telegram_sync(msg)
    else:
        bot_global_state["is_running"] = True
        msg = "[명령] 봇 가동 시작!"
        bot_global_state["logs"].append(msg)
        logger.info(msg)
        send_telegram_sync(msg)
        asyncio.create_task(async_trading_loop())

    return {"is_running": bot_global_state["is_running"]}

# ===== 신규 엔드포인트 =====

@app_server.get("/api/v1/stats")
async def fetch_statistics():
    """성과 분석 통계"""
    trades = get_trades(limit=1000)

    total_trades = len(trades)
    win_trades = len([t for t in trades if t['pnl_percent'] and t['pnl_percent'] > 0])
    win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0

    total_pnl_percent = sum([t.get('pnl_percent', 0) for t in trades])

    # Max Drawdown 계산
    max_drawdown = 0
    if trades:
        initial_balance = 100000  # 임시값
        running_balance = initial_balance
        running_max = initial_balance
        for trade in trades:
            pnl = trade.get('pnl', 0)
            running_balance += pnl
            running_max = max(running_max, running_balance)
            drawdown = (running_max - running_balance) / running_max if running_max > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

    # Sharpe Ratio 계산 (간단한 버전)
    sharpe_ratio = 0
    if total_trades > 1:
        pnl_percent_list = [t.get('pnl_percent', 0) for t in trades]
        if pnl_percent_list:
            import statistics
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
        engine = OKXEngine()
        if not engine.exchange:
            return {"error": "거래소 연결 실패"}

        ohlcv = engine.exchange.fetch_ohlcv(symbol, "1m", limit=limit)
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
        backtester = Backtester(initial_seed=75.0)
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
async def fetch_system_logs(limit: int = 100):
    """DB 저장 로그 조회"""
    logs = get_logs(limit=limit)
    return logs

if __name__ == "__main__":
    init_db()
    logger.info("API 서버 시작")
    uvicorn.run("api_server:app_server", host="127.0.0.1", port=8000, reload=True)
