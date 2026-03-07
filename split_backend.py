"""
split_backend.py — trading_loop.py 와 diagnostics.py 를 분리하는 스크립트
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BACKEND = r'C:\Users\ailee\github\okx\backend'

# ────────────────────────────────────────────────────────────
# 1. trading_loop.py 분리
# ────────────────────────────────────────────────────────────
with open(os.path.join(BACKEND, 'core', 'trading_loop.py'), encoding='utf-8') as f:
    tl_lines = f.readlines()

print(f"trading_loop.py: {len(tl_lines)} lines")

# 함수 경계 (1-indexed, 확인된 값)
# 35-213  : _detect_and_handle_manual_close
# 214-382 : execute_entry_order + _auto_tune_by_balance
# 383-end : async_trading_loop

close_handler_body   = tl_lines[34:213]   # lines 35-213
entry_executor_body  = tl_lines[213:382]   # lines 214-382
trading_loop_body    = tl_lines[382:]      # lines 383-end

# ── close_handler.py ──────────────────────────────────────
close_handler_header = '''\
"""
core/close_handler.py — 수동 청산 감지 및 PnL 정산 처리
  - _detect_and_handle_manual_close : OKX 체결 영수증 기반 수동청산 감지/정산/알림
"""
import asyncio
import time as _time

from database import get_config, save_trade
from notifier import send_telegram_sync
from logger import get_logger
from core.state import bot_global_state, _g
from core.helpers import _reset_position_state
from core.tg_formatters import _TG_LINE, _tg_manual_exit

logger = get_logger(__name__)


'''
with open(os.path.join(BACKEND, 'core', 'close_handler.py'), 'w', encoding='utf-8') as f:
    f.write(close_handler_header)
    f.writelines(close_handler_body)
print(f"  -> core/close_handler.py 생성: {len(close_handler_body)+close_handler_header.count(chr(10))} (approx) lines")

# ── entry_executor.py ──────────────────────────────────────
entry_executor_header = '''\
"""
core/entry_executor.py — 진입 주문 실행 + Adaptive Shield 잔고 기반 파라미터 튜닝
  - execute_entry_order   : 시장가/Smart Limit/Shadow 진입 주문
  - _auto_tune_by_balance : 잔고 티어별 파라미터 자동 전환
"""
import asyncio
import time as _time

from database import get_config
from notifier import send_telegram_sync
from logger import get_logger
from core.state import bot_global_state, _g, _loop_xray_state, BALANCE_TIERS
from core.helpers import _emit_thought, _log_trade_attempt
from core.tg_formatters import _tg_entry, _tg_pending

logger = get_logger(__name__)


'''
with open(os.path.join(BACKEND, 'core', 'entry_executor.py'), 'w', encoding='utf-8') as f:
    f.write(entry_executor_header)
    f.writelines(entry_executor_body)
print(f"  -> core/entry_executor.py 생성: {len(entry_executor_body)} lines (body)")

# ── trading_loop.py (async_trading_loop 만 잔류) ──────────
new_tl_header = '''\
"""
core/trading_loop.py — 메인 트레이딩 루프 (다중 심볼 백그라운드)
  - async_trading_loop : 실전 매매 메인 루프
  _detect_and_handle_manual_close → core.close_handler
  execute_entry_order / _auto_tune_by_balance → core.entry_executor
"""
import asyncio
import time as _time

from database import get_config, set_config, save_trade
from okx_engine import OKXEngine
from strategy import TradingStrategy
from notifier import send_telegram_sync
from logger import get_logger
from core.state import (
    bot_global_state, ai_brain_state, _g, state_lock,
    _loop_xray_state, BALANCE_TIERS,
    PRESET_GATE_CONFIGS, PRESET_DISPLAY, _PRESET_PRIORITY, _scalp_fitness_alert_state,
)
from core.helpers import (
    _reset_position_state, _emit_thought, _is_bypass_active, _save_strategy_state,
    _log_trade_attempt, _log_decision_trail, _finalize_pipeline, _calc_preset_fitness,
    _sym_short,
)
from core.tg_formatters import (
    _TG_LINE, _tg_entry, _tg_pending, _tg_exit, _tg_manual_exit,
    _tg_scanner, _tg_volume_spike, _tg_circuit_breaker,
)
from core.close_handler import _detect_and_handle_manual_close
from core.entry_executor import execute_entry_order, _auto_tune_by_balance

logger = get_logger(__name__)
_last_valid_balance = 0.0  # [방어 필터] 마지막 정상 잔고 (API 오류 방어용)


'''
with open(os.path.join(BACKEND, 'core', 'trading_loop.py'), 'w', encoding='utf-8') as f:
    f.write(new_tl_header)
    f.writelines(trading_loop_body)
print(f"  -> core/trading_loop.py 재작성: {len(trading_loop_body)} lines (body)")

# ────────────────────────────────────────────────────────────
# 2. diagnostics.py 분리
# ────────────────────────────────────────────────────────────
with open(os.path.join(BACKEND, 'routers', 'diagnostics.py'), encoding='utf-8') as f:
    diag_lines = f.readlines()

print(f"\ndiagnostics.py: {len(diag_lines)} lines")

# 745 줄부터 xray (0-indexed: 744)  // 748번째 줄 @router.get("/xray/loop_state")
# 분리 지점: line 744 까지 diagnostics, 745부터 xray
split_at = 744  # 0-indexed exclusive → lines 1-744 stay in diagnostics

diag_body = diag_lines[:split_at]
xray_body  = diag_lines[split_at:]   # 나머지 xray 함수들 (주석 포함)

# ── diagnostics.py 덮어쓰기 (xray 제거) ──────────────────
with open(os.path.join(BACKEND, 'routers', 'diagnostics.py'), 'w', encoding='utf-8') as f:
    f.writelines(diag_body)
print(f"  -> routers/diagnostics.py 재작성: {len(diag_body)} lines")

# ── xray.py 신규 생성 ──────────────────────────────────────
xray_header = '''\
"""
routers/xray.py — X-Ray 실시간 진단 엔드포인트
Routes: GET /xray/loop_state, /xray/blocker_wizard, /xray/trade_attempts,
        /xray/gate_scoreboard, /xray/okx_deep_verify
"""
import asyncio
import math
import time as _time
import datetime
from datetime import datetime as _dt

import pandas as pd
from fastapi import APIRouter

from database import get_config
from logger import get_logger
from strategy import TradingStrategy
from core.state import (
    bot_global_state, ai_brain_state, _g,
    _loop_xray_state, _trade_attempt_log, BALANCE_TIERS,
)

router = APIRouter()
logger = get_logger(__name__)


'''
with open(os.path.join(BACKEND, 'routers', 'xray.py'), 'w', encoding='utf-8') as f:
    f.write(xray_header)
    f.writelines(xray_body)
print(f"  -> routers/xray.py 생성: {len(xray_body)} lines (body)")

print("\n[완료] 스크립트 종료")
