# OKX Trading Bot - 빠른 시작 가이드

## 🎯 핵심 포인트

이 업그레이드는 기본 자동매매 봇을 **프로덕션 레벨 시스템**으로 전환했습니다.

## ⚙️ 필수 설정

### 1. 환경변수 설정 (`.env` 파일)
```env
OKX_API_KEY=your_api_key
OKX_SECRET_KEY=your_secret_key
OKX_PASSWORD=your_password

# 선택사항: Telegram 알림
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 2. 데이터베이스 초기화
```bash
cd backend
python -c "from database import init_db; init_db()"
# 또는 api_server.py 실행 시 자동 초기화
```

### 3. 로그 디렉토리 생성
```bash
mkdir -p backend/logs
```

## 🚀 실행 방법

### 백엔드 시작
```bash
cd backend
python api_server.py
```

출력:
```
--- [환경변수 메모리 적재 상태 점검] ---
1. OKX_API_KEY 존재 여부: 🟢 정상
2. OKX_SECRET_KEY 존재 여부: 🟢 정상
3. OKX_PASSWORD 존재 여부: 🟢 정상
----------------------------------------

[시스템] OKX API 연결 성공 및 마켓 데이터 로드 완료.
INFO:api_server:API 서버 시작
Uvicorn running on http://127.0.0.1:8000
```

### 프론트엔드 접속
```
http://127.0.0.1:8000
```

## 📊 주요 기능 사용법

### 1. 봇 시작/중지
- "Start Bot" 버튼 클릭
- 초록색 "Running" 상태 확인

### 2. 설정 조정 (실시간 적용)
**설정 패널**에서:
- **리스크 비율**: 거래당 투자 비율 (기본 1%)
- **레버리지**: 배수 설정 (기본 1배)
- **심볼**: 거래 심볼 선택 (쉼표 구분: BTC/USDT:USDT, ETH/USDT:USDT)

### 3. 성과 분석 확인
**성과 분석 패널**:
- **총 거래 수**: 누적 거래 횟수
- **승률**: 수익 거래 비율 (%)
- **총 수익률**: 누적 수익률 (%)
- **Max DD**: 최대 낙폭 (%)
- **Sharpe Ratio**: 위험 대비 수익 지표

### 4. 실시간 차트 보기
**실시간 차트 패널**:
- BTC/USDT:USDT 가격 캔들스틱
- 자동 갱신 (10초마다)

### 5. 다중 심볼 모니터링
**다중 심볼 현황**:
- 각 심볼별 포지션 카드
- 현재 가격, 진입가, 수익률 표시

### 6. 백테스팅 실행
**백테스팅 패널**:
1. 심볼 선택 (기본: BTC/USDT:USDT)
2. 타임프레임 선택 (1m, 5m, 15m, 1h)
3. 캔들 수 입력 (기본: 100)
4. "백테스팅 실행" 버튼 클릭
5. 결과 확인 (거래수, 승률, 수익률)

## 📁 중요 파일 위치

```
okx/
├── backend/
│   ├── logger.py              ← 구조화된 로깅
│   ├── database.py            ← SQLite DB
│   ├── notifier.py            ← Telegram 알림
│   ├── backtester.py          ← 백테스팅 엔진
│   ├── api_server.py          ← API 서버 (메인)
│   ├── strategy.py            ← 거래 전략
│   ├── okx_engine.py          ← OKX 연결
│   ├── logs/                  ← 로그 저장
│   └── trading_data.db        ← 거래 데이터 DB
├── frontend/
│   ├── index.html             ← 대시보드 UI
│   ├── api_client.js          ← API 클라이언트
│   └── vercel.json            ← 배포 설정
└── IMPLEMENTATION_SUMMARY.md  ← 상세 문서
```

## 📡 API 엔드포인트

### 기존 (하위 호환)
- `GET /api/v1/status` - 봇 상태
- `GET /api/v1/brain` - AI 뇌 상태
- `GET /api/v1/trades` - 거래 내역
- `POST /api/v1/toggle` - 봇 시작/중지

### 신규 (Phase 4)
- `GET /api/v1/stats` - 성과 분석
- `GET /api/v1/config` - 설정 조회
- `POST /api/v1/config` - 설정 변경
- `GET /api/v1/ohlcv` - 차트 데이터
- `POST /api/v1/backtest` - 백테스팅
- `GET /api/v1/symbols` - 심볼 목록
- `GET /api/v1/logs` - 로그 조회

## 🔧 트러블슈팅

### 문제: "거래소 연결 실패"
**해결책**:
1. `.env` 파일 확인 (3개 키 모두 있는지)
2. OKX 모의투자 계정 확인
3. API 키 재생성 후 `.env` 업데이트
4. `override=True` 설정 활성화

```python
# api_server.py에 이미 포함됨
load_dotenv(dotenv_path=env_path, override=True)
```

### 문제: "포지션 청산이 안 됨"
**해결책**:
1. 봇 로그에서 오류 메시지 확인
2. OKX 모의투자 계정 잔고 확인 (최소 0.001 BTC 이상)
3. `strategy.py` 파라미터 조정 (RSI, MACD 임계값)

### 문제: "Telegram 알림이 안 옴"
**해결책**:
1. `.env`에서 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 설정
2. 없으면 자동으로 스킵 (오류 안 남)
3. Telegram 봇 토큰 재확인

### 문제: "데이터베이스 오류"
**해결책**:
1. `backend/trading_data.db` 파일 삭제
2. `python -c "from database import init_db; init_db()"` 실행
3. 자동 재생성됨

## 📈 거래 흐름

```
1. 봇 시작 (Start Bot)
   ↓
2. 설정된 심볼별로 1분마다 시장 분석
   ↓
3. 진입 신호 감지 (LONG/SHORT)
   ↓
4. 시장가 주문 실행
   ↓
5. 포지션 보유 중 PnL% 실시간 계산
   ↓
6. 손절(2%) 또는 익절(3%+) 조건 만족
   ↓
7. 포지션 청산 + DB 저장 + Telegram 알림
   ↓
8. 성과 분석 업데이트
```

## 💾 데이터 저장소

### SQLite 테이블

#### `trades` 테이블
```
id | symbol | position_type | entry_price | exit_price | pnl | pnl_percent | ...
1  | BTC... | LONG          | 30000       | 30600      | 60  | 2.0         | ...
```

#### `bot_config` 테이블
```
key                  | value
symbols              | ["BTC/USDT:USDT"]
risk_per_trade       | 0.01
leverage             | 1
telegram_enabled     | false
```

#### `system_logs` 테이블
```
level | message                        | created_at
INFO  | [봇] OKX 거래소 연결 성공     | 2026-02-25 10:00:00
WARN  | [오류] API 호출 실패           | 2026-02-25 10:05:00
```

## 🎓 학습 리소스

- **strategy.py**: 매매 신호 로직 수정
- **backtester.py**: 전략 검증 방법
- **api_client.js**: UI 업데이트 로직
- **IMPLEMENTATION_SUMMARY.md**: 상세 설명서

## 📞 지원

문제 발생 시 로그 확인:
```bash
# 터미널 출력 로그 (실시간)
# 또는 파일 로그 조회
cat backend/logs/trading.log | tail -20
```

JSON Lines 로그 분석:
```bash
# 최근 INFO 레벨 로그만 필터
grep '"level": "INFO"' backend/logs/trading.log | tail -10
```

---

**마지막 업데이트**: 2026-02-25
**버전**: 2.0 (10단계 전체 업그레이드)
