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

        return df

    def check_entry_signal(self, df):
        """
        가장 최근 캔들을 분석하여 매수/매도 진입 시그널 판단
        공격적 다중 지표 (Multi-Indicators) 적용
        반환값: (진입신호, 상태메세지) 형태의 튜플
        """
        if len(df) < 2:
            return "HOLD", "데이터 부족 대기"
            
        latest = df.iloc[-1]
        previous = df.iloc[-2]
        
        rsi_val = latest['rsi']
        macd_val = latest['macd']
        
        # [테스트 모드] 진입 조건 완화: BB 제거, RSI 범위 확대, MACD 크로스만 유지
        # 원본 LONG:  BB하단 AND MACD골든크로스 AND RSI<=40
        # 원본 SHORT: BB상단 AND MACD데드크로스 AND RSI>=60
        long_macd = (latest['macd'] > latest['macd_signal']) and (previous['macd'] <= previous['macd_signal'])
        long_rsi = latest['rsi'] <= 55   # [테스트] 원본: 40

        if long_macd and long_rsi:
            return "LONG", f"상승 감지 (RSI {rsi_val:.1f}, MACD 상향 돌파)"

        short_macd = (latest['macd'] < latest['macd_signal']) and (previous['macd'] >= previous['macd_signal'])
        short_rsi = latest['rsi'] >= 45  # [테스트] 원본: 60

        if short_macd and short_rsi:
            return "SHORT", f"하락 감지 (RSI {rsi_val:.1f}, MACD 하향 돌파)"
            
        return "HOLD", f"현재 RSI {rsi_val:.1f} / MACD {macd_val:.2f} - 타점 탐색 중"

    def evaluate_risk_management(self, entry_price, current_price, highest_price, position_side, symbol="BTC/USDT:USDT"):
        """
        파산 방지 핵심 모듈: 현재 진행 중인 포지션의 강제 청산(손절/익절) 여부 반환
        비트코인과 알트코인의 변동성을 다르게 고려하여 하드 스탑로스를 차등 적용합니다.
        """
        if position_side == "LONG":
            return_rate = (current_price - entry_price) / entry_price
            drawdown_from_high = (highest_price - current_price) / highest_price
        elif position_side == "SHORT":
            return_rate = (entry_price - current_price) / entry_price
            drawdown_from_high = (current_price - highest_price) / highest_price
        else:
            return "KEEP"

        # 1. 하드 스탑로스 (Hard Stop-loss)
        # 알트코인은 변동성이 극심하므로(Whipsaw) 스탑로스를 5~7% 수준으로 더 넓혀서 방어합니다.
        dynamic_sl_rate = self.hard_stop_loss_rate
        if not symbol.startswith("BTC"):
            dynamic_sl_rate = max(0.05, dynamic_sl_rate * 3) # 알트코인은 최소 5% 보장, 혹은 기존 설정의 3배 확장

        if return_rate <= -dynamic_sl_rate:
            return "STOP_LOSS"

        # 2. 트레일링 스탑 (Trailing Stop)
        # 익절 활성화 라인이나 추적 폭도 알트코인 특성에 맞춰 변형할 수 있으나, 현재는 수익 확보를 위해 동일 적용
        if return_rate >= self.trailing_stop_activation:
            if drawdown_from_high >= self.trailing_stop_rate:
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
