const API_URL = `/api/v1`;
let chart = null;
let candleSeries = null;
let lastLogId = 0;
let lastCandleData = null;   // WebSocket 실시간 캔들 업데이트용
let currentSymbol = 'BTC/USDT:USDT'; // 현재 감시 심볼 캐시 (syncConfig에서 갱신)
let isInitialLogLoad = true; // 초기 로드 폭탄 방어: false 전환 후부터 토스트 발생

// --- UI Utilities ---

/**
 * showToast(title, message, type)
 * type: 'SUCCESS' | 'ERROR' | 'INFO'
 * 4초 후 fade-out 후 DOM 자동 제거 (메모리 누수 없음)
 */
function showToast(title, message, type) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const themes = {
        SUCCESS: { border: '#00ff88', titleColor: '#00ff88', icon: '✅', bg: 'rgba(0,255,136,0.06)' },
        ERROR:   { border: '#ff4d4d', titleColor: '#ff4d4d', icon: '🚨', bg: 'rgba(255,77,77,0.06)' },
        INFO:    { border: '#60a5fa', titleColor: '#60a5fa', icon: '⚡', bg: 'rgba(96,165,250,0.06)' },
    };
    const t = themes[type] || themes.INFO;

    const toast = document.createElement('div');
    toast.className = 'toast-enter pointer-events-auto';
    toast.style.cssText = `
        min-width:280px; max-width:360px;
        background:rgba(22,27,34,0.92);
        backdrop-filter:blur(12px);
        border:1px solid ${t.border};
        border-left:3px solid ${t.border};
        border-radius:0.625rem;
        padding:10px 14px;
        box-shadow:0 4px 24px rgba(0,0,0,0.4);
        background-color:${t.bg};
    `;
    toast.innerHTML = `
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:3px;">
            <span style="font-size:13px;">${t.icon}</span>
            <span style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;color:${t.titleColor};letter-spacing:0.05em;">${title}</span>
        </div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:#8b949e;line-height:1.5;word-break:break-word;max-height:54px;overflow:hidden;">${message}</div>
    `;

    container.appendChild(toast);

    // 4초 후 fade-out → 애니메이션 종료 시 DOM 제거
    const DISPLAY_MS = 4000;
    const ANIM_MS = 350;
    setTimeout(() => {
        toast.classList.remove('toast-enter');
        toast.classList.add('toast-leave');
        setTimeout(() => toast.remove(), ANIM_MS);
    }, DISPLAY_MS);
}

// CountUp animation utility
function updateNumberText(elementId, newValue, formatCb) {
    const el = document.getElementById(elementId);
    if (!el) return;

    const oldVal = parseFloat(el.dataset.val || 0);
    const newVal = parseFloat(newValue || 0);

    if (oldVal === newVal) {
        if (!el.textContent) el.textContent = formatCb ? formatCb(newVal) : newVal.toFixed(2);
        return;
    }

    // Flash effect
    const parent = el.closest('.flash-target') || el;
    parent.classList.remove('flash');
    void parent.offsetWidth; // trigger reflow
    parent.classList.add('flash');

    // Count up animation (0.2s)
    const duration = 200;
    let startTimestamp = null;
    const step = (timestamp) => {
        if (!startTimestamp) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);
        const current = oldVal + progress * (newVal - oldVal);
        el.textContent = formatCb ? formatCb(current) : current.toFixed(2);
        if (progress < 1) {
            window.requestAnimationFrame(step);
        } else {
            el.textContent = formatCb ? formatCb(newVal) : newVal.toFixed(2);
            el.dataset.val = newVal;
        }
    };
    window.requestAnimationFrame(step);
}

// Simple text update with flash
function updateText(elementId, text, flash = true) {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (el.textContent === text) return;

    el.textContent = text;
    if (flash) {
        const parent = el.closest('.flash-target') || el;
        parent.classList.remove('flash');
        void parent.offsetWidth;
        parent.classList.add('flash');
    }
}

// Tick Flash (Green/Red) for Websocket Ultra-low latency
function updatePriceWithTickFlash(price) {
    const el2 = document.getElementById('hero-price');
    const oldPrice = parseFloat(el2 ? el2.dataset.val : 0) || price;

    const formattedPrice = price.toFixed(2);
    let flashClass = '';

    if (price > oldPrice) {
        flashClass = 'tick-flash-green';
    } else if (price < oldPrice) {
        flashClass = 'tick-flash-red';
    }

    [el2].forEach(el => {
        if (!el) return;
        el.textContent = formattedPrice;
        el.dataset.val = price;
        if (flashClass) {
            el.classList.remove('tick-flash-green', 'tick-flash-red');
            void el.offsetWidth; // trigger reflow
            el.classList.add(flashClass);

            // cleanup after animation
            setTimeout(() => {
                el.classList.remove(flashClass);
            }, 200);
        }
    });
}

// --- Status Sync ---

