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

    conn.commit()

    # 기본 설정값 초기화 (INSERT OR IGNORE: 기존 값 유지, 신규 키만 추가)
    default_config = {
        'symbols': json.dumps(['BTC/USDT:USDT']),
        'risk_per_trade': '0.01',
        'hard_stop_loss_rate': '0.005',
        'trailing_stop_activation': '0.003',
        'trailing_stop_rate': '0.002',
        'daily_max_loss_rate': '0.05',
        'timeframe': '1m',
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
    }
    for key, value in default_config.items():
        cursor.execute('INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)', (key, value))
    conn.commit()

    conn.close()

def save_trade(symbol: str, position_type: str, entry_price: float, amount: float,
               exit_price: float = None, pnl: float = None, pnl_percent: float = None,
               fee: float = 0.0, gross_pnl: float = 0.0,
               exit_reason: str = None, leverage: int = 1):
    """거래 기록 저장"""
    conn = get_connection()
    cursor = conn.cursor()

    entry_time = datetime.now()
    exit_time = datetime.now() if exit_price else None

    cursor.execute('''
        INSERT INTO trades
        (symbol, position_type, entry_price, exit_price, entry_time, exit_time,
         pnl, pnl_percent, fee, gross_pnl, amount, exit_reason, leverage)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (symbol, position_type, entry_price, exit_price, entry_time, exit_time,
          pnl, pnl_percent, fee, gross_pnl, amount, exit_reason, leverage))

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

def get_config(key: str = None) -> Any:
    """설정 조회"""
    conn = get_connection()
    cursor = conn.cursor()

    if key:
        cursor.execute('SELECT value FROM bot_config WHERE key = ?', (key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            value = row[0]
            # JSON 파싱 시도
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return None
    else:
        # 모든 설정 반환
        cursor.execute('SELECT key, value FROM bot_config')
        rows = cursor.fetchall()
        conn.close()
        config = {}
        for row in rows:
            try:
                config[row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                config[row[0]] = row[1]
        return config

def set_config(key: str, value: Any):
    """설정 저장"""
    conn = get_connection()
    cursor = conn.cursor()

    # JSON 변환
    if isinstance(value, (list, dict)):
        value = json.dumps(value)
    else:
        value = str(value)

    cursor.execute('INSERT OR REPLACE INTO bot_config (key, value, updated_at) VALUES (?, ?, ?)',
                   (key, value, datetime.now()))
    conn.commit()
    conn.close()

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

# 데이터베이스 초기화 (모듈 로드 시 자동 실행)
init_db()
