# 대공사 기능 체크리스트 (Refactoring Verification Checklist)

> 대공사 후 이 목록을 기준으로 모든 기능이 정상 동작하는지 검증한다.
> 검증 완료 시 [ ] → [x] 로 체크.

---

## 1. 코어 트레이딩 루프 (async_trading_loop)

### 1-1. 봇 제어
- [ ] 봇 시작/정지 토글 (`POST /api/v1/toggle`)
- [ ] 봇 상태 조회 (`GET /api/v1/status`) — is_running, balance, 포지션 정보
- [ ] 브레인 상태 조회 (`GET /api/v1/brain`) — 7게이트 상태, 모놀로그

### 1-2. 진입 (Entry)
- [ ] 7-Gate 필터 (ADX, CHOP, Volume, Disparity, Macro EMA200, RSI, MACD)
- [ ] execute_entry_order — 시장가/지정가 주문 실행:
  - [ ] Market: create_market_buy/sell_order → 즉시 체결 + 거래소 응답 체결가 반영
  - [ ] Smart Limit: fetch_order_book (best_bid/ask) + EMA20 기준 지정가 주문
  - [ ] Smart Limit → PENDING 상태 (pending_order_id) + 5분 미체결 자동 취소
  - [ ] Smart Limit 체결 시 거래소 초기 TP/SL 방어막 배치 (Phase 28)
  - [ ] Shadow Mode: CCXT 바이패스 (paper_xxx ID)
  - [ ] 3회 재시도 (50013 에러 방어), 실패 시 예외 raise
  - [ ] ENTRY_ORDER_TYPE config 키로 모드 전환
- [ ] Direction Mode (AUTO / LONG / SHORT)
- [ ] Shadow Hunting (페이퍼 진입)
- [ ] Shadow Mode (CCXT 바이패스, 페이퍼 트레이딩)
- [ ] 대기 주문 (Pending Order) 생성 및 체결 감시

### 1-3. 청산 (Exit)
- [ ] Hard Stop Loss (하드 손절)
- [ ] Trailing Stop (트레일링 익절 — 활성화 + 추적 거리)
- [ ] 수동 청산 방어 로직 (`_detect_and_handle_manual_close`)
- [ ] 대기 주문 취소 (`POST /api/v1/cancel_pending`)
- [ ] 긴급 전체 청산 (`POST /api/v1/close-position`)

### 1-4. 리스크 관리
- [ ] Kill Switch (일일 손실 한도 초과 시 자동 중단)
- [ ] 연패 쿨다운 (consecutive loss → cooldown)
- [ ] Circuit Breaker (서킷 브레이커)
- [ ] Margin Guard 백그라운드 루프 (`_margin_guard_bg_loop`)
- [ ] 방어적 사이징 (5% 버퍼 + min 1계약 + max capacity cap)
- [ ] Auto-tune by balance (`_auto_tune_by_balance`)

### 1-5. 스캐너
- [ ] 오토 스캔 (고볼륨 코인 자동 탐지) — `auto_scan_enabled` (15분 주기)
  - [ ] 포지션 보유 중 타겟 변경 보류
  - [ ] OKX 전체 마켓 24h 거래량 Top 3 탐색
  - [ ] Margin Guard 부적합 레버리지 검출 → 텔레그램 알림
  - [ ] 스캐너 결과 텔레그램 알림 (_tg_scanner)
  - [ ] _emit_thought 스캐너 상태 기록
- [ ] Volume Spike 감지 (3분 독립 주기):
  - [ ] detect_volume_spikes (okx_engine.py) — fetch_tickers 전체 시장 1회 스캔
  - [ ] spike_threshold (기본 2.0배) / spike_min_volume (기본 $15M)
  - [ ] USDT 선물만 + BTC/ETH 제외 + 최소 거래대금 필터
  - [ ] 텔레그램 알림 (_tg_volume_spike — 상위 3개)
- [ ] 스파이크 자동 전환 — `spike_auto_switch`:
  - [ ] 포지션 보유 중 전환 보류
  - [ ] 1위 스파이크 → 3번째 심볼 슬롯 교체
  - [ ] 텔레그램 자동 전환 알림 (코인명 + 변동률)
- [ ] 타겟 심볼 변경 (`POST /api/v1/config?key=symbols`)
- [ ] 커스텀 심볼 검색

---

## 2. 백그라운드 서브시스템

- [ ] OKX Private WebSocket 연결 (`private_ws_loop`)
- [ ] OKX 거래 동기화 루프 (`_okx_trade_sync_loop`)
- [ ] Heartbeat Monitor (`_heartbeat_monitor_loop`) — 서브시스템 장애 감지 + 텔레그램 알림
- [ ] Margin Guard 루프 (`_margin_guard_bg_loop`)
- [ ] Dashboard WebSocket 브로드캐스트 (`/ws/dashboard`)
- [ ] 수동 거래 동기화 (`POST /api/v1/sync_trades`)

---

## 3. API 엔드포인트 전체 목록

