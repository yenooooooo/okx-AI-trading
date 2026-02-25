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
    "logs": ["[시스템] API 통신 브릿지가 준비되었습니다."]
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
    global bot_global_state
    engine_api = OKXEngine()
    
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
                
                if latest_rsi < 30:
                    ai_brain_state["decision"] = "과매도 감지 - 매수 기회 포착 중"
                elif latest_rsi > 70:
                    ai_brain_state["decision"] = "과매수 감지 - 매도 준비 및 관망"
                else:
                    ai_brain_state["decision"] = "추세 탐색 중 - RSI 중립 구간"
                    
            except Exception as e:
                pass # 일시적인 API 에러 무시
            
            await asyncio.sleep(3) # UI 과부하 방지를 위해 3초 단위로 갱신
        except Exception as e:
            bot_global_state["logs"].append(f"[오류] 통신 장애: {str(e)}")
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