async function syncBotStatus() {
    try {
        const response = await fetch(`${API_URL}/status`);
        const data = await response.json();

        // 1. Balance (REST API 데이터는 웹소켓 상태와 무관하게 항상 동기화)
        // 웹소켓은 더 이상 잔고를 건드리지 않으므로, 여기서 무조건 업데이트해야 함.
        updateNumberText('current-balance', data.balance);
        updateNumberText('balance-krw', data.balance * 1350, val => `≈ ${Math.floor(val).toLocaleString()} KRW`);

        // 2. Position
        const symbols = data.symbols || {};
        const firstSymbol = Object.keys(symbols)[0];
        const symbolData = firstSymbol ? symbols[firstSymbol] : null;

        const posCard = document.getElementById('active-position-card');
        const posNone = document.getElementById('position-none');
        const posActive = document.getElementById('position-active');

        const posSymbolEl = document.getElementById('pos-symbol');

        if (!symbolData || symbolData.position === 'NONE') {
            posNone.classList.remove('hidden');
            posActive.classList.add('hidden');
            posActive.classList.remove('flex');
            posCard.className = "glass-panel p-5 transition-all duration-500 border-navy-border flex-grow flex flex-col relative overflow-hidden";
            if (posSymbolEl) posSymbolEl.classList.add('hidden');
        } else {
            posNone.classList.add('hidden');
            posActive.classList.remove('hidden');
            posActive.classList.add('flex');
            if (posSymbolEl) {
                posSymbolEl.textContent = firstSymbol.split(':')[0];
                posSymbolEl.classList.remove('hidden');
            }

            // 웹소켓(priceWs)이 연결되어 있을 때는 REST API 구형 가격/수익률 데이터 표시는 무시.
            // (단, 포지션 유무, 진입가, 목표가 등 고정데이터는 계속 연동)
            updateText('pos-type', symbolData.position);
            updateNumberText('pos-entry', symbolData.entry_price);
            // TP/SL 상태 동기화 (백엔드 실시간 계산값 기반)
            const trailingActive = symbolData.trailing_active === true;
            const trailingTarget = parseFloat(symbolData.trailing_target || 0);
            const realSl = parseFloat(symbolData.real_sl || 0);

            if (trailingActive && trailingTarget > 0) {
                updateText('pos-tp', trailingTarget.toFixed(4));
                updateText('pos-tp-expect', 'Trailing Active 🎯');
            } else {
                updateText('pos-tp', '대기중');
                updateText('pos-tp-expect', '목표가 대기중');
            }

            updateNumberText('pos-sl', realSl > 0 ? realSl : 0);
            updateText('pos-sl-expect', realSl > 0 ? '(Dynamic)' : '');

            // PnL(%) 및 USDT 수익금 동기화
            const pnl = parseFloat(symbolData.unrealized_pnl_percent || 0);
            const pnlUsdt = parseFloat(symbolData.unrealized_pnl || 0);
            const pnlSign = pnl >= 0 ? '+' : '';

            updateNumberText('pos-roi', pnl, val => `${pnlSign}${val.toFixed(2)}%`);
            updateNumberText('pos-pnl-usdt', pnlUsdt, val => `${pnlSign}${val.toFixed(2)} USDT`);
            updateNumberText('pos-current', symbolData.current_price);

            const roiEl = document.getElementById('pos-roi');
            const pnlUsdtEl = document.getElementById('pos-pnl-usdt');

            // 색상 및 글로우 동적 적용 (숏/롱 관계없이 수익 여부에 따름)
            if (pnl > 0) {
                roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-neon-green';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono block mt-1 flash-target text-neon-green';
                posCard.className = "glass-panel p-5 transition-all duration-500 flex flex-col relative overflow-hidden glow-green";
            } else if (pnl < 0) {
                roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-neon-red';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono block mt-1 flash-target text-neon-red';
                posCard.className = "glass-panel p-5 transition-all duration-500 flex flex-col relative overflow-hidden glow-red";
            } else {
                roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-gray-400';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono block mt-1 flash-target text-gray-400';
                posCard.className = "glass-panel p-5 transition-all duration-500 border-navy-border flex flex-col relative overflow-hidden";
            }
        }

        // --- NEW: Market Radar ---
        if (data.symbols) {
            const radarContainer = document.getElementById('market-radar-list');
            if (radarContainer) {
                let radarHtml = '';
                const symKeys = Object.keys(data.symbols);
                symKeys.slice(0, 3).forEach(sym => {
                    const symData = data.symbols[sym];
                    const priceStr = symData.current_price ? parseFloat(symData.current_price).toFixed(4) : "0.00";
                    const pnl = parseFloat(symData.unrealized_pnl_percent || 0);
                    let colorObj = "text-gray-500";
                    let valStr = `$${priceStr}`;

                    if (symData.position !== "NONE") {
                        colorObj = pnl >= 0 ? "text-neon-green" : "text-neon-red";
                        const sign = pnl > 0 ? "+" : "";
                        valStr = `${sign}${pnl.toFixed(2)}%`;
                    } else if (symData.current_price !== undefined && symData.current_price > 0) {
                        colorObj = "text-text-main";
                    }
                    const shortSym = sym.split(':')[0];

                    radarHtml += `
                        <div class="flex justify-between items-center text-[11px] bg-navy-900/40 p-1.5 rounded border border-navy-border/50">
                            <span class="font-mono text-gray-300 font-bold">${shortSym}</span>
                            <span class="font-mono ${colorObj}">${valStr}</span>
                        </div>
                    `;
                });
                if (radarHtml) radarContainer.innerHTML = radarHtml;
            }
        }

        // 3. Status Info
        const statusDot = document.getElementById('status-dot');
        const statusPing = document.getElementById('status-ping');
        const statusText = document.getElementById('bot-status-text');
        const toggleBtn = document.getElementById('toggle-bot-btn');

        if (data.is_running) {
            statusDot.className = 'relative inline-flex rounded-full h-3 w-3 bg-neon-green';
            statusPing.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-green opacity-75';
            statusText.textContent = '🟢 시스템 가동 중';
            statusText.className = 'font-mono text-sm tracking-widest text-neon-green uppercase';
            toggleBtn.textContent = '🛑 시스템 중지';
            toggleBtn.className = 'px-6 py-2 bg-navy-800 border border-neon-red hover:bg-neon-red hover:text-white text-neon-red text-sm font-bold rounded transition-all font-mono tracking-widest';
        } else {
            statusDot.className = 'relative inline-flex rounded-full h-3 w-3 bg-neon-red';
            statusPing.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-red opacity-75';
            statusText.textContent = '🛑 시스템 중지';
            statusText.className = 'font-mono text-sm tracking-widest text-gray-400 uppercase';
            toggleBtn.textContent = '🟢 시스템 가동';
            toggleBtn.className = 'px-6 py-2 bg-navy-800 border border-neon-green hover:bg-neon-green hover:text-navy-900 text-neon-green text-sm font-bold rounded transition-all font-mono tracking-widest';
        }

    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncBotStatus 실패 (엔드포인트: /api/v1/status):", error);
    }
}

