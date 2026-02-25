import pandas as pd

class TradingStrategy:
    def __init__(self, initial_seed=100000):
        """
        매매 전략 및 시드 보호를 위한 핵심 파라미터 세팅
        기본 시드: 100,000원 (환율에 맞춰 USDT로 변환하여 관리될 예정)
        """
        self.initial_seed = initial_seed
        self.max_daily_loss_rate = 0.05       # 하루 최대 손실 5% 도달 시 매매 전면 중단 (서킷 브레이커)
        self.hard_stop_loss_rate = 0.02       # 진입가 대비 2% 하락 시 무조건 손절 (파산 원천 차단)
        self.trailing_stop_activation = 0.03  # 수익 3% 이상 도달 시 트레일링 스탑 활성화
        self.trailing_stop_rate = 0.01        # 고점 대비 1% 하락 시 익절 처리

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
        """
        if len(df) < 2:
            return "HOLD"
            
        latest = df.iloc[-1]
        previous = df.iloc[-2]
        
        # [매수(LONG) 타점]: 주가가 볼린저 밴드 하단 터치 또는 이탈 AND MACD 선이 Signal 선을 상향 돌파(골든크로스) AND RSI가 40 이하
        long_bb = latest['close'] <= latest['lower_band']
        long_macd = (latest['macd'] > latest['macd_signal']) and (previous['macd'] <= previous['macd_signal'])
        long_rsi = latest['rsi'] <= 40
        
        if long_bb and long_macd and long_rsi:
            return "LONG"
        
        # [매도(SHORT) 타점]: 주가가 볼린저 밴드 상단 터치 또는 돌파 AND MACD 선이 Signal 선을 하향 돌파(데드크로스) AND RSI가 60 이상
        short_bb = latest['close'] >= latest['upper_band']
        short_macd = (latest['macd'] < latest['macd_signal']) and (previous['macd'] >= previous['macd_signal'])
        short_rsi = latest['rsi'] >= 60
        
        if short_bb and short_macd and short_rsi:
            return "SHORT"
            
        return "HOLD"

    def evaluate_risk_management(self, entry_price, current_price, highest_price, position_side):
        """
        파산 방지 핵심 모듈: 현재 진행 중인 포지션의 강제 청산(손절/익절) 여부 반환
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
        if return_rate <= -self.hard_stop_loss_rate:
            return "STOP_LOSS"

        # 2. 트레일링 스탑 (Trailing Stop)
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
