
async function setTargetSymbol(newSymbol) {
    // 사전 차단: 포지션 보유 중 타겟 변경 강력 차단
    const currentPos = document.getElementById('pos-type')?.textContent;
    if (currentPos && currentPos.trim() !== 'NONE') {
        showToast('변경 불가', '현재 포지션이 열려있어 타겟을 변경할 수 없습니다. 청산 후 시도하세요.', 'ERROR');
        return;
    }
    try {
        // 서버 상태 업데이트
        await fetch(`${API_URL}/config?key=symbols&value=${encodeURIComponent(JSON.stringify([newSymbol]))}`, { method: 'POST' });
        // UI 및 신경망 딥 싱크 — executeDeepSync 단일 진실 소스 활용 (DRY)
        executeDeepSync(newSymbol);
        showToast('타겟 변경', `조준경이 ${newSymbol}로 전환되었습니다.`, 'INFO');
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] setTargetSymbol 실패:", error);
        showToast('오류', '타겟 변경에 실패했습니다.', 'ERROR');
    }
}

// --- AI 볼륨 스캐너 토글 ---
async function toggleAutoScan(checked) {
    const track = document.getElementById('auto-scan-track');
    const thumb = document.getElementById('auto-scan-thumb');
    if (track) track.className = `block w-8 h-4 rounded-full border transition-colors ${checked ? 'bg-neon-green/30 border-neon-green' : 'bg-navy-900 border-navy-border'}`;
    if (thumb) thumb.className = `absolute top-0.5 w-3 h-3 rounded-full transition-all ${checked ? 'bg-neon-green left-4' : 'bg-gray-500 left-0.5'}`;
    try {
        await fetch(`${API_URL}/config?key=auto_scan_enabled&value=${checked}`, { method: 'POST' });
        showToast('AI 스캐너', checked ? '볼륨 스캐너 활성화 — 15분마다 최적 코인 탐색' : '볼륨 스캐너 비활성화 — 수동 타겟 모드', checked ? 'INFO' : 'WARNING');
    } catch (e) {
        console.error('[ANTIGRAVITY 디버그] toggleAutoScan 실패:', e);
    }
}

// --- 거래량 스파이크 자동 전환 토글 ---
async function toggleSpikeAutoSwitch(checked) {
    const track = document.getElementById('spike-switch-track');
    const thumb = document.getElementById('spike-switch-thumb');
    if (track) track.className = `block w-8 h-4 rounded-full border transition-colors ${checked ? 'bg-orange-500/30 border-orange-500' : 'bg-navy-900 border-navy-border'}`;
    if (thumb) thumb.className = `absolute top-0.5 w-3 h-3 rounded-full transition-all ${checked ? 'bg-orange-500 left-4' : 'bg-gray-500 left-0.5'}`;
    try {
        await fetch(`${API_URL}/config?key=spike_auto_switch&value=${checked}`, { method: 'POST' });
        showToast('스파이크 감지', checked ? '거래량 폭발 시 자동 타겟 전환 활성화' : '자동 전환 비활성화 (알림만 유지)', checked ? 'INFO' : 'WARNING');
    } catch (e) {
        console.error('[Spike] toggleSpikeAutoSwitch 실패:', e);
    }
}

// --- 커스텀 알트코인 검색기 ---
async function searchCustomTarget() {
    const input = document.getElementById('custom-target-input');
    if (!input) return;
    const raw = input.value.trim().toUpperCase();
    if (!raw) return;
    // "XRP" → "XRP/USDT:USDT" / 이미 포맷된 값이면 그대로 사용
    const formatted = raw.includes('/') ? raw : `${raw}/USDT:USDT`;
    input.value = '';
    await setTargetSymbol(formatted);
}

async function updateOrderType(typeStr) {
    try {
        const response = await fetch(`${API_URL}/config?key=ENTRY_ORDER_TYPE&value=${encodeURIComponent(typeStr)}`, { method: 'POST' });
        await response.json();

        const btnMarket = document.getElementById('btn-market-type');
        const btnLimit = document.getElementById('btn-limit-type');
        if (btnMarket && btnLimit) {
            if (typeStr === 'Smart Limit') {
                btnLimit.className = 'flex-1 py-1.5 rounded transition bg-neon-green text-navy-900 font-bold';
                btnMarket.className = 'flex-1 py-1.5 rounded transition text-gray-400 hover:text-white';
            } else {
                btnMarket.className = 'flex-1 py-1.5 rounded transition bg-neon-green text-navy-900 font-bold';
                btnLimit.className = 'flex-1 py-1.5 rounded transition text-gray-400 hover:text-white';
            }
        }
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] updateOrderType 실패 (엔드포인트: /api/v1/config POST, key=ENTRY_ORDER_TYPE):", error);
    }
}