// --- Brain Sync (3초 인터벌 - status와 분리) ---
async function syncBrain() {
    try {
        const brainRes = await fetch(`${API_URL}/brain`);
        const brainData = await brainRes.json();

        const symbolBrains = brainData.symbols || {};
        const brainState = symbolBrains[currentSymbol] || Object.values(symbolBrains)[0];

        if (!brainState) return;

        // [A] 진입 관문 체크리스트
        if (brainState.gates) {
            renderGates(brainState.gates, brainState.gates_passed || 0);
        }
        // [B] 봇 혼잣말 피드
        if (brainState.monologue) {
            renderMonologue(brainState.monologue);
        }

        // WebSocket 연결 중엔 REST가 hero-price를 덮어쓰지 않음 (실시간 보호)
        if (brainState.price && (!priceWs || priceWs.readyState !== WebSocket.OPEN)) {
            updateNumberText('hero-price', brainState.price);
        }
        if (brainState.decision) {
            updateText('brain-decision', brainState.decision, false);
        }
        if (brainState.rsi) {
            const rsi = parseFloat(brainState.rsi);
            updateNumberText('brain-rsi', rsi);
            const rsiEl = document.getElementById('brain-rsi');
            rsiEl.className = rsi <= 30 ? 'font-mono flash-target font-bold text-neon-green' : (rsi >= 70 ? 'font-mono flash-target font-bold text-neon-red' : 'font-mono flash-target font-bold text-text-main');
            const marker = document.getElementById('rsi-marker');
            if (marker) marker.style.left = `${Math.max(0, Math.min(100, rsi))}%`;

            // --- AI Confidence Matrix (RSI 50% + MACD 50% 복합 지표) ---
            // RSI 컴포넌트: 낮을수록 LONG 신호 (과매도 = 반등 압력)
            const rsiLongScore = Math.max(0, Math.min(100, 100 - rsi));
            // MACD 컴포넌트: 양수면 LONG 신호, 음수면 SHORT 신호 (±100 정규화)
            const macdRaw = parseFloat(brainState.macd) || 0;
            const macdAbs = Math.max(Math.abs(macdRaw), 0.0001);
            const macdLongScore = Math.max(0, Math.min(100, 50 + (macdRaw / macdAbs) * 50));
            // RSI 50% + MACD 50% 가중 합산
            const longProb = Math.round(rsiLongScore * 0.5 + macdLongScore * 0.5);
            const shortProb = 100 - longProb;

            const longProbEl = document.getElementById('ai-long-prob');
            const longBarEl = document.getElementById('ai-long-bar');
            if (longProbEl && longBarEl) {
                longProbEl.textContent = `${longProb}%`;
                longBarEl.style.width = `${longProb}%`;
                longProbEl.className = longProb >= 50 ? 'text-neon-green font-bold text-[10px]' : 'text-gray-500 font-bold text-[10px]';
            }

            const shortProbEl = document.getElementById('ai-short-prob');
            const shortBarEl = document.getElementById('ai-short-bar');
            if (shortProbEl && shortBarEl) {
                shortProbEl.textContent = `${shortProb}%`;
                shortBarEl.style.width = `${shortProb}%`;
                shortProbEl.className = shortProb >= 50 ? 'text-neon-red font-bold text-[10px]' : 'text-gray-500 font-bold text-[10px]';
            }
        }
        // --- CHOP Index 렌더링 (횡보장 탐지) ---
        if (brainState.chop !== undefined) {
            const chop = parseFloat(brainState.chop) || 0;
            const chopEl = document.getElementById('brain-chop');
            const chopBar = document.getElementById('chop-bar');
            const chopStatus = document.getElementById('chop-status');
            if (chopEl) chopEl.textContent = chop.toFixed(1);
            if (chopBar) {
                const pct = Math.max(0, Math.min(100, chop));
                chopBar.style.width = `${pct}%`;
                if (chop >= 61.8) {
                    // 횡보장 — 빨간색
                    chopBar.style.background = '#ff4d4d';
                    chopBar.style.boxShadow = '0 0 8px rgba(255,77,77,0.7)';
                } else if (chop <= 38.2) {
                    // 추세장 — 녹색
                    chopBar.style.background = '#00ff88';
                    chopBar.style.boxShadow = '0 0 8px rgba(0,255,136,0.7)';
                } else {
                    // 중립
                    chopBar.style.background = '#aaa';
                    chopBar.style.boxShadow = '0 0 4px #aaa';
                }
            }
            if (chopStatus) {
                if (chop >= 61.8) {
                    chopStatus.textContent = '🔴 횡보장 (진입 차단)';
                    chopStatus.className = 'font-bold text-neon-red text-[10px]';
                } else if (chop <= 38.2) {
                    chopStatus.textContent = '🟢 추세장 (진입 가능)';
                    chopStatus.className = 'font-bold text-neon-green text-[10px]';
                } else {
                    chopStatus.textContent = '🟡 중립';
                    chopStatus.className = 'font-bold text-yellow-400 text-[10px]';
                }
            }
        }

        if (brainState.macd !== undefined) {
            const macd = parseFloat(brainState.macd);
            updateNumberText('brain-macd', macd);
            const macdEl = document.getElementById('brain-macd');
            // MACD >= 0 green (0 포함 중립), < 0 red
            macdEl.className = macd >= 0 ? 'font-mono flash-target font-bold text-neon-green' : 'font-mono flash-target font-bold text-neon-red';

            // MACD 게이지 동적 스케일
            const posBar = document.getElementById('macd-bar-pos');
            const negBar = document.getElementById('macd-bar-neg');
            if (posBar && negBar) {
                const absMaxMacd = Math.max(Math.abs(macd), parseFloat(posBar.dataset.maxMacd || 1));
                posBar.dataset.maxMacd = absMaxMacd;
                negBar.dataset.maxMacd = absMaxMacd;
                const pct = Math.min(100, (Math.abs(macd) / absMaxMacd) * 100);
                if (macd >= 0) {
                    negBar.style.width = '0%';
                    posBar.style.width = `${pct}%`;
                } else {
                    posBar.style.width = '0%';
                    negBar.style.width = `${pct}%`;
                }
            }
        }
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncBrain 실패 (엔드포인트: /api/v1/brain):", error);
    }
}

// --- [A] 진입 관문 체크리스트 렌더링 ---
function renderGates(gates, passed) {
    const passedEl = document.getElementById('gates-passed');
    const barEl = document.getElementById('gates-bar');
    if (!gates || !passedEl || !barEl) return;

    passedEl.textContent = passed;
    const pct = Math.round((passed / 6) * 100);
    barEl.style.width = `${pct}%`;
    // 0~3: 빨강, 4~5: 노랑, 6: 초록
    if (passed <= 3) {
        barEl.style.background = '#ff4d4d';
        barEl.style.boxShadow = '0 0 6px rgba(255,77,77,0.5)';
        passedEl.className = 'text-neon-red font-bold';
    } else if (passed <= 5) {
        barEl.style.background = '#facc15';
        barEl.style.boxShadow = '0 0 6px rgba(250,204,21,0.5)';
        passedEl.className = 'text-yellow-400 font-bold';
    } else {
        barEl.style.background = '#00ff88';
        barEl.style.boxShadow = '0 0 6px rgba(0,255,136,0.5)';
        passedEl.className = 'text-neon-green font-bold';
    }

    const gateMap = {
        adx: 'gate-adx',
        chop: 'gate-chop',
        volume: 'gate-volume',
        disparity: 'gate-disparity',
        macd_rsi: 'gate-macd-rsi',
        macro: 'gate-macro',
    };
    for (const [key, elId] of Object.entries(gateMap)) {
        const el = document.getElementById(elId);
        if (!el || !gates[key]) continue;
        const g = gates[key];
        if (g.pass) {
            el.innerHTML = `<span class="text-neon-green">✅</span> <span class="text-neon-green">${g.value}</span>`;
        } else {
            el.innerHTML = `<span class="text-neon-red">❌</span> <span class="text-gray-500">${g.value}</span>`;
        }
    }
}

// --- [B] 봇 혼잣말 피드 렌더링 ---
let _lastMonologueLatest = '';
function renderMonologue(lines) {
    if (!lines || lines.length === 0) return;
    const latest = lines[lines.length - 1];
    if (latest === _lastMonologueLatest) return; // 최신 메시지 동일하면 스킵
    _lastMonologueLatest = latest;

    const feed = document.getElementById('monologue-feed');
    if (!feed) return;

    // 최신 10개만 표시 (위에서 아래로 최신 → 오래된 순)
    const recent = lines.slice(-10).reverse();
    feed.innerHTML = recent.map((line, i) => {
        const isLatest = i === 0;
        const isEntry = line.includes('🟢') || line.includes('🔴');
        let cls = 'text-[11px] font-mono py-0.5 px-1 rounded transition-all';
        if (isEntry) cls += ' text-neon-green bg-neon-green/10 font-bold animate-pulse';
        else if (isLatest) cls += ' text-gray-300';
        else cls += ' text-gray-600';
        return `<div class="${cls}">${line}</div>`;
    }).join('');
}

async function toggleBot() {
    try {
        const response = await fetch(`${API_URL}/toggle`, { method: 'POST' });
        const result = await response.json();
        syncBotStatus();
    } catch (error) {
        alert('Toggle target failed: ' + error.message);
    }
}

