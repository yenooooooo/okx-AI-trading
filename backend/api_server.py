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

class LogList(list):
    def append(self, msg):
        super().append(msg)
        if len(self) > 300:
            self.pop(0)
        try:
            # 에러 키워드 감지
            lvl = "ERROR" if "[오류]" in msg or "실패" in msg else "INFO"
            save_log(level=lvl, message=msg)
        except Exception as e:
            logger.error(f"DB 저장 오류: {e}")

# 전역 상태 (다중 심볼 지원)
bot_global_state = {
    "is_running": False,
    "balance": 0.0,
    "symbols": {},  # symbol별 상태
    "logs": LogList(["[시스템] API 통신 브릿지가 준비되었습니다."]),
}

ai_brain_state = {
    "symbols": {}  # symbol별 뇌 상태
}

trade_history = []
_trading_task = None  # 중복 루프 방지용 태스크 추적

async def async_trading_loop():
    """다중 심볼 백그라운드 매매 루프"""
    global bot_global_state, ai_brain_state, _trading_task

    engine_api = OKXEngine()
    strategy_instance = TradingStrategy(initial_seed=75.0)

    bot_global_state["logs"].append("[봇] OKX 거래소 연결 확인 및 자동매매 대기 중...")
    logger.info("자동매매 루프 시작")
    import time
    last_log_time = 0

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

                    # 매매 시그널 및 AI 판단 상태 평가 (포지션 유무와 관계없이 항상 화면 표시를 위해 계산)
                    signal, analysis_msg = strategy_instance.check_entry_signal(df)

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
                            highest = bot_global_state["symbols"][symbol].get("highest_price", entry)
                            risk_action = strategy_instance.evaluate_risk_management(
                                entry, current_price, highest, position_side
                            )

                            if risk_action != "KEEP":
                                # 1. 실제 거래소 청산 API 호출 (트랜잭션 무결성 방어)
                                try:
                                    amount = 1  # 임시 수량
                                    if position_side == "LONG":
                                        engine_api.exchange.create_market_sell_order(symbol, amount)
                                    elif position_side == "SHORT":
                                        engine_api.exchange.create_market_buy_order(symbol, amount)

                                    # 2. 거래소 API 체결 완벽 성공 시에만 내부 DB 및 상태 업데이트
                                    pnl_percent = pnl
                                    pnl_amount = (curr_bal / 100) * pnl_percent

                                    save_trade(
                                        symbol=symbol,
                                        position_type=position_side,
                                        entry_price=entry,
                                        exit_price=current_price,
                                        pnl=pnl_amount,
                                        pnl_percent=pnl_percent,
                                        amount=amount,
                                        exit_reason=risk_action,
                                        leverage=leverage
                                    )

                                    # 3. 브리핑 강화 알림
                                    msg = f"[청산 완료] 포지션 종료 및 거래소 체결 확인 (수익률: {pnl_percent:+.2f}%) - 사유: {risk_action}"
                                    bot_global_state["logs"].append(msg)
                                    logger.info(msg)
                                    send_telegram_sync(msg)

                                    # 4. 프론트엔드 포지션 초기화
                                    bot_global_state["symbols"][symbol]["position"] = "NONE"
                                    bot_global_state["symbols"][symbol]["entry_price"] = 0.0
                                    bot_global_state["symbols"][symbol]["take_profit_price"] = 0.0
                                    bot_global_state["symbols"][symbol]["stop_loss_price"] = 0.0

                                except Exception as e:
                                    # API 호출 실패 시 에러만 기록하고 포지션을 유지 (DB 업데이트 안함)
                                    err_msg = f"[{symbol}] 청산 체결 실패 (망 오류 등): {str(e)} - 다음 루프 재시도"
                                    bot_global_state["logs"].append(err_msg)
                                    logger.error(err_msg)

                    # 포지션 없을 때 진입 신호 체크
                    if bot_global_state["symbols"][symbol]["position"] == "NONE":
                        # signal, analysis_msg는 위에서 이미 평가됨
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
                                # TP/SL 가격 계산 (전략 파라미터 기반)
                                sl_rate = strategy_instance.hard_stop_loss_rate
                                tp_rate = strategy_instance.trailing_stop_activation
                                if signal == "LONG":
                                    bot_global_state["symbols"][symbol]["take_profit_price"] = round(current_price * (1 + tp_rate), 2)
                                    bot_global_state["symbols"][symbol]["stop_loss_price"] = round(current_price * (1 - sl_rate), 2)
                                else:
                                    bot_global_state["symbols"][symbol]["take_profit_price"] = round(current_price * (1 - tp_rate), 2)
                                    bot_global_state["symbols"][symbol]["stop_loss_price"] = round(current_price * (1 + sl_rate), 2)

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

            # 5초마다 엔진 맥박(Pulse) 로그 출력
            current_time = time.time()
            if current_time - last_log_time >= 5:
                for sym, stat in ai_brain_state["symbols"].items():
                    price = stat.get('price', 0)
                    rsi = stat.get('rsi', 0)
                    macd = stat.get('macd', 0)
                    engine_msg = f"[감시] {sym} 현재가: {price} | RSI: {rsi} | MACD: {macd} | 매수 타점 대기 중..."
                    bot_global_state["logs"].append(engine_msg)
                    logger.info(engine_msg)
                last_log_time = current_time

            await asyncio.sleep(3)

        except Exception as e:
            err_msg = f"[오류] 매매 루프: {str(e)}"
            bot_global_state["logs"].append(err_msg)
            logger.error(err_msg)
            await asyncio.sleep(5)

