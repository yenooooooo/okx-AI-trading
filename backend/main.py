import time
import pandas as pd
from okx_engine import OKXEngine
from strategy import TradingStrategy

# --- 봇 설정 영역 ---
SYMBOL = "BTC/USDT:USDT"  # 대상 코인 (비트코인 무기한 선물)
TIMEFRAME = "1m"          # 1분봉 기준 (빠른 단기매매 테스트용)
PAPER_TRADING = False     # 실제 모의투자 계정으로 주문 실행
# --------------------

def run_bot():
    print("[시스템] OKX 자동매매 봇 초기화 중...")
    engine = OKXEngine()
    
    # 시드 10만원 = 약 75 USDT 기준으로 초기화 (환율에 따라 변동 가능)
    strategy = TradingStrategy(initial_seed=75.0) 

    if not engine.exchange:
        print("[치명적 오류] 거래소 엔진 초기화 실패. 봇을 종료합니다.")
        return

    # 포지션 상태 관리 변수
    current_position = None  # "LONG", "SHORT", None
    entry_price = 0.0
    highest_price = 0.0
    
    print(f"[시스템] 봇 구동 시작. 대상: {SYMBOL}, 캔들: {TIMEFRAME}, 모의투자 모드: {PAPER_TRADING}")
    print("-" * 50)

    while True:
        try:
            # 1. 일일 최대 손실 한도 체크 (서킷 브레이커)
            current_balance = engine.get_usdt_balance()
            if strategy.is_daily_drawdown_exceeded(current_balance):
                print("[긴급] 일일 최대 손실 한도(-5%) 도달! 뇌동매매 방지를 위해 봇을 전면 중단합니다.")
                break # 무한 루프 종료

            # 2. 시장 데이터(OHLCV) 수집 (최근 100개 캔들)
            ohlcv = engine.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=100)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # 3. 기술적 지표 계산 및 현재가 확인
            df = strategy.calculate_indicators(df)
            current_price = engine.get_current_price(SYMBOL)
            
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {SYMBOL} 현재가: {current_price} USDT | 보유 잔고: {current_balance:.2f} USDT")

            # 4. 포지션 유지 중일 때: 리스크 관리 (익절/손절 체크)
            if current_position:
                # 트레일링 스탑을 위한 논리적 최고/최저가 갱신
                if current_position == "LONG" and current_price > highest_price:
                    highest_price = current_price
                elif current_position == "SHORT" and current_price < highest_price:
                    highest_price = current_price

                risk_action = strategy.evaluate_risk_management(entry_price, current_price, highest_price, current_position)
                
                if risk_action != "KEEP":
                    print(f"[{'모의' if PAPER_TRADING else '실제'} 포지션 청산] 사유: {risk_action}")
                    print(f" - 진입가: {entry_price} -> 청산가: {current_price}")
                    
                    if not PAPER_TRADING:
                        # 실제 ccxt 시장가 청산 로직
                        amount = 1 # 테스트용 1계약 고정 청산
                        if current_position == "LONG":
                            engine.exchange.create_market_sell_order(SYMBOL, amount)
                        elif current_position == "SHORT":
                            engine.exchange.create_market_buy_order(SYMBOL, amount)
                        
                    # 포지션 초기화
                    current_position = None
                    entry_price = 0.0
                    highest_price = 0.0
                    print("-" * 50)
                    time.sleep(60) # 청산 후 시장 안정을 위해 1분 대기
                    continue 

            # 5. 포지션이 없을 때: 신규 진입 시그널 체크
            if not current_position:
                signal = strategy.check_entry_signal(df)
                if signal in ["LONG", "SHORT"]:
                    print(f"[{'모의' if PAPER_TRADING else '실제'} 신규 진입 포착] 방향: {signal} @ {current_price} USDT")
                    
                    if not PAPER_TRADING:
                        # 실제 ccxt 시장가 진입 로직
                        amount = 1 # 테스트용 1계약 고정 진입
                        if signal == "LONG":
                            engine.exchange.create_market_buy_order(SYMBOL, amount)
                        elif signal == "SHORT":
                            engine.exchange.create_market_sell_order(SYMBOL, amount)
                        
                    # 포지션 기록
                    current_position = signal
                    entry_price = current_price
                    highest_price = current_price
                    print("-" * 50)

            # API 호출 제한 방지 (Rate Limit) 및 다음 분석을 위한 대기
            time.sleep(60)

        except Exception as e:
            print(f"[루프 에러] 실행 중 통신 장애 또는 오류 발생: {e}")
            print("30초 후 재시도합니다...")
            time.sleep(30)

if __name__ == "__main__":
    run_bot()