// --- Config Sync ---
async function syncConfig() {
    try {
        const response = await fetch(`${API_URL}/config`);
        const configs = await response.json();
        for (const [key, val] of Object.entries(configs)) {
            if (key === 'risk_per_trade') {
                const input = document.getElementById('config-risk-rate');
                const v = parseFloat(val) * 100;
                if (input) input.value = v;
                updateText('risk-val-display', v.toFixed(1) + '%', false);
            } else if (key === 'leverage') {
                const input = document.getElementById('config-leverage');
                if (input) input.value = val;
                updateText('lev-val-display', val + 'x', false);
            } else if (key === 'symbols') {
                const input = document.getElementById('config-symbols');
                if (input && Array.isArray(val)) input.value = val.join(' | ');
                if (Array.isArray(val) && val.length > 0) currentSymbol = val[0]; // 차트용 심볼 갱신
            } else if (key === 'ENTRY_ORDER_TYPE') {
                const btnMarket = document.getElementById('btn-market-type');
                const btnLimit = document.getElementById('btn-limit-type');
                if (btnMarket && btnLimit) {
                    if (val === 'Smart Limit') {
                        btnLimit.className = 'flex-1 py-1.5 rounded transition bg-neon-green text-navy-900 font-bold';
                        btnMarket.className = 'flex-1 py-1.5 rounded transition text-gray-400 hover:text-white';
                    } else {
                        btnMarket.className = 'flex-1 py-1.5 rounded transition bg-neon-green text-navy-900 font-bold';
                        btnLimit.className = 'flex-1 py-1.5 rounded transition text-gray-400 hover:text-white';
                    }
                }
            } else if (key === 'manual_override_enabled') {
                const toggle = document.getElementById('manual-override-toggle');
                const panel = document.getElementById('manual-override-panel');
                const status = document.getElementById('override-status');
                const enabled = val === true || val === 'true';
                if (toggle) toggle.checked = enabled;
                if (panel) panel.classList.toggle('hidden', !enabled);
                if (status) status.textContent = enabled ? '활성 — 아래 설정값으로 자동매매' : '해제 — 잔고 비율 자동 계산';
            } else if (key === 'manual_amount') {
                const input = document.getElementById('manual-amount');
                const display = document.getElementById('manual-amount-display');
                if (input) input.value = val;
                if (display) display.textContent = val;
            } else if (key === 'manual_leverage') {
                const input = document.getElementById('manual-leverage');
                const display = document.getElementById('manual-lev-display');
                if (input) input.value = val;
                if (display) display.textContent = val + 'x';
            } else if (key === 'SHADOW_MODE_ENABLED') {
                const toggle = document.getElementById('shadow-mode-toggle');
                const enabled = val === true || val === 'true';
                if (toggle) toggle.checked = enabled;
                applyShadowModeVisuals(enabled);
            }
        }
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncConfig 실패 (엔드포인트: /api/v1/config GET):", error);
    }
}

// --- 버튼 피드백 공통 헬퍼 ---
function flashBtn(btn, success) {
    if (!btn) return;
    const orig = btn.textContent;
    const origClass = btn.className;
    if (success) {
        btn.textContent = '✓ APPLIED';
        btn.className = origClass.replace(/border-navy-border|hover:border-gray-400|text-gray-300|hover:text-white|hover:border-gray-500/g, '').trim()
            + ' border-neon-green text-neon-green';
    } else {
        btn.textContent = '✗ FAILED';
        btn.className = origClass.replace(/border-navy-border|hover:border-gray-400|text-gray-300|hover:text-white|hover:border-gray-500/g, '').trim()
            + ' border-neon-red text-neon-red';
    }
    setTimeout(() => {
        btn.textContent = orig;
        btn.className = origClass;
    }, 2000);
}

async function updateConfigValue(key) {
    const btn = document.querySelector(`[onclick="updateConfigValue('${key}')"]`);
    try {
        let value;
        if (key === 'risk_per_trade') {
            value = parseFloat(document.getElementById('config-risk-rate').value) / 100;
        } else if (key === 'leverage') {
            value = parseInt(document.getElementById('config-leverage').value);
        }
        const response = await fetch(`${API_URL}/config?key=${encodeURIComponent(key)}&value=${encodeURIComponent(value)}`, { method: 'POST' });
        await response.json();
        flashBtn(btn, true);
    } catch (error) {
        console.error(`[ANTIGRAVITY 디버그] updateConfigValue('${key}') 실패 (엔드포인트: /api/v1/config POST):`, error);
        flashBtn(btn, false);
    }
}

async function updateConfigSymbols() {
    const btn = document.querySelector('[onclick="updateConfigSymbols()"]');
    try {
        const symbolsText = document.getElementById('config-symbols').value;
        const symbols = symbolsText.split(/[,|]/).map(s => s.trim()).filter(s => s);
        const symbolsJson = JSON.stringify(symbols);
        const response = await fetch(`${API_URL}/config?key=symbols&value=${encodeURIComponent(symbolsJson)}`, { method: 'POST' });
        await response.json();
        if (symbols.length > 0) currentSymbol = symbols[0];
        initPriceWebSocket();
        syncChart();
        flashBtn(btn, true);
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] updateConfigSymbols 실패 (엔드포인트: /api/v1/config POST):", error);
        flashBtn(btn, false);
    }
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
function initChart() {
    const container = document.getElementById('chart-container');
    if (!container || chart) return;

    chart = LightweightCharts.createChart(container, {
        autoSize: true,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#8b949e',
        },
        grid: {
            vertLines: { color: 'rgba(48, 54, 61, 0.5)' },
            horzLines: { color: 'rgba(48, 54, 61, 0.5)' },
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
            borderColor: '#30363d'
        },
        rightPriceScale: {
            borderColor: '#30363d'
        }
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00ff88',
        downColor: '#ff4d4d',
        borderVisible: false,
        wickUpColor: '#00ff88',
        wickDownColor: '#ff4d4d'
    });

    // Add dummy overlay for moving averages (visuals requested)
    const smaSeries = chart.addLineSeries({ color: 'rgba(88, 166, 255, 0.5)', lineWidth: 1 });
}

