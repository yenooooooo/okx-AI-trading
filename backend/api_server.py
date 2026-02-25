import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from okx_engine import OKXEngine
from strategy import TradingStrategy

app_server = FastAPI()

# 프론트엔드(HTML)에서 오는 요청을 허용하기 위한 CORS 설정
app_server.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 봇과 UI가 실시간으로 공유할 전역 상태 변수
bot_global_state = {
    "is_running": False,
    "balance": 0.0,
    "position": "NONE",
    "entry_price": 0.0,
    "logs": ["[시스템] API 통신 브릿지가 준비되었습니다."],
    "unrealized_pnl_percent": 0.0,
    "take_profit_price": 0.0,
    "stop_loss_price": 0.0,
    "current_price": 0.0
}

# AI 뇌 구조 및 판단 상태 전역 변수
ai_brain_state = {
    "price": None,
    "rsi": None,
    "decision": "대기 중..."
}

# 최근 매매 기록 리스트
trade_history = []

async def async_trading_loop():
    """웹 서버와 동시에 돌아가는 백그라운드 매매 루프 (main.py의 로직을 비동기로 재구성)"""
    global bot_global_state, ai_brain_state
    engine_api = OKXEngine()
    
    # 전략 인스턴스 생성
    strategy_instance = TradingStrategy(initial_seed=75.0)
    
    bot_global_state["logs"].append("[봇] OKX 거래소 연결 확인 및 자동매매 대기 중...")
    
    while bot_global_state["is_running"]:
        try:
            # 잔고 실시간 연동
            curr_bal = engine_api.get_usdt_balance()
            bot_global_state["balance"] = round(curr_bal, 2)
            
            # 시장 데이터 감시 및 AI 뇌 구조 업데이트
            # 임시로 main.py의 분석 로직을 일부 모방 (비동기 루프 내이므로)
            import pandas as pd
            try:
                ohlcv = engine_api.exchange.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=30)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                current_price = engine_api.get_current_price("BTC/USDT:USDT")
                bot_global_state["current_price"] = current_price
                
                # 포지션 상태(PnL, TP/SL) 실시간 계산
                if bot_global_state["position"] != "NONE":
                    entry = bot_global_state["entry_price"]
                    if entry > 0 and current_price:
                        if bot_global_state["position"] == "LONG":
                            pnl = ((current_price - entry) / entry) * 100
                            tp = entry * (1 + strategy_instance.trailing_stop_activation)
                            sl = entry * (1 - strategy_instance.hard_stop_loss_rate)
                        elif bot_global_state["position"] == "SHORT":
                            pnl = ((entry - current_price) / entry) * 100
                            tp = entry * (1 - strategy_instance.trailing_stop_activation)
                            sl = entry * (1 + strategy_instance.hard_stop_loss_rate)
                        
                        bot_global_state["unrealized_pnl_percent"] = round(pnl, 2)
                        bot_global_state["take_profit_price"] = round(tp, 2)
                        bot_global_state["stop_loss_price"] = round(sl, 2)

                # 매우 얕은 RSI 계산 (전략 클래스를 import하여 쓰거나 직접 계산)
                # 여기서는 UI 시연을 위해 간단한 직접 계산 사용
                delta = df['close'].diff(1)
                gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                latest_rsi = rsi.iloc[-1]
                
                ai_brain_state["price"] = current_price
                ai_brain_state["rsi"] = round(latest_rsi, 2) if not pd.isna(latest_rsi) else 50.0
                
                # 수다쟁이 모드 (상세 로직 중계)
                decision_msg = ""
                if pd.isna(latest_rsi):
                    decision_msg = "추세 탐색 중 - 데이터 대기"
                elif latest_rsi < 30:
                    decision_msg = f"현재 RSI {latest_rsi:.1f} - 초과매도 상태! 30 위로 반등할 때까지 진입 대기 중..."
                elif latest_rsi > 70:
                    decision_msg = f"현재 RSI {latest_rsi:.1f} - 시장 관망 중 (상단 터치 대기)"
                else:
                    decision_msg = f"현재 RSI {latest_rsi:.1f} - 시장 관망 중 (타점 아님)"

                # 포지션이 비어 있을 때만 진입 시그널 판단
                if bot_global_state["position"] == "NONE":
                    df = strategy_instance.calculate_indicators(df)
                    signal = strategy_instance.check_entry_signal(df)
                    if signal in ["LONG", "SHORT"]:
                        decision_msg = f"조건 충족! 현재가 ${current_price}에 RSI {latest_rsi:.1f} 반등 확인. 즉시 시장가 매수({signal}) API 호출 시도!"
                        print(f"[{signal} 진입 시도] {decision_msg}")
                        bot_global_state["logs"].append(f"[알림] {decision_msg}")
                        
                        try:
                            # 실제 시장가 API 주문 시도 (1계약)
                            amount = 1
                            if signal == "LONG":
                                engine_api.exchange.create_market_buy_order("BTC/USDT:USDT", amount)
                            elif signal == "SHORT":
                                engine_api.exchange.create_market_sell_order("BTC/USDT:USDT", amount)
                                
                            bot_global_state["position"] = signal
                            bot_global_state["entry_price"] = current_price
                            bot_global_state["logs"].append("[진입 성공] 시장가 매매 정상 체결!")
                        except Exception as api_err:
                            err_str = f"API 호출 에러: 최소 주문 수량 미달 또는 잔고 부족 ({str(api_err)})"
                            print(f"\033[91m[ERROR] {err_str}\033[0m")
                            bot_global_state["logs"].append(f"[ERROR] {err_str}")
                            decision_msg = f"API 오류로 진입 실패!"
                            
                ai_brain_state["decision"] = decision_msg
                    
            except Exception as e:
                pass # 일시적인 API 에러 무시
            
            await asyncio.sleep(3) # UI 과부하 방지를 위해 3초 단위로 갱신
        except Exception as e:
            err_msg = f"[오류] 통신 장애: {str(e)}"
            bot_global_state["logs"].append(f"\033[91m{err_msg}\033[0m")
            print(f"\033[91m{err_msg}\033[0m")
            await asyncio.sleep(5)

@app_server.get("/api/v1/status")
async def fetch_current_status():
    """프론트엔드에서 1초마다 호출하여 화면을 갱신할 상태값 반환"""
    return bot_global_state

@app_server.get("/api/v1/brain")
async def fetch_brain_status():
    """방금 분석한 최신 시황 데이터와 AI의 '현재 판단 상태' 메세지를 JSON으로 반환"""
    return ai_brain_state

@app_server.get("/api/v1/trades")
async def fetch_trades_history():
    """최근 매매 내역과 계산된 수익률(%) 리스트를 JSON으로 반환"""
    return trade_history

@app_server.post("/api/v1/toggle")
async def toggle_bot_action():
    """프론트엔드 Start/Stop 버튼 클릭 시 동작"""
    global bot_global_state
    
    if bot_global_state["is_running"]:
        bot_global_state["is_running"] = False
        bot_global_state["logs"].append("[명령] 사용자에 의해 봇이 정지되었습니다.")
    else:
        bot_global_state["is_running"] = True
        bot_global_state["logs"].append("[명령] 방어형 리스크 관리 봇 가동 시작!")
        asyncio.create_task(async_trading_loop())
        
    return {"is_running": bot_global_state["is_running"]}

if __name__ == "__main__":
    # 향후 시스템 실행 시 main.py 대신 이 서버를 구동합니다.
    uvicorn.run("api_server:app_server", host="127.0.0.1", port=8000, reload=True)
