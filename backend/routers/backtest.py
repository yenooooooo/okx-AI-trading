"""
routers/backtest.py — OHLCV, 백테스팅, 파라미터 최적화, 설정 이력
Routes: GET /ohlcv, POST /backtest, POST /optimize, POST /optimize/apply, GET /config/history
"""
import asyncio
import random
import time

import pandas as pd
from fastapi import APIRouter

from database import get_config, set_config, get_config_history
from logger import get_logger
from backtester import Backtester
from strategy import TradingStrategy
from notifier import send_telegram_sync
from core.state import bot_global_state, _g
from core.tg_formatters import _TG_LINE

router = APIRouter()
logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/ohlcv
# ════════════════════════════════════════════════════════════════════════════
@router.get("/ohlcv")
async def fetch_ohlcv(symbol: str = "BTC/USDT:USDT", limit: int = 100):
    """OHLCV 캔들 데이터 (차트용)"""
    _active_strategy = _g.get("strategy")
    try:
        engine = _g.get("engine")
        if not engine or not engine.exchange:
            return {"error": "거래소 연결 실패"}

        _bt_tf = str(get_config('timeframe') or '15m')
        ohlcv = await asyncio.to_thread(engine.exchange.fetch_ohlcv, symbol, _bt_tf, limit=limit)

        if not ohlcv or len(ohlcv) == 0:
            warn_msg = f"[차트 경고 🟡] [{symbol}] OKX 샌드박스가 OHLCV 데이터를 제공하지 않습니다. 가짜(Mock) 차트 데이터를 사용합니다."
            logger.warning(warn_msg)
            bot_global_state["logs"].append(warn_msg)
            current_time = int(time.time() * 1000)
            mock_ohlcv = []
            base_price = await asyncio.to_thread(engine.get_current_price, symbol)
            if not base_price:
                base_price = 50000.0

            for i in range(limit):
                ts = current_time - ((limit - i) * 60 * 1000)
                open_p = base_price + random.uniform(-10, 10)
                close_p = open_p + random.uniform(-15, 15)
                high_p = max(open_p, close_p) + random.uniform(1, 15)
                low_p = min(open_p, close_p) - random.uniform(1, 15)
                volume = random.uniform(0.1, 5.0)
                mock_ohlcv.append({
                    'timestamp': ts,
                    'open': round(open_p, 2),
                    'high': round(high_p, 2),
                    'low': round(low_p, 2),
                    'close': round(close_p, 2),
                    'volume': round(volume, 4)
                })
                base_price = close_p
            return mock_ohlcv

        # ── 지표 계산 (_active_strategy 있을 때만) ──
        try:
            if _active_strategy is not None:
                _df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    _df[col] = _df[col].astype(float)
                _df = _active_strategy.calculate_indicators(_df)

                result = []
                for _, row in _df.iterrows():
                    result.append({
                        'timestamp': int(row['timestamp']),
                        'open':   float(row['open']),
                        'high':   float(row['high']),
                        'low':    float(row['low']),
                        'close':  float(row['close']),
                        'volume': float(row['volume']),
                        'ema_20':      round(float(row['ema_20']),      4) if 'ema_20'      in _df.columns and not pd.isna(row['ema_20'])      else None,
                        'rsi':         round(float(row['rsi']),         2) if 'rsi'         in _df.columns and not pd.isna(row['rsi'])         else None,
                        'macd':        round(float(row['macd']),        6) if 'macd'        in _df.columns and not pd.isna(row['macd'])        else None,
                        'macd_signal': round(float(row['macd_signal']), 6) if 'macd_signal' in _df.columns and not pd.isna(row['macd_signal']) else None,
                    })
                return result
        except Exception as _ie:
            logger.warning(f"OHLCV 지표 계산 실패 (fallback): {_ie}")

        result = []
        for candle in ohlcv:
            result.append({
                'timestamp': int(candle[0]),
                'open': candle[1],
                'high': candle[2],
                'low': candle[3],
                'close': candle[4],
                'volume': candle[5]
            })
        return result
    except Exception as e:
        logger.error(f"OHLCV 조회 실패: {e}")
        return {"error": str(e)}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/backtest
# ════════════════════════════════════════════════════════════════════════════
@router.post("/backtest")
async def run_backtest(symbol: str = "BTC/USDT:USDT", timeframe: str = "1m", limit: int = 100, slippage_bps: float = 5.0):
    """백테스팅 실행 (슬리피지 시뮬레이션 포함)"""
    _engine = _g.get("engine")
    try:
        backtester = Backtester(initial_seed=75.0, engine=_engine, slippage_bps=slippage_bps)
        result = backtester.run(symbol=symbol, timeframe=timeframe, limit=limit)
        result["slippage_bps"] = slippage_bps
        return result
    except Exception as e:
        logger.error(f"백테스팅 실패: {e}")
        return {"error": str(e)}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/optimize
