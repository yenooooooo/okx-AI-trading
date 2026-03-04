"""
[Parameter Optimizer] 파라미터 그리드 서치 + 백테스트 기반 자동 추천 엔진
라이브 격리: TradingStrategy 독립 인스턴스 사용, _active_strategy 접근 불가
"""
import itertools
import time as _time
from typing import Dict, List, Any, Optional
from logger import get_logger

logger = get_logger(__name__)

# ═══════════ 파라미터 바운드 (절대 Min/Max — 위반 시 과적합 또는 위험) ═══════════
PARAM_BOUNDS = {
    'hard_stop_loss_rate':     {'min': 0.002, 'max': 0.02,  'steps': [0.003, 0.005, 0.008, 0.012]},
    'trailing_stop_activation': {'min': 0.002, 'max': 0.02,  'steps': [0.003, 0.005, 0.008, 0.012]},
    'trailing_stop_rate':      {'min': 0.001, 'max': 0.01,  'steps': [0.002, 0.003, 0.005, 0.007]},
    'adx_threshold':           {'min': 15.0,  'max': 35.0,  'steps': [20.0, 25.0, 28.0, 32.0]},
    'adx_max':                 {'min': 35.0,  'max': 65.0,  'steps': [40.0, 45.0, 50.0, 55.0]},
    'chop_threshold':          {'min': 45.0,  'max': 70.0,  'steps': [50.0, 55.0, 61.8, 65.0]},
    'volume_surge_multiplier': {'min': 1.0,   'max': 3.0,   'steps': [1.3, 1.5, 1.8, 2.2]},
    'min_take_profit_rate':    {'min': 0.005, 'max': 0.03,  'steps': [0.008, 0.01, 0.015, 0.02]},
}

# 최적화 쿨다운 (1시간)
_last_optimize_time = 0
OPTIMIZE_COOLDOWN_SEC = 3600

# 과적합 필터 기준
OVERFIT_WIN_RATE_MAX = 92.0      # 승률 92% 초과 → 과적합 폐기
OVERFIT_MDD_MIN = 0.01           # MDD 0.01% 미만 → 비현실적 폐기
MIN_TRADES_FOR_VALIDITY = 5      # 최소 5건 이상 거래 필요


def _clamp(value: float, param_name: str) -> float:
    """파라미터 바운드 강제 적용"""
    bounds = PARAM_BOUNDS.get(param_name)
    if not bounds:
        return value
    return max(bounds['min'], min(bounds['max'], value))