async function syncChart() {
    try {
        if (!chart) initChart();

        const response = await fetch(`${API_URL}/ohlcv?symbol=${encodeURIComponent(currentSymbol)}&limit=60`);
        const ohlcv = await response.json();

        const overlay = document.getElementById('chart-overlay');

        if (ohlcv.error || !Array.isArray(ohlcv) || ohlcv.length === 0) {
            if (overlay) overlay.classList.remove('hidden');
            return;
        }

        if (overlay) overlay.classList.add('hidden');

        if (!candleSeries) return;

        const KST_OFFSET_SEC = 9 * 3600; // KST = UTC+9 (초 단위)

        const data = ohlcv.map(candle => ({
            time: Math.floor(candle.timestamp / 1000) + KST_OFFSET_SEC, // KST (+9h) 물리적 시프팅
            open: parseFloat(candle.open),
            high: parseFloat(candle.high),
            low: parseFloat(candle.low),
            close: parseFloat(candle.close),
        }));

        candleSeries.setData(data);
        lastCandleData = data[data.length - 1]; // 마지막 캔들 저장

        // ── 온차트 매매 타점 마커 렌더링 ──────────────────────────────────────
        try {
            const tradesRes = await fetch(`${API_URL}/trades`);
            const allTrades = await tradesRes.json();

            if (Array.isArray(allTrades) && allTrades.length > 0) {
                // 현재 차트 심볼과 일치하는 거래만 필터링
                const symbolTrades = allTrades.filter(t => t.symbol === currentSymbol);
                const markers = [];

                symbolTrades.forEach(trade => {
                    const posType = (trade.position_type || '').toUpperCase();
                    const pnl = parseFloat(trade.pnl ?? 0);

                    // ── 진입(Entry) 마커 ──
                    if (trade.entry_time) {
                        // DB 저장값은 UTC naive("YYYY-MM-DD HH:MM:SS") → 'Z' 접미사로 UTC 강제 파싱
                        const entryTs = Math.floor(
                            new Date(String(trade.entry_time).replace(' ', 'T') + 'Z').getTime() / 1000
                        ) + KST_OFFSET_SEC;

                        if (!isNaN(entryTs)) {
                            markers.push({
                                time:     entryTs,
                                position: posType === 'LONG' ? 'belowBar' : 'aboveBar',
                                color:    posType === 'LONG' ? '#00ff88' : '#ff4d4d',
                                shape:    posType === 'LONG' ? 'arrowUp'  : 'arrowDown',
                                text:     posType === 'LONG' ? '🟢 LONG 진입' : '🔴 SHORT 진입',
                            });
                        }
                    }

                    // ── 청산(Exit) 마커 (exit_time 존재 시만) ──
                    if (trade.exit_time) {
                        const exitTs = Math.floor(
                            new Date(String(trade.exit_time).replace(' ', 'T') + 'Z').getTime() / 1000
                        ) + KST_OFFSET_SEC;

                        if (!isNaN(exitTs)) {
                            const isProfit = pnl >= 0;
                            markers.push({
                                time:     exitTs,
                                position: posType === 'LONG' ? 'aboveBar' : 'belowBar',
                                color:    isProfit ? '#00ff88' : '#ff4d4d',
                                shape:    'circle',
                                text:     isProfit ? '✅ 익절' : '💀 손절',
                            });
                        }
                    }
                });

                // Lightweight Charts 필수: time 오름차순 정렬 (미정렬 시 내부 에러 발생)
                markers.sort((a, b) => a.time - b.time);
                candleSeries.setMarkers(markers);
            } else {
                // 거래 없음 → 마커 초기화
                candleSeries.setMarkers([]);
            }
        } catch (markerErr) {
            // 마커 오류는 차트 캔들 자체를 훼손하지 않음 — warn 후 무시
            console.warn("Marker Sync Failed:", markerErr);
        }
        // ──────────────────────────────────────────────────────────────────────

    } catch (error) {
        const overlay = document.getElementById('chart-overlay');
        if (overlay) overlay.classList.remove('hidden');
        console.error("Chart Sync Failed:", error);
    }
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

        const fragment = document.createDocumentFragment();

        logs.forEach(log => {
            const msg = log.message || '';

            let colorClass = 'text-gray-400';
            if (log.level === 'ERROR' || msg.includes('[오류]') || msg.includes('[긴급]')) {
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
            logDiv.innerHTML = `<span class="text-gray-600 mr-2">[${timeStr}]</span><span class="text-gray-500 mr-2">[system@antigravity ~]$</span>${msg}`;
            fragment.appendChild(logDiv);

            if (log.id && log.id > lastLogId) lastLogId = log.id;

            // ── 토스트 트리거 (초기 로드 폭탄 방어: isInitialLogLoad가 false일 때만) ──
            if (!isInitialLogLoad) {
                const isClear    = msg.includes('청산');
                const isProfit   = msg.includes('+') || msg.includes('수익률: +');
                const isLoss     = msg.includes('-');
                const isEntry    = msg.includes('진입 성공');
                const isAlert    = msg.includes('킬스위치') || msg.includes('쿨다운');

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
            // ─────────────────────────────────────────────────────────────────
        });

        logContainer.appendChild(fragment);
        logContainer.scrollTop = logContainer.scrollHeight;

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
        // lastLogId는 유지 — 화면만 지우고 이미 본 로그는 재표시하지 않음
    }
}

// --- Stats Tracker ---
async function syncStats() {
    try {
        const response = await fetch(`${API_URL}/stats`);
        const stats = await response.json();

        updateNumberText('stats-total-trades', stats.total_trades || 0, val => Math.floor(val));
        updateNumberText('stats-win-rate', stats.win_rate || 0, val => `${val.toFixed(2)}%`);

        // Daily Net PnL — 양수 초록 / 음수 빨강 동적 스타일
        const dailyNetEl = document.getElementById('stats-daily-net');
        if (dailyNetEl) {
            const dailyNetVal = parseFloat(stats.daily_net_pnl || 0);
            const dailyNetSign = dailyNetVal >= 0 ? '+' : '';
            dailyNetEl.textContent = `${dailyNetSign}${dailyNetVal.toFixed(2)} USDT`;
            dailyNetEl.className = dailyNetEl.className.replace(/text-neon-(green|red)/g, '') + (dailyNetVal >= 0 ? ' text-neon-green' : ' text-neon-red');
        }

        // Total Net PnL — 양수 초록 / 음수 빨강 동적 스타일
        const totalNetEl = document.getElementById('stats-total-net');
        if (totalNetEl) {
            const totalNetVal = parseFloat(stats.total_net_pnl || 0);
            const totalNetSign = totalNetVal >= 0 ? '+' : '';
            totalNetEl.textContent = `${totalNetSign}${totalNetVal.toFixed(2)} USDT`;
            totalNetEl.className = totalNetEl.className.replace(/text-neon-(green|red)/g, '') + (totalNetVal >= 0 ? ' text-neon-green' : ' text-neon-red');
        }

        // --- NEW: Recent Executions ---
        try {
            const tradesRes = await fetch(`${API_URL}/trades`);
            const trades = await tradesRes.json();

            const historyContainer = document.getElementById('recent-executions-list');
            if (historyContainer && trades && Array.isArray(trades) && trades.length > 0) {
                let histHtml = '';
                // The API /api/v1/trades returns latest 100 trades (DESC order mapped from DB)
                trades.slice(0, 3).forEach(t => {
                    const pnlVal = parseFloat(t.pnl || 0);
                    const isProfit = pnlVal >= 0;
                    const sign = isProfit ? '+' : '';
                    const color = isProfit ? 'text-neon-green' : 'text-neon-red';
                    const bg = isProfit ? 'bg-navy-900/40 border-l-2 border-l-neon-green' : 'bg-navy-900/40 border-l-2 border-l-neon-red';
                    const shortSym = (t.symbol || 'UNKNOWN').split(':')[0];
                    const pnlStr = t.pnl_percent !== undefined && t.pnl_percent !== null ? `${sign}${parseFloat(t.pnl_percent).toFixed(2)}%` : `${sign}${pnlVal.toFixed(2)}`;
                    const feeStr = t.fee ? ` (F: ${parseFloat(t.fee).toFixed(3)})` : '';
                    const usdtStr = `(Net: ${pnlVal > 0 ? '+' : ''}${pnlVal.toFixed(2)})`;

                    histHtml += `
                        <div class="flex justify-between items-center text-[11px] ${bg} p-1.5 rounded border border-navy-border/50">
                            <div class="flex flex-col ml-1">
                                <span class="font-mono text-gray-300"><span class="${t.position_side === 'LONG' ? 'text-neon-green' : 'text-neon-red'} font-bold">${(t.position_side || 'UKNWN').substring(0, 1)}</span> · ${shortSym}</span>
                                <span class="font-mono text-[9px] text-gray-500">${usdtStr}${feeStr}</span>
                            </div>
                            <span class="font-mono ${color} font-bold mr-1 text-right text-[12px]">${pnlStr}</span>
                        </div>
                    `;
                });
                historyContainer.innerHTML = histHtml;
            } else if (historyContainer) {
                historyContainer.innerHTML = `<div class="flex items-center justify-center text-[10px] bg-navy-900/40 p-2 rounded text-gray-500 font-mono italic">No recent executions</div>`;
            }
        } catch (te) {
            console.warn("Trades Sync Failed:", te);
        }
    } catch (e) {
        console.warn("Stats Sync Failed:", e);
    }
}

// --- Manual Override ---
let isManualPanelOpen = false;

function toggleManualPanel() {
    isManualPanelOpen = !isManualPanelOpen;
    const panel = document.getElementById('manual-override-panel');
    const chevron = document.getElementById('manual-panel-chevron');

    if (panel && chevron) {
        if (isManualPanelOpen) {
            panel.classList.remove('hidden');
            chevron.classList.remove('-rotate-90');
        } else {
            panel.classList.add('hidden');
            chevron.classList.add('-rotate-90');
        }
    }
}

async function toggleManualOverride() {
    const enabled = document.getElementById('manual-override-toggle').checked;
    const status = document.getElementById('override-status');

    if (status) {
        status.textContent = enabled ? '활성 — 아래 설정값으로 자동매매' : '해제 — 잔고 비율 자동 계산';
        if (enabled) {
            status.classList.add('text-neon-green');
            status.classList.remove('text-gray-500');
        } else {
            status.classList.add('text-gray-500');
            status.classList.remove('text-neon-green');
        }
    }

    await fetch(`${API_URL}/config?key=manual_override_enabled&value=${enabled}`, { method: 'POST' });
}

async function saveManualOverride() {
    const btn = document.querySelector('[onclick="saveManualOverride()"]');
    try {
        const amount = document.getElementById('manual-amount').value;
        const leverage = document.getElementById('manual-leverage').value;
        await Promise.all([
            fetch(`${API_URL}/config?key=manual_amount&value=${encodeURIComponent(amount)}`, { method: 'POST' }),
            fetch(`${API_URL}/config?key=manual_leverage&value=${encodeURIComponent(leverage)}`, { method: 'POST' })
        ]);
        flashBtn(btn, true);
    } catch (error) {
        flashBtn(btn, false);
    }
}

// --- Shadow Mode (Paper Trading) ---
function applyShadowModeVisuals(enabled) {
    const watermark = document.getElementById('shadow-watermark');
    const header = document.querySelector('header');
    const status = document.getElementById('shadow-mode-status');
    const closeBtn = document.getElementById('btn-close-paper');
    if (watermark) watermark.classList.toggle('active', enabled);
    if (header) header.classList.toggle('shadow-active-glow', enabled);
    if (closeBtn) closeBtn.classList.toggle('hidden', !enabled);
    if (status) {
        status.textContent = enabled
            ? '활성 — 가상 매매 모드 (OKX API 미실행, PnL 시뮬레이션)'
            : '해제 — 실전 거래 모드 (OKX API 실행)';
        status.className = enabled
            ? 'text-[10px] font-mono text-purple-400 mt-1 px-1 transition-colors'
            : 'text-[10px] font-mono text-gray-500 mt-1 px-1 transition-colors';
    }
}

async function toggleShadowMode() {
    const toggle = document.getElementById('shadow-mode-toggle');
    const enabled = toggle ? toggle.checked : false;
    try {
        await fetch(`${API_URL}/config?key=SHADOW_MODE_ENABLED&value=${encodeURIComponent(enabled ? 'true' : 'false')}`, { method: 'POST' });
        applyShadowModeVisuals(enabled);
    } catch (error) {
        console.error('Shadow mode toggle failed:', error);
        if (toggle) toggle.checked = !enabled; // 롤백
    }
}

// --- Stress Injector (Fire Drill) ---
async function injectStress(type) {
    const labels = { KILL_SWITCH: '킬스위치 (-10% 폭락)', LOSS_STREAK: '3연패 쿨다운 (15분)' };
    if (!confirm(`🚨 [소방훈련] ${labels[type] || type}\n실제 방어 로직을 강제 발동시킵니다. 진행하시겠습니까?`)) return;
    try {
        const response = await fetch(`${API_URL}/inject_stress?type=${encodeURIComponent(type)}`, { method: 'POST' });
        const result = await response.json();
        if (result.error) throw new Error(result.error);
        alert(`🚨 ${result.message}`);
        updateLogs();
    } catch (error) {
        alert('스트레스 주입 실패: ' + error.message);
    }
}

async function resetStress() {
    try {
        const response = await fetch(`${API_URL}/reset_stress`, { method: 'POST' });
        const result = await response.json();
        if (result.error) throw new Error(result.error);
        alert(result.message);
        updateLogs();
    } catch (error) {
        alert('해제 실패: ' + error.message);
    }
}

async function closePaperPosition() {
    if (!confirm('Paper 포지션을 현재가 기준으로 강제 청산하시겠습니까?')) return;
    try {
        const response = await fetch(`${API_URL}/close_paper`, { method: 'POST' });
        const result = await response.json();
        if (result.error) throw new Error(result.error);
        alert('👻 Paper 포지션 청산 완료. 터미널 로그를 확인하세요.');
        updateLogs();
        syncBotStatus();
    } catch (error) {
        alert('Paper 청산 실패: ' + error.message);
    }
}

// --- Test Order Function ---
async function testOrder(direction) {
    try {
        const dir = (direction || 'LONG').toUpperCase();
        const response = await fetch(`${API_URL}/test_order?direction=${encodeURIComponent(dir)}`, { method: 'POST' });
        const result = await response.json();
        if (result.error) throw new Error(result.error);
        alert(`테스트 ${dir} 주문이 브레인으로 전송되었습니다. 터미널 로그를 확인하십시오.`);
        updateLogs();
        syncBotStatus();
    } catch (error) {
        alert('테스트 진입 실패: ' + error.message);
    }
}

// --- WebSocket (0.1s Ultra-low Latency) ---
let priceWs = null;
let _wsManualRestart = false; // 수동 재시작 시 onclose 자동 재연결 타이머 중복 방지

function initPriceWebSocket() {
    // OKX Public Demo Trading WebSocket URL
    const wsUrl = "wss://ws.okx.com:8443/ws/v5/public";

    if (priceWs) {
        _wsManualRestart = true; // onclose에서 5초 타이머 건너뜀
        priceWs.close();
    }

    priceWs = new WebSocket(wsUrl);

    // OKX WebSocket keepalive: 25초마다 ping 전송 (30초 타임아웃 방지)
    let _wsPingInterval = null;
    priceWs.addEventListener('open', () => {
        _wsPingInterval = setInterval(() => {
            if (priceWs && priceWs.readyState === WebSocket.OPEN) {
                priceWs.send('ping');
            } else {
                clearInterval(_wsPingInterval);
            }
        }, 25000);
    });
    priceWs.addEventListener('close', () => clearInterval(_wsPingInterval));

    priceWs.onopen = async () => {
        // Fetch current symbol to subscribe
        const response = await fetch(`${API_URL}/config`);
        const config = await response.json();
        const symbolRaw = Array.isArray(config.symbols) ? config.symbols[0] : 'BTC/USDT:USDT';

        // Convert symbol format. Ex: "BTC/USDT:USDT" -> "BTC-USDT-SWAP"
        let okxSymbol = symbolRaw.split(':')[0].replace('/', '-');
        if (symbolRaw.includes('USDT')) okxSymbol += '-SWAP';

        const subscribeMsg = {
            op: "subscribe",
            args: [{
                channel: "tickers",
                instId: okxSymbol
            }]
        };
        if (priceWs.readyState === WebSocket.OPEN) {
            priceWs.send(JSON.stringify(subscribeMsg));
            console.log("WebSocket connected. Subscribed strictly to: " + okxSymbol);
        }
    };

    priceWs.onmessage = (event) => {
        if (event.data === 'pong') return; // ping 응답 무시
        const data = JSON.parse(event.data);
        if (data && data.data && data.data.length > 0) {
            const ticker = data.data[0];
            const price = parseFloat(ticker.last);
            if (!isNaN(price)) {
                // 1. 메인 패널 현재가 초저지연 업데이트
                updatePriceWithTickFlash(price);

                // 2. 차트 마지막 캔들 실시간 업데이트 (close/high/low)
                if (candleSeries && lastCandleData) {
                    lastCandleData = {
                        ...lastCandleData,
                        close: price,
                        high: Math.max(lastCandleData.high, price),
                        low: Math.min(lastCandleData.low, price)
                    };
                    candleSeries.update(lastCandleData);
                }

                // 3. pos-current 마크가격 표시 (pos-roi는 백엔드 OKX 정확값으로 처리)
            }
        }
    };

    priceWs.onerror = (error) => {
        console.error("WebSocket Error: ", error);
    };

    priceWs.onclose = () => {
        if (_wsManualRestart) {
            _wsManualRestart = false; // 플래그 리셋 (수동 재시작이므로 타이머 없이 종료)
            return;
        }
        console.log("WebSocket disconnected. Auto-reconnecting in 5 seconds...");
        setTimeout(initPriceWebSocket, 5000); // 5초 후 자동 재시도
    };
}



// --- System Health Check (실제 API 핑 기반 — 표면 체크 아님) ---
async function syncSystemHealth() {
    try {
        const res = await fetch(`${API_URL}/system_health`);
        if (!res.ok) return;
        const data = await res.json();

        function applyBadge(dotId, textId, connected, connectedLabel) {
            const dot = document.getElementById(dotId);
            const text = document.getElementById(textId);
            if (!dot || !text) return;
            if (connected) {
                dot.className = 'w-2 h-2 rounded-full bg-neon-green animate-pulse transition-colors duration-500';
                text.textContent = connectedLabel || 'Connected';
                text.className = 'text-[10px] font-mono text-neon-green';
            } else {
                dot.className = 'w-2 h-2 rounded-full bg-red-500 transition-colors duration-500';
                text.textContent = 'Disconnected';
                text.className = 'text-[10px] font-mono text-red-400';
            }
        }

        applyBadge('badge-okx-dot', 'badge-okx-text', data.okx_connected, 'Connected');
        // Telegram: 실제 봇 이름도 표시 (빈 문자열이면 그냥 Connected)
        const tgLabel = data.telegram_connected
            ? (data.telegram_bot_name ? data.telegram_bot_name : 'Connected')
            : 'Disconnected';
        applyBadge('badge-tg-dot', 'badge-tg-text', data.telegram_connected, tgLabel);
        applyBadge('badge-engine-dot', 'badge-engine-text', data.strategy_engine_running, 'Running');

        const ts = document.getElementById('health-last-checked');
        if (ts) ts.textContent = `Last checked: ${new Date().toLocaleTimeString('ko-KR')}`;
    } catch (e) {
        console.warn('System health check failed:', e);
    }
}

// --- Init & Intervals (Parallel Optimization) ---
async function initializeApp() {
    // 순차적 페칭 대신 Promise.all을 활용해 병렬 스레드로 대기 시간 단축
    initPriceWebSocket(); // 웹소켓 즉각 연결
    initChart();
    await Promise.all([
        syncConfig(),
        syncBotStatus(),
        syncBrain(),
        syncStats(),
        syncChart(),
        updateLogs(),
        syncSystemHealth(),
        fetchAndRenderHeatmap(),  // 히트맵 초기 렌더링
    ]);

    // 초기 렌더링 후 타이머 설정
    setInterval(syncBotStatus, 1000);
    setInterval(syncBrain, 3000);       // Brain (RSI/MACD/price) - status와 분리
    setInterval(syncChart, 5000);
    setInterval(syncStats, 5000);
    setInterval(updateLogs, 3000);
    setInterval(syncConfig, 30000);     // 외부 설정 변경 자동 반영
    setInterval(syncSystemHealth, 5000); // 헬스 체크 (5초 — 매매 뇌 부하 최소화)
    setInterval(fetchAndRenderHeatmap, 60000); // 히트맵 1분마다 갱신
}

// Start
initializeApp();

// ════════════ DB Wipe ════════════

async function wipeDatabase() {
    const input = prompt('⚠️ 경고: 이 작업은 모든 거래 기록을 영구 삭제합니다.\n실전 투입 준비가 완료된 경우에만 실행하세요.\n\n초기화하려면 아래에 정확히 CONFIRM 을 입력하세요:');
    if (input === null) return; // 취소
    if (input.trim() !== 'CONFIRM') {
        alert('입력값이 일치하지 않습니다. 초기화가 취소되었습니다.');
        return;
    }

    try {
        const res = await fetch(`${API_URL}/wipe_db`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            alert('✅ DB 초기화 완료. 실전 매매 준비 상태로 전환됩니다.');
            location.reload();
        } else {
            alert(`❌ 초기화 실패: ${data.message}`);
        }
    } catch (e) {
        alert(`❌ 서버 통신 오류: ${e.message}`);
        console.error('wipeDatabase Error:', e);
    }
}

// ════════════ History Modal ════════════

let _historyData = null;

function _renderHistoryTable(bodyId, rows) {
    const tbody = document.getElementById(bodyId);
    if (!tbody) return;

    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-gray-600 font-mono text-[11px]">기록 없음</td></tr>`;
        return;
    }

    tbody.innerHTML = rows.map(row => {
        const netColor = row.net_pnl >= 0 ? 'text-neon-green' : 'text-neon-red';
        const grossColor = row.gross_pnl >= 0 ? 'text-neon-green' : 'text-gray-400';
        const netSign = row.net_pnl >= 0 ? '+' : '';
        const grossSign = row.gross_pnl >= 0 ? '+' : '';
        return `
            <tr class="border-b border-navy-border/40 hover:bg-navy-800/40 transition-colors">
                <td class="py-2.5 text-left text-gray-300">${row.date}</td>
                <td class="py-2.5 text-right text-gray-400">${row.total_trades}</td>
                <td class="py-2.5 text-right ${row.win_rate >= 50 ? 'text-neon-green' : 'text-neon-red'}">${row.win_rate.toFixed(2)}%</td>
                <td class="py-2.5 text-right ${grossColor}">${grossSign}${row.gross_pnl.toFixed(4)}</td>
                <td class="py-2.5 text-right font-bold ${netColor}">${netSign}${row.net_pnl.toFixed(4)}</td>
            </tr>`;
    }).join('');
}

