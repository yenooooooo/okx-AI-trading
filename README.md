<div align="center">

# 🌌 ANTIGRAVITY v3.5

### *Premium OKX AI Futures Trading Terminal*

[![Version](https://img.shields.io/badge/Version-3.5-00ff88?style=for-the-badge&logo=probot&logoColor=white)](.)
[![OKX](https://img.shields.io/badge/Exchange-OKX_Futures-000000?style=for-the-badge&logo=okx&logoColor=white)](.)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](.)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](.)
[![SQLite](https://img.shields.io/badge/DB-SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](.)
[![Telegram](https://img.shields.io/badge/Notify-Telegram_Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](.)

> **7중 방어 관문 + 실시간 UI 튜닝 + Shadow Mode + 초단타 FRENZY 프리셋까지**
> 기관급 리스크 관리 철학을 탑재한 차세대 OKX 선물 자동매매 터미널

</div>

---

## 📁 프로젝트 파일 구조

```
okx/
├── backend/
│   ├── api_server.py       ← FastAPI 메인 서버 · REST API · WebSocket · 매매 루프
│   ├── strategy.py         ← AI 매매 뇌 · 진입 관문 · 리스크 관리 · 지표 계산
│   ├── okx_engine.py       ← OKX CCXT 거래소 어댑터 · 주문 실행 · 잔고/포지션 조회
│   ├── database.py         ← SQLite 영속화 · 거래 기록 · 설정 CRUD · 로그 저장
│   ├── backtester.py       ← 과거 데이터 백테스팅 엔진 (Sharpe·MDD 산출)
│   ├── notifier.py         ← Telegram Bot 알림 · 양방향 명령 제어 · 보안 인증
│   ├── logger.py           ← 공통 로거 설정
│   ├── main.py             ← 서버 진입점 (uvicorn 실행)
│   └── .env                ← API 키 / Telegram 토큰 (gitignore 필수)
│
└── frontend/
    ├── index.html          ← 단일 페이지 대시보드 UI (Tailwind CSS · 8개 패널)
    └── api_client.js       ← 전체 UI 로직 · REST 통신 · 프리셋 · 실시간 동기화
```

---

## 🏗️ 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (index.html)                      │
│  Dashboard UI ──── api_client.js ──── WebSocket / REST API       │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP / WS
┌────────────────────────────▼────────────────────────────────────┐
│                      api_server.py (FastAPI)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Trading Loop │  │  REST API    │  │  WebSocket Broadcast  │  │
│  │ (asyncio)    │  │  Endpoints   │  │  (실시간 상태 push)    │  │
│  └──────┬───────┘  └──────┬───────┘  └───────────────────────┘  │
└─────────┼─────────────────┼──────────────────────────────────────┘
          │                 │
    ┌─────▼─────┐     ┌─────▼──────┐     ┌─────────────┐
    │ strategy  │     │ database   │     │  notifier   │
    │  (AI 뇌)  │     │ (SQLite)   │     │ (Telegram)  │
    └─────┬─────┘     └────────────┘     └─────────────┘
          │
    ┌─────▼──────────┐
    │  okx_engine    │ ──── CCXT ──── OKX Exchange API
    │  (주문 실행)    │              (Futures Perpetual)
    └────────────────┘
```

---

## 🧠 AI 매매 뇌 (`strategy.py`)

### 핵심 클래스: `TradingStrategy`

#### 📐 기술 지표 계산 엔진 — `calculate_indicators(df)`
> 위치: `strategy.py:77`
> 순수 Pandas 구현 (외부 TA 라이브러리 의존성 제로)

| 지표 | 계산식 | 컬럼명 |
|------|--------|--------|
| **RSI** | Wilder's EWM 방식 · 14주기 | `rsi` |
| **MACD** | EMA(12) - EMA(26) · Signal EMA(9) | `macd`, `macd_signal` |
| **볼린저 밴드** | SMA(20) ± 2σ | `upper_band`, `lower_band` |
| **거래량 SMA** | SMA(20) | `vol_sma_20` |
| **EMA 20** | 단기 이격도 방어용 | `ema_20` |
| **ATR** | True Range · 14주기 평균 | `atr` |
| **ADX** | Wilder's EWM · +DM/-DM · 14주기 | `adx` |
| **Choppiness Index** | `100 × log10(ATR합계14 / HL범위14) / log10(14)` | `chop` |

---

#### 🔭 거시 추세 캐시 — `get_macro_ema_200(engine_api, symbol)`
> 위치: `strategy.py:44`

- **1h 타임프레임** 고정으로 200 EMA 조회 (5m 매매 타임프레임과 완전 독립)
- `asyncio.to_thread` 비동기 처리로 메인 루프 블로킹 방지
- **15분(900초)** 단위 인메모리 캐싱 → OKX Rate Limit 방어
- API 실패 시 캐시 값 자동 폴백

---

#### 🚦 7중 진입 관문 — `check_entry_signal(df, current_price, macro_ema_200)`
> 위치: `strategy.py:145`

```
Gate 1  연패 쿨다운 체크        → N연패 후 M초 진입 완전 차단
Gate 2  ADX 범위 필터           → adx_threshold ≤ ADX ≤ adx_max (횡보/과열 이중 방어)
Gate 3  Choppiness Index 필터   → CHOP ≥ chop_threshold 시 횡보장 차단
Gate 4  일일 킬스위치 체크       → 당일 손실 한계 도달 시 24h 진입 잠금
Gate 5  방향별 이격도 필터       → LONG: 가격 ≤ EMA20 × (1 + threshold)
                                  SHORT: 가격 ≥ EMA20 × (1 - threshold)
Gate 6  MACD + RSI 복합 조건    → LONG: MACD↑ AND RSI 30~55
                                  SHORT: MACD↓ AND RSI 45~70
Gate 7  거시 추세 필터           → LONG: 가격 > 1h EMA200
                                  SHORT: 가격 < 1h EMA200
```

> **[v3.5] Gate Bypass**: 초단타 모드 전용 — 관문별 개별 해제 스위치

| 플래그 | 해제되는 관문 | 인스턴스 변수 |
|--------|-------------|------------|
| `bypass_macro` | Gate 7 (1h EMA200 거시 추세) | `self.bypass_macro` |
| `bypass_disparity` | Gate 5 (EMA20 이격도) | `self.bypass_disparity` |
| `bypass_indicator` | Gate 6 RSI 조건 (MACD 방향만 유지) | `self.bypass_indicator` |

---

#### 🛡️ 리스크 관리 엔진 — `evaluate_risk_management(...)`
> 위치: `strategy.py:250`
> 반환값: `(action, sl_price, trailing_active, trailing_target)`

```python
# 하드 스탑로스 계산
LONG:  hard_sl = entry_price × (1 - hard_stop_loss_rate)
SHORT: hard_sl = entry_price × (1 + hard_stop_loss_rate)

# 분할 익절 후 손익분기 손절 전환
부분 익절 완료 시: hard_sl = entry_price ± 0.1% (수익 보호)

# 트레일링 스탑 발동 조건
수익 ≥ entry_price × trailing_stop_activation

# 트레일링 거리
highest_price × trailing_stop_rate

# 청산 트리거
현재 낙폭 ≥ trailing_distance → "TRAILING_STOP_EXIT"
```

---

#### 📦 동적 포지션 사이징 — `calculate_position_size_dynamic(...)`
> 위치: `strategy.py:310`

```
Risk Amount = Equity × risk_rate (DB에서 주입)
Stop Distance = ATR × 1.5
Position Contracts = Risk Amount / Stop Distance / contract_size
```
- ATR이 넓으면(변동성 큼) 수량 감소, ATR이 좁으면 수량 증가
- 최소 1계약 보장

---

#### 📅 일일 킬스위치 시스템 — `check_daily_reset()` · `record_daily_pnl()`
> 위치: `strategy.py:348`, `strategy.py:364`

- **KST(UTC+9) 자정** 기준으로 일일 누적 PnL 초기화
- 당일 손실 ≥ `daily_max_loss_pct` (기본 7%) 시 킬스위치 발동
- 킬스위치 발동 → **24시간 진입 완전 잠금** (`kill_switch_until = now + 86400`)
- 다음 날 자정 자동 해제

---

#### 🔁 연패 쿨다운 — `record_trade_result(is_loss)`
> 위치: `strategy.py:296`

- N연패 (`cooldown_losses_trigger`) 도달 시 M초 (`cooldown_duration_sec`) 진입 차단
- 승리 1회로 카운터 즉시 리셋

---

## ⚙️ 백엔드 서버 (`api_server.py`)

### 핵심 글로벌 상태

```python
bot_global_state = {
    "is_running": bool,          # 봇 동작 여부
    "balance": float,            # 현재 USDT 잔고
    "symbols": {                 # 심볼별 포지션 상태
        "BTC/USDT:USDT": {
            "position": str,         # "LONG" | "SHORT" | "NONE"
            "entry_price": float,    # 진입가
            "current_price": float,  # 현재가 (Mark Price)
            "unrealized_pnl": float, # 미실현 손익 (USDT)
            "unrealized_pnl_percent": float, # 미실현 ROE (%)
            "highest_price": float,  # 포지션 중 최고/최저가
            "leverage": int,
        }
    },
    "logs": deque(maxlen=300),   # 실시간 시스템 로그
}

ai_brain_state = {              # AI 뇌 현재 상태
    "signal": str,              # "LONG" | "SHORT" | "HOLD"
    "reason": str,              # 현재 HOLD 이유 또는 진입 근거
    "rsi": float,
    "macd": float,
    "adx": float,
    ...
}

_active_strategy: TradingStrategy   # Split Brain 방지용 전략 인스턴스 참조
```

---

### 🔄 메인 매매 루프 — `async_trading_loop()`
> 위치: `api_server.py:495`
> 비동기 백그라운드 태스크 (asyncio.Task)

**루프 사이클 흐름:**
```
1. Split Brain 체크   → strategy_instance ≠ _active_strategy 시 즉시 교체
2. 실시간 설정 동기화 → 매 루프마다 DB → strategy_instance 14개 파라미터 강제 주입
3. 볼륨 스캐너 체크   → 15분마다 OKX 24h 거래량 Top 3 코인 자동 감시 타겟 갱신
4. 심볼별 처리        → 각 감시 타겟에 대해:
   ├─ 일일 리셋 체크
   ├─ OHLCV 조회 → 지표 계산
   ├─ 거시 추세(1h EMA200) 조회 (캐시 활용)
   ├─ 진입 신호 판단 (7중 관문)
   ├─ 포지션 없음 → 진입 주문 실행
   └─ 포지션 있음 → 리스크 관리 (SL/Trailing 청산 판단)
```

**매 루프 DB → 전략 인스턴스 실시간 동기화 파라미터 (14개):**

| DB 키 | 전략 변수 | 단위 |
|-------|----------|-----|
| `daily_max_loss_rate` | `daily_max_loss_pct` | ratio |
| `adx_threshold` | `adx_threshold` | 수치 |
| `adx_max` | `adx_max` | 수치 |
| `chop_threshold` | `chop_threshold` | 수치 |
| `volume_surge_multiplier` | `volume_surge_multiplier` | 배수 |
| `fee_margin` | `fee_margin` | ratio |
| `hard_stop_loss_rate` | `hard_stop_loss_rate` | ratio |
| `trailing_stop_activation` | `trailing_stop_activation` | ratio |
| `trailing_stop_rate` | `trailing_stop_rate` | ratio |
| `cooldown_losses_trigger` | `cooldown_losses_trigger` | 횟수 |
| `cooldown_duration_sec` | `cooldown_duration_sec` | 초 |
| `disparity_threshold` | `disparity_threshold` | DB:% → /100 → ratio |
| `bypass_macro/disparity/indicator` | `bypass_*` | bool string |

---

### 📡 REST API 엔드포인트 전체 목록

| Method | 경로 | 설명 |
|--------|------|------|
| `GET` | `/` | 대시보드 HTML 서빙 |
| `GET` | `/api/v1/status` | 봇 상태·잔고·포지션·엔진 모드 조회 |
| `GET` | `/api/v1/brain` | AI 뇌 현재 상태 (신호·지표값) 조회 |
| `GET` | `/api/v1/trades` | 최근 거래 내역 100건 (DB) |
| `GET` | `/api/v1/stats` | 성과 통계 (승률·MDD·Sharpe·KST 일일 지표) |
| `GET` | `/api/v1/history` | 일별/월별 수익 히스토리 (KST 기준) |
| `GET` | `/api/v1/logs` | 시스템 로그 조회 (`after_id` 증분 방식) |
| `GET` | `/api/v1/config` | 전체 설정 조회 |
| `POST` | `/api/v1/config` | 설정 변경 (`key`, `value` query param) |
| `POST` | `/api/v1/toggle` | 봇 시작/중지 토글 |
| `POST` | `/api/v1/tuning/reset` | AI 순정 모드 딥 리셋 (14개 튜닝키 DB 삭제 + 전략 재생성) |
| `POST` | `/api/v1/test_order` | 테스트 주문 (Shadow Mode 등) |
| `POST` | `/api/v1/manual_close` | 수동 포지션 청산 |
| `POST` | `/api/v1/wipe_db` | 전체 거래 기록 초기화 (실전 투입 전 사용) |
| `GET` | `/api/v1/export_csv` | 전체 거래 내역 CSV 다운로드 (BOM 포함) |
| `GET` | `/api/v1/ohlcv` | 5분봉 OHLCV 캔들 데이터 (차트용) |
| `POST` | `/api/v1/backtest` | 백테스팅 실행 |
| `WebSocket` | `/ws/dashboard` | 실시간 대시보드 상태 push |

---

### 🔗 거래소 연동 (`okx_engine.py`)

**CCXT OKX 어댑터 — `OKXEngine` 클래스**

| 메서드 | 기능 | 위치 |
|--------|------|------|
| `__init__()` | API 키 로드 · 인증 검증 · 마켓 로드 | `okx_engine.py:10` |
| `get_usdt_balance()` | Trading잔고 + Funding잔고 합산 조회 | `okx_engine.py:73` |
| `get_current_price(symbol)` | Mark Price 우선, 없으면 Last Price | `okx_engine.py:97` |
| `get_open_positions()` | 현재 열린 선물 포지션 전체 조회 | `okx_engine.py:111` |
| `close_position(symbol, side, amount)` | 시장가 청산 주문 실행 | `okx_engine.py:126` |
| `cancel_order(order_id, symbol)` | 대기 주문 취소 | `okx_engine.py:140` |
| `get_recent_trade_receipts(symbol)` | 최근 체결 영수증 조회 | `okx_engine.py:153` |
| `calculate_realized_pnl(trades, entry)` | 정밀 순손익 계산 (수수료 양방향 역산) | `okx_engine.py:159` |
| `scan_top_volume_coins(limit)` | 24h 거래량 Top N 코인 자동 스캔 | `okx_engine.py:~200` |

**OKX 연결 설정:**
- 상품 유형: `defaultType: 'swap'` (무기한 선물 전용)
- Rate Limit: CCXT 내장 자동 제한
- 주문 실행: Market Order 또는 Smart Limit Order (Maker 우선)
- 진입가 확정: `fetch_my_trades()` 체결 영수증 기반 정밀 평균가 산출

---

## 🗄️ 데이터베이스 (`database.py`)

### SQLite 스키마

**`trades` 테이블** — 거래 기록 영속화
```sql
id              INTEGER PRIMARY KEY AUTOINCREMENT
symbol          TEXT NOT NULL
position_type   TEXT NOT NULL          -- "LONG" | "SHORT"
entry_price     REAL NOT NULL
exit_price      REAL
entry_time      TIMESTAMP NOT NULL
exit_time       TIMESTAMP
pnl             REAL                   -- 순손익 (Net PnL, fee 차감)
pnl_percent     REAL
fee             REAL DEFAULT 0.0       -- 총 수수료 (양방향 합산)
gross_pnl       REAL DEFAULT 0.0       -- 총수익 (fee 차감 전)
amount          REAL NOT NULL          -- 계약 수
exit_reason     TEXT                   -- "STOP_LOSS" | "TRAILING_STOP_EXIT" | ...
leverage        INTEGER DEFAULT 1
created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

**`bot_config` 테이블** — 설정 영속화
```sql
key         TEXT PRIMARY KEY
value       TEXT NOT NULL
updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

**`system_logs` 테이블** — 시스템 로그 영속화
```sql
id          INTEGER PRIMARY KEY AUTOINCREMENT
level       TEXT NOT NULL
message     TEXT NOT NULL
created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

### DB 함수 목록

| 함수 | 기능 |
|------|------|
| `init_db()` | 테이블 생성 + 기본 설정값 초기화 (모듈 로드 시 자동) |
| `save_trade(...)` | 거래 기록 저장 |
| `get_trades(limit, symbol)` | 거래 기록 조회 |
| `get_config(key)` | 설정 조회 (JSON 자동 파싱) |
| `set_config(key, value)` | 설정 저장 (list/dict → JSON 자동 직렬화) |
| `delete_configs(keys)` | 복수 설정 키 일괄 삭제 (AUTO RESET 전용) |
| `save_log(level, message)` | 시스템 로그 저장 |
| `get_logs(limit, after_id)` | 로그 조회 (증분 방식 지원) |
| `wipe_all_trades()` | trades 테이블 전체 삭제 |

---

## 📱 텔레그램 알림 & 제어 (`notifier.py`)

### 보안 아키텍처
- `@auth_required` 데코레이터: `.env`의 `TELEGRAM_CHAT_ID`와 불일치 시 **즉시 무시**
- 모든 명령 핸들러에 1차 인가 필터 적용 (중앙화된 보안 로직)

### 알림 포맷

**진입 알림 (`_tg_entry`)**
```
⚡ ANTIGRAVITY  |  진입
────────────────────────
📈 LONG  ·  BTC/USDT
────────────────────────
가격   │  $95,420.00
수량   │  2계약  ·  3x
────────────────────────
[진입 근거 데이터]
📈 1h 추세: Uptrend (Price > EMA)
🔥 거래량 폭발: 2.14x
🛡️ ATR 방어선: ATR(14): 145.32 -> SL: 217.98
```

**청산 알림 (`_tg_exit`)**
```
⚡ ANTIGRAVITY  |  청산
────────────────────────
✅ LONG 익절  ·  BTC/USDT
────────────────────────
체결가  │  $96,800.00
총수익  │  +$27.60
수수료  │  -$4.13
순수익  │  +$23.47 (+0.94%)
사유    │  트레일링 익절
```

### 봇 명령어 (`python-telegram-bot v20+` 비동기)

| 명령어 | 기능 |
|--------|------|
| `/status` | 봇 상태 · 잔고 · 포지션 · 실시간 PnL 요약 |
| `/start` | 자동매매 시작 |
| `/stop` | 자동매매 중지 |

---

## 🖥️ 대시보드 UI (`index.html` + `api_client.js`)

### 기술 스택
- **Tailwind CSS** Play CDN (JIT) + 커스텀 색상 팔레트
  - `navy-900: #0d1117` · `navy-800: #161b22` · `navy-border: #30363d`
  - `neon-green: #00ff88` · `neon-red: #ff4444`
- **Vanilla JS** ES6+ (No Framework)
- **WebSocket** 실시간 상태 수신

---

### 패널 1 — 메인 헤더 & 제어 바

| 요소 | 기능 | JS 함수 |
|------|------|---------|
| START / STOP 버튼 | 자동매매 토글 | `toggleBot()` |
| 잔고 표시 | USDT 총 자산 실시간 표시 | `syncBotStatus()` |
| 현재가 디스플레이 | Tick 플래시 (상승:녹색 / 하락:빨강) | `updatePriceWithTickFlash(price)` |
| 레버리지 슬라이더 | 1x~50x 조절 + 즉시 DB 저장 | `setLeverage(val)` |
| AUTO RESET 버튼 | AI 순정 모드 딥 리셋 | `resetToAuto()` |
| 감시 타겟 그리드 | BTC·ETH·SOL 등 원클릭 전환 | `setTargetSymbol(symbol)` |

---

### 패널 2 — 포지션 모니터

| 표시 항목 | 설명 |
|----------|------|
| 포지션 방향 뱃지 | LONG (녹색) · SHORT (빨강) · 없음 (회색) |
| 진입가 / 현재가 | 실시간 Mark Price 업데이트 |
| 미실현 ROE (%) | OKX percentage + 선물 ROE 공식 Fallback |
| 미실현 PnL (USDT) | 수수료 차감 전 원시값 |
| 수동 청산 버튼 | 즉시 시장가 청산 | `manualClose()` |

---

### 패널 3 — AI 뇌 모니터

> **실시간 7중 관문 시각화** — `/api/v1/brain` 30초 폴링

| 표시 항목 | 설명 |
|----------|------|
| 현재 신호 | LONG 🟢 / SHORT 🔴 / HOLD ⚪ |
| HOLD 이유 | 어느 관문에서 차단되었는지 한글 메시지 |
| RSI / MACD / ADX 수치 | 현재 지표 수치 표시 |
| 1h 거시추세 | Uptrend / Downtrend / N/A |
| 거래량 폭발 배수 | 현재 거래량 / SMA20 배수 |

---

### 패널 4 — 실시간 로그

- `/api/v1/logs` 증분 폴링 (`after_id` 방식 → 중복 없음)
- `WARN` · `ERROR` · `INFO` 레벨별 색상 구분
- 최대 300개 인메모리 유지

---

### 패널 5 — 성과 통계 대시보드

> `/api/v1/stats` 데이터 기반

| 지표 | 설명 |
|------|------|
| 총 거래 수 | 전체 누적 |
| 승률 (%) | 순손익 > 0 기준 |
| 총 순손익 | 수수료 완전 차감 Net PnL |
| 최대 낙폭 (MDD) | 누적 잔고 기준 최대 하락폭 |
| Sharpe Ratio | 평균 수익률 / 표준편차 |
| KST 오늘 지표 | 당일 거래수·손익·승률 |

---

### 패널 6 — 수익 히스토리 모달

> `/api/v1/history` — KST 자정 기준 일별/월별 집계

| 표시 항목 | 설명 |
|----------|------|
| 일별 집계 | 날짜·거래수·승률·Gross PnL·Net PnL |
| 월별 집계 | 월·거래수·승률·Gross PnL·Net PnL |
| CSV 다운로드 버튼 | `export_csv()` → Excel 한글 BOM 포함 |

---

### 패널 7 — Shadow Mode (가상 매매)

> 실전 투입 전 전략 검증을 위한 Paper Trading 모드

| 기능 | 설명 |
|------|------|
| Shadow Mode 토글 | 실제 주문 없이 신호 시뮬레이션 |
| 시각적 경계 모드 | 활성화 시 UI 전체 테두리 경고 표시 |
| 수동 주문 오버라이드 | 계약수·레버리지 직접 지정 후 즉시 주문 |
| 수동 오버라이드 패널 | `manual_override_enabled`, `manual_amount`, `manual_leverage` |

---

### 패널 8 — 엔진 튜닝 모달 (`openTuningModal()`)

> AI 뇌의 모든 파라미터를 실시간으로 조작하는 고급 제어 패널

#### 📊 Section 0 — 리스크 관리
| 파라미터 | ID | 설명 |
|---------|-----|------|
| 리스크 비율 (%) | `config-risk_per_trade` | 거래당 자본 리스크 · 온도계 UI 연동 |

**리스크 온도계 (`updateRiskThermometer(value)`)**
- `≤ 2%` → 🛡️ 방어력 극대화 (초록)
- `≤ 5%` → ⚖️ 표준 밸런스 (노랑)
- `> 5%` → ⚠️ 초고위험 세팅 (주황 pulse)

#### 📐 Section 1 — 추세 강도 필터
| 파라미터 | ID | 기본값 | 설명 |
|---------|-----|--------|------|
| ADX 하한 | `tuning-adx-threshold` | 25.0 | 추세 없음 차단 |
| ADX 상한 | `tuning-adx-max` | 40.0 | 과열장 추격 방지 |
| CHOP 상한 | `tuning-chop-threshold` | 61.8 | 횡보장 차단 |

#### 📈 Section 2 — 진입 필터
| 파라미터 | ID | 기본값 | 설명 |
|---------|-----|--------|------|
| 거래량 서지 배율 | `tuning-volume-surge` | 1.5x | SMA20 대비 N배 이상 |
| 이격도 한계치 (%) | `config-disparity_threshold` | 0.8% | EMA20 이격 허용 한도 (슬라이더) |

#### 🛡️ Section 3 — 리스크 관리
| 파라미터 | ID | 기본값 | 설명 |
|---------|-----|--------|------|
| 수수료 마진 | `tuning-fee-margin` | 0.0015 | OKX Maker 실제 수수료 |
| 하드 손절 비율 | `tuning-hard-stop-loss` | 0.005 | 진입가 대비 0.5% |
| 트레일링 발동 | `tuning-trailing-activation` | 0.003 | 0.3% 수익 시 트레일링 ON |
| 트레일링 거리 | `tuning-trailing-rate` | 0.002 | 고점 대비 0.2% 낙폭 시 청산 |

#### ⏱️ Section 4 — 연패 쿨다운
| 파라미터 | ID | 기본값 | 설명 |
|---------|-----|--------|------|
| 쿨다운 연패 횟수 | `tuning-cooldown-losses` | 3회 | N연패 시 발동 |
| 쿨다운 시간 | `tuning-cooldown-duration` | 900초 | 진입 차단 시간 |

#### ⚠️ Section 5 — Gate Bypass (초단타 전용)
| 스위치 | ID | 해제 내용 |
|--------|-----|---------|
| 1h 거시추세 무시 | `config-bypass_macro` | EMA200 역행 진입 허용 |
| 이격도 무시 | `config-bypass_disparity` | EMA20 과도 이격 허용 |
| MACD/RSI 무시 | `config-bypass_indicator` | RSI 범위 제한 해제 (MACD 방향만 유지) |

#### ⚡ Section 6 — 원클릭 프리셋

| 프리셋 | 키 | 전략 특성 |
|--------|-----|---------|
| 🎯 스나이퍼 | `sniper` | ADX 30~45 · 보수적 · 고정밀 · 2연패 30분 쿨다운 |
| 🌊 트렌드 라이더 | `trend_rider` | ADX 25~60 · 추세추종 · 넓은 트레일링 |
| ⚡ 스캘퍼 | `scalper` | ADX 20~50 · 고빈도 · 좁은 손절·익절 |
| 🛡️ 아이언 돔 | `iron_dome` | ADX 28~42 · 철벽 방어 · 2연패 1시간 차단 |
| 🔄 팩토리 리셋 | `factory_reset` | 모든 파라미터 초기값 복원 |
| 🔥 FRENZY (광기) | `frenzy` | ADX 15 · 이격도 3% · 틱 익절 · 관문 3종 ALL OFF |

**프리셋 적용 시 동작 (`applyPreset(presetName)`):**
1. TUNING_INPUT_MAP 기준 숫자 인풋 값 주입 + `preset-flash` 애니메이션
2. `dispatchEvent('input')` → 온도계·슬라이더 표시 즉각 갱신
3. Bypass 체크박스 3종 강제 동기화
4. `saveTuningConfig()` → 전체 파라미터 병렬 POST 저장
5. `updateActiveTuningBadge()` → 엔진 상태 뱃지 갱신

#### 🤖 저장 & 리셋 버튼
| 버튼 | 기능 |
|------|------|
| SAVE & APPLY | 전체 파라미터 병렬 POST → 즉각 반영 |
| 🤖 AUTO RESET | DB 14개 튜닝 키 삭제 + 전략 인스턴스 재생성 → 팩토리 리셋 프리셋 UI 동기화 |

---

### `api_client.js` 핵심 함수 목록

| 함수 | 역할 |
|------|------|
| `syncBotStatus()` | 30초 주기 상태 폴링 · 가격 Tick 플래시 · 엔진 뱃지 갱신 |
| `syncConfig()` | 전체 설정 조회 → 14개 파라미터 UI 동기화 |
| `saveTuningConfig()` | 14개 파라미터 병렬 POST 저장 |
| `applyPreset(name)` | 프리셋 값 주입 + 체크박스 처리 + 저장 |
| `resetToAuto()` | AUTO RESET — 서버 딥 리셋 + factory_reset 프리셋 동기화 |
| `updateRiskThermometer(v)` | risk_per_trade 입력 연동 3단계 위험도 UI |
| `updatePriceWithTickFlash(p)` | 가격 변동 감지 → 색상 플래시 (`price < 100 ? 4 : 2` 소수점) |
| `openTuningModal()` | 모달 오픈 + 최신 설정 즉시 syncConfig |
| `toggleBot()` | 봇 시작/중지 |
| `manualClose()` | 수동 즉시 청산 |
| `setTargetSymbol(symbol)` | 감시 타겟 심볼 전환 + DB 저장 |
| `flashBtn(btn, success)` | 버튼 피드백 공통 헬퍼 |

---

## 🗓️ 성능 지표 계산 (`api_server.py:1553`)

| 지표 | 계산 방식 |
|------|---------|
| **승률** | `순손익 > 0` 거래 / 전체 거래 × 100 |
| **총 Net PnL** | `SUM(pnl)` — 수수료 완전 차감 후 순손익 |
| **총 Gross PnL** | `SUM(gross_pnl)` — 수수료 차감 전 |
| **Max Drawdown** | 누적 잔고 고점 대비 최대 하락폭 (시간순 누적) |
| **Sharpe Ratio** | `mean(pnl_pct) / std(pnl_pct)` |
| **KST 일일 지표** | DB `created_at(UTC)` → KST 변환 후 당일 필터링 |

---

## 🚀 설치 및 실행

### 1. 저장소 클론

```bash
git clone https://github.com/yenooooooo/okx-AI-trading.git
cd okx-AI-trading
```

### 2. 가상환경 & 의존성 설치

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**주요 의존 패키지:**
```
fastapi          uvicorn        ccxt
python-dotenv    pandas         numpy
python-telegram-bot>=20.0
```

### 3. 환경변수 설정

`backend/.env` 파일 생성:
```env
# OKX API
OKX_API_KEY=your_api_key
OKX_SECRET_KEY=your_secret_key
OKX_PASSWORD=your_api_password

# Telegram (선택사항)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

> **OKX API 키 발급 주의사항:**
> - 선물 거래 권한 활성화 필수
> - IP 화이트리스트 설정 권장
> - Read + Trade 권한 부여

### 4. 서버 실행

```bash
cd backend
python main.py
```

### 5. 대시보드 접속

```
http://localhost:8000
```

---

## 🔒 보안 설계

| 보안 요소 | 구현 방식 |
|----------|---------|
| API 키 보호 | `.env` 파일 격리 · gitignore 처리 |
| Telegram 인가 | `@auth_required` 데코레이터 · CHAT_ID 1:1 매칭 |
| 불법 접근 차단 | 불일치 chat_id 메시지 즉시 무시 + 경고 로그 |
| 위험 작업 보호 | `wipe_db` · `tuning/reset` — Admin 전용 엔드포인트 |

---

## ⚡ 주요 설계 원칙

| 원칙 | 적용 사례 |
|------|---------|
| **Split Brain 방지** | `_active_strategy` 전역 참조로 루프 내 전략 인스턴스 교체 감지 |
| **DRY 단일화** | `TUNING_INPUT_MAP` — syncConfig·saveTuningConfig·applyPreset 공유 진실 소스 |
| **실시간 동기화** | 매 루프(~3초)마다 DB → 전략 인스턴스 14개 파라미터 강제 주입 |
| **Zero Hardcoding** | 모든 리스크 파라미터 DB 연동 · UI에서 실시간 조작 가능 |
| **비동기 일관성** | FastAPI + asyncio · Telegram polling 블로킹 없이 통합 |
| **정밀 PnL 산출** | OKX `fillPnl` + 양방향 수수료 역산 → 1원 오차 없는 Net PnL |

---

## ⚠️ 면책 조항

> 본 소프트웨어는 교육 및 연구 목적으로 제공됩니다.
> 자동매매로 인한 모든 손실은 사용자 본인에게 있으며,
> 반드시 **Shadow Mode (가상 매매)** 충분한 검증 후 실전 투입하시기 바랍니다.
> 암호화폐 선물 거래는 원금 손실을 초과하는 손실이 발생할 수 있습니다.

---

<div align="center">

**ANTIGRAVITY Project** — *Defying Market Gravity with Logic.*

`v3.5` · FastAPI · CCXT · SQLite · Tailwind CSS · OKX Futures

</div>
