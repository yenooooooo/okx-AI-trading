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
            print(f"[OKX 엔진] .env 로딩 경로: {env_path}")
            print(f"[OKX 엔진] API Key 앞 6자: {str(self.api_key)[:6]}***")

            self.exchange = ccxt.okx({
                'apiKey': self.api_key,
                'secret': self.secret_key,
                'password': self.password,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'swap',
                }
            })

            # [진단 Step 1] 필수 credential 누락 검증
            self.exchange.check_required_credentials()
            print("[OKX 엔진] check_required_credentials() 통과 — 키 형식 이상 없음")

            # [진단 Step 2] 퍼블릭 마켓 로드
            self.exchange.load_markets()
            print("[OKX 엔진] load_markets() 성공 — 퍼블릭 연결 정상")

            # [진단 Step 3] 프라이빗 인증 강제 테스트 (잔고 1회 동기 호출)
            test_balance = self.exchange.fetch_balance({'type': 'trading'})
            usdt_total = float(test_balance.get('USDT', {}).get('total', 0.0) or 0.0)
            print(f"[OKX 엔진] \033[92m✅ 프라이빗 인증 성공! Trading 잔고: {usdt_total:.2f} USDT\033[0m")
            print("[시스템] OKX API 연결 성공 및 마켓 데이터 로드 완료.")

        except Exception as e:
            # [진단 Step 4] 원인 별 색상 경고 출력
            raw_err = str(e)
            print(f"\033[91m[치명적 오류] OKX 프라이빗 인증 실패!\033[0m")
            print(f"\033[91m원인 RAW: {raw_err}\033[0m")
            if "ip" in raw_err.lower() or "403" in raw_err:
                print("\033[91m→ IP 제한 가능성: OKX API 설정에서 IP 화이트리스트를 확인하세요.\033[0m")
            elif "invalid" in raw_err.lower() or "auth" in raw_err.lower():
                print("\033[91m→ 키/비밀번호 오류: .env의 OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSWORD 재확인.\033[0m")
            elif "permission" in raw_err.lower():
                print("\033[91m→ 권한 부족: 해당 API 키에 선물 거래 권한이 활성화되어 있는지 확인하세요.\033[0m")
            else:
                print("\033[91m→ 알 수 없는 오류 — 위 RAW 메시지를 OKX 공식 문서와 대조하세요.\033[0m")
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

    def cancel_order(self, order_id: str, symbol: str):
        """
        주어진 order_id와 symbol을 사용하여 기존 (대기) 주문을 취소합니다.
        """
        if not self.exchange:
            raise Exception("OKX 거래소가 연결되지 않았습니다.")
            
        try:
            return self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            print(f"[주문 취소 실패] {symbol} (Order ID: {order_id}): {e}")
            raise e

    def get_recent_trade_receipts(self, symbol: str, limit: int = 20):
        """지정된 심볼의 최근 체결 영수증(Trades) 원본 배열 반환"""
        if not self.exchange:
            raise Exception("OKX 거래소가 연결되지 않았습니다.")
        return self.exchange.fetch_my_trades(symbol, limit=limit)

    def calculate_realized_pnl(self, matching_trades: list, entry_price: float) -> tuple:
        """
        체결 영수증(Trades) 배열과 진입가를 바탕으로,
        총수익(Gross), 수수료(Fee, 양방향 역산), 순수익(Net), 평균체결가를 반환합니다.
        (DRY 원칙 강제: 모든 청산 로직에서 이 단일 함수만 사용)
        """
        total_gross = sum(float(t.get('info', {}).get('fillPnl', 0) or 0) for t in matching_trades)
        exit_fee = sum(float(t.get('info', {}).get('fee', 0) or 0) for t in matching_trades)
        total_amount = sum(t.get('amount', 0) for t in matching_trades)
        
        # CCXT's cost for OKX derivatives includes contract_size multiplier. 
        # Using cost/amount returns 1/100th of the actual price for BTC.
        # We must calculate the weighted average price directly using price and amount.
        sum_price_amount = sum(float(t.get('price', 0)) * float(t.get('amount', 0)) for t in matching_trades)
        avg_fill_price = sum_price_amount / total_amount if total_amount > 0 else 0.0
        
        if avg_fill_price == 0.0 and matching_trades:
            avg_fill_price = float(matching_trades[0].get('price', entry_price))
            
        # 1. 터미널/OKX 오차 원인 제거: 진입 시 발생한 수수료 역산 후 합산
        total_fee = exit_fee
        if avg_fill_price > 0 and entry_price > 0:
            entry_fee = exit_fee * (entry_price / avg_fill_price)
            total_fee += entry_fee
            
        net_pnl = total_gross + total_fee
        return net_pnl, total_gross, total_fee, avg_fill_price



    async def scan_top_volume_coins(self, limit: int = 3) -> list:
        """
        OKX Swap 시장에서 24시간 거래량 기준 상위 USDT 페어 심볼을 가져옵니다.
        """
        if not self.exchange:
            raise Exception("OKX 거래소가 연결되지 않았습니다.")
            
        import asyncio
        loop = asyncio.get_event_loop()
        # 동기 함수인 fetch_tickers를 비동기로 실행하여 블로킹 방지
        tickers = await loop.run_in_executor(None, self.exchange.fetch_tickers)
        
        # 조건: 1) USDT 페어, 2) Swap(무기한 선물)
        usdt_swap_tickers = []
        for symbol, data in tickers.items():
            if "USDT" in symbol and ":" in symbol and "USDT" in symbol.split(":")[1]:
                vol = float(data.get('quoteVolume', 0) or 0)
                if vol > 0:
                    usdt_swap_tickers.append((symbol, vol))
                    
        # 거래량 기준 내림차순 정렬
        usdt_swap_tickers.sort(key=lambda x: x[1], reverse=True)
        
        # 상위 N개 심볼 추출
        top_symbols = [item[0] for item in usdt_swap_tickers[:limit]]
        return top_symbols
