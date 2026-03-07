"""
core/state.py — 전역 상태 + 상수 + 싱글톤 레퍼런스
모든 모듈이 여기서 상태를 import. 순환 임포트 방지를 위해 외부 모듈 의존성 없음.
"""
import asyncio
import time as _time

# ════════════════════════════════════════════════════════════════════════════
# 싱글톤 뮤터블 레퍼런스 (global 키워드 없이 크로스-모듈 공유)
# ════════════════════════════════════════════════════════════════════════════
# 사용법: from core.state import _g
#         _g["engine"] = OKXEngine()   (쓰기)
#         eng = _g["engine"]           (읽기)
_g = {
    "engine": None,            # OKXEngine 싱글톤
    "strategy": None,          # TradingStrategy 활성 인스턴스
    "trading_task": None,      # 메인 트레이딩 루프 태스크
    "broadcast_task": None,    # WS 브로드캐스트 태스크
    "private_ws_task": None,   # OKX Private WS 태스크
    "margin_guard_bg_task": None,
    "trade_sync_task": None,
    "heartbeat_task": None,
}

# ════════════════════════════════════════════════════════════════════════════
# LogList — 순환 300개 제한 + DB 자동 저장 (database.save_log 지연 임포트)
# ════════════════════════════════════════════════════════════════════════════
class LogList(list):
    def append(self, msg):
        super().append(msg)
        if len(self) > 300:
            self.pop(0)
        lvl = "ERROR" if "[오류]" in msg or "실패" in msg else "INFO"
        # DB 저장 실패 시 1회 재시도 — 묵살 방지
        for _attempt in range(2):
            try:
                from database import save_log
                from logger import get_logger
                save_log(level=lvl, message=msg)
                break
            except Exception as e:
                if _attempt == 1:
                    try:
                        get_logger(__name__).error(
                            f"DB 저장 최종 실패 (로그 유실): {e} | msg={msg[:80]}"
                        )
                    except Exception:
                        pass

# ════════════════════════════════════════════════════════════════════════════
# 전역 봇 상태 (다중 심볼 지원)
# ════════════════════════════════════════════════════════════════════════════
bot_global_state = {
    "is_running": False,
    "balance": 0.0,
    "symbols": {},
    "logs": LogList(["[봇] 시스템 코어 초기화 완료 - API 브릿지 대기 중"]),
    "stress_inject": None,
}

# ════════════════════════════════════════════════════════════════════════════
# AI Brain 상태 (7-Gate 분석 결과)
# ════════════════════════════════════════════════════════════════════════════
ai_brain_state = {
    "symbols": {}  # symbol별 뇌 상태
}
_scalp_fitness_alert_state = {}   # 프리셋 추천 TG 알림 쿨다운
_thought_throttle = {}             # [Consciousness Stream] 반복 사고 쓰로틀

trade_history = []

# ════════════════════════════════════════════════════════════════════════════
# [X-Ray] 매매 진단 시스템
# ════════════════════════════════════════════════════════════════════════════
_trade_attempt_log = []   # 매매 시도 이력 링 버퍼 (최대 50건)
_TRADE_ATTEMPT_MAX = 50
_loop_xray_state = {
    "last_scan_time": 0,
    "last_entry_attempt_time": 0,
    "last_entry_attempt_symbol": "",
    "last_entry_attempt_result": "",
    "last_entry_attempt_reason": "",
    "last_successful_entry_time": 0,
    "last_successful_entry_symbol": "",
    "loop_cycle_count": 0,
}

# ════════════════════════════════════════════════════════════════════════════
# [Flight Recorder] Decision Trail 링 버퍼
# ════════════════════════════════════════════════════════════════════════════
_decision_trail_log = []
_DECISION_TRAIL_MAX = 20
_ALL_PIPELINE_STEPS = [
    "active_target", "direction_mode", "position_sizing",
    "micro_account", "margin_check", "order_execution"
]

# [Phase 20.1] 동시성 충돌 방어용 Mutex Lock
state_lock = asyncio.Lock()

# ════════════════════════════════════════════════════════════════════════════
# [Heartbeat Monitor] 이전 상태 추적
# ════════════════════════════════════════════════════════════════════════════
_heartbeat_prev_status = {}   # 이전 상태 기억 (장애→복구 전환 감지)
_heartbeat_fail_streak = {}   # 연속 FAIL 카운터 (오탐 방지)

