function formatTerminalMsg(rawMsg) {
    let html = rawMsg;

    // ── ① 뱃지 치환 (bracket tag → colored badge span) ──
    // [시스템 뱃지] 파란색
    html = html.replace(/\[엔진\]/g,
        '<span class="inline-block bg-blue-500/10 text-blue-400 border border-blue-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1">ENGINE</span>');
    html = html.replace(/\[시스템\]/g,
        '<span class="inline-block bg-blue-500/10 text-blue-400 border border-blue-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1">SYS</span>');
    html = html.replace(/\[스캐너 가동\]/g,
        '<span class="inline-block bg-blue-500/10 text-blue-400 border border-blue-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1">SCANNER</span>');

    // [상태 뱃지] 회색
    html = html.replace(/\[감시\]/g,
        '<span class="inline-block bg-gray-500/10 text-gray-400 border border-gray-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1">WATCH</span>');
    html = html.replace(/\[봇\]/g,
        '<span class="inline-block bg-gray-500/10 text-gray-400 border border-gray-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1">BOT</span>');

    // [경고 뱃지] 빨간색 — 더 구체적인 패턴을 먼저 처리해 충돌 방지
    html = html.replace(/\[킬스위치 발동\]/g,
        '<span class="inline-block bg-red-500/10 text-red-400 border border-red-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1 font-bold">KILL·SWITCH</span>');
    html = html.replace(/\[소방훈련\]/g,
        '<span class="inline-block bg-red-500/10 text-red-400 border border-red-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1 font-bold">FIRE·DRILL</span>');
    html = html.replace(/\[긴급\]/g,
        '<span class="inline-block bg-red-500/10 text-red-400 border border-red-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1 font-bold">CRITICAL</span>');
    html = html.replace(/\[오류\]/g,
        '<span class="inline-block bg-red-500/10 text-red-400 border border-red-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1 font-bold">ALERT</span>');

    // [PAPER 모드 뱃지] 보라색
    html = html.replace(/\[👻 PAPER\]/g,
        '<span class="inline-block bg-purple-500/10 text-purple-400 border border-purple-500/20 px-1.5 py-0.5 rounded font-mono text-[9px] mx-1">PAPER</span>');

    // ── ② 가격 ($N,NNN.NN) 하이라이팅 — 흰색 굵게 ──
    html = html.replace(/(\$[\d,]+(?:\.\d+)?)/g,
        '<strong class="text-white font-bold tracking-wider">$1</strong>');

    // ── ③ 수익률 (+/-N.NN%) 하이라이팅 — + 초록 / - 빨간 ──
    html = html.replace(/(\+\d+(?:\.\d+)?%)/g,
        '<strong class="text-neon-green font-bold">$1</strong>');
    html = html.replace(/(-\d+(?:\.\d+)?%)/g,
        '<strong class="text-neon-red font-bold">$1</strong>');

    // ── ④ 방향성 (LONG / SHORT) 하이라이팅 — 단어 경계 `\b` 로 부분 매치 차단 ──
    html = html.replace(/\bLONG\b/g,
        '<span class="text-neon-green font-bold border-b border-neon-green/30">LONG</span>');
    html = html.replace(/\bSHORT\b/g,
        '<span class="text-neon-red font-bold border-b border-neon-red/30">SHORT</span>');

    return html;
}

// --- Terminal Scroll Control ---

function initTerminalScroll() {
    const logContainer = document.getElementById('system-log-terminal');
    const alertEl = document.getElementById('new-log-alert');
    if (!logContainer) return;

    logContainer.addEventListener('scroll', () => {
        const isAtBottom = logContainer.scrollHeight - logContainer.scrollTop - logContainer.clientHeight < 15;
        if (isAtBottom) {
            isTerminalPaused = false;
            unreadLogCount = 0;
            if (alertEl) alertEl.classList.add('hidden');
        } else {
            isTerminalPaused = true;
        }
    });
}

function resumeTerminalScroll() {
    const logContainer = document.getElementById('system-log-terminal');
    const alertEl = document.getElementById('new-log-alert');
    if (logContainer) {
        logContainer.scrollTo({ top: logContainer.scrollHeight, behavior: 'smooth' });
    }
    isTerminalPaused = false;
    unreadLogCount = 0;
    if (alertEl) alertEl.classList.add('hidden');
}

