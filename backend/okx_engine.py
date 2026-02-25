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
        """현재 USDT 잔고 조회 (Funding + Trading 합산)"""
        if not self.exchange:
            return 0.0
        try:
            # Trading 계정 잔고 조회
            trading_balance = self.exchange.fetch_balance({'type': 'trading'})
            trading_usdt = float(trading_balance.get('USDT', {}).get('free', 0.0))
            
            # Funding 계정 잔고 조회
            funding_balance = self.exchange.fetch_balance({'type': 'funding'})
            funding_usdt = float(funding_balance.get('USDT', {}).get('free', 0.0))
            
            return trading_usdt + funding_usdt
        except Exception as e:
            print(f"[조회 오류] 잔고 조회 실패: {e}")
            return 0.0

    def get_current_price(self, symbol="BTC/USDT:USDT"):
        """현재가 조회"""
        if not self.exchange:
            return None
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return float(ticker['last'])
        except Exception as e:
            print(f"[조회 오류] 현재가 조회 실패: {e}")
            return None