# ===== 기존 엔드포인트 (하위 호환) =====

@app_server.post("/api/v1/test_order")
async def execute_test_order():
    """강제 테스트 매수 (Market Buy) 실행 엔드포인트"""
    try:
        if not bot_global_state["is_running"]:
            return {"error": "시스템이 중지되어 있습니다. 먼저 가동해 주세요."}
            
        symbol = list(bot_global_state["symbols"].keys())[0] if bot_global_state["symbols"] else "BTC/USDT:USDT"
        
        # 포지션이 이미 있을 경우 방어
        if bot_global_state["symbols"][symbol]["position"] != "NONE":
            err_msg = "[오류] 이미 포지션을 보유 중이어서 테스트 매수를 진행할 수 없습니다."
            bot_global_state["logs"].append(err_msg)
            return {"error": "이미 포지션이 존재합니다."}

        engine_api = OKXEngine()
        if not engine_api.exchange:
            return {"error": "OKX 거래소 인스턴스가 연결되지 않았습니다."}
            
        amount = 1 # 테스트 수량
        try:
            # 시장가 매수
            engine_api.exchange.create_market_buy_order(symbol, amount)
            
            # 테스트 진입 로그 기록
            test_msg = f"[{symbol}] 테스트 매수(LONG) 강제 진입 성공! (수량: {amount})"
            bot_global_state["logs"].append(test_msg)
            logger.info(test_msg)
            send_telegram_sync(test_msg)
            
            # 포지션 상태 억지로 반영 (다음 루프에서 동기화될 임시값)
            ticker = engine_api.exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            bot_global_state["symbols"][symbol]["position"] = "LONG"
            bot_global_state["symbols"][symbol]["entry_price"] = current_price
            bot_global_state["symbols"][symbol]["highest_price"] = current_price
            bot_global_state["symbols"][symbol]["lowest_price"] = current_price
            
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
        engine = OKXEngine()
        if engine.exchange:
            # 1. 잔고 무조건 갱신 (0이어도 반영 - 초기 로드 0 버그 수정)
            curr_bal = engine.get_usdt_balance()
            bot_global_state["balance"] = round(curr_bal, 2)

            # 2. OKX 포지션 Hydration - CCXT ROE(percentage) 직접 바이패스
            try:
                positions = engine.exchange.fetch_positions()
                # 먼저 모든 심볼을 NONE으로 리셋
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
            except Exception as pe:
                logger.warning(f"포지션 데이터 스캔 실패: {pe}")
    except Exception as e:
        logger.warning(f"실시간 잔고/포지션 갱신 실패: {e}")
        
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
    global bot_global_state, _trading_task

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
        # 중복 태스크 방지: 이전 태스크가 완료된 경우에만 새 태스크 생성
        if _trading_task is None or _trading_task.done():
            _trading_task = asyncio.create_task(async_trading_loop())

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
async def fetch_system_logs(limit: int = 50):
    """DB 저장 로그 조회 (최신 50개)"""
    # get_logs가 역순(최신순)으로 반환한다고 가정 시 프론트에서 랜더링하기 편하게 다시 정방향(오래된것 -> 최신) 정렬
    logs = get_logs(limit=limit)
    if not logs:
        return []
        
    formatted_logs = []
    for log in reversed(logs): # 역순 정렬을 되돌림
        formatted_logs.append({
            "level": log.get("level", "INFO"),
            "message": log.get("message", ""),
            "created_at": log.get("created_at", "")
        })
    return formatted_logs

if __name__ == "__main__":
    init_db()
    logger.info("API 서버 시작")
    uvicorn.run("api_server:app_server", host="0.0.0.0", port=8000, reload=False)
