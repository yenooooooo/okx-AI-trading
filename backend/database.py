import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), 'trading_data.db')

def get_connection():
    """SQLite 데이터베이스 연결"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """데이터베이스 초기화 및 테이블 생성"""
    conn = get_connection()
    cursor = conn.cursor()

    # trades 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            position_type TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            entry_time TIMESTAMP NOT NULL,
            exit_time TIMESTAMP,
            pnl REAL,
            pnl_percent REAL,
            fee REAL DEFAULT 0.0,
            gross_pnl REAL DEFAULT 0.0,
            amount REAL NOT NULL,
            exit_reason TEXT,
            leverage INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 기존 DB 호환용 컬럼 추가 (fee, gross_pnl)
    try:
        cursor.execute('ALTER TABLE trades ADD COLUMN fee REAL DEFAULT 0.0')
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute('ALTER TABLE trades ADD COLUMN gross_pnl REAL DEFAULT 0.0')
    except sqlite3.OperationalError:
        pass

    # OKX 싱크 전용 컬럼 마이그레이션
    try:
        cursor.execute('ALTER TABLE trades ADD COLUMN okx_order_id TEXT')
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute('ALTER TABLE trades ADD COLUMN source TEXT DEFAULT "BOT"')
    except sqlite3.OperationalError:
        pass

    # [Phase TF] 타임프레임 컬럼 마이그레이션
    try:
        cursor.execute('ALTER TABLE trades ADD COLUMN timeframe TEXT DEFAULT "15m"')
    except sqlite3.OperationalError:
        pass

    # bot_config 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # system_logs 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # config_history 테이블 (설정 변경 이력 추적)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT NOT NULL,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()

    # 기본 설정값 초기화 (INSERT OR IGNORE: 기존 값 유지, 신규 키만 추가)
    default_config = {
        'symbols': json.dumps(['BTC/USDT:USDT']),
        'risk_per_trade': '0.01',
        'hard_stop_loss_rate': '0.005',
        'trailing_stop_activation': '0.003',
        'trailing_stop_rate': '0.002',
        'daily_max_loss_rate': '0.05',
        'timeframe': '15m',  # [Phase 24] 5m → 15m (소액 계좌 노이즈 필터링 강화)
        'leverage': '1',
        'telegram_enabled': 'false',
        'manual_override_enabled': 'false',
        'manual_amount': '1',
        'manual_leverage': '1',
        'ENTRY_ORDER_TYPE': 'Market',
        'adx_threshold': '25.0',
        'adx_max': '40.0',
        'chop_threshold': '61.8',
        'volume_surge_multiplier': '1.5',
        'fee_margin': '0.0015',
        'cooldown_losses_trigger': '3',
        'cooldown_duration_sec': '900',
        'auto_scan_enabled': 'false',
        'direction_mode': 'AUTO',  # [Phase 18.1] 방향 모드 (AUTO/LONG/SHORT)
        'exit_only_mode': 'false', # [Phase 19] 퇴근 모드 (Exit-Only)
        'shadow_hunting_enabled': 'false',  # [Phase 23] 그림자 사냥(Shadow Hunting) 모드
        'SHADOW_MODE_ENABLED': 'false',      # [Phase 30] 섀도우(Paper) 모드 기본값
        'min_take_profit_rate': '0.01',   # [Phase 24] 최소 익절 목표율 1.0% (R:R 1:2 강제)
        'auto_preset_enabled': 'true',    # [Phase 25] Adaptive Shield 기본 활성화
        '_current_adaptive_tier': '',     # [Phase 25] 현재 적용 중인 방어 티어
        # [Phase 21.2] 스트레스 테스트 바이패스 (값 = 활성화 타임스탬프, "0" = 비활성)
        'stress_bypass_kill_switch': '0',
        'stress_bypass_cooldown_loss': '0',
        'stress_bypass_daily_loss': '0',
        'stress_bypass_reentry_cd': '0',
        'stress_bypass_stale_price': '0',
    }
    for key, value in default_config.items():
        cursor.execute('INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)', (key, value))
    conn.commit()

    conn.close()

def save_trade(symbol: str, position_type: str, entry_price: float, amount: float,
               exit_price: float = None, pnl: float = None, pnl_percent: float = None,
               fee: float = 0.0, gross_pnl: float = 0.0,
               exit_reason: str = None, leverage: int = 1,
               entry_time=None, exit_time=None,
               okx_order_id: str = None, source: str = 'BOT',
               timeframe: str = '15m'):
    """거래 기록 저장 (OKX 싱크 시 entry_time/exit_time/okx_order_id/source 직접 지정 가능)"""
    conn = get_connection()
    cursor = conn.cursor()

    entry_time = entry_time or datetime.now()
    exit_time = exit_time or (datetime.now() if exit_price else None)
    # created_at: 싱크 시 실제 거래 날짜로 일별 통계 정확 집계
    created_at = exit_time or entry_time

    cursor.execute('''
        INSERT INTO trades
        (symbol, position_type, entry_price, exit_price, entry_time, exit_time,
         pnl, pnl_percent, fee, gross_pnl, amount, exit_reason, leverage,
         okx_order_id, source, created_at, timeframe)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (symbol, position_type, entry_price, exit_price, entry_time, exit_time,
          pnl, pnl_percent, fee, gross_pnl, amount, exit_reason, leverage,
          okx_order_id, source, created_at, timeframe))

    conn.commit()
    trade_id = cursor.lastrowid
    conn.close()

    return trade_id

def get_trades(limit: int = 100, symbol: str = None) -> List[Dict]:
    """거래 기록 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    if symbol:
        cursor.execute('SELECT * FROM trades WHERE symbol = ? ORDER BY created_at DESC LIMIT ?', (symbol, limit))
    else:
        cursor.execute('SELECT * FROM trades ORDER BY created_at DESC LIMIT ?', (limit,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def get_config(key: str = None, symbol: str = "GLOBAL") -> Any:
    """설정 조회. symbol 지정 시 심볼 전용 값 우선, 없으면 GLOBAL 값으로 Fallback (하위 호환 100% 보장)"""
    conn = get_connection()
    cursor = conn.cursor()

    if key:
        # 1차: 심볼 전용 키 (`SYMBOL::KEY`) 조회
        actual_key = f"{symbol}::{key}" if symbol != "GLOBAL" else key
        cursor.execute('SELECT value FROM bot_config WHERE key = ?', (actual_key,))
        row = cursor.fetchone()
        # 2차: 심볼 전용 키가 없으면 GLOBAL 키로 Fallback (기존 동작 완전 보존)
        if not row and symbol != "GLOBAL":
            cursor.execute('SELECT value FROM bot_config WHERE key = ?', (key,))
            row = cursor.fetchone()
        conn.close()
        if row:
            value = row[0]
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return None
    else:
        # 모든 설정 반환 (심볼 접두 키 `SYMBOL::KEY` 제외하여 GLOBAL 설정만 반환)
        cursor.execute("SELECT key, value FROM bot_config WHERE key NOT LIKE '%::%'")
        rows = cursor.fetchall()
        conn.close()
        config = {}
        for row in rows:
            try:
                config[row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                config[row[0]] = row[1]
        return config

def set_config(key: str, value: Any, symbol: str = "GLOBAL"):
    """설정 저장. symbol 지정 시 `SYMBOL::KEY` 형식으로 심볼 전용 저장 (GLOBAL이면 기존 방식)"""
    conn = get_connection()
    cursor = conn.cursor()

    # 저장 키 합성 (심볼 전용이면 `SYMBOL::KEY` 형식)
    actual_key = f"{symbol}::{key}" if symbol != "GLOBAL" else key

    # JSON 변환
    if isinstance(value, (list, dict)):
        value = json.dumps(value)
    else:
        value = str(value)

    # [변경 이력] 이전 값 조회 후 기록
    try:
        cursor.execute('SELECT value FROM bot_config WHERE key = ?', (actual_key,))
        row = cursor.fetchone()
        old_value = row[0] if row else None
        # 값이 실제로 변경된 경우에만 이력 기록 (동일 값 재저장 시 이력 미생성)
        if old_value != value:
            cursor.execute(
                'INSERT INTO config_history (key, old_value, new_value, changed_at) VALUES (?, ?, ?, ?)',
                (actual_key, old_value, value, datetime.now())
            )
    except Exception:
        pass  # 이력 기록 실패가 설정 저장을 막으면 안 됨

    cursor.execute('INSERT OR REPLACE INTO bot_config (key, value, updated_at) VALUES (?, ?, ?)',
                   (actual_key, value, datetime.now()))
    conn.commit()
    conn.close()


def get_config_history(limit: int = 50) -> List[Dict]:
    """설정 변경 이력 조회 (최신순)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, key, old_value, new_value, changed_at FROM config_history ORDER BY id DESC LIMIT ?',
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def save_log(level: str, message: str):
    """시스템 로그 저장"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('INSERT INTO system_logs (level, message) VALUES (?, ?)', (level, message))
    conn.commit()
    conn.close()

def get_logs(limit: int = 100, after_id: int = 0) -> List[Dict]:
    """로그 조회. after_id > 0 이면 해당 id 이후 신규 로그만 오름차순 반환."""
    conn = get_connection()
    cursor = conn.cursor()

    if after_id > 0:
        cursor.execute(
            'SELECT * FROM system_logs WHERE id > ? ORDER BY id ASC LIMIT ?',
            (after_id, limit)
        )
    else:
        cursor.execute('SELECT * FROM system_logs ORDER BY created_at DESC LIMIT ?', (limit,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def wipe_all_trades():
    """trades 테이블 전체 삭제 — 실전 투입 전 테스트 데이터 완전 초기화"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM trades')
    conn.commit()
    conn.close()

def delete_configs(keys: List[str]):
    """여러 설정 키를 한 번에 삭제 (AI 순정 모드 복귀용)"""
    if not keys:
        return
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ','.join('?' * len(keys))
    cursor.execute(f'DELETE FROM bot_config WHERE key IN ({placeholders})', keys)
    conn.commit()
    conn.close()

def delete_symbol_configs(symbol: str) -> int:
    """[Phase 30] 특정 심볼의 전용 설정 전체 삭제 (SYMBOL::* 패턴 고아 키 청소)"""
    if not symbol:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    prefix = f"{symbol}::"
    cursor.execute('DELETE FROM bot_config WHERE key LIKE ?', (prefix + '%',))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def trade_exists_by_okx_id(okx_order_id: str) -> bool:
    """OKX posId 기준 중복 거래 존재 여부 확인 (싱크 중복 방지)"""
    if not okx_order_id:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM trades WHERE okx_order_id = ? LIMIT 1', (okx_order_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

# 데이터베이스 초기화 (모듈 로드 시 자동 실행)
init_db()
