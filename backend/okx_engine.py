import ccxt
import os
from dotenv import load_dotenv

# 현재 파일 위치를 기준으로 .env 절대 경로 생성
current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')

class OKXEngine:
    def __init__(self):
        """OKX API 인스턴스 초기화 및 강력한 환경변수 검증"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(current_dir, '.env')
        
        if not os.path.exists(env_path):
            print(f"[시스템 경고] .env 파일을 찾을 수 없습니다! (경로: {env_path})")
        else:
            # override=True 를 추가하여 터미널의 이전 기억(캐시)을 무시하고 무조건 최신 .env 값을 강제 적용합니다.
            load_dotenv(dotenv_path=env_path, override=True)

        self.api_key = os.getenv("OKX_API_KEY")
        self.secret_key = os.getenv("OKX_SECRET_KEY")
        self.password = os.getenv("OKX_PASSWORD")

        # 3가지 키가 모두 정상적으로 메모리에 들어왔는지 터미널에 출력하여 진단 (보안상 값은 숨김)
        print("\n--- [환경변수 메모리 적재 상태 점검] ---")
        print(f"1. OKX_API_KEY 존재 여부: {'🟢 정상' if self.api_key else '🔴 실패 (None)'}")
        print(f"2. OKX_SECRET_KEY 존재 여부: {'🟢 정상' if self.secret_key else '🔴 실패 (None)'}")
        print(f"3. OKX_PASSWORD 존재 여부: {'🟢 정상' if self.password else '🔴 실패 (None)'}")
        print("----------------------------------------\n")

        if not self.api_key or not self.secret_key or not self.password:
            print("[치명적 오류] 3개의 키 중 하나라도 '실패(None)'가 뜨면 OKX 연결이 불가능합니다.")
            self.exchange = None
            return

        try:
            self.exchange = ccxt.okx({
                'apiKey': self.api_key,
                'secret': self.secret_key,
                'password': self.password,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'swap',
                    'sandbox': True  # 이 부분이 추가되어야 모의투자 서버로 접속합니다.
                }
            })
            # 모의투자 모드임을 명시적으로 선언
            self.exchange.set_sandbox_mode(True)
            self.exchange.load_markets()
            print("[시스템] OKX API 연결 성공 및 마켓 데이터 로드 완료.")
        except Exception as e:
            print(f"[치명적 오류] OKX 거래소 서버 연결 실패. 암호문(Password)이나 키 값이 잘못되었습니다. 에러: {e}")
            self.exchange = None

    def get_usdt_balance(self):
        """현재 USDT 총 자산 조회 (Trading 증거금 + Funding + 미실현 수익 포함)"""
        if not self.exchange:
            return 0.0
        try:
            # 1. Trading 계정 잔고 조회 (total = free + collateral)
            trading_balance = self.exchange.fetch_balance({'type': 'trading'})
            trading_usdt = float(trading_balance.get('USDT', {}).get('total', 0.0) or 0.0)

            # 2. Funding 계정 잔고 조회
            funding_balance = self.exchange.fetch_balance({'type': 'funding'})
            funding_usdt = float(funding_balance.get('USDT', {}).get('free', 0.0) or 0.0)

            total = trading_usdt + funding_usdt

            # 3. Fallback: 만약 0이면 Unified(기본) 모드로 재조회
            if total == 0:
                unified_balance = self.exchange.fetch_balance()
                unified_usdt = float(unified_balance.get('USDT', {}).get('total', 0.0) or 0.0)
                if unified_usdt > 0:
                    return unified_usdt

            return total
        except Exception as e:
            print(f"[조회 오류] 잔고 조회 실패: {e}")
            return 0.0

    def get_current_price(self, symbol="BTC/USDT:USDT"):
        """현재가 조회 - Mark Price 우선 (선물 손절/익절 정석 기준)"""
        if not self.exchange:
            return None
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            # Mark Price 우선 사용 (OKX 선물 청산 기준과 일치)
            mark_price = ticker.get('info', {}).get('markPx')
            if mark_price:
                return float(mark_price)
            return float(ticker['last'])  # fallback
        except Exception as e:
            print(f"[조회 오류] 현재가 조회 실패: {e}")
            return None

    def get_open_positions(self):
        """현재 열린 포지션 조회 (다중 심볼 대응)"""
        if not self.exchange:
            return []
        try:
            positions = self.exchange.fetch_positions()
            open_positions = []
            for pos in positions:
                if pos.get('contracts') and float(pos.get('contracts', 0)) != 0:
                    open_positions.append({
                        'symbol': pos.get('symbol'),
                        'contracts': float(pos.get('contracts', 0)),
                        'contractSize': pos.get('contractSize'),
                        'side': pos.get('side'),  # 'long' or 'short'
                        'collateral': float(pos.get('collateral', 0)),
                        'markPrice': float(pos.get('markPrice', 0))
                    })
            return open_positions
        except Exception as e:
            print(f"[조회 오류] 포지션 조회 실패: {e}")
            return []

    def close_position_and_get_pnl(self, symbol, side, amount):
        """
        포지션 시장가 청산 후 OKX 공식 실현 수익(Gross PnL + Fee = Net PnL) 확보
        반환: (순수익금_USDT, 수익률_퍼센트, 추가조회성공여부)
        """
        if not self.exchange:
            raise Exception("OKX 거래소가 연결되지 않았습니다.")
            
        # 1. 청산 주문 실행
        if side.upper() == "LONG":
            order = self.exchange.create_market_sell_order(symbol, amount)
        else:
            order = self.exchange.create_market_buy_order(symbol, amount)
            
        order_id = order.get('id')
        
        # 2. 체결 내역 업데이트 대기 (OKX 매칭 엔진 반영 시간)
        import time
        time.sleep(1.0)
        
        # 3. fetch_my_trades 를 통한 영수증 조회 (수수료 및 funding fee 포함)
        try:
            trades = self.exchange.fetch_my_trades(symbol, limit=20)
            matching_trades = [t for t in trades if str(t.get('order')) == str(order_id)]
            
            if not matching_trades:
                time.sleep(1.5) # 한 번 더 대기
                trades = self.exchange.fetch_my_trades(symbol, limit=20)
                matching_trades = [t for t in trades if str(t.get('order')) == str(order_id)]

            if matching_trades:
                total_gross_pnl = 0.0
                total_fee = 0.0
                total_cost = 0.0
                
                for t in matching_trades:
                    # OKX info 객체의 fillPnl 추출
                    fill_pnl = float(t.get('info', {}).get('fillPnl', 0) or 0)
                    total_gross_pnl += fill_pnl
                    
                    # 수수료 추출 (OKX 네이티브 fee는 마이너스 값)
                    raw_fee = float(t.get('info', {}).get('fee', 0) or 0)
                    total_fee += raw_fee
                    
                    # 진입 가치(원금) 추정용 (cost)
                    total_cost += t.get('cost', 0)
                    
                net_pnl = total_gross_pnl + total_fee
                # 수익률 계산: CCXT cost는 레버리지가 적용된 총 가치일 수 있음
                # OKX의 PnL 계산에 맞춰, 봇 엔진 루틴에서 수익률 재계산을 위한 기본값만 넘김
                return net_pnl, 0.0, True
        except Exception as e:
            print(f"[경고] 청산 영수증(PnL) 조회 실패: {e}")
            
        # 영수증 조회가 실패하더라도 청산 자체는 성공했으니 None 반환
        return None, None, False
