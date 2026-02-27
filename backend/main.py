import time
import pandas as pd
from okx_engine import OKXEngine
from strategy import TradingStrategy

# --- 봇 설정 영역 ---
SYMBOL = "BTC/USDT:USDT"  # 대상 코인 (비트코인 무기한 선물)
TIMEFRAME = "5m"          # 5분봉 기준 (노이즈 필터링 및 수수료 방어선 확보)
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
            ohlcv = engine.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=200)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # 3. 기술적 지표 계산 및 현재가 확인
            df = strategy.calculate_indicators(df)
            current_price = engine.get_current_price(SYMBOL)
            
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {SYMBOL} 현재가: {current_price} USDT | 보유 잔고: {current_balance:.2f} USDT")
            
            # 수다쟁이 모드 (상세 현황 터미널 중계)
            latest = df.iloc[-1]
            rsi_val = latest['rsi']
            
            if pd.isna(rsi_val):
                print(f" -> 추세 탐색 중: 지표 데이터 형성 대기 중...")
            elif rsi_val < 30:
                print(f" -> 현재 RSI {rsi_val:.1f} - 초과매도 상태! 30 위로 반등할 때까지 진입 대기 대기 중...")
            elif rsi_val > 70:
                print(f" -> 현재 RSI {rsi_val:.1f} - 초과매수 상태! (타점 대기 중)")
            else:
                print(f" -> 현재 RSI {rsi_val:.1f} - 시장 관망 중 (타점 아님)")

            # 4. 포지션 유지 중일 때: 리스크 관리 (익절/손절 체크)
            if current_position:
                # 트레일링 스탑을 위한 논리적 최고/최저가 갱신
                if current_position == "LONG" and current_price > highest_price:
                    highest_price = current_price
                elif current_position == "SHORT" and current_price < highest_price:
                    highest_price = current_price

                current_atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else float(entry_price * 0.01)
                risk_action = strategy.evaluate_risk_management(entry_price, current_price, highest_price, current_position, current_atr)
                
                if risk_action != "KEEP":
                    print(f"[{'모의' if PAPER_TRADING else '실제'} 포지션 청산] 사유: {risk_action}")
                    print(f" - 진입가: {entry_price} -> 청산가: {current_price}")
                    
                    if not PAPER_TRADING:
                        # 실제 ccxt 시장가 청산 로직 (리팩토링된 분리 방식)
                        amount = 1 # 테스트용 1계약 고정 청산
                        order_id = engine.close_position(SYMBOL, current_position, amount)
                        
                        # 수익 요약 조회를 위해 엔진의 영수증 내역을 직접 파싱
                        time.sleep(1.0) # 네트워크 딜레이 확보
                        try:
                            trades = engine.get_recent_trade_receipts(SYMBOL, limit=20)
                            matching_trades = [t for t in trades if str(t.get('order')) == str(order_id)]
                            if matching_trades:
                                total_gross_pnl = sum(float(t.get('info', {}).get('fillPnl', 0) or 0) for t in matching_trades)
                                total_fee = sum(float(t.get('info', {}).get('fee', 0) or 0) for t in matching_trades)
                                net_pnl = total_gross_pnl + total_fee
                                print(f" -> [영수증 확인] 체결 성공, 확정 실현수익(Net PnL): {net_pnl:+.4f} USDT")
                            else:
                                print(" -> [영수증 확인] 지연 중 - 체결 완료 확인 불가")
                        except Exception as e:
                            print(f" -> 영수증 파싱 예외 발생: {e}")
                            
                    # 포지션 초기화
                    current_position = None
                    entry_price = 0.0
                    highest_price = 0.0
                    print("-" * 50)
                    time.sleep(300) # 청산 후 시장 안정을 위해 5분(다음 캔들) 대기
                    continue 

            # 5. 포지션이 없을 때: 신규 진입 시그널 체크
            if not current_position:
                signal, msg, payload = strategy.check_entry_signal(df)
                if signal in ["LONG", "SHORT"]:
                    # 기존 CLI용 단순 메시지 대신 페이로드 내용까지 출력
                    print(f"[{'모의' if PAPER_TRADING else '실제'} 신규 진입 포착] {msg}")
                    if payload:
                        print(f" -> 1h 추세: {payload.get('ema_status', 'N/A')} | 거래량폭발: {payload.get('vol_multiplier', 'N/A')} | ATR방어선: {payload.get('atr_sl_margin', 'N/A')}")
                    
                    if not PAPER_TRADING:
                        try:
                            # 실제 ccxt 시장가 진입 로직
                            amount = 1 # 테스트용 1계약 고정 진입
                            if signal == "LONG":
                                engine.exchange.create_market_buy_order(SYMBOL, amount)
                            elif signal == "SHORT":
                                engine.exchange.create_market_sell_order(SYMBOL, amount)
                        except Exception as e:
                            err_msg = f"API 호출 에러: 최소 주문 수량 미달, 잔고 부족 등 ({str(e)})"
                            print(f"\033[91m[ERROR] {err_msg}\033[0m")
                            print("-" * 50)
                            time.sleep(10)
                            continue # 다음 루프로
                        
                    # 포지션 기록
                    current_position = signal
                    entry_price = current_price
                    highest_price = current_price
                    print("[진입 성공] 포지션 상태 갱신 완료!")
                    print("-" * 50)

            # API 호출 제한 방지 (Rate Limit) 및 다음 5분봉 캔들 대기
            time.sleep(300)

        except Exception as e:
            print(f"[루프 에러] 실행 중 통신 장애 또는 오류 발생: {e}")
            print("30초 후 재시도합니다...")
            time.sleep(30)

if __name__ == "__main__":
    run_bot()