### 3-1. 상태/조회
- [ ] `GET /api/v1/status` — 봇 상태
- [ ] `GET /api/v1/brain` — 브레인 분석
- [ ] `GET /api/v1/trades` — 거래 히스토리
- [ ] `GET /api/v1/stats` — 통계
- [ ] `GET /api/v1/stats/advanced` — 고급 통계
- [ ] `GET /api/v1/history_stats` — 히스토리 통계
- [ ] `GET /api/v1/symbols` — 심볼 목록
- [ ] `GET /api/v1/logs` — 시스템 로그
- [ ] `GET /api/v1/system_health` — 시스템 헬스
- [ ] `GET /api/v1/ohlcv` — OHLCV 차트 데이터
- [ ] `GET /api/v1/config` — 설정 조회
- [ ] `GET /api/v1/config/history` — 설정 변경 이력

### 3-2. 제어/설정
- [ ] `POST /api/v1/toggle` — 봇 시작/정지
- [ ] `POST /api/v1/config` — 설정 변경
- [ ] `POST /api/v1/timeframe/switch` — 타임프레임 전환
- [ ] `POST /api/v1/tuning/reset` — 튜닝 리셋

### 3-3. 스트레스 테스트
- [ ] `GET /api/v1/stress_bypass` — 바이패스 현황
- [ ] `POST /api/v1/stress_bypass` — 바이패스 토글
- [ ] `POST /api/v1/test_order` — 테스트 주문
- [ ] `POST /api/v1/close_paper` — 페이퍼 포지션 청산
- [ ] `POST /api/v1/inject_stress` — 스트레스 주입
- [ ] `POST /api/v1/reset_stress` — 스트레스 리셋

### 3-4. 백테스트/옵티마이저
- [ ] `POST /api/v1/backtest` — 백테스트 실행
- [ ] `POST /api/v1/optimize` — 옵티마이저 실행
- [ ] `POST /api/v1/optimize/apply` — 최적화 결과 적용

### 3-5. 진단/모니터링
- [ ] `GET /api/v1/diagnostic` — 전체 진단
- [ ] `GET /api/v1/health_check` — 헬스 체크

### 3-6. X-Ray 분석
- [ ] `GET /api/v1/xray/loop_state` — 루프 상태
- [ ] `GET /api/v1/xray/blocker_wizard` — 블로커 분석
- [ ] `GET /api/v1/xray/trade_attempts` — 거래 시도 이력
- [ ] `GET /api/v1/xray/gate_scoreboard` — 게이트 스코어보드
- [ ] `GET /api/v1/xray/okx_deep_verify` — OKX 검증

### 3-7. 데이터 관리
- [ ] `POST /api/v1/wipe_db` — DB 초기화
- [ ] `GET /api/v1/export_csv` — CSV 내보내기
- [ ] `POST /api/v1/sync_trades` — OKX 거래 동기화
- [ ] `POST /api/v1/cancel_pending` — 대기 주문 취소

### 3-8. WebSocket
- [ ] `/ws/dashboard` — 대시보드 실시간 업데이트

---

## 4. 프론트엔드 UI 기능 (api_client.js + index.html)

> 프론트엔드 세부 항목은 파일 분리됨:
> **[REFACTOR_CHECKLIST_FRONTEND.md](REFACTOR_CHECKLIST_FRONTEND.md)** 참조 (~300줄, 200+ 체크 항목)

---

## 5. 텔레그램 알림 (notifier.py)

- [ ] 진입 알림 (_tg_entry)
- [ ] 대기 주문 알림 (_tg_pending)
- [ ] 청산 알림 (_tg_exit)
- [ ] 수동 청산 알림 (_tg_manual_exit)
- [ ] 스캐너 결과 알림 (_tg_scanner)
- [ ] Volume Spike 알림 (_tg_volume_spike)
- [ ] Margin Guard 알림 (_tg_margin_guard)
- [ ] Circuit Breaker 알림 (_tg_circuit_breaker)
- [ ] 시스템 시작/종료 알림 (_tg_system)
- [ ] Heartbeat 장애/복구 알림
- [ ] HTML 특수문자 안전화 (<, >, &)

---

## 6. 기타 백엔드 모듈

### strategy.py
- [ ] 7-Gate 계산 로직
- [ ] Hard SL / Trailing Stop 계산
- [ ] Kill Switch / Cooldown 상태 관리

### database.py
- [ ] trades 테이블 CRUD
- [ ] bot_config 테이블 CRUD
- [ ] system_logs 테이블 CRUD

### okx_engine.py
- [ ] CCXT OKX 어댑터 — 잔고, 주문, 포지션 조회
- [ ] 레버리지 설정
- [ ] 시장가/지정가 주문

### logger.py
- [ ] 구조화 로깅

---

## 7. 인프라

- [ ] FastAPI lifespan (startup_event / shutdown_event)
- [ ] Static file serving (frontend)
- [ ] CORS 설정
- [ ] AWS PM2 프로세스 관리 호환
- [ ] .env 환경변수 로딩
