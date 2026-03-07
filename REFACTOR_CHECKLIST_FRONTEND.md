# 프론트엔드 파일 분리 체크리스트 (api_client.js → modules/)

> 분리 방식: **방법 A (단순 스크립트 분리)** — 전역 스코프 유지, HTML onclick 수정 없음
> 분리 후 이 체크리스트를 기준으로 교차 검증한다.
> 검증 완료 시 [ ] → [x]

---

## 원본 정보

- 원본: `frontend/api_client.js` (5080줄, 114개 함수)
- 목표: `frontend/modules/` 폴더 내 17개 파일
- `index.html`: `<script src="api_client.js">` → 17개 `<script src="modules/xxx.js">` 교체

---

## 모듈 적재 순서 (index.html script 태그 순서 — 반드시 준수)

```html
<script src="modules/state.js"></script>
<script src="modules/ui.js"></script>
<script src="modules/websocket.js"></script>
<script src="modules/chart.js"></script>
<script src="modules/terminal.js"></script>
<script src="modules/gates.js"></script>
<script src="modules/presets.js"></script>
<script src="modules/scanner.js"></script>
<script src="modules/sync.js"></script>
<script src="modules/tuning.js"></script>
<script src="modules/analytics.js"></script>
<script src="modules/backtest.js"></script>
<script src="modules/manual_override.js"></script>
<script src="modules/stress.js"></script>
<script src="modules/diagnostic.js"></script>
<script src="modules/xray.js"></script>
<script src="modules/main.js"></script>
```

---

## 1. state.js — 전역 변수 (목표: ~30줄)

> 모든 모듈의 의존 기반. 반드시 첫 번째 로드.

### 전역 변수 (원본 line 1-18)
- [ ] `const API_URL` (line 1)
- [ ] `let _isDemoMode` (line 2)
- [ ] `let chart, candleSeries, volumeSeries, ema20Series, ema200Series` (lines 3-7)
- [ ] `let rsiChart, rsiSeries, macdChart, macdHistSeries, macdSignalSeries` (lines 8-9)
- [ ] `let entryPriceLine, tpPriceLine, slPriceLine` (line 10)
- [ ] `let lastLogId` (line 11)
- [ ] `let lastCandleData` (line 12)
- [ ] `let currentSymbol` (line 13)
- [ ] `let isInitialLogLoad` (line 14)
- [ ] `const processedLogIds` (line 15)
- [ ] `let currentLogFilter` (line 16)
- [ ] `let isTerminalPaused` (line 17)
- [ ] `let unreadLogCount` (line 18)

### 검증
- [ ] state.js 파일 존재
- [ ] state.js 50줄 이하
- [ ] 다른 모듈에 동일 변수 중복 선언 없음

---

## 2. ui.js — UI 유틸리티 (목표: ~350줄)

### 함수 목록 (원본 line)
- [ ] `parseTimeframeMs(tf)` (29)
- [ ] `mobileScrollTo(id)` (39)
- [ ] `lockBodyScroll()` (178)
- [ ] `unlockBodyScroll()` (180)
- [ ] `showToast(title, message, type)` (189)
- [ ] `updateNumberText(elementId, newValue, formatCb)` (234)
- [ ] `updateText(elementId, text, flash)` (271)
- [ ] `updatePriceWithTickFlash(price)` (286)
- [ ] `flashBtn(btn, success)` (2376)
- [ ] `switchMainTab(tabName)` (4931)
- [ ] `toggleFAB()` (4948)
- [ ] `getThemeColor(varName)` (4975)
- [ ] `toggleTheme()` (4979)
- [ ] `initTheme()` (4993)
- [ ] `updateThemeIcon()` (5001)
- [ ] `updateChartTheme()` (5009)

### 검증
- [ ] ui.js 파일 존재
- [ ] ui.js 400줄 이하

---

## 3. websocket.js — WebSocket (목표: ~120줄)

### 변수
- [ ] `let priceWs` (3752)
- [ ] `let _wsManualRestart` (3753)

### 함수 목록
- [ ] `initPriceWebSocket()` (3755)

### 검증
- [ ] websocket.js 파일 존재
- [ ] websocket.js 150줄 이하

---

## 4. chart.js — 차트 (목표: ~400줄)

