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
        # [데모 모드] .env에서 OKX_DEMO=true 시 데모 트레이딩 서버 사용
        self.is_demo = os.getenv("OKX_DEMO", "false").strip().lower() in ("true", "1", "yes")

        if not self.api_key or not self.secret_key or not self.password:
            print("[치명적 오류] OKX API 키가 누락되었습니다.")
            self.exchange = None
            return

        try:
            _mode_label = "🧪 DEMO" if self.is_demo else "⚡ LIVE"
            print(f"[OKX 엔진] .env 로딩 경로: {env_path}")
            print(f"[OKX 엔진] 모드: {_mode_label}")
            print(f"[OKX 엔진] API Key 앞 6자: {str(self.api_key)[:6]}***")

            self.exchange = ccxt.okx({
                'apiKey': self.api_key,
                'secret': self.secret_key,
                'password': self.password,
                'enableRateLimit': True,
                'timeout': 10000,
                'options': {
                    'defaultType': 'swap',
                }
            })

            # [데모 모드] sandbox 활성화 → API URL이 demo-trading.okx.com으로 전환
            if self.is_demo:
                self.exchange.set_sandbox_mode(True)
                print("[OKX 엔진] 🧪 Sandbox(Demo) 모드 활성화 — demo-trading.okx.com 연결")

            # [진단 Step 1] 필수 credential 누락 검증
            self.exchange.check_required_credentials()
            print("[OKX 엔진] check_required_credentials() 통과 — 키 형식 이상 없음")

            # [진단 Step 2] 퍼블릭 마켓 로드 (최대 3회 재시도)
            import time as _init_time
            for _retry in range(3):
                try:
                    self.exchange.load_markets()
                    print("[OKX 엔진] load_markets() 성공 — 퍼블릭 연결 정상")
                    break
                except Exception as _lm_err:
                    print(f"[OKX 엔진] load_markets() 실패 ({_retry+1}/3): {_lm_err}")
                    if _retry < 2:
                        _init_time.sleep(3)
                    else:
                        raise _lm_err

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

    def get_position_contracts(self, symbol: str) -> float:
        """거래소에서 해당 심볼의 실제 포지션 계약 수 조회 (청산 시 정확한 수량 보장)"""
        if not self.exchange:
            return 0
        try:
            positions = self.exchange.fetch_positions([symbol])
            for pos in positions:
                if pos.get('symbol') == symbol and float(pos.get('contracts', 0)) > 0:
                    return float(pos['contracts'])
        except Exception as e:
            print(f"[조회 오류] 포지션 수량 조회 실패 ({symbol}): {e}")
        return 0

    def close_position(self, symbol: str, side: str, amount: float):
        """
        포지션 시장가 청산 명령만 수행 후 order_id 반환
        [Bug Fix] 내부 amount가 0이거나 최소 미달이면 거래소 실제 수량으로 대체
        """
        if not self.exchange:
            raise Exception("OKX 거래소가 연결되지 않았습니다.")

        # [방어] 전달받은 amount가 0 이하이면 거래소 실제 포지션 수량으로 대체
        if amount <= 0:
            actual = self.get_position_contracts(symbol)
            if actual > 0:
                amount = actual
            else:
                raise Exception(f"청산 불가: 내부 수량 {amount}, 거래소 수량 {actual} — 포지션 없음")

        # [방어] OKX 최소 계약 단위 보장 (BTC: 1계약 = 0.01)
        try:
            market = self.exchange.market(symbol)
            min_amount = float(market.get('limits', {}).get('amount', {}).get('min', 1))
            if amount < min_amount:
                actual = self.get_position_contracts(symbol)
                amount = max(actual, min_amount) if actual > 0 else min_amount
        except Exception:
            pass

        import time as _cp_time
        last_err = None
        for _retry in range(3):
            try:
                if side.upper() == "LONG":
                    order = self.exchange.create_market_sell_order(symbol, amount)
                else:
                    order = self.exchange.create_market_buy_order(symbol, amount)
                return order.get('id')
            except Exception as e:
                last_err = e
                print(f"[청산 재시도] {symbol} {side} 시도 {_retry+1}/3 실패: {e}")
                if _retry < 2:
                    _cp_time.sleep(2)
        # 3회 모두 실패
        raise Exception(f"[치명] {symbol} 청산 3회 실패: {last_err}")

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

    async def detect_volume_spikes(self, min_quote_volume: float = 15_000_000,
                                    spike_multiplier: float = 2.0, top_n: int = 5) -> list:
        """
        거래량 폭발 감지: 가격 변동률이 N% 이상인 고거래대금 코인 탐지
        fetch_tickers 1회 호출로 전체 시장 스캔 (API Rate Limit 보호)

        Returns: [{"symbol": ..., "spike_score": 4.7, "price_change_pct": ..., "volume_24h_usd": ..., "base": ...}, ...]
        """
        if not self.exchange:
            raise Exception("OKX 거래소가 연결되지 않았습니다.")

        # [방어] 파라미터 범위 강제 교정
        min_quote_volume = max(1_000_000, min(float(min_quote_volume), 500_000_000))
        spike_multiplier = max(0.5, min(float(spike_multiplier), 20.0))
        top_n = max(1, min(int(top_n), 20))

        import asyncio
        tickers = await asyncio.to_thread(self.exchange.fetch_tickers)

        if not tickers:
            return []

        spikes = []
        _scanned = 0
        _passed_vol = 0
        for symbol, data in tickers.items():
            # USDT 무기한 선물만
            if "USDT" not in symbol or ":" not in symbol:
                continue
            _scanned += 1

            quote_vol = float(data.get('quoteVolume', 0) or 0)
            # 최소 거래대금 필터 (소형 코인 제외)
            if quote_vol < min_quote_volume:
                continue
            _passed_vol += 1

            # BTC, ETH 제외 (항상 거래량 높아서 노이즈)
            base = symbol.split('/')[0]
            if base in ('BTC', 'ETH'):
                continue

            # 가격 변동률
            pct_change = float(data.get('percentage', 0) or 0)

            # 스파이크 판정: |가격변동률| >= N%
            if abs(pct_change) < spike_multiplier:
                continue

            # 스파이크 스코어 = |가격변동률| × (거래대금 / 1억) 정규화
            spike_score = abs(pct_change) * (quote_vol / 100_000_000)

            spikes.append({
                "symbol": symbol,
                "spike_score": round(spike_score, 2),
                "price_change_pct": round(pct_change, 2),
                "volume_24h_usd": round(quote_vol, 0),
                "base": base,
            })

        # [진단 로깅] 스캔 결과 요약 (감지 실패 디버깅용)
        print(f"[Spike] 스캔 {_scanned}개 | 거래대금 통과 {_passed_vol}개 (기준: ${min_quote_volume/1e6:.0f}M) | 변동률 기준 {spike_multiplier}% | 감지 {len(spikes)}개")

        # 스파이크 스코어 기준 정렬
        spikes.sort(key=lambda x: x['spike_score'], reverse=True)
        return spikes[:top_n]