// --- Terminal Logs ---
async function updateLogs() {
    try {
        const url = lastLogId > 0
            ? `${API_URL}/logs?limit=100&after_id=${lastLogId}`
            : `${API_URL}/logs?limit=50`;

        const response = await fetch(url);
        const logs = await response.json();
        const logContainer = document.getElementById('system-log-terminal');
        if (!logContainer || !logs.length) return;

        // Set 메모리 누수 방지: 1000개 초과 시 초기화 (lastLogId가 서버 페이지네이션 담당)
        if (processedLogIds.size > 1000) processedLogIds.clear();

        const fragment = document.createDocumentFragment();

        logs.forEach(log => {
            // ── [Race Condition 방어] 이미 렌더링된 ID는 즉시 skip ──
            if (log.id && processedLogIds.has(log.id)) return;

            const msg = log.message || '';
            const formattedMsg = formatTerminalMsg(msg);

            // ── [카테고리 분류 알고리즘] ALERT > TRADE > SYSTEM 우선순위 ──
            const isAlertLevel = log.level === 'ERROR'
                || msg.includes('[오류]') || msg.includes('[긴급]')
                || msg.includes('킬스위치') || msg.includes('쿨다운')
                || msg.includes('💀') || msg.includes('🚨');
            const isTradeKeyword = (msg.includes('진입') || msg.includes('청산')
                || msg.includes('체결') || msg.includes('LONG') || msg.includes('SHORT')
                || msg.includes('PAPER') || msg.includes('TEST')
                || msg.includes('🎯') || msg.includes('💰')
                || msg.includes('📈') || msg.includes('📉'))
                && !msg.includes('타점 탐색 중'); // 반복성 탐색 로그는 SYSTEM 분류

            const isDiagKeyword = msg.includes('🩻');

            let category = 'SYSTEM';
            if (isDiagKeyword) {
                category = 'DIAG';
            } else if (isAlertLevel) {
                category = 'ALERT';
            } else if (isTradeKeyword) {
                category = 'TRADE';
            }

            // ── 색상 클래스 (기존 로직 100% 보존) ──
            let colorClass = 'text-gray-400';
            if (isAlertLevel) {
                colorClass = 'text-neon-red drop-shadow-[0_0_5px_rgba(255,77,77,0.8)]';
            } else if (msg.includes('[봇]') || msg.includes('진입 성공') || msg.includes('청산') || msg.includes('[엔진]') || msg.includes('[스캐너 가동]')) {
                colorClass = 'text-neon-green drop-shadow-[0_0_5px_rgba(0,255,136,0.8)]';
            }

            let timeStr = '';
            if (log.created_at) {
                const utcDateStr = log.created_at.replace(' ', 'T') + 'Z';
                const dateObj = new Date(utcDateStr);
                timeStr = dateObj.toLocaleTimeString('ko-KR', { hour12: false, timeZone: 'Asia/Seoul' });
            }

            const logDiv = document.createElement('div');
            logDiv.className = colorClass + ' break-words';
            logDiv.dataset.category = category;
            logDiv.innerHTML = `<span class="text-gray-600 mr-2">[${timeStr}]</span><span class="text-gray-500 mr-2">[system@antigravity ~]$</span>${formattedMsg}`;

            // ── 현재 필터와 불일치 시 처음부터 숨김 상태로 append ──
            if (currentLogFilter !== 'ALL' && category !== currentLogFilter) {
                logDiv.style.display = 'none';
            }

            fragment.appendChild(logDiv);

            // ── ID 갱신 및 렌더링 확정 기록 ──
            if (log.id) {
                if (log.id > lastLogId) lastLogId = log.id;
                processedLogIds.add(log.id);
            }

            // ── 토스트 트리거 (초기 로드 폭탄 방어: isInitialLogLoad가 false일 때만) ──
            if (!isInitialLogLoad) {
                const isClear = msg.includes('청산');
                const isProfit = msg.includes('+') || msg.includes('수익률: +');
                const isLoss = msg.includes('-');
                const isEntry = msg.includes('진입 성공');
                const isAlert = msg.includes('킬스위치') || msg.includes('쿨다운');

                if (isClear && isProfit) {
                    showToast('TAKE PROFIT (익절)', msg, 'SUCCESS');
                } else if (isClear && isLoss) {
                    showToast('STOP LOSS (손절)', msg, 'ERROR');
                } else if (isEntry) {
                    showToast('POSITION ENTRY (진입)', msg, 'INFO');
                } else if (isAlert) {
                    showToast('SYSTEM ALERT (경고)', msg, 'ERROR');
                }
            }
        });

        const appendedCount = fragment.childNodes.length;
        logContainer.appendChild(fragment);

        if (!isTerminalPaused) {
            // 자동 추적 모드: 맨 아래로 따라간다
            logContainer.scrollTop = logContainer.scrollHeight;
        } else if (appendedCount > 0) {
            // Scroll Hold 모드: 미확인 카운트 증가 + 팝업 표시
            unreadLogCount += appendedCount;
            const alertEl = document.getElementById('new-log-alert');
            if (alertEl) {
                const countEl = document.getElementById('unread-log-count');
                if (countEl) countEl.textContent = unreadLogCount;
                alertEl.classList.remove('hidden');
            }
        }

        // 첫 번째 폴링 완료 후 플래그 해제 → 이후 신규 로그만 토스트 발생
        isInitialLogLoad = false;

    } catch (error) {
        console.warn("Log Sync Failed:", error);
    }
}

function clearLogs() {
    const logContainer = document.getElementById('system-log-terminal');
    if (logContainer) {
        logContainer.innerHTML = '<div class="text-gray-500">[system@antigravity ~]$ Buffer cleared.</div>';
        // lastLogId / processedLogIds 유지 — 화면만 지우고 이미 본 로그는 재표시하지 않음
    }
}

function setLogFilter(filterType) {
    currentLogFilter = filterType;

    // ── 버튼 활성 스타일 갱신 ──
    document.querySelectorAll('.log-filter-btn').forEach(btn => {
        if (btn.dataset.filter === filterType) {
            btn.className = 'log-filter-btn text-[9px] font-mono px-2 py-0.5 rounded border border-neon-green text-neon-green bg-neon-green/5 transition';
        } else {
            btn.className = 'log-filter-btn text-[9px] font-mono px-2 py-0.5 rounded border border-navy-border/50 text-gray-500 hover:text-gray-300 hover:border-gray-500 transition';
        }
    });

    // ── 기존 로그 div 표시/숨김 토글 ──
    const logContainer = document.getElementById('system-log-terminal');
    if (!logContainer) return;
    logContainer.querySelectorAll('[data-category]').forEach(div => {
        div.style.display = (filterType === 'ALL' || div.dataset.category === filterType) ? '' : 'none';
    });
}

// --- Stats Tracker ---
