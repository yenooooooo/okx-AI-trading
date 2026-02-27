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
        self.volume_surge_multiplier = 1.5    # [Phase 2] 거래량 폭발 기준 배수 (원본 0.5 -> 1.5 상향)
        self.fee_margin = 0.003               # [Step 2] 수수료 방어 마진: 왕복 수수료 + 슬리피지 0.3%
        self.adx_threshold = 25.0            # [Step 1] ADX 추세 강도 필터 하한 (25 이상)
        self.adx_max = 40.0                  # [Step 2] ADX 과열 필터 상한 (40 초과 시 진입 차단)
        self.macro_cache = {}                 # 1시간봉 거시적 추세 데이터 캐싱

    async def get_macro_ema_200(self, engine_api, symbol):
        """
        [Phase 1] 거시적 추세 파악용 1시간봉 200 EMA 조회 및 캐싱 (15분 유지)
        API Rate Limit 우회를 위해 asyncio.to_thread 비동기 실행 및 자체 캐시 사용
        1h 타임프레임 고정 — 매매 기준 타임프레임(5m)과 완전 독립 운용
        """
        import time
        import asyncio
        import pandas as pd

        now = time.time()
        # 15분(900초) 단위 캐싱
        if symbol in self.macro_cache and (now - self.macro_cache[symbol]['timestamp'] < 900):
            return self.macro_cache[symbol]['ema_200']

        try:
            # 백엔드 엔진의 ccxt ohlcv 조회를 비동기로 우회 실행 (1h 고정)
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
        [Step 1] 5분봉 기준 + ADX(14기간) 추가
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

        # 4-1. [Step 3] 단기 이격도 방어를 위한 EMA 20 계산 (5분봉 기준)
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()

        # 5. [Phase 3] ATR 계산 (14주기)
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()

        # 6. [Step 1] ADX 계산 (14주기) — Wilder's EWM 방식, 순수 Pandas 구현
        # +DM: 오늘 고가 상승폭 > 오늘 저가 하락폭 이고 양수인 경우만 유효
        # -DM: 오늘 저가 하락폭 > 오늘 고가 상승폭 이고 양수인 경우만 유효
        alpha_wilder = 1.0 / 14
        high_diff = df['high'].diff()
        low_diff = -df['low'].diff()
        plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0.0)
        minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0.0)
        smoothed_plus_dm = plus_dm.ewm(alpha=alpha_wilder, adjust=False).mean()
        smoothed_minus_dm = minus_dm.ewm(alpha=alpha_wilder, adjust=False).mean()
        smoothed_tr = true_range.ewm(alpha=alpha_wilder, adjust=False).mean()
        di_denom = smoothed_tr.replace(0, float('nan'))
        plus_di = 100 * smoothed_plus_dm / di_denom
        minus_di = 100 * smoothed_minus_dm / di_denom
        di_sum = (plus_di + minus_di).replace(0, float('nan'))
        dx = 100 * (plus_di - minus_di).abs() / di_sum
        df['adx'] = dx.ewm(alpha=alpha_wilder, adjust=False).mean()

        return df

    def check_entry_signal(self, df, current_price=None, macro_ema_200=None):
        """
        가장 최근 캔들을 분석하여 매수/매도 진입 시그널 판단
        [Step 1] ADX >= 25 횡보장 필터 추가 (5분봉 기준)
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
        adx_val = latest['adx'] if 'adx' in latest and not pd.isna(latest['adx']) else 0.0

        # [Step 1,2] ADX 횡보장 및 과열장 필터 (25 <= ADX <= 40)
        if adx_val < self.adx_threshold:
            return "HOLD", f"횡보장 차단 — ADX {adx_val:.1f} < {self.adx_threshold:.0f} (추세 없음, 관망)", None
        if adx_val > self.adx_max:
            return "HOLD", f"과열장 차단 — ADX {adx_val:.1f} > {self.adx_max:.0f} (추세 끝물, 추격매수 방지)", None

        # [Phase 2] 거래량 폭발 필터링
        volume_verified = vol_val > (vol_sma_20 * self.volume_surge_multiplier) if not pd.isna(vol_sma_20) else True
        
        # [Step 3] 단기 5m EMA 20 이격도(Disparity) 필터: 0.8% 이상 벌어지면 진입 금지 (가짜 상승/하락 추격 방지)
        ema_20_val = latest['ema_20'] if 'ema_20' in latest and not pd.isna(latest['ema_20']) else current_price
        disparity_pct = abs((current_price - ema_20_val) / ema_20_val) * 100 if current_price and ema_20_val else 0.0
        
        if disparity_pct >= 0.8:
            return "HOLD", f"이격도 초과 차단 — EMA20 대비 {disparity_pct:.2f}% 이격 (안전 이격거리 0.8% 초과)", None

        # [Telegram/Logging Payload Packaging]
        payload = {
            "ema_status": (
                f"Uptrend (Price > EMA)" if macro_ema_200 is not None and current_price is not None and current_price > macro_ema_200
                else (f"Downtrend (Price < EMA)" if macro_ema_200 is not None else "N/A")
            ),
            "vol_multiplier": f"{vol_val / vol_sma_20:.2f}x" if not pd.isna(vol_sma_20) and vol_sma_20 > 0 else "N/A",
            "atr_sl_margin": (
                f"ATR(14): {latest['atr']:.2f} -> SL Margin: {latest['atr'] * 2.5:.2f}"
                if 'atr' in latest and not pd.isna(latest['atr']) else "N/A"
            ),
            "adx": f"{adx_val:.1f}",
            "disparity": f"{disparity_pct:.2f}%",
        }

        long_macd = (latest['macd'] > latest['macd_signal'])
        long_rsi = latest['rsi'] <= 70

        if long_macd and long_rsi:
            if not volume_verified:
                return "HOLD", f"거래량 부족 차단 (현재 {vol_val:.1f} <= SMA {vol_sma_20:.1f} * {self.volume_surge_multiplier})", None
            return "LONG", f"상승 감지 (RSI {rsi_val:.1f}, MACD 상향, ADX {adx_val:.1f}, 거래량 충족)", payload

        short_macd = (latest['macd'] < latest['macd_signal'])
        short_rsi = latest['rsi'] >= 30

        if short_macd and short_rsi:
            if not volume_verified:
                return "HOLD", f"거래량 부족 차단 (현재 {vol_val:.1f} <= SMA {vol_sma_20:.1f} * {self.volume_surge_multiplier})", None
            return "SHORT", f"하락 감지 (RSI {rsi_val:.1f}, MACD 하향, ADX {adx_val:.1f}, 거래량 충족)", payload

        return "HOLD", f"현재 RSI {rsi_val:.1f} / MACD {macd_val:.2f} / ADX {adx_val:.1f} - 타점 탐색 중", None

    def evaluate_risk_management(self, entry_price, current_price, highest_price, position_side, current_atr, symbol="BTC/USDT:USDT", partial_tp_executed=False):
        """
        파산 방지 핵심 모듈: 현재 진행 중인 포지션의 강제 청산(손절/익절) 여부 반환
        [Step 2] 수수료 방어선 적용:
          - 하드 SL: ATR * 2.5 (5분봉 꼬리 길이 대비 여유 확보)
          - 트레일링 발동: entry_price * fee_margin + ATR * 0.5
            → 왕복 수수료 완전 회수 후 추가 순익이 발생한 구간에서만 발동
          - 트레일링 간격: ATR * 1.0 (작은 파동 휩쏘 방지, 기존 0.5에서 상향)
        """
        if current_atr <= 0 or pd.isna(current_atr):
            current_atr = entry_price * 0.01  # ATR 계산 불가시 진입가의 1%로 임시대체

        if position_side == "LONG":
            profit_usdt = current_price - entry_price
            drawdown_usdt = highest_price - current_price
            if partial_tp_executed:
                hard_sl_price = entry_price + (entry_price * 0.001)  # 1차 익절 후 본전 방어선 (+수수료 마진)
            else:
                hard_sl_price = entry_price - (current_atr * 2.5)
        elif position_side == "SHORT":
            profit_usdt = entry_price - current_price
            drawdown_usdt = current_price - highest_price
            if partial_tp_executed:
                hard_sl_price = entry_price - (entry_price * 0.001)  # 1차 익절 후 본전 방어선 (-수수료 마진)
            else:
                hard_sl_price = entry_price + (current_atr * 2.5)
        else:
            return "KEEP"

        # 1. 하드 스탑로스 (Hard Stop-loss) — ATR * 2.5 (5분봉 꼬리 대비 상향)
        if position_side == "LONG" and current_price <= hard_sl_price:
            return "STOP_LOSS"
        if position_side == "SHORT" and current_price >= hard_sl_price:
            return "STOP_LOSS"

        # 2. 트레일링 스탑 (Trailing Stop)
        # 발동 조건: 수수료 방어 마진(0.3%) + ATR*0.5 만큼의 수익이 확보된 이후에만 활성화
        # 즉, 왕복 수수료를 완전히 회수하고 추가 수익이 생긴 시점부터 추적 시작
        fee_cover_threshold = (entry_price * self.fee_margin) + (current_atr * 0.5)
        if profit_usdt >= fee_cover_threshold:
            # 추적 간격: 최고점/최저점 대비 ATR * 1.0 이상 꺾일 때 청산 (작은 파동에 털리지 않음)
            if drawdown_usdt >= (current_atr * 1.0):
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
        if contract_size > 0:
            contracts = max(1.0, round(size / contract_size))
            return contracts * contract_size

        return max(size, 0.001)