### 함수 목록
- [ ] `initChart()` (2476)
- [ ] `syncChart()` (2557)

### 검증
- [ ] chart.js 파일 존재
- [ ] chart.js 450줄 이하
- [ ] `chart`, `candleSeries` 등 state.js 변수 참조 가능 (state.js 먼저 로드)

---

## 5. terminal.js — 터미널 로그 (목표: ~250줄)

### 함수 목록
- [ ] `formatTerminalMsg(rawMsg)` (2797)
- [ ] `initTerminalScroll()` (2850)
- [ ] `resumeTerminalScroll()` (2867)
- [ ] `updateLogs()` (2879)
- [ ] `clearLogs()` (3003)
- [ ] `setLogFilter(filterType)` (3011)

### 검증
- [ ] terminal.js 파일 존재
- [ ] terminal.js 300줄 이하
- [ ] `lastLogId`, `currentLogFilter`, `isTerminalPaused`, `unreadLogCount` 참조 정상

---

## 6. gates.js — 게이트/브레인 렌더링 (목표: ~450줄)

### 변수
- [ ] `let _lastMonologueLatest` (1057)
- [ ] `const _PIPELINE_STEP_META` (1155)
- [ ] `let _lastTrailTimestamp` (1164)
- [ ] `let _lastMismatchReport` (1220)

### 함수 목록
- [ ] `renderGates(gates, passed, liveGates)` (937)
- [ ] `renderMonologue(lines)` (1058)
- [ ] `renderEntryGuards(guards)` (1098)
- [ ] `renderDecisionTrail(trail)` (1165)
- [ ] `checkConfigMismatch(activeConfig)` (1221)
- [ ] `updateActiveTuningBadge()` (1275)

### 검증
- [ ] gates.js 파일 존재
- [ ] gates.js 500줄 이하

---

## 7. presets.js — 프리셋 설정 (목표: ~350줄)

### 변수/상수
- [ ] `const PRESET_LABELS` (1359)
- [ ] `const PRESET_CONFIGS` (1371)
- [ ] `const PRESET_TF_OVERLAY` (1462)
- [ ] `const PRESET_PROTECTED_KEYS` (1559)
- [ ] `const TUNING_INPUT_MAP` (1562)

### 함수 목록
- [ ] `_getEffectivePreset(presetName)` (1527)
- [ ] `updateRiskThermometer(value)` (1537)
- [ ] `applyPreset(presetName)` (1723)

### 검증
- [ ] presets.js 파일 존재
- [ ] presets.js 400줄 이하
- [ ] `PRESET_CONFIGS` 전역 접근 가능

---

## 8. scanner.js — 심볼 스캐너 (목표: ~150줄)

### 변수
- [ ] `const _BASE_SYMBOLS` (3932)

### 함수 목록
- [ ] `setTargetSymbol(newSymbol)` (2395)
- [ ] `toggleAutoScan(checked)` (2415)
- [ ] `toggleSpikeAutoSwitch(checked)` (2429)
- [ ] `searchCustomTarget()` (2443)
- [ ] `updateOrderType(typeStr)` (2454)
- [ ] `fetchLiveTickers()` (3889)
- [ ] `_syncSymbolDropdowns(symbolList, activeSymbol)` (3937)

### 검증
- [ ] scanner.js 파일 존재
- [ ] scanner.js 200줄 이하

---

## 9. sync.js — 핵심 동기화 (목표: ~750줄)

### 함수 목록
- [ ] `executeDeepSync(newSymbol)` (321)
- [ ] `syncBotStatus()` (359)
- [ ] `syncBrain()` (726)
- [ ] `syncConfig(symbol)` (1581)
- [ ] `toggleBot()` (1346)
- [ ] `syncOkxTrades()` (117)
- [ ] `forceRefreshGates()` (143)
- [ ] `toggleTimeframe()` (50)
- [ ] `_applyTimeframeToggleUI(tf)` (104)
- [ ] `syncSystemHealth()` (3852)

### 검증
- [ ] sync.js 파일 존재
- [ ] sync.js 800줄 이하
- [ ] `currentSymbol` 갱신 로직 포함
- [ ] `syncBotStatus` → `renderGates` 호출 정상 (gates.js 먼저 로드됨)
- [ ] `syncConfig` → `PRESET_CONFIGS` 참조 정상 (presets.js 먼저 로드됨)