async function openHistoryModal() {
    const modal = document.getElementById('history-modal');
    if (!modal) return;

    // 로딩 상태 초기화
    const dailyBody = document.getElementById('history-daily-body');
    const monthlyBody = document.getElementById('history-monthly-body');
    if (dailyBody) dailyBody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-gray-600 font-mono text-[11px]">데이터 로딩 중...</td></tr>`;
    if (monthlyBody) monthlyBody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-gray-600 font-mono text-[11px]">데이터 로딩 중...</td></tr>`;

    modal.classList.remove('hidden');
    switchHistoryTab('daily');

    try {
        const res = await fetch(`${API_URL}/history_stats`);
        _historyData = await res.json();
        _renderHistoryTable('history-daily-body', _historyData.daily || []);
        _renderHistoryTable('history-monthly-body', _historyData.monthly || []);
        // 히트맵도 동일 데이터로 갱신 (별도 fetch 없이 재사용)
        renderHeatmap(_historyData.daily || []);
    } catch (e) {
        if (dailyBody) dailyBody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-neon-red font-mono text-[11px]">데이터 로드 실패</td></tr>`;
        if (monthlyBody) monthlyBody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-neon-red font-mono text-[11px]">데이터 로드 실패</td></tr>`;
        console.error("History Stats Fetch Failed:", e);
    }
}