# ════════════════════════════════════════════════════════════════════════════
@router.post("/optimize")
async def run_optimizer(
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "15m",
    limit: int = 1000,
    slippage_bps: float = 5.0,
):
    """파라미터 그리드 서치 최적화 실행 (CPU-heavy → to_thread 격리)"""
    from optimizer import run_optimization
    _engine = _g.get("engine")

    try:
        current_config = {}
        _opt_keys = [
            'hard_stop_loss_rate', 'trailing_stop_activation', 'trailing_stop_rate',
            'adx_threshold', 'adx_max', 'chop_threshold',
            'volume_surge_multiplier', 'min_take_profit_rate',
        ]
        for _ok in _opt_keys:
            _ov = get_config(_ok)
            if _ov is not None:
                current_config[_ok] = _ov

        _seed = 75.0
        _seed_cfg = get_config('initial_seed')
        if _seed_cfg:
            try:
                _seed = float(_seed_cfg)
            except (ValueError, TypeError):
                pass

        result = await asyncio.to_thread(
            run_optimization,
            engine=_engine,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            slippage_bps=slippage_bps,
            initial_seed=_seed,
            selected_params=None,
            current_config=current_config,
        )

        result['current_config'] = current_config
        return result

    except Exception as e:
        logger.error(f"[Optimizer] API 호출 실패: {e}")
        return {"status": "error", "message": str(e), "recommendations": []}


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/optimize/apply
# ════════════════════════════════════════════════════════════════════════════
@router.post("/optimize/apply")
async def apply_optimization(rank: int = 1, params: str = ""):
    """최적화 추천안 적용 (rank 또는 직접 params JSON) — 포지션 보유 중 차단"""
    import json as _json
    _active_strategy = _g.get("strategy")

    try:
        _has_pos = any(
            s.get("position", "NONE") != "NONE"
            for s in bot_global_state.get("symbols", {}).values()
        )
        if _has_pos:
            return {
                "success": False,
                "message": "포지션 보유 중에는 파라미터 변경 불가 — 청산 후 재시도하세요",
            }

        if bot_global_state.get("is_running", False):
            logger.warning("[Optimizer] ⚠️ 봇 가동 중 파라미터 적용 — 다음 루프부터 즉시 반영됩니다.")

        if not params:
            return {"success": False, "message": "적용할 파라미터가 없습니다"}

        try:
            param_dict = _json.loads(params)
        except _json.JSONDecodeError:
            return {"success": False, "message": "파라미터 JSON 파싱 실패"}

        if not isinstance(param_dict, dict) or not param_dict:
            return {"success": False, "message": "유효한 파라미터 딕셔너리가 아닙니다"}

        from optimizer import PARAM_BOUNDS, _clamp
        applied = {}
        _prev_values = {}
        for pk, pv in param_dict.items():
            if pk not in PARAM_BOUNDS:
                continue
            try:
                val = float(pv)
                val = _clamp(val, pk)
            except (ValueError, TypeError):
                continue

            _prev_raw = get_config(pk)
            _prev_values[pk] = float(_prev_raw) if _prev_raw is not None else None

            set_config(pk, str(val))

            if _active_strategy and hasattr(_active_strategy, pk):
                try:
                    if pk == 'disparity_threshold':
                        setattr(_active_strategy, pk, val / 100.0)
                    else:
                        setattr(_active_strategy, pk, val)
                except (ValueError, TypeError):
                    pass

            applied[pk] = val

        if not applied:
            return {"success": False, "message": "적용 가능한 파라미터가 없습니다"}

        logger.info(f"[Optimizer] 추천안 적용 완료: {applied}")

        _tg_lines = [f"⚙️ <b>파라미터 최적화 적용</b>\n{_TG_LINE}"]
        for _ak, _av in applied.items():
            _prev = _prev_values.get(_ak)
            if _prev is not None:
                _tg_lines.append(f"  • {_ak}: {_prev} → <b>{_av}</b>")
            else:
                _tg_lines.append(f"  • {_ak}: <b>{_av}</b> (신규)")
        _tg_lines.append(f"{_TG_LINE}\n📌 다음 루프부터 즉시 반영됩니다.")
        try:
            send_telegram_sync("\n".join(_tg_lines))
        except Exception:
            pass

        return {"success": True, "applied": applied, "count": len(applied)}

    except Exception as e:
        logger.error(f"[Optimizer] 적용 실패: {e}")
        return {"success": False, "message": str(e)}


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/config/history
# ════════════════════════════════════════════════════════════════════════════
@router.get("/config/history")
async def fetch_config_history(limit: int = 50):
    """설정 변경 이력 조회 (READ-ONLY)"""
    try:
        history = get_config_history(limit=limit)
        return {"history": history, "count": len(history)}
    except Exception as e:
        logger.error(f"[ConfigHistory] 조회 실패: {e}")
        return {"history": [], "count": 0}