---

## 10. tuning.js — 튜닝 모달 (목표: ~300줄)

### 변수
- [ ] `let _section1Collapsed` (1775)

### 함수 목록
- [ ] `toggleSection1()` (1776)
- [ ] `openTuningModal()` (1796)
- [ ] `closeTuningModal()` (2291)
- [ ] `saveTuningConfig()` (2297)
- [ ] `resetToAuto()` (2349)
- [ ] `_applyDirectionModeUI(mode)` (1805)
- [ ] `setDirectionMode(mode)` (1825)
- [ ] `toggleShadowHunting(enabled)` (1836)
- [ ] `toggleAutoPreset(enabled)` (1845)
- [ ] `onModalSymbolChange(newSymbol)` (2284)
- [ ] `applyRecommendedLeverage()` (5045)

### 검증
- [ ] tuning.js 파일 존재
- [ ] tuning.js 350줄 이하

---

## 11. analytics.js — 통계/분석 (목표: ~500줄)

### 변수
- [ ] `let _historyData` (4133)

### 함수 목록
- [ ] `syncStats()` (3032)
- [ ] `syncAdvancedStats()` (3166)
- [ ] `renderHeatmap(dailyData)` (4234)
- [ ] `fetchAndRenderHeatmap()` (4435)
- [ ] `loadConfigHistory()` (4082)
- [ ] `_renderHistoryTable(bodyId, rows)` (4135)
- [ ] `openHistoryModal()` (4160)
- [ ] `closeHistoryModal()` (4188)
- [ ] `switchHistoryTab(tab)` (4194)
- [ ] `downloadCSV()` (4449)
- [ ] `wipeDatabase()` (4057)

### 검증
- [ ] analytics.js 파일 존재
- [ ] analytics.js 600줄 이하

---

## 12. backtest.js — 백테스트/옵티마이저 (목표: ~300줄)

### 변수
- [ ] `let _btChart` (3269)
- [ ] `let _btEquityChart` (3270)

### 함수 목록
- [ ] `runBacktestVisualizer()` (3272)
- [ ] `runOptimizer()` (3409)
- [ ] `applyOptimization(rank, paramsJSON)` (3515)

### 검증
- [ ] backtest.js 파일 존재
- [ ] backtest.js 350줄 이하

---

## 13. manual_override.js — 수동 매매 (목표: ~200줄)

### 변수
- [ ] `let isManualPanelOpen` (3540)

### 함수 목록
- [ ] `toggleManualPanel()` (3542)
- [ ] `setQuickAmount(percent)` (3559)
- [ ] `updateOrderPreview()` (3582)
- [ ] `toggleOverrideVisuals(isActive)` (3612)
- [ ] `toggleManualOverride()` (3628)
- [ ] `saveManualOverride()` (3649)
- [ ] `applyShadowModeVisuals(enabled)` (3665)
- [ ] `toggleShadowMode()` (3683)
- [ ] `emergencyCloseAll()` (4957)
- [ ] `abortPendingOrder()` (2266)

### 검증
- [ ] manual_override.js 파일 존재
- [ ] manual_override.js 250줄 이하

---

## 14. stress.js — 스트레스 테스트 (목표: ~150줄)

### 함수 목록
- [ ] `injectStress(type)` (3696)
- [ ] `resetStress()` (3710)
- [ ] `closePaperPosition()` (3722)
- [ ] `testOrder(direction)` (3737)
- [ ] `fetchStressBypass()` (4460)
- [ ] `toggleStressBypass(feature, enabled)` (4467)
- [ ] `refreshStressBypassUI()` (4476)

### 검증
- [ ] stress.js 파일 존재
- [ ] stress.js 200줄 이하

---

## 15. diagnostic.js — 진단/헬스체크 (목표: ~350줄)

