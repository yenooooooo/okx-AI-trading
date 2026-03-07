async function initializeApp() {
    // [UI Overhaul] 테마 초기화 (localStorage 기반 — 깜빡임 방지를 위해 최우선 실행)
    initTheme();

    // [Phase 18.2] 부팅 시퀀스 교정: 백엔드에서 현재 타겟(Symbol)을 가장 먼저 알아옴
    await syncConfig();
    // [Fix] GLOBAL 호출로 currentSymbol 확정 후, 심볼 전용 설정 재로드 (leverage 등 GLOBAL↔심볼 불일치 해소)
    if (currentSymbol) await syncConfig(currentSymbol);

    // 이제 currentSymbol이 비트코인이 아닌 '실제 타겟'으로 맞춰졌으므로 차트와 소켓 연결
    initPriceWebSocket();
    initChart();
    initTerminalScroll();

    // 나머지 신경망 데이터 병렬 동기화
    await Promise.all([
        syncBotStatus(),
        syncBrain(),
        syncStats(),
        syncChart(),
        updateLogs(),
        syncSystemHealth(),
        fetchAndRenderHeatmap(),
        fetchLiveTickers(),
        syncAdvancedStats(),
    ]);

    // 초기 렌더링 후 타이머 설정
    setInterval(syncBotStatus, 1000);
    setInterval(syncBrain, 3000);
    setInterval(syncChart, 5000);
    setInterval(syncStats, 5000);
    setInterval(updateLogs, 3000);
    setInterval(() => syncConfig(currentSymbol), 30000);  // [방어막] 심볼별 설정 유지 (GLOBAL 덮어쓰기 방지)
    setInterval(syncSystemHealth, 5000);
    setInterval(fetchAndRenderHeatmap, 60000);
    setInterval(fetchLiveTickers, 5000);
    setInterval(syncAdvancedStats, 30000);  // [Advanced Analytics] 30초마다 갱신
    // [Phase 21.2] 스트레스 바이패스 상태 주기적 갱신 (10초마다 카운트다운 동기화)
    setInterval(refreshStressBypassUI, 10000);
    refreshStressBypassUI();

    // [확정봉 카운트다운] 1초 인터벌 — 다음 봉 완성까지 남은 시간 표시
    // 확정봉 ts = 캔들 시작 시각. 다음 확정봉 갱신 = ts + 2*tfMs
    // 예: 02:15봉(시작) → +15분=02:30(종료) → +15분=02:45(다음 캔들 종료 = 다음 갱신)
    setInterval(() => {
        const el = document.getElementById('gate-countdown');
        if (!el || !window._confirmedCandleTs || !window._currentTimeframe) return;
        const tfMs = parseTimeframeMs(window._currentTimeframe);
        const nextUpdate = window._confirmedCandleTs + (tfMs * 2);
        const remaining = nextUpdate - Date.now();
        if (remaining <= 0) {
            el.textContent = '새 봉 확인 중...';
            el.className = 'font-mono text-[10px] text-yellow-400 animate-pulse';
        } else {
            const m = Math.floor(remaining / 60000);
            const s = Math.floor((remaining % 60000) / 1000);
            el.textContent = `다음: ${m}분 ${s}초`;
            el.className = 'font-mono text-[10px] text-gray-500';
        }
    }, 1000);
}

// Start
initializeApp();

// ════════════ DB Wipe ════════════