# ════════════════════════════════════════════════════════════════════════════
# [Phase 25] Adaptive Shield — 잔고 기반 자동 방어 티어
# ════════════════════════════════════════════════════════════════════════════
BALANCE_TIERS = {
    'CRITICAL': {
        'max_balance': 20,
        'config': {
            'exit_only_mode': 'true', 'risk_per_trade': '0.005',
            'hard_stop_loss_rate': '0.003', 'trailing_stop_activation': '0.005',
            'trailing_stop_rate': '0.003', 'min_take_profit_rate': '0.008',
            'adx_threshold': '30.0', 'adx_max': '45.0', 'chop_threshold': '50.0',
            'volume_surge_multiplier': '2.0', 'fee_margin': '0.002',
            'cooldown_losses_trigger': '1', 'cooldown_duration_sec': '3600',
            'daily_max_loss_rate': '0.03',
        },
        'emoji': '🔴', 'description': '긴급 방어 — 신규 진입 차단, 기존 포지션만 관리',
    },
    'MICRO': {
        'max_balance': 100,
        'config': {
            'exit_only_mode': 'false', 'risk_per_trade': '0.01',
            'hard_stop_loss_rate': '0.005', 'trailing_stop_activation': '0.003',
            'trailing_stop_rate': '0.002', 'min_take_profit_rate': '0.006',
            'adx_threshold': '28.0', 'adx_max': '50.0', 'chop_threshold': '55.0',
            'volume_surge_multiplier': '1.8', 'fee_margin': '0.002',
            'cooldown_losses_trigger': '2', 'cooldown_duration_sec': '1800',
            'daily_max_loss_rate': '0.05',
        },
        'emoji': '🟡', 'description': '소액 보호 — 빠른 트레일링 추적, 수수료 포함 본전 보장',
    },
    'STANDARD': {
        'max_balance': 500,
        'config': {
            'exit_only_mode': 'false', 'risk_per_trade': '0.015',
            'hard_stop_loss_rate': '0.006', 'trailing_stop_activation': '0.005',
            'trailing_stop_rate': '0.003', 'min_take_profit_rate': '0.008',
            'adx_threshold': '30.0', 'adx_max': '45.0', 'chop_threshold': '55.0',
            'volume_surge_multiplier': '2.0', 'fee_margin': '0.002',
            'cooldown_losses_trigger': '2', 'cooldown_duration_sec': '1800',
            'daily_max_loss_rate': '0.05',
        },
        'emoji': '🟢', 'description': '표준 운용 — 정밀 스나이퍼 진입',
    },
    'GROWTH': {
        'max_balance': float('inf'),
        'config': {
            'exit_only_mode': 'false', 'risk_per_trade': '0.02',
            'hard_stop_loss_rate': '0.008', 'trailing_stop_activation': '0.005',
            'trailing_stop_rate': '0.004', 'min_take_profit_rate': '0.008',
            'adx_threshold': '25.0', 'adx_max': '60.0', 'chop_threshold': '58.0',
            'volume_surge_multiplier': '1.3', 'fee_margin': '0.001',
            'cooldown_losses_trigger': '4', 'cooldown_duration_sec': '600',
            'daily_max_loss_rate': '0.07',
        },
        'emoji': '🔵', 'description': '성장 추세 추종 — 넓은 트레일링, 수익 극대화',
    },
}

# ════════════════════════════════════════════════════════════════════════════
# [Phase TF] 타임프레임별 매매 파라미터 프리셋
# ════════════════════════════════════════════════════════════════════════════
TIMEFRAME_PRESETS = {
    '5m': {
        'hard_stop_loss_rate': '0.003',
        'trailing_stop_activation': '0.002',
        'trailing_stop_rate': '0.0015',
        'min_take_profit_rate': '0.005',
        'adx_threshold': '28.0',
        'adx_max': '45.0',
        'chop_threshold': '55.0',
        'volume_surge_multiplier': '1.8',
        'disparity_threshold': '1.0',
        'cooldown_duration_sec': '300',
    },
    '15m': {
        'hard_stop_loss_rate': '0.005',
        'trailing_stop_activation': '0.003',
        'trailing_stop_rate': '0.002',
        'min_take_profit_rate': '0.01',
        'adx_threshold': '25.0',
        'adx_max': '40.0',
        'chop_threshold': '61.8',
        'volume_surge_multiplier': '1.5',
        'disparity_threshold': '0.8',
        'cooldown_duration_sec': '900',
    },
}
ALLOWED_TIMEFRAMES = {'5m', '15m'}

# ════════════════════════════════════════════════════════════════════════════
# [프리셋 추천] 게이트 기준값 + 표시 정보
# ════════════════════════════════════════════════════════════════════════════
PRESET_GATE_CONFIGS = {
    'sniper':        {'adx_min': 30, 'adx_max': 45,  'chop_max': 55,   'vol_min': 2.0, 'bypass_macro': False, 'bypass_indicator': False},
    'trend_rider':   {'adx_min': 25, 'adx_max': 60,  'chop_max': 58,   'vol_min': 1.3, 'bypass_macro': False, 'bypass_indicator': False},
    'scalper':       {'adx_min': 20, 'adx_max': 50,  'chop_max': 65,   'vol_min': 1.2, 'bypass_macro': False, 'bypass_indicator': False},
    'iron_dome':     {'adx_min': 28, 'adx_max': 42,  'chop_max': 50,   'vol_min': 2.5, 'bypass_macro': False, 'bypass_indicator': False},
    'frenzy':        {'adx_min': 15, 'adx_max': 999, 'chop_max': 60,   'vol_min': 1.2, 'bypass_macro': True,  'bypass_indicator': True},
    'micro_seed':    {'adx_min': 28, 'adx_max': 50,  'chop_max': 55,   'vol_min': 1.8, 'bypass_macro': False, 'bypass_indicator': False},
    'scalp_context': {'adx_min': 20, 'adx_max': 50,  'chop_max': 61.8, 'vol_min': 1.2, 'bypass_macro': False, 'bypass_indicator': True},
    'factory_reset': {'adx_min': 25, 'adx_max': 40,  'chop_max': 61.8, 'vol_min': 1.5, 'bypass_macro': False, 'bypass_indicator': False},
}

PRESET_DISPLAY = {
    'sniper': ('🎯', '스나이퍼'), 'trend_rider': ('🌊', '트렌드라이더'),
    'scalper': ('⚡', '스캘퍼'), 'iron_dome': ('🛡️', '아이언돔'),
    'frenzy': ('🔥', 'FRENZY'), 'micro_seed': ('💎', '마이크로시드'),
    'scalp_context': ('📡', '스캘프CTX'), 'factory_reset': ('🔄', '팩토리리셋'),
}

# 추천 우선순위 (동점 시: 보수적 프리셋 우선)
_PRESET_PRIORITY = ['sniper', 'iron_dome', 'micro_seed', 'trend_rider', 'scalp_context', 'scalper', 'factory_reset', 'frenzy']