def generate_grid(selected_params: List[str] = None) -> List[Dict[str, float]]:
    """
    선택된 파라미터에 대한 그리드 조합 생성
    selected_params가 None이면 전체 파라미터 대상
    조합 수 제한: 최대 600개 (CPU 보호)
    """
    if selected_params is None:
        selected_params = list(PARAM_BOUNDS.keys())

    # 유효한 파라미터만 필터
    valid_params = [p for p in selected_params if p in PARAM_BOUNDS]
    if not valid_params:
        return []

    # 조합 수가 600 초과 시 각 파라미터의 steps 수를 줄임
    param_steps = {}
    for p in valid_params:
        param_steps[p] = PARAM_BOUNDS[p]['steps']

    total_combos = 1
    for steps in param_steps.values():
        total_combos *= len(steps)

    # 600개 초과 시 각 파라미터를 3단계로 축소
    if total_combos > 600:
        for p in param_steps:
            steps = param_steps[p]
            if len(steps) > 3:
                param_steps[p] = [steps[0], steps[len(steps) // 2], steps[-1]]

    # 그리드 생성
    keys = list(param_steps.keys())
    values_list = [param_steps[k] for k in keys]
    grid = []
    for combo in itertools.product(*values_list):
        grid.append(dict(zip(keys, combo)))

    return grid


def run_optimization(
    engine,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "15m",
    limit: int = 1000,
    slippage_bps: float = 5.0,
    initial_seed: float = 75.0,
    selected_params: List[str] = None,
    current_config: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    그리드 서치 최적화 실행 (동기 함수 — asyncio.to_thread로 호출할 것)

    Returns: {
        'status': 'success' | 'cooldown' | 'error',
        'recommendations': [...],  # TOP 3 추천안
        'total_tested': int,
        'elapsed_sec': float,
        'current_config': {...},   # 현재 설정 (비교용)
    }
    """
    global _last_optimize_time
    import pandas as pd
    from strategy import TradingStrategy

    # ── 쿨다운 체크 ──
    now = _time.time()
    if now - _last_optimize_time < OPTIMIZE_COOLDOWN_SEC:
        remaining = int(OPTIMIZE_COOLDOWN_SEC - (now - _last_optimize_time))
        return {
            'status': 'cooldown',
            'message': f'최적화 쿨다운 중 ({remaining}초 후 재실행 가능)',
            'remaining_sec': remaining,
            'recommendations': [],
        }

    _last_optimize_time = now
    start_time = _time.time()

    try:
        # ── OHLCV 데이터 1회 로드 (전 조합 공유) ──
        if not engine or not engine.exchange:
            return {'status': 'error', 'message': 'OKXEngine 미초기화', 'recommendations': []}

        logger.info(f"[Optimizer] 최적화 시작: {symbol} {timeframe} ({limit}봉)")
        ohlcv = engine.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df_raw = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        if len(df_raw) < 100:
            return {'status': 'error', 'message': f'데이터 부족 ({len(df_raw)}봉)', 'recommendations': []}

        # ── 그리드 생성 ──
        grid = generate_grid(selected_params)
        if not grid:
            return {'status': 'error', 'message': '유효한 파라미터 없음', 'recommendations': []}

        logger.info(f"[Optimizer] 그리드 {len(grid)}개 조합 테스트 시작")

        # ── 현재 설정값 (비교 기준) ──
        if current_config is None:
            current_config = {}

        results = []

        for idx, params in enumerate(grid):
            try:
                # 격리된 Strategy 인스턴스 생성 (라이브와 무관)
                strategy = TradingStrategy(initial_seed=initial_seed)

                # 그리드 파라미터 주입
                for param_name, param_val in params.items():
                    clamped = _clamp(param_val, param_name)
                    if hasattr(strategy, param_name):
                        setattr(strategy, param_name, clamped)

                # 나머지 파라미터는 현재 설정값 유지 (그리드 대상 아닌 것)
                for cfg_key, cfg_val in current_config.items():
                    if cfg_key not in params and hasattr(strategy, cfg_key):
                        try:
                            setattr(strategy, cfg_key, float(cfg_val))
                        except (ValueError, TypeError):
                            pass

                # 지표 계산 (df 복사본 사용)
                df = df_raw.copy()
                df = strategy.calculate_indicators(df)

                # 백테스트 시뮬레이션
                bt_result = _run_single_backtest(strategy, df, initial_seed, slippage_bps)

                # 과적합 필터
                if bt_result['total_trades'] < MIN_TRADES_FOR_VALIDITY:
                    continue
                if bt_result['win_rate'] > OVERFIT_WIN_RATE_MAX:
                    continue
                if bt_result['max_drawdown'] < OVERFIT_MDD_MIN and bt_result['total_trades'] > 3:
                    continue

                results.append({
                    'params': params,
                    'total_trades': bt_result['total_trades'],
                    'win_rate': bt_result['win_rate'],
                    'total_pnl_percent': bt_result['total_pnl_percent'],
                    'max_drawdown': bt_result['max_drawdown'],
                    'sharpe_ratio': bt_result['sharpe_ratio'],
                    # 복합 스코어: Sharpe × (1 - MDD/100) × 승률가중
                    'score': bt_result['sharpe_ratio'] * (1 - bt_result['max_drawdown'] / 100) * (bt_result['win_rate'] / 50),
                })

            except Exception as e:
                logger.debug(f"[Optimizer] 조합 #{idx} 실패: {e}")
                continue

        # ── 결과 정렬 및 TOP 3 추출 ──
        results.sort(key=lambda x: x['score'], reverse=True)
        top_3 = results[:3]

        # 추천안에 현재값 대비 변경점 표시
        recommendations = []
        for rank, r in enumerate(top_3, 1):
            diffs = {}
            for param_name, param_val in r['params'].items():
                current_val = current_config.get(param_name)
                if current_val is not None:
                    try:
                        cv = float(current_val)
                        if abs(cv - param_val) > 1e-6:
                            diffs[param_name] = {
                                'current': cv,
                                'recommended': param_val,
                                'direction': 'UP' if param_val > cv else 'DOWN',
                            }
                    except (ValueError, TypeError):
                        diffs[param_name] = {'current': str(current_val), 'recommended': param_val, 'direction': 'NEW'}
                else:
                    diffs[param_name] = {'current': None, 'recommended': param_val, 'direction': 'NEW'}

            recommendations.append({
                'rank': rank,
                'params': r['params'],
                'diffs': diffs,
                'total_trades': r['total_trades'],
                'win_rate': round(r['win_rate'], 2),
                'total_pnl_percent': round(r['total_pnl_percent'], 2),
                'max_drawdown': round(r['max_drawdown'], 2),
                'sharpe_ratio': round(r['sharpe_ratio'], 2),
                'score': round(r['score'], 3),
            })

        elapsed = round(_time.time() - start_time, 1)
        logger.info(f"[Optimizer] 최적화 완료: {len(results)}/{len(grid)}개 유효 | {elapsed}초 소요")

        return {
            'status': 'success',
            'recommendations': recommendations,
            'total_tested': len(grid),
            'total_valid': len(results),
            'elapsed_sec': elapsed,
            'symbol': symbol,
            'timeframe': timeframe,
            'candles': limit,
        }

    except Exception as e:
        logger.error(f"[Optimizer] 최적화 실패: {e}")
        _last_optimize_time = 0  # 실패 시 쿨다운 리셋
        return {'status': 'error', 'message': str(e), 'recommendations': []}


def _run_single_backtest(strategy, df, initial_seed: float, slippage_bps: float) -> Dict[str, Any]:
    """단일 파라미터 조합 백테스트 (경량 버전 — candles/markers 생략)"""
    import pandas as pd

    factor = slippage_bps / 10000.0
    position = None
    entry_price = 0.0
    balance = initial_seed
    balances = [initial_seed]
    trades_count = 0
    wins = 0

    for idx in range(50, len(df)):
        current_price = df.iloc[idx]['close']

        if position == "LONG":
            extreme_price = df.iloc[:idx + 1]['high'].max()
        elif position == "SHORT":
            extreme_price = df.iloc[:idx + 1]['low'].min()
        else:
            extreme_price = entry_price

        # 포지션 보유 중 → 리스크 관리
        if position:
            _atr = float(df.iloc[idx]['atr']) if 'atr' in df.columns and not pd.isna(df.iloc[idx]['atr']) else entry_price * 0.01
            if _atr <= 0:
                _atr = entry_price * 0.01
            action, _, _, _ = strategy.evaluate_risk_management(
                entry_price, current_price, extreme_price, position, _atr
            )
            if action != "KEEP":
                exit_price = current_price * (1 - factor) if position == "LONG" else current_price * (1 + factor)
                pnl = (exit_price - entry_price) / entry_price if position == "LONG" else (entry_price - exit_price) / entry_price
                balance *= (1 + pnl)
                trades_count += 1
                if pnl > 0:
                    wins += 1
                position = None
                entry_price = 0.0

            balances.append(balance)

        # 포지션 없음 → 진입 시그널
        if not position and idx >= 50:
            signal, _, _ = strategy.check_entry_signal(df.iloc[:idx + 1], current_price=current_price)
            if signal in ("LONG", "SHORT"):
                position = signal
                entry_price = current_price * (1 + factor) if signal == "LONG" else current_price * (1 - factor)

    # 잔여 포지션 강제 청산
    if position:
        last_price = df.iloc[-1]['close']
        exit_price = last_price * (1 - factor) if position == "LONG" else last_price * (1 + factor)
        pnl = (exit_price - entry_price) / entry_price if position == "LONG" else (entry_price - exit_price) / entry_price
        balance *= (1 + pnl)
        trades_count += 1
        if pnl > 0:
            wins += 1

    # 통계
    win_rate = (wins / trades_count * 100) if trades_count > 0 else 0
    total_pnl_pct = ((balance - initial_seed) / initial_seed) * 100

    # MDD
    max_dd = 0
    running_max = initial_seed
    for b in balances:
        running_max = max(running_max, b)
        dd = (running_max - b) / running_max if running_max > 0 else 0
        max_dd = max(max_dd, dd)

    # Sharpe
    sharpe = 0
    if len(balances) > 1:
        returns = [(balances[i] - balances[i - 1]) / balances[i - 1] for i in range(1, len(balances)) if balances[i - 1] > 0]
        if len(returns) > 1:
            import statistics
            mean_r = statistics.mean(returns) * 252
            std_r = statistics.stdev(returns) * (252 ** 0.5)
            if std_r > 0:
                sharpe = mean_r / std_r

    return {
        'total_trades': trades_count,
        'win_rate': round(win_rate, 2),
        'total_pnl_percent': round(total_pnl_pct, 2),
        'max_drawdown': round(max_dd * 100, 2),
        'sharpe_ratio': round(sharpe, 2),
    }