### 함수 목록
- [ ] `runDiagnostic()` (1868)
- [ ] `toggleDiagDetail(idx)` (1946)
- [ ] `closeDiagnosticModal()` (1855)
- [ ] `runHealthCheck()` (1958)
- [ ] `closeHealthModal()` (1861)
- [ ] `_hcFetchBackend()` (2017)
- [ ] `_hcPingSweep(progressEl)` (2030)
- [ ] `_hcTestWebSocket()` (2085)
- [ ] `_hcRenderSection(title, checks)` (2140)
- [ ] `_hcRenderEndpointSection(title, checks)` (2172)
- [ ] `_hcRenderButtonMap(endpoints, pingResults)` (2200)

### 검증
- [ ] diagnostic.js 파일 존재
- [ ] diagnostic.js 400줄 이하

---

## 16. xray.js — X-Ray 진단 (목표: ~400줄)

### 변수
- [ ] `let _currentXrayTab` (4586)

### 함수 목록
- [ ] `openXrayModal()` (4588)
- [ ] `closeXrayModal()` (4594)
- [ ] `switchXrayTab(tab)` (4599)
- [ ] `refreshCurrentXrayTab()` (4607)
- [ ] `_xrayLoadLoopState(container)` (4626)
- [ ] `_xrayRunBlockerWizard(container)` (4721)
- [ ] `_xrayLoadTradeAttempts(container)` (4768)
- [ ] `_xrayLoadGateScoreboard(container)` (4808)
- [ ] `_xrayLoadOkxDeepVerify(container)` (4869)

### 검증
- [ ] xray.js 파일 존재
- [ ] xray.js 450줄 이하

---

## 17. main.js — 앱 초기화 (목표: ~80줄)

### 함수 목록
- [ ] `initializeApp()` (3989)
- [ ] `DOMContentLoaded` 이벤트 리스너 (api_client.js 맨 끝)

### 검증
- [ ] main.js 파일 존재
- [ ] main.js 100줄 이하
- [ ] `DOMContentLoaded` → `initializeApp()` 호출

---

## 전체 교차 검증

### A. 파일 17개 전부 존재
- [ ] `modules/state.js`
- [ ] `modules/ui.js`
- [ ] `modules/websocket.js`
- [ ] `modules/chart.js`
- [ ] `modules/terminal.js`
- [ ] `modules/gates.js`
- [ ] `modules/presets.js`
- [ ] `modules/scanner.js`
- [ ] `modules/sync.js`
- [ ] `modules/tuning.js`
- [ ] `modules/analytics.js`
- [ ] `modules/backtest.js`
- [ ] `modules/manual_override.js`
- [ ] `modules/stress.js`
- [ ] `modules/diagnostic.js`
- [ ] `modules/xray.js`
- [ ] `modules/main.js`

### B. 함수 114개 전부 존재 (누락 없음)
- [ ] `grep -r "^function\|^async function" frontend/modules/ | wc -l` → 114

### C. 중복 없음
- [ ] `grep -rh "^function\|^async function" frontend/modules/ | sort | uniq -d` → 결과 없음

### D. index.html 수정
- [ ] 원본 `<script src="api_client.js">` 제거됨
- [ ] 17개 script 태그 올바른 순서로 추가됨
- [ ] state.js 가 첫 번째 로드
- [ ] main.js 가 마지막 로드

### E. 줄 수 보존
- [ ] `wc -l frontend/modules/*.js` 합계 ≈ 5080줄 (±150 허용)

### F. 핵심 기능 동작 (배포 후 확인)
- [ ] 봇 시작/정지 버튼 동작
- [ ] 7-Gate 렌더링 정상
- [ ] 차트 초기화 + 실시간 업데이트
- [ ] 터미널 로그 갱신
- [ ] 프리셋 변경 적용
- [ ] 튜닝 모달 열기/닫기/저장
- [ ] 타임프레임 전환
- [ ] X-Ray 모달 동작
- [ ] 진단/헬스체크 동작
- [ ] 스트레스 테스트 동작
- [ ] 수동 오버라이드 동작
- [ ] WebSocket 실시간 가격 수신
- [ ] 통계/히트맵 렌더링
- [ ] 백테스트 실행

---

> **교차 검증 명령어 (분리 후 실행):**
> ```bash
> wc -l frontend/modules/*.js
> grep -r "^function \|^async function " frontend/modules/ | wc -l
> grep -rh "^function \|^async function " frontend/modules/ | sort | uniq -d
> ```
