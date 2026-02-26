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
            load_dotenv(dotenv_path=env_path, override=True)

        self.api_key = os.getenv("OKX_API_KEY")
        self.secret_key = os.getenv("OKX_SECRET_KEY")
        self.password = os.getenv("OKX_PASSWORD")

        if not self.api_key or not self.secret_key or not self.password:
            print("[치명적 오류] OKX API 키가 누락되었습니다.")
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
                    'sandbox': True  # 모의투자 모드
                }
            })
            self.exchange.set_sandbox_mode(True)
            self.exchange.load_markets()
            print("[시스템] OKX API 연결 성공 및 마켓 데이터 로드 완료.")
        except Exception as e:
            print(f"[치명적 오류] OKX 서버 연결 실패. 암호문이나 키 값이 잘못되었습니다. 에러: {e}")
            self.exchange = None

    def get_usdt_balance(self):
        """현재 USDT 총 자산 조회 (Trading 증거금 + Funding + 미실현 수익 포함)"""
        if not self.exchange:
            return 0.0
        try:
            trading_balance = self.exchange.fetch_balance({'type': 'trading'})
            trading_usdt = float(trading_balance.get('USDT', {}).get('total', 0.0) or 0.0)

            funding_balance = self.exchange.fetch_balance({'type': 'funding'})
            funding_usdt = float(funding_balance.get('USDT', {}).get('free', 0.0) or 0.0)

            total = trading_usdt + funding_usdt

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
        """현재가 조회 - Mark Price 우선"""
        if not self.exchange:
            return None
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            mark_price = ticker.get('info', {}).get('markPx')
            if mark_price:
                return float(mark_price)
            return float(ticker['last'])
        except Exception as e:
            print(f"[조회 오류] 현재가 조회 실패: {e}")
            return None

    def get_open_positions(self):
        """현재 열린 포지션 조회"""
        if not self.exchange:
            return []
        try:
            positions = self.exchange.fetch_positions()
            open_positions = []
            for pos in positions:
                if pos.get('contracts') and float(pos.get('contracts', 0)) != 0:
                    open_positions.append(pos)
            return open_positions
        except Exception as e:
            print(f"[조회 오류] 포지션 조회 실패: {e}")
            return []

    def close_position(self, symbol: str, side: str, amount: float):
        """
        포지션 시장가 청산 명령만 수행 후 order_id 반환
        """
        if not self.exchange:
            raise Exception("OKX 거래소가 연결되지 않았습니다.")
            
        if side.upper() == "LONG":
            order = self.exchange.create_market_sell_order(symbol, amount)
        else:
            order = self.exchange.create_market_buy_order(symbol, amount)
            
        return order.get('id')

    def get_recent_trade_receipts(self, symbol: str, limit: int = 20):
        """지정된 심볼의 최근 체결 영수증(Trades) 원본 배열 반환"""
        if not self.exchange:
            raise Exception("OKX 거래소가 연결되지 않았습니다.")
        return self.exchange.fetch_my_trades(symbol, limit=limit)