function closeHistoryModal() {
    const modal = document.getElementById('history-modal');
    if (modal) modal.classList.add('hidden');
}

function switchHistoryTab(tab) {
    const dailyTab = document.getElementById('history-tab-daily');
    const monthlyTab = document.getElementById('history-tab-monthly');
    const dailyBtn = document.getElementById('tab-btn-daily');
    const monthlyBtn = document.getElementById('tab-btn-monthly');

    const activeClass = ['border-neon-green', 'text-neon-green', 'bg-neon-green/10'];
    const inactiveClass = ['border-navy-border', 'text-gray-500', 'bg-transparent'];

    if (tab === 'daily') {
        if (dailyTab) dailyTab.classList.remove('hidden');
        if (monthlyTab) monthlyTab.classList.add('hidden');
        if (dailyBtn) { activeClass.forEach(c => dailyBtn.classList.add(c)); inactiveClass.forEach(c => dailyBtn.classList.remove(c)); }
        if (monthlyBtn) { inactiveClass.forEach(c => monthlyBtn.classList.add(c)); activeClass.forEach(c => monthlyBtn.classList.remove(c)); }
    } else {
        if (dailyTab) dailyTab.classList.add('hidden');
        if (monthlyTab) monthlyTab.classList.remove('hidden');
        if (monthlyBtn) { activeClass.forEach(c => monthlyBtn.classList.add(c)); inactiveClass.forEach(c => monthlyBtn.classList.remove(c)); }
        if (dailyBtn) { inactiveClass.forEach(c => dailyBtn.classList.add(c)); activeClass.forEach(c => dailyBtn.classList.remove(c)); }
    }
}