// --- Chart Sync ---

async function fetchLiveTickers() {
    try {
        const res = await fetch('https://www.okx.com/api/v5/market/tickers?instType=SWAP');
        if (!res.ok) return;
        const { data } = await res.json();
        if (!Array.isArray(data)) return;

        // instId 키로 O(1) 접근용 Map 생성
        const tickerMap = new Map(data.map(t => [t.instId, t]));

        document.querySelectorAll('.target-coin-btn').forEach(btn => {
            const dataSymbol = btn.dataset.symbol; // e.g. "BTC/USDT:USDT"
            if (!dataSymbol) return;

            // BTC/USDT:USDT → BTC-USDT-SWAP
            const instId = dataSymbol.split('/')[0] + '-USDT-SWAP';
            const ticker = tickerMap.get(instId);
            if (!ticker) return;

            const last = parseFloat(ticker.last);
            const open24h = parseFloat(ticker.open24h);
            if (!open24h || isNaN(last)) return;

            const changePct = (last - open24h) / open24h * 100;
            const sign = changePct >= 0 ? '+' : '';
            const colorClass = changePct >= 0 ? 'text-neon-green' : 'text-neon-red';

            let badge = btn.querySelector('.ticker-badge');
            if (!badge) {
                badge = document.createElement('span');
                btn.appendChild(badge);
            }
            badge.className = `ticker-badge ml-1.5 text-[9px] ${colorClass}`;
            badge.textContent = `${sign}${changePct.toFixed(1)}%`;
        });
    } catch (e) {
        // OKX Public API 오류 시 조용히 무시 (뱃지 미갱신)
    }
}

// ════════════ [Dynamic Symbol] 스캐너 연동 심볼 드롭다운 동적 갱신 ════════════

// 기본 하드코딩 심볼 (HTML에 항상 존재 — fallback 보장)
const _BASE_SYMBOLS = new Set([
    'BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT',
    'DOGE/USDT:USDT', 'XRP/USDT:USDT', 'AVAX/USDT:USDT',
]);

function _syncSymbolDropdowns(symbolList, activeSymbol) {
    // 대상 드롭다운 ID 목록
    const selectIds = ['modal-target-symbol', 'bt-symbol', 'opt-symbol'];

    for (const selId of selectIds) {
        const sel = document.getElementById(selId);
        if (!sel) continue;

        // 현재 드롭다운에 있는 값 수집
        const existing = new Set();
        for (const opt of sel.options) {
            existing.add(opt.value);
        }

        // API 심볼 중 드롭다운에 없는 것만 추가
        for (const sym of symbolList) {
            if (!existing.has(sym)) {
                const opt = document.createElement('option');
                opt.value = sym;
                opt.textContent = sym.split(':')[0]; // "PEPE/USDT:USDT" → "PEPE/USDT"
                sel.appendChild(opt);
                existing.add(sym);
            }
        }

        // 활성 심볼 선택 동기화
        if (activeSymbol && existing.has(activeSymbol)) {
            sel.value = activeSymbol;
        }
    }

    // 튜닝 패널 타겟 그리드 버튼도 동적 추가
    const btnGrid = document.querySelector('.target-coin-btn')?.parentElement;
    if (btnGrid) {
        const existingBtns = new Set();
        btnGrid.querySelectorAll('.target-coin-btn').forEach(b => existingBtns.add(b.dataset.symbol));

        for (const sym of symbolList) {
            if (!existingBtns.has(sym)) {
                const btn = document.createElement('button');
                btn.className = 'target-coin-btn flex items-center justify-center text-xs py-2 rounded font-mono font-bold transition-all border border-navy-border/50 bg-navy-900/40 text-gray-500 hover:text-gray-300';
                btn.dataset.symbol = sym;
                btn.onclick = () => setTargetSymbol(sym);
                btn.textContent = sym.split('/')[0]; // "PEPE/USDT:USDT" → "PEPE"
                btnGrid.appendChild(btn);
                existingBtns.add(sym);
            }
        }
    }
}

// --- Init & Intervals (Parallel Optimization) ---
