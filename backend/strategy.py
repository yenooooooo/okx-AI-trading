import pandas as pd

class TradingStrategy:
    def __init__(self, initial_seed=100000):
        """
        매매 전략 및 시드 보호를 위한 핵심 파라미터 세팅
        기본 시드: 100,000원 (환율에 맞춰 USDT로 변환하여 관리될 예정)
        """
        self.initial_seed = initial_seed
        self.max_daily_loss_rate = 0.50       # [테스트] 원본: 0.05 (5%) → 사실상 서킷 브레이커 해제
        self.hard_stop_loss_rate = 0.005      # [테스트] 원본: 0.02 (2%) → 0.5% 빠른 손절
        self.trailing_stop_activation = 0.003 # [테스트] 원본: 0.03 (3%) → 0.3% 수익 시 트레일링 활성화
        self.trailing_stop_rate = 0.002       # [테스트] 원본: 0.01 (1%) → 0.2% 하락 시 익절
        # self.volume_surge_multiplier = 1.5    # [Phase 2] 거래량 폭발 기준 배수
        self.volume_surge_multiplier = 0.5    # [Phase 2] 거래량 폭발 기준 배수
        self.macro_cache = {}                 # 1시간봉 거시적 추세 데이터 캐싱

    async def get_macro_ema_200(self, engine_api, symbol):
        """
        [Phase 1] 거시적 추세 파악용 1시간봉 200 EMA 조회 및 캐싱 (15분 유지)
        API Rate Limit 우회를 위해 asyncio.to_thread 비동기 실행 및 자체 캐시 사용 
        """
        import time
        import asyncio
        import pandas as pd
        
        now = time.time()
        # 15분(900초) 단위 캐싱
        if symbol in self.macro_cache and (now - self.macro_cache[symbol]['timestamp'] < 900):
            return self.macro_cache[symbol]['ema_200']
            
        try:
            # 백엔드 엔진의 ccxt ohlcv 조회를 비동기로 우회 실행
            ohlcv_1h = await asyncio.to_thread(engine_api.exchange.fetch_ohlcv, symbol, "1h", limit=200)
            if not ohlcv_1h or len(ohlcv_1h) < 200:
                return None
                
            df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_1h['ema_200'] = df_1h['close'].ewm(span=200, adjust=False).mean()
            ema_200 = df_1h['ema_200'].iloc[-1]
            
            self.macro_cache[symbol] = {'timestamp': now, 'ema_200': ema_200}
            return ema_200
        except Exception as e:
            from logger import get_logger
            logger = get_logger(__name__)
            logger.warning(f"[{symbol}] 거시적 추세(1h EMA200) 조회 실패 (캐시 재사용): {e}")
            return self.macro_cache.get(symbol, {}).get('ema_200', None)

    def calculate_indicators(self, df):
        """
        OHLCV 데이터프레임을 받아 보수적 매매를 위한 기술적 지표 계산
        호환성 및 설치 오류 방지를 위해 순수 Pandas 로직으로 구현
        """
        # 1. RSI 계산 (14주기)
        delta = df['close'].diff(1)
        gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # 2. 볼린저 밴드 계산 (20주기, 2표준편차)
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std_20'] = df['close'].rolling(window=20).std()
        df['upper_band'] = df['sma_20'] + (df['std_20'] * 2)
        df['lower_band'] = df['sma_20'] - (df['std_20'] * 2)

        # 3. MACD 계산 (12, 26, 9)
        df['ema_12'] = df['close'].ewm(span=12, adjust=False).mean()
        df['ema_26'] = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

        # 4. [Phase 2] 거래량 SMA 계산 (20주기)
        df['vol_sma_20'] = df['volume'].rolling(window=20).mean()

        # 5. [Phase 3] ATR 계산 (14주기)
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()

        return df

    def check_entry_signal(self, df, current_price=None, macro_ema_200=None):
        """
        가장 최근 캔들을 분석하여 매수/매도 진입 시그널 판단
        공격적 다중 지표 (Multi-Indicators) 및 [Phase 1] 1h EMA 거시적 추세 필터 적용
        반환값: (진입신호, 상태메세지, 페이로드) 형태의 튜플
        """
        if len(df) < 2:
            return "HOLD", "데이터 부족 대기", None
            
        latest = df.iloc[-1]
        previous = df.iloc[-2]
        
        rsi_val = latest['rsi']
        macd_val = latest['macd']
        vol_val = latest['volume']
        vol_sma_20 = latest['vol_sma_20']
        
        # [Phase 2] 거래량 폭발 필터링
        volume_verified = vol_val > (vol_sma_20 * self.volume_surge_multiplier) if not pd.isna(vol_sma_20) else True
        
        # [Telegram/Logging Payload Packaging]
        payload = {
            "ema_status": f"Uptrend (Price > EMA)" if macro_ema_200 is not None and current_price is not None and current_price > macro_ema_200 else (f"Downtrend (Price < EMA)" if macro_ema_200 is not None else "N/A"),
            "vol_multiplier": f"{vol_val / vol_sma_20:.2f}x" if not pd.isna(vol_sma_20) and vol_sma_20 > 0 else "N/A",
            "atr_sl_margin": f"ATR(14): {latest['atr']:.2f} -> SL Margin: {latest['atr'] * 2.0:.2f}" if 'atr' in latest and not pd.isna(latest['atr']) else "N/A"
        }
        
        # [테스트 모드] 진입 조건 완화: BB 제거, RSI 범위 확대, MACD 크로스만 유지
        # 원본 LONG:  BB하단 AND MACD골든크로스 AND RSI<=40
        # 원본 SHORT: BB상단 AND MACD데드크로스 AND RSI>=60
        # long_macd = (latest['macd'] > latest['macd_signal']) and (previous['macd'] <= previous['macd_signal'])
        long_macd = (latest['macd'] > latest['macd_signal'])
        # long_rsi = latest['rsi'] <= 55   # [테스트] 원본: 40
        long_rsi = latest['rsi'] <= 70

        if long_macd and long_rsi:
            if not volume_verified:
                return "HOLD", f"거래량 부족 차단 (현재 {vol_val:.1f} <= SMA {vol_sma_20:.1f} * {self.volume_surge_multiplier})", None
            if macro_ema_200 is not None and current_price is not None:
                if current_price <= macro_ema_200:
                    return "HOLD", f"LONG 역추세 차단 (현재가 <= 1h EMA 200: {macro_ema_200:.2f})", None
            return "LONG", f"상승 감지 (RSI {rsi_val:.1f}, MACD 상향 돌파, 거래량 충족)", payload

        # short_macd = (latest['macd'] < latest['macd_signal']) and (previous['macd'] >= previous['macd_signal'])
        short_macd = (latest['macd'] < latest['macd_signal'])
        # short_rsi = latest['rsi'] >= 45  # [테스트] 원본: 60
        short_rsi = latest['rsi'] >= 30
        

        if short_macd and short_rsi:
            if not volume_verified:
                return "HOLD", f"거래량 부족 차단 (현재 {vol_val:.1f} <= SMA {vol_sma_20:.1f} * {self.volume_surge_multiplier})", None
            if macro_ema_200 is not None and current_price is not None:
                if current_price >= macro_ema_200:
                    return "HOLD", f"SHORT 역추세 차단 (현재가 >= 1h EMA 200: {macro_ema_200:.2f})", None
            return "SHORT", f"하락 감지 (RSI {rsi_val:.1f}, MACD 하향 돌파, 거래량 충족)", payload
            
        return "HOLD", f"현재 RSI {rsi_val:.1f} / MACD {macd_val:.2f} - 타점 탐색 중", None

    def evaluate_risk_management(self, entry_price, current_price, highest_price, position_side, current_atr, symbol="BTC/USDT:USDT"):
        """
        파산 방지 핵심 모듈: 현재 진행 중인 포지션의 강제 청산(손절/익절) 여부 반환
        [Phase 3] 하드 스탑로스와 트레일링 스탑을 고정 %가 아닌 변동성(ATR) 기반으로 설정
        """
        if current_atr <= 0 or pd.isna(current_atr):
            current_atr = entry_price * 0.01  # ATR 계산 불가시 진입가의 1%로 임시대체
            
        if position_side == "LONG":
            profit_usdt = current_price - entry_price
            drawdown_usdt = highest_price - current_price
            hard_sl_price = entry_price - (current_atr * 2.0)
        elif position_side == "SHORT":
            profit_usdt = entry_price - current_price
            drawdown_usdt = current_price - highest_price
            hard_sl_price = entry_price + (current_atr * 2.0)
        else:
            return "KEEP"

        # 1. 하드 스탑로스 (Hard Stop-loss) - ATR * 2.0 기준
        if position_side == "LONG" and current_price <= hard_sl_price:
            return "STOP_LOSS"
        if position_side == "SHORT" and current_price >= hard_sl_price:
            return "STOP_LOSS"

        # 2. 트레일링 스탑 (Trailing Stop)
        # 발동 기준: 수익금이 (ATR * 1.0) 사이즈를 돌파했을 때
        if profit_usdt >= (current_atr * 1.0):
            # 추적 간격: 최고점/최저점 대비 "ATR * 0.5" 하락 시 청산
            if drawdown_usdt >= (current_atr * 0.5):
                return "TRAILING_STOP_EXIT"

        return "KEEP"
        
    def is_daily_drawdown_exceeded(self, current_balance):
        """일일 누적 손실 한도 초과 여부 확인 (뇌동매매 방지용)"""
        loss_rate = (self.initial_seed - current_balance) / self.initial_seed
        if loss_rate >= self.max_daily_loss_rate:
            return True
        return False

    def calculate_position_size(self, balance, risk_rate, entry_price, leverage=1, contract_size=0.01):
        """
        동적 포지션 사이즈 계산
        공식: size = (balance × risk_rate × leverage) / entry_price
        최소값: 지정된 계약 크기(contract_size) 에 맞춰 반올림 처리
        """
        if balance <= 0 or entry_price <= 0:
            return float(contract_size)

        size = (balance * risk_rate * leverage) / entry_price
        
        # 계약 단위에 맞춘 소수점/정량 정리
        # 예: size가 0.015 이고 contract_size가 0.01 이면, 0.01로 조정
        if contract_size > 0:
            contracts = max(1.0, round(size / contract_size))
            return contracts * contract_size
            
        return max(size, 0.001)