// 모달 외부 클릭 시 닫기
document.addEventListener('click', (e) => {
    const modal = document.getElementById('history-modal');
    if (modal && !modal.classList.contains('hidden') && e.target === modal) {
        closeHistoryModal();
    }
});

// ══════════════════════════════════════

// ════════════ PnL Heatmap ════════════

/**
 * renderHeatmap(dailyData)
 * - dailyData: /api/v1/history_stats의 daily 배열 [{ date, net_pnl, total_trades }, ...]
 * - 최근 26주(182일) GitHub 스타일 그리드를 #pnl-heatmap에 렌더링
 * - 외부 라이브러리 없이 순수 JS/HTML로 구현
 */
function renderHeatmap(dailyData) {
    const container = document.getElementById('pnl-heatmap');
    if (!container) return;

    // ── PnL 맵 구성 ──
    const pnlMap = {}; // { 'YYYY-MM-DD': { net_pnl, total_trades } }
    if (Array.isArray(dailyData)) {
        dailyData.forEach(d => {
            pnlMap[d.date] = { net_pnl: parseFloat(d.net_pnl || 0), total_trades: d.total_trades || 0 };
        });
    }

    // ── 색상 스케일 기준값 산출 (빈 배열 안전 처리) ──
    const profits = Object.values(pnlMap).map(v => v.net_pnl).filter(v => v > 0);
    const losses  = Object.values(pnlMap).map(v => v.net_pnl).filter(v => v < 0);
    const maxProfit = profits.length > 0 ? Math.max(...profits) : 1;
    const maxLoss   = losses.length  > 0 ? Math.abs(Math.min(...losses)) : 1;

    function _cellColor(dateStr) {
        const d = pnlMap[dateStr];
        if (!d || d.net_pnl === 0) return '#161b22'; // 거래 없음
        const pnl = d.net_pnl;
        if (pnl > 0) {
            const r = Math.min(pnl / maxProfit, 1);
            if (r < 0.25) return '#0e4429';
            if (r < 0.5)  return '#006d32';
            if (r < 0.75) return '#26a641';
            return '#39d353';
        } else {
            const r = Math.min(Math.abs(pnl) / maxLoss, 1);
            if (r < 0.25) return '#3d0000';
            if (r < 0.5)  return '#7a0000';
            if (r < 0.75) return '#b00020';
            return '#ff4d4d';
        }
    }

    // ── 날짜 범위 생성: KST 기준 오늘부터 26주(182일) 전까지 ──
    // KST 오늘 날짜를 UTC Date 객체로 계산
    const kstNow  = new Date(Date.now() + 9 * 3600 * 1000);
    const todayKst = new Date(Date.UTC(kstNow.getUTCFullYear(), kstNow.getUTCMonth(), kstNow.getUTCDate()));
    const todayStr = todayKst.toISOString().split('T')[0];

    // 26주 전 일요일(getUTCDay()=0)까지 롤백하여 그리드 시작점 확정
    const startDate = new Date(todayKst);
    startDate.setUTCDate(startDate.getUTCDate() - 26 * 7);
    const startOffset = startDate.getUTCDay(); // 0=Sun ... 6=Sat
    startDate.setUTCDate(startDate.getUTCDate() - startOffset); // 해당 주의 일요일로 정렬

    // ── 주(Week) 단위 열 생성 ──
    const weeks = [];
    const cur = new Date(startDate);
    while (cur <= todayKst) {
        const week = [];
        for (let dow = 0; dow < 7; dow++) {
            week.push(cur.toISOString().split('T')[0]);
            cur.setUTCDate(cur.getUTCDate() + 1);
        }
        weeks.push(week);
    }

    // ── HTML 구성 ──
    let html = '';
    weeks.forEach(week => {
        html += `<div class="flex flex-col shrink-0" style="gap:2px;">`;
        week.forEach(dateStr => {
            const isFuture = dateStr > todayStr;
            if (isFuture) {
                // 미래 날짜: 투명 칸
                html += `<div style="width:11px;height:11px;border-radius:2px;background:transparent;"></div>`;
                return;
            }
            const color  = _cellColor(dateStr);
            const d      = pnlMap[dateStr];
            const pnlVal = d ? d.net_pnl : 0;
            const pnlStr = pnlVal >= 0 ? `+${pnlVal.toFixed(2)}` : `${pnlVal.toFixed(2)}`;
            const trades = d ? d.total_trades : 0;
            html += `<div
                class="heatmap-cell"
                style="width:11px;height:11px;border-radius:2px;background:${color};cursor:default;"
                data-date="${dateStr}"
                data-pnl="${pnlVal}"
                data-trades="${trades}"
                data-label="${dateStr} | Net PnL: ${pnlStr} USDT | 거래: ${trades}건"
            ></div>`;
        });
        html += `</div>`;
    });
    container.innerHTML = html;

    // ── 커스텀 툴팁 이벤트 바인딩 ──
    const tooltip = document.getElementById('heatmap-tooltip');
    if (!tooltip) return;
    container.querySelectorAll('.heatmap-cell').forEach(cell => {
        cell.addEventListener('mousemove', (e) => {
            tooltip.textContent = cell.dataset.label;
            tooltip.classList.remove('hidden');
            tooltip.style.left = (e.clientX + 14) + 'px';
            tooltip.style.top  = (e.clientY - 32) + 'px';
        });
        cell.addEventListener('mouseleave', () => {
            tooltip.classList.add('hidden');
        });
    });
}

/** history_stats를 fetch 후 히트맵 렌더링 (페이지 로드 & 주기적 갱신용) */
async function fetchAndRenderHeatmap() {
    try {
        const res  = await fetch(`${API_URL}/history_stats`);
        const data = await res.json();
        // 모달이 열려있을 경우 테이블도 함께 갱신 (데이터 일관성)
        if (_historyData === null) _historyData = data;
        renderHeatmap(data.daily || []);
    } catch (e) {
        console.warn('Heatmap fetch failed:', e);
    }
}

// ════════════ CSV Download ════════════

function downloadCSV() {
    const a = document.createElement('a');
    a.href = `${API_URL}/export_csv`;
    a.download = 'antigravity_trades.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// ══════════════════════════════════════
