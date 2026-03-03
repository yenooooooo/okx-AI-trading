import pandas as pd
import time as _time

class TradingStrategy:
    def __init__(self, initial_seed=100000):
        """
        매매 전략 및 시드 보호를 위한 핵심 파라미터 세팅
        [v2.1] 손익비 정상화 + 거시 추세 필터 + 연패 쿨다운 적용
        """
        self.hard_stop_loss_rate = 0.005      # 레거시 호환성 유지
        self.trailing_stop_activation = 0.003 # 레거시 호환성 유지
        self.trailing_stop_rate = 0.002       # 레거시 호환성 유지
        self.volume_surge_multiplier = 1.5    # 거래량 폭발 기준 배수
        self.fee_margin = 0.0015              # [v2.1] 수수료 방어 마진: 0.15% (OKX maker 실제 수수료 수준)
        self.adx_threshold = 25.0             # ADX 추세 강도 필터 하한
        self.adx_max = 40.0                   # ADX 과열 필터 상한
        self.macro_cache = {}                 # 1시간봉 거시적 추세 데이터 캐싱

        # [v2.1] 연패 쿨다운 메커니즘 (3연패 시 15분 진입 차단)
        self.consecutive_loss_count = 0
        self.loss_cooldown_until = 0
        self.cooldown_losses_trigger = 3
        self.cooldown_duration_sec = 900

        # [v2.2] Choppiness Index 횡보 필터
        self.chop_threshold = 61.8            # CHOP ≥ 61.8 시 횡보장 판정, 진입 차단

        # [v2.2] 일일 킬스위치 (Daily Drawdown Limit)
        self.daily_max_loss_pct = 0.07        # 일일 최대 손실 7%
        self.daily_pnl_accumulated = 0.0      # 오늘 누적 순손익 (USDT)
        self.daily_start_balance = 0.0        # 오늘 시작 잔고 (UTC 자정 기준)
        self.daily_reset_date = ""            # 마지막 리셋 날짜 (YYYY-MM-DD)
        self.kill_switch_active = False       # 킬스위치 발동 여부
        self.kill_switch_until = 0            # 킬스위치 해제 타임스탬프

        # [Phase 14.2] 이격도 동적 한계치 (UI 직결 — DB에서 % 단위로 수신, 내부는 비율)
        self.disparity_threshold = 0.008  # 기본 0.8% → 0.008 비율 (DB: "0.8" → /100.0 변환 후 주입)

        # [v3.5] Gate Bypass 스위치 (초단타 전용 방어 관문 개별 해제)
        self.bypass_macro = False       # True: 1h EMA200 거시 추세 필터 무시
        self.bypass_disparity = False   # True: EMA20 이격도 필터 무시
        self.bypass_indicator = False   # True: RSI 범위 조건 무시 (MACD 방향만 사용)

        # [Phase 24] 최소 익절 목표율 — 이 수익률 이전에는 트레일링 EXIT 금지 (R:R 강제)
        self.min_take_profit_rate = 0.01  # 1.0% — SL 0.5% 대비 최소 R:R 1:2 보장

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

        # 7. [v2.2] Choppiness Index 계산 (14주기)
        # CHOP = 100 * LOG10(SUM(ATR, 14) / (Highest_High_14 - Lowest_Low_14)) / LOG10(14)
        import numpy as np
        atr_sum_14 = true_range.rolling(window=14).sum()
        highest_14 = df['high'].rolling(window=14).max()
        lowest_14 = df['low'].rolling(window=14).min()
        hl_range_14 = (highest_14 - lowest_14).replace(0, float('nan'))
        df['chop'] = 100 * np.log10(atr_sum_14 / hl_range_14) / np.log10(14)

        return df

    def check_entry_signal(self, df, current_price=None, macro_ema_200=None):
        """
        가장 최근 캔들을 분석하여 매수/매도 진입 시그널 판단
        [v2.1] 거시 추세 강제 필터 + RSI 구간 강화 + 연패 쿨다운
        """
        if len(df) < 2:
            return "HOLD", "데이터 부족 대기", None

        # [v2.1] 연패 쿨다운 체크 — 3연패 후 15분간 진입 차단
        if _time.time() < self.loss_cooldown_until:
            remaining = int(self.loss_cooldown_until - _time.time())
            return "HOLD", f"연패 쿨다운 — {remaining}초 후 진입 재개 ({self.consecutive_loss_count}연패 방어)", None

        latest = df.iloc[-1]
        previous = df.iloc[-2]

        rsi_val = latest['rsi']
        macd_val = latest['macd']
        vol_val = latest['volume']
        vol_sma_20 = latest['vol_sma_20']
        adx_val = latest['adx'] if 'adx' in latest and not pd.isna(latest['adx']) else 0.0

        # ADX 횡보장 및 과열장 필터 (25 <= ADX <= 40)
        if adx_val < self.adx_threshold:
            return "HOLD", f"횡보장 차단 — ADX {adx_val:.1f} < {self.adx_threshold:.0f} (추세 없음, 관망)", None
        if adx_val > self.adx_max:
            return "HOLD", f"과열장 차단 — ADX {adx_val:.1f} > {self.adx_max:.0f} (추세 끝물, 추격매수 방지)", None

        # [v2.2] Choppiness Index 횡보 필터: CHOP >= 61.8 시 횡보장으로 판단, 진입 차단
        chop_val = latest['chop'] if 'chop' in latest and not pd.isna(latest['chop']) else 50.0
        if chop_val >= self.chop_threshold:
            return "HOLD", f"횡보장(CHOP) 차단 — CHOP {chop_val:.1f} ≥ {self.chop_threshold} (톱니바퀴 시장, 관망)", None

        # [v2.2] 일일 킬스위치 체크
        if self.kill_switch_active and _time.time() < self.kill_switch_until:
            remaining_h = (self.kill_switch_until - _time.time()) / 3600
            return "HOLD", f"🚨 킬스위치 발동 중 — {remaining_h:.1f}시간 후 해제 (일일 최대 손실 도달)", None
        elif self.kill_switch_active and _time.time() >= self.kill_switch_until:
            self.kill_switch_active = False
            self.daily_pnl_accumulated = 0.0

        # 거래량 폭발 필터링
        volume_verified = vol_val > (vol_sma_20 * self.volume_surge_multiplier) if not pd.isna(vol_sma_20) else True

        # 단기 5m EMA 20 이격도 필터 — [Phase 14.2] 방향별 동적 한계치 (UI 직결, 하드코딩 완전 제거)
        ema_20_val = latest['ema_20'] if 'ema_20' in latest and not pd.isna(latest['ema_20']) else current_price
        disparity_pct = abs((current_price - ema_20_val) / ema_20_val) * 100 if current_price and ema_20_val else 0.0

        # LONG: 가격이 EMA20 위로 threshold 초과 시 추격매수 차단
        # SHORT: 가격이 EMA20 아래로 threshold 초과 시 추격매도 차단
        long_disparity_ok = self.bypass_disparity or (current_price <= ema_20_val * (1.0 + self.disparity_threshold))
        short_disparity_ok = self.bypass_disparity or (current_price >= ema_20_val * (1.0 - self.disparity_threshold))

        # Payload 패키징 (Telegram HTML 파싱 에러 방지를 위해 <, > 기호 제거)
        payload = {
            "ema_status": (
                "상승장 (UP)" if macro_ema_200 is not None and current_price is not None and current_price > macro_ema_200
                else ("하락장 (DOWN)" if macro_ema_200 is not None else "N/A")
            ),
            "vol_multiplier": f"{vol_val / vol_sma_20:.2f}x" if not pd.isna(vol_sma_20) and vol_sma_20 > 0 else "N/A",
            "atr_sl_margin": (
                f"ATR(14): {latest['atr']:.2f} ➔ SL Margin: {latest['atr'] * 1.5:.2f}"
                if 'atr' in latest and not pd.isna(latest['atr']) else "N/A"
            ),
            "adx": f"{adx_val:.1f}",
            "disparity": f"{disparity_pct:.2f}%",
        }

        # [v2.1] MACD 크로스오버 + RSI 구간 복합 조건
        # LONG: MACD > Signal AND RSI 30~55 (과매도 반등 구간)
        # SHORT: MACD < Signal AND RSI 45~70 (과매수 하락 구간)
        long_macd = (latest['macd'] > latest['macd_signal'])
        long_rsi = (30 <= latest['rsi'] <= 55)

        short_macd = (latest['macd'] < latest['macd_signal'])
        short_rsi = (45 <= latest['rsi'] <= 70)

        # [v3.5] bypass_indicator: True 시 RSI 조건 무시, MACD 방향만으로 진입 판단
        long_entry = long_macd and (self.bypass_indicator or long_rsi)
        short_entry = short_macd and (self.bypass_indicator or short_rsi)

        # LONG 신호 판단
        if long_entry:
            if not long_disparity_ok:
                return "HOLD", f"이격도 초과 차단 — EMA20 대비 {disparity_pct:.2f}% 위로 이격 (LONG 추격 금지, 한계 {self.disparity_threshold * 100:.1f}%)", None
            if not volume_verified:
                return "HOLD", f"거래량 부족 차단 (현재 {vol_val:.1f} <= SMA {vol_sma_20:.1f} * {self.volume_surge_multiplier})", None
            # [v2.1] 거시 추세 강제 필터: 1h EMA200 아래에서 LONG 절대 차단 ([v3.5] bypass_macro 해제 시 무시)
            if not self.bypass_macro and macro_ema_200 is not None and current_price is not None and current_price < macro_ema_200:
                return "HOLD", f"거시 추세 역행 차단 — 1h EMA200(${macro_ema_200:.2f}) 아래에서 LONG 금지", None
            return "LONG", f"상승 감지 (RSI {rsi_val:.1f}, MACD 상향, ADX {adx_val:.1f}, 거래량 충족)", payload

        # SHORT 신호 판단
        if short_entry:
            if not short_disparity_ok:
                return "HOLD", f"이격도 초과 차단 — EMA20 대비 {disparity_pct:.2f}% 아래로 이격 (SHORT 추격 금지, 한계 {self.disparity_threshold * 100:.1f}%)", None
            if not volume_verified:
                return "HOLD", f"거래량 부족 차단 (현재 {vol_val:.1f} <= SMA {vol_sma_20:.1f} * {self.volume_surge_multiplier})", None
            # [v2.1] 거시 추세 강제 필터: 1h EMA200 위에서 SHORT 절대 차단 ([v3.5] bypass_macro 해제 시 무시)
            if not self.bypass_macro and macro_ema_200 is not None and current_price is not None and current_price > macro_ema_200:
                return "HOLD", f"거시 추세 역행 차단 — 1h EMA200(${macro_ema_200:.2f}) 위에서 SHORT 금지", None
            return "SHORT", f"하락 감지 (RSI {rsi_val:.1f}, MACD 하향, ADX {adx_val:.1f}, 거래량 충족)", payload

        return "HOLD", f"현재 RSI {rsi_val:.1f} / MACD {macd_val:.2f} / ADX {adx_val:.1f} - 타점 탐색 중", None

    def evaluate_risk_management(self, entry_price, current_price, highest_price, position_side, current_atr, symbol="BTC/USDT:USDT", partial_tp_executed=False, contracts=1, breakeven_stop_active=False):
        """
        파산 방지 핵심 모듈 — 하드코딩 제거 및 UI 튜닝 파라미터(%) 직결 완료
        반환값: (action, effective_sl, trailing_active, trailing_target)
        [Phase 26] effective_sl = max(hard_sl, trailing_target) 병합으로 거래소 SL이 트레일링을 자동 추적
        [Phase TF] 1계약 조기 트레일링: 본전 방어 후 즉시 SL이 수익을 추적 (빈틈 제거)
        """
        _is_single_contract = int(contracts) <= 1

        # ── 손절가 및 수익/낙폭 계산 ──
        if position_side == "LONG":
            profit_usdt = current_price - entry_price
            drawdown_usdt = highest_price - current_price
            hard_sl_price = (entry_price + (entry_price * 0.001)) if partial_tp_executed else (entry_price - (entry_price * self.hard_stop_loss_rate))
        elif position_side == "SHORT":
            profit_usdt = entry_price - current_price
            drawdown_usdt = current_price - highest_price
            hard_sl_price = (entry_price - (entry_price * 0.001)) if partial_tp_executed else (entry_price + (entry_price * self.hard_stop_loss_rate))
        else:
            return "KEEP", 0.0, False, 0.0

        # ── 1. 하드 스탑로스 ──
        if position_side == "LONG" and current_price <= hard_sl_price:
            return "STOP_LOSS", hard_sl_price, False, 0.0
        if position_side == "SHORT" and current_price >= hard_sl_price:
            return "STOP_LOSS", hard_sl_price, False, 0.0

        # ── 2. 트레일링 스탑 (UI 변수 100% 직결) ──
        activation_threshold_usdt = entry_price * self.trailing_stop_activation
        trailing_active = profit_usdt >= activation_threshold_usdt
        trailing_target = 0.0

        if trailing_active:
            trailing_distance_usdt = highest_price * self.trailing_stop_rate

            if position_side == "LONG":
                trailing_target = highest_price - trailing_distance_usdt
            else:
                trailing_target = highest_price + trailing_distance_usdt

            profit_rate = profit_usdt / entry_price if entry_price > 0 else 0.0
            if drawdown_usdt >= trailing_distance_usdt:
                # [Phase TF] 1계약 조기 트레일링: 본전 방어 발동 후에는 min TP 가드 없이 즉시 EXIT 허용
                # 다계약은 기존 로직 유지 (min_take_profit_rate 충족 필요)
                if _is_single_contract and partial_tp_executed:
                    return "TRAILING_STOP_EXIT", hard_sl_price, True, trailing_target
                elif _is_single_contract and breakeven_stop_active:
                    # [Breakeven Stop] 래칫 활성 후 트레일링 풀백 시 즉시 EXIT (최소 본전 보장)
                    return "TRAILING_STOP_EXIT", hard_sl_price, True, trailing_target
                elif profit_rate >= self.min_take_profit_rate:
                    return "TRAILING_STOP_EXIT", hard_sl_price, True, trailing_target

        # ── 3. [Phase 26] effective_sl 병합: 트레일링 타겟을 거래소 SL에 반영 ──
        effective_sl = hard_sl_price
        if trailing_active and trailing_target > 0:
            profit_rate = profit_usdt / entry_price if entry_price > 0 else 0.0
            # [Phase TF] 1계약 조기 트레일링: 본전 방어 후 즉시 SL이 트레일링 위치를 추적
            # [Breakeven Stop] breakeven_stop_active 래칫 활성 시에도 SL 업그레이드 허용
            _sl_upgrade_allowed = (_is_single_contract and partial_tp_executed) or (_is_single_contract and breakeven_stop_active) or (profit_rate >= self.min_take_profit_rate)
            if _sl_upgrade_allowed:
                if position_side == "LONG":
                    effective_sl = max(hard_sl_price, trailing_target)
                else:
                    effective_sl = min(hard_sl_price, trailing_target)

        # [Breakeven Stop] 1계약 조기 본전 방어 — 래칫 보장 (max/min으로 유리한 방향만 선택)
        # trailing_target이 entry보다 유리하면 trailing_target 유지, 아니면 entry가 최소 보장선
        if breakeven_stop_active and _is_single_contract and not partial_tp_executed:
            if position_side == "LONG":
                effective_sl = max(effective_sl, entry_price)
            else:
                effective_sl = min(effective_sl, entry_price)

        return "KEEP", effective_sl, trailing_active, trailing_target

    def recalculate_shadow_risk(self, shadow_entry_price: float, direction: str, current_atr: float):
        """
        [Phase 23 - Gemini Architect Logic]
        그림자 사냥 체결 시, 새로운 꼬리 진입가를 기준으로 방어막(SL/TP) 재계산.
        Returns (new_hard_sl, new_trailing_activation)
        """
        sl_margin = shadow_entry_price * 0.005  # 손절 0.5% 타이트

        if direction == "LONG":
            new_hard_sl = shadow_entry_price - sl_margin
            new_trailing_activation = shadow_entry_price + (current_atr * 1.5)
        else:
            new_hard_sl = shadow_entry_price + sl_margin
            new_trailing_activation = shadow_entry_price - (current_atr * 1.5)

        return new_hard_sl, new_trailing_activation

    def record_trade_result(self, is_loss):
        """
        [v2.1] 청산 결과를 연패 카운터에 반영
        api_server.py 청산 블록에서 호출
        """
        if is_loss:
            self.consecutive_loss_count += 1
            if self.consecutive_loss_count >= self.cooldown_losses_trigger:
                self.loss_cooldown_until = _time.time() + self.cooldown_duration_sec
        else:
            self.consecutive_loss_count = 0
            self.loss_cooldown_until = 0


    def calculate_position_size_dynamic(self, equity, current_price, leverage=1, contract_size=0.01, risk_rate=0.02):
        """
        [v2.4] 증거금 부족(51008) 완전 차단 방어막 (Impenetrable Armor)
        UI 버그 자동차단, 수수료 여유분 확보, 최대 계약수 캡(Cap) 적용
        """
        if equity <= 0 or current_price <= 0:
            return 1

        # [방어 1] UI에서 FRENZY 광기 모드가 1.0(100%)을 보내는 버그 자동 교정
        # risk_rate가 1.0 이상이면 퍼센트로 입력한 것으로 간주 (예: 1.0 -> 0.01)
        if risk_rate >= 1.0:
            risk_rate = risk_rate / 100.0

        # [방어 2] 거래소 수수료(Taker) 및 슬리피지를 위해 최대 가용 시드를 95%로 제한
        safe_equity = equity * 0.95

        # 기본 정률법 계산 (잔고 * 리스크 비율 * 레버리지 / 현재 가격)
        notional = equity * risk_rate * leverage
        position_size_raw = notional / current_price

        # OKX Swap 계약 단위 정수화
        if contract_size > 0:
            contracts = round(position_size_raw / contract_size)
        else:
            contracts = round(position_size_raw)

        # 최소 1계약 보장
        contracts = max(1, contracts)

        # [방어 3] 내 잔고(safe_equity)로 실제 감당 가능한 최대 계약수(Max Capacity) 계산
        # 1계약 당 필요 증거금 = (계약단위 * 현재가) / 레버리지
        margin_per_contract = (contract_size * current_price) / leverage
        max_possible_contracts = int(safe_equity / margin_per_contract)

        # 감당 가능한 계약 수가 1 이상일 때만 캡(Cap)을 씌움
        # (만약 시드가 너무 작아 1계약도 못 사면, 어차피 거래소가 튕겨내도록 1 전송)
        if max_possible_contracts >= 1:
            contracts = min(contracts, max_possible_contracts)

        return int(contracts)

    def calculate_position_size(self, balance, risk_rate, entry_price, leverage=1, contract_size=0.01):
        """
        레거시 호환용 포지션 사이즈 계산 (수동 오버라이드 모드에서 사용)
        """
        if balance <= 0 or entry_price <= 0:
            return float(contract_size)

        size = (balance * risk_rate * leverage) / entry_price

        if contract_size > 0:
            contracts = max(1.0, round(size / contract_size))
            return contracts * contract_size

        return max(size, 0.001)

    def check_daily_reset(self, current_balance):
        """
        [v2.2] KST(UTC+9) 자정 기준 일일 리셋 및 킬스위치 체크
        매 루프 시작 시 호출하여 날짜가 바뀌면 누적 PnL 초기화
        """
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        today_str = datetime.now(KST).strftime("%Y-%m-%d")

        if today_str != self.daily_reset_date:
            self.daily_reset_date = today_str
            self.daily_start_balance = current_balance
            self.daily_pnl_accumulated = 0.0
            if self.kill_switch_active and _time.time() >= self.kill_switch_until:
                self.kill_switch_active = False

    def record_daily_pnl(self, pnl_usdt):
        """
        [v2.2] 청산 마다 일일 누적 PnL에 반영
        킬스위치 발동 여부 리턴
        """
        self.daily_pnl_accumulated += pnl_usdt

        # 킬스위치 체크: 누적 손실이 시작 잔고의 7%를 초과하면 발동
        if self.daily_start_balance > 0:
            loss_pct = abs(self.daily_pnl_accumulated) / self.daily_start_balance
            if self.daily_pnl_accumulated < 0 and loss_pct >= self.daily_max_loss_pct:
                self.kill_switch_active = True
                self.kill_switch_until = _time.time() + 86400  # 24시간 락
                return True  # 킬스위치 발동!

        return False

    def save_state(self, set_config_fn):
        """[방어 상태 영속화] 킬스위치·쿨다운 상태를 SQLite에 저장 — 서버 재시작 후 복원 가능"""
        set_config_fn("strategy_ks_active", "1" if self.kill_switch_active else "0")
        set_config_fn("strategy_ks_until", str(self.kill_switch_until))
        set_config_fn("strategy_cd_until", str(self.loss_cooldown_until))
        set_config_fn("strategy_cd_count", str(self.consecutive_loss_count))

    def load_state(self, get_config_fn):
        """[방어 상태 복원] 서버 시작 시 SQLite에서 방어 상태 복원 — 만료된 상태는 무시"""
        try:
            now = _time.time()
            ks_until = float(get_config_fn("strategy_ks_until") or 0)
            cd_until = float(get_config_fn("strategy_cd_until") or 0)
            # 만료되지 않은 상태만 복원 (재시작 전 이미 해제된 잠금은 무시)
            if ks_until > now:
                self.kill_switch_active = (str(get_config_fn("strategy_ks_active")) == "1")
                self.kill_switch_until = ks_until
            if cd_until > now:
                self.loss_cooldown_until = cd_until
                self.consecutive_loss_count = int(get_config_fn("strategy_cd_count") or 0)
        except (TypeError, ValueError):
            pass  # DB 값 없거나 형식 오류 시 기본값 유지
