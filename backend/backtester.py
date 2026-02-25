import pandas as pd
from okx_engine import OKXEngine
from strategy import TradingStrategy
from logger import get_logger
from typing import Dict, List, Any

logger = get_logger(__name__)

class Backtester:
    """과거 데이터를 이용한 백테스팅 엔진"""

    def __init__(self, initial_seed: float = 100000.0, engine=None):
        self.initial_seed = initial_seed
        self.engine = engine if engine else OKXEngine()  # 싱글톤 주입 또는 자체 생성
        self.strategy = TradingStrategy(initial_seed=initial_seed)

    def run(self, symbol: str = "BTC/USDT:USDT", timeframe: str = "1m", limit: int = 100) -> Dict[str, Any]:
        """
        백테스팅 실행
        symbol: 거래 심볼
        timeframe: 캔들 타입 (1m, 5m, 15m, 1h 등)
        limit: 조회할 과거 캔들 수

        반환값: {
            total_trades: 총 거래 수,
            win_rate: 승률,
            total_pnl_percent: 총 수익률,
            max_drawdown: 최대 낙폭,
            sharpe_ratio: 샤프 지수,
            trades_log: 거래 상세 기록
        }
        """
        try:
            if not self.engine.exchange:
                logger.error("OKX 거래소 연결 실패")
                return {
                    'total_trades': 0,
                    'win_rate': 0,
                    'total_pnl_percent': 0,
                    'max_drawdown': 0,
                    'sharpe_ratio': 0,
                    'trades_log': []
                }

            logger.info(f"백테스팅 시작: {symbol} {timeframe} (한도: {limit})")

            # 과거 데이터 수집
            ohlcv = self.engine.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # 기술적 지표 계산
            df = self.strategy.calculate_indicators(df)

            trades_log = []
            position = None
            entry_price = 0.0
            entry_idx = 0
            balance = self.initial_seed
            balances = [self.initial_seed]
            highest_balance = self.initial_seed

            # 캔들별 시뮬레이션
            for idx in range(1, len(df)):
                current_price = df.iloc[idx]['close']
                # LONG: 고점(high) 추적 / SHORT: 저점(low) 추적 → trailing stop 정확도 보장
                if position == "LONG":
                    extreme_price = df.iloc[:idx+1]['high'].max()
                elif position == "SHORT":
                    extreme_price = df.iloc[:idx+1]['low'].min()
                else:
                    extreme_price = entry_price

                # 포지션 유지 중 - 리스크 관리
                if position:
                    risk_action = self.strategy.evaluate_risk_management(
                        entry_price, current_price, extreme_price, position
                    )

                    if risk_action != "KEEP":
                        # 포지션 청산
                        pnl = 0
                        if position == "LONG":
                            pnl = (current_price - entry_price) / entry_price
                        elif position == "SHORT":
                            pnl = (entry_price - current_price) / entry_price

                        pnl_percent = pnl * 100
                        balance = balance * (1 + pnl)

                        trades_log.append({
                            'entry_idx': entry_idx,
                            'entry_price': entry_price,
                            'exit_idx': idx,
                            'exit_price': current_price,
                            'position': position,
                            'pnl_percent': pnl_percent,
                            'exit_reason': risk_action,
                            'balance': balance
                        })

                        position = None
                        entry_price = 0.0

                    balances.append(balance)
                    highest_balance = max(highest_balance, balance)

                # 포지션 없을 때 - 진입 신호 체크
                if not position and idx >= 26:  # 지표 계산에 필요한 최소 캔들 수
                    signal, _ = self.strategy.check_entry_signal(df.iloc[:idx+1])

                    if signal in ["LONG", "SHORT"]:
                        position = signal
                        entry_price = current_price
                        entry_idx = idx

            # 통계 계산
            total_trades = len(trades_log)
            win_trades = len([t for t in trades_log if t['pnl_percent'] > 0])
            win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
            total_pnl_percent = ((balance - self.initial_seed) / self.initial_seed) * 100

            # Max Drawdown 계산
            max_drawdown = 0
            if balances:
                running_max = self.initial_seed
                for b in balances:
                    running_max = max(running_max, b)
                    drawdown = (running_max - b) / running_max
                    max_drawdown = max(max_drawdown, drawdown)

            # Sharpe Ratio 계산 (간단한 버전)
            sharpe_ratio = 0
            if len(balances) > 1:
                returns = [(balances[i] - balances[i-1]) / balances[i-1] for i in range(1, len(balances))]
                if returns:
                    import statistics
                    mean_return = statistics.mean(returns) * 252        # 연평균화
                    std_dev = (statistics.stdev(returns) * (252 ** 0.5)) if len(returns) > 1 else 0  # std도 연평균화
                    if std_dev > 0:
                        sharpe_ratio = mean_return / std_dev
                    else:
                        sharpe_ratio = 0

            logger.info(f"백테스팅 완료: {total_trades}거래, 승률 {win_rate:.1f}%, 수익률 {total_pnl_percent:.2f}%")

            return {
                'total_trades': total_trades,
                'win_rate': round(win_rate, 2),
                'total_pnl_percent': round(total_pnl_percent, 2),
                'max_drawdown': round(max_drawdown * 100, 2),
                'sharpe_ratio': round(sharpe_ratio, 2),
                'trades_log': trades_log
            }

        except Exception as e:
            logger.error(f"백테스팅 실행 중 오류: {e}")
            return {
                'total_trades': 0,
                'win_rate': 0,
                'total_pnl_percent': 0,
                'max_drawdown': 0,
                'sharpe_ratio': 0,
                'trades_log': [],
                'error': str(e)
            }
