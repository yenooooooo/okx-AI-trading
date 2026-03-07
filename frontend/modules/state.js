const API_URL = `/api/v1`;
let _isDemoMode = false; // [데모 모드] 백엔드 status.is_demo에서 동기화
let chart = null;
let candleSeries = null;
let volumeSeries = null;
let ema20Series  = null;
let ema200Series = null;
let rsiChart = null, rsiSeries = null;
let macdChart = null, macdHistSeries = null, macdSignalSeries = null;
let entryPriceLine = null, tpPriceLine = null, slPriceLine = null;
let lastLogId = 0;
let lastCandleData = null;   // WebSocket 실시간 캔들 업데이트용
let currentSymbol = 'BTC/USDT:USDT'; // 현재 감시 심볼 캐시 (syncConfig에서 갱신)
let isInitialLogLoad = true; // 초기 로드 폭탄 방어: false 전환 후부터 토스트 발생
const processedLogIds = new Set(); // Race condition 방어: 이미 렌더링된 로그 ID 기록
let currentLogFilter = 'ALL';      // 터미널 카테고리 필터 현재 상태
let isTerminalPaused = false;      // Smart Auto-Scroll: 사용자가 위를 보고 있으면 true
let unreadLogCount = 0;            // Smart Auto-Scroll: 일시정지 중 누적된 미확인 로그 수

// [확정봉 카운트다운] 글로벌 캐시
window._confirmedCandleTs = 0;     // 확정봉 타임스탬프(ms)
window._currentTimeframe = '15m';  // 현재 타임프레임 문자열

/**
 * parseTimeframeMs(tf) — 타임프레임 문자열을 밀리초로 변환
 * @param {string} tf - "1m", "5m", "15m", "1h", "4h", "1d" 등
 * @returns {number} 밀리초
 */
