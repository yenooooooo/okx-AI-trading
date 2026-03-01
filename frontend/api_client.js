const API_URL = `/api/v1`;
let chart = null;
let candleSeries = null;
let lastLogId = 0;
let lastCandleData = null;   // WebSocket 실시간 캔들 업데이트용
let currentSymbol = 'BTC/USDT:USDT'; // 현재 감시 심볼 캐시 (syncConfig에서 갱신)
let isInitialLogLoad = true; // 초기 로드 폭탄 방어: false 전환 후부터 토스트 발생
const processedLogIds = new Set(); // Race condition 방어: 이미 렌더링된 로그 ID 기록
let currentLogFilter = 'ALL';      // 터미널 카테고리 필터 현재 상태
let isTerminalPaused = false;      // Smart Auto-Scroll: 사용자가 위를 보고 있으면 true
let unreadLogCount = 0;            // Smart Auto-Scroll: 일시정지 중 누적된 미확인 로그 수

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
        ERROR: { border: '#ff4d4d', titleColor: '#ff4d4d', icon: '🚨', bg: 'rgba(255,77,77,0.06)' },
        INFO: { border: '#60a5fa', titleColor: '#60a5fa', icon: '⚡', bg: 'rgba(96,165,250,0.06)' },
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

    // [최적화] 100달러 미만 코인(XRP, DOGE 등)은 소수점 4자리, 그 이상은 2자리로 동적 출력
    const decimals = price < 100 ? 4 : 2;
    const formattedPrice = price.toFixed(decimals);

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

// --- [CORE] Deep Sync 헬퍼 — 타겟 변경 시 모든 신경망 일괄 초기화 (단일 진실 소스) ---
async function executeDeepSync(newSymbol) {
    // 1. 글로벌 심볼 즉각 갱신
    currentSymbol = newSymbol;

    // 2. 조준경 뱃지 갱신
    const targetBadge = document.getElementById('hero-target-badge');
    if (targetBadge) targetBadge.textContent = newSymbol;
    // [Phase 18.1] 좌측 패널 심볼 배지 즉시 갱신
    const leftSymBadge = document.getElementById('left-panel-symbol-badge');
    if (leftSymBadge) leftSymBadge.textContent = newSymbol.split(':')[0];
    // [Phase 18.1] 모달 심볼 드롭다운 동기화
    const modalSymSel = document.getElementById('modal-target-symbol');
    if (modalSymSel) modalSymSel.value = newSymbol;

    // 3. Ghost Data 방지 — 가격 및 차트 즉시 초기화
    const heroPriceEl = document.getElementById('hero-price');
    if (heroPriceEl) heroPriceEl.textContent = '---';
    if (candleSeries) candleSeries.setData([]);

    // 4. 혼잣말 리셋
    const feed = document.getElementById('monologue-feed');
    if (feed) feed.innerHTML = `<div class="text-[11px] font-mono text-neon-green italic animate-pulse">🎯 [${newSymbol}] 조준 완료. 데이터 딥싱크(Deep Sync) 중...</div>`;

    // 5. 그리드 버튼 UI 활성 상태 갱신
    document.querySelectorAll('.target-coin-btn').forEach(btn => {
        if (btn.dataset.symbol === newSymbol) {
            btn.className = 'target-coin-btn flex items-center justify-center text-xs py-2 rounded font-mono font-bold transition-all border border-neon-green text-neon-green bg-neon-green/10';
        } else {
            btn.className = 'target-coin-btn flex items-center justify-center text-xs py-2 rounded font-mono font-bold transition-all border border-navy-border/50 bg-navy-900/40 text-gray-500 hover:text-gray-300';
        }
    });

    // 6. 신경망 재연결 — 웹소켓 즉각 연결 후 차트·뇌 병렬 완료 대기
    // initPriceWebSocket은 동기식이므로 먼저 실행, syncChart·syncBrain은 Promise.all로 병렬 처리
    initPriceWebSocket();
    await Promise.all([syncChart(), syncBrain()]);
}

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
        // Chimera 버그 수정: Object.keys()[0]은 메모리 순서 기반이라 타겟 변경 후에도 구형 심볼을 가리킴.
        // active_target은 서버가 get_config('symbols')[0]을 직접 읽어 반환하므로 항상 최신 타겟이 보장됨.
        const activeTarget = data.active_target || Object.keys(symbols)[0];
        const symbolData = activeTarget ? symbols[activeTarget] : null;

        // --- Auto-Tracking: 백엔드 타겟 변경 자동 감지 → Deep Sync 트리거 ---
        if (activeTarget && activeTarget !== currentSymbol) {
            const posTypeEl = document.getElementById('pos-type');
            const posType = posTypeEl ? posTypeEl.textContent.trim() : 'NONE';
            if (!posType || posType === 'NONE') {
                executeDeepSync(activeTarget);
                return; // Deep Sync 후 이 사이클의 나머지 UI 업데이트 스킵 (다음 폴링에서 정상 처리)
            }
        }

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
                posSymbolEl.textContent = activeTarget.split(':')[0];
                posSymbolEl.classList.remove('hidden');
            }

            // [Phase 24] PENDING 상태 시 철거 버튼 노출 / 아닐 시 숨김
            const _isPending = symbolData.position && symbolData.position.startsWith('PENDING');
            const _abortBtn = document.getElementById('btn-abort-pending');
            if (_abortBtn) _abortBtn.classList.toggle('hidden', !_isPending);

            // 웹소켓(priceWs)이 연결되어 있을 때는 REST API 구형 가격/수익률 데이터 표시는 무시.
            // (단, 포지션 유무, 진입가, 목표가 등 고정데이터는 계속 연동)
            updateText('pos-type', symbolData.position);
            updateNumberText('pos-entry', symbolData.entry_price);
            // TP/SL 상태 동기화 (백엔드 실시간 계산값 기반)
            const realSl = parseFloat(symbolData.real_sl || 0);

            // [Phase 16] 다이내믹 목표가 렌더링 (백엔드에서 완성된 문자열이 넘어옴)
            const posTpEl = document.getElementById('pos-tp');
            if (posTpEl) {
                const tpVal = symbolData.take_profit_price;
                if (tpVal && tpVal !== 0 && tpVal !== '0.0') {
                    posTpEl.textContent = tpVal;
                } else {
                    posTpEl.textContent = '대기중';
                }
            }
            // pos-tp-expect는 trailing 상태에 맞게 유지
            const trailingActive = symbolData.trailing_active === true;
            const trailingTarget = parseFloat(symbolData.trailing_target || 0);
            if (trailingActive && trailingTarget > 0) {
                updateText('pos-tp-expect', 'Trailing Active 🎯');
            } else {
                updateText('pos-tp-expect', '1차 익절 대기 중 ⏳');
            }

            updateNumberText('pos-sl', realSl > 0 ? realSl : 0);
            updateText('pos-sl-expect', realSl > 0 ? '(Dynamic)' : '');

            // PnL(%) 및 USDT 수익금 동기화
            const pnl = parseFloat(symbolData.unrealized_pnl_percent || 0);
            const pnlUsdt = parseFloat(symbolData.unrealized_pnl || 0);
            const pnlSign = pnl >= 0 ? '+' : '';

            updateNumberText('pos-roi', pnl, val => `${pnlSign}${val.toFixed(2)}%`);
            updateNumberText('pos-pnl-usdt', pnlUsdt, val => `${pnlSign}${val.toFixed(2)} USDT`);
            // [최적화] 현재가는 카운트업 애니메이션 제거하고 즉각 반영 (지연시간 0)
            const posCurrentEl = document.getElementById('pos-current');
            if (posCurrentEl) {
                const p = parseFloat(symbolData.current_price);
                const decimals = p < 100 ? 4 : 2;
                const newText = p.toFixed(decimals);
                if (posCurrentEl.textContent !== newText) {
                    posCurrentEl.textContent = newText;
                    // 미세 깜빡임 효과만 유지
                    posCurrentEl.classList.remove('flash');
                    void posCurrentEl.offsetWidth;
                    posCurrentEl.classList.add('flash');
                }
            }

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

        // 4. Engine Live Status Badge
        if (data.engine_status) {
            const badgeEl = document.getElementById('engine-live-badge');
            if (badgeEl) {
                if (data.engine_status.mode === 'AUTO') {
                    badgeEl.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold border flex items-center gap-1.5 transition-all bg-blue-500/10 border-blue-500/50 text-blue-400';
                    badgeEl.innerHTML = `<span class="animate-pulse">🤖</span> 순정 AI 다이내믹 연산 중`;
                } else {
                    badgeEl.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold border flex items-center gap-1.5 transition-all bg-orange-500/10 border-orange-500/50 text-orange-400';
                    badgeEl.innerHTML = `<span class="animate-pulse">⚙️</span> 수동 통제 중 (Risk: ${data.engine_status.risk}%)`;
                }
            }
        }

        // 5. [Phase 25] Adaptive Shield 방어 티어 배지 실시간 렌더링
        const tierBadge = document.getElementById('adaptive-tier-badge');
        if (tierBadge) {
            const tierName = data.adaptive_tier || '';
            const tierMap = {
                'CRITICAL': { emoji: '🔴', cls: 'border-red-500/50 bg-red-500/10 text-red-400', label: 'CRITICAL — 긴급 방어' },
                'MICRO':    { emoji: '🟡', cls: 'border-yellow-500/50 bg-yellow-500/10 text-yellow-400', label: 'MICRO — 소액 보호' },
                'STANDARD': { emoji: '🟢', cls: 'border-green-500/50 bg-green-500/10 text-green-400', label: 'STANDARD — 표준 운용' },
                'GROWTH':   { emoji: '🔵', cls: 'border-blue-500/50 bg-blue-500/10 text-blue-400', label: 'GROWTH — 성장 추종' },
            };
            const t = tierMap[tierName];
            if (t) {
                tierBadge.textContent = `${t.emoji} ${t.label}`;
                tierBadge.className = `inline-block px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold tracking-wider border ${t.cls}`;
            } else {
                tierBadge.textContent = '🛡️ OFF — 수동 모드';
                tierBadge.className = 'inline-block px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold tracking-wider border border-gray-600/50 bg-gray-800/50 text-gray-500';
            }
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
        // active_target을 서버로부터 직접 수신해 brainState 조회 키로 사용
        // (currentSymbol은 executeDeepSync 호출 직후 갱신 전 구형 심볼일 수 있으므로 신뢰도 낮음)
        const activeTarget = brainData.active_target || currentSymbol;
        const brainState = symbolBrains[activeTarget] || Object.values(symbolBrains)[0];

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

        // UI 개선: 세로 구분선(border-l)과 '목표:' 레이블을 추가하여 가독성 극대화
        const targetHtml = g.target ? `<span class="text-[9px] text-gray-500 ml-1.5 border-l border-gray-600/50 pl-1.5 tracking-wider">목표: ${g.target}</span>` : '';

        if (g.pass) {
            el.innerHTML = `<span class="text-neon-green text-[10px] mr-1">✅</span><span class="text-neon-green font-bold text-[11px]">${g.value}</span>${targetHtml}`;
        } else {
            el.innerHTML = `<span class="text-neon-red text-[10px] mr-1">❌</span><span class="text-gray-400 font-bold text-[11px]">${g.value}</span>${targetHtml}`;
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

// --- [C] 활성 전술 프리셋 감지 및 뱃지 렌더링 ---
function updateActiveTuningBadge() {
    const badge = document.getElementById('active-tuning-badge');
    if (!badge) return;

    // DOM 에서 현재 튜닝 파라미터 값 수집
    const currentVals = {};
    for (const [key, { id, parse }] of Object.entries(TUNING_INPUT_MAP)) {
        const input = document.getElementById(id);
        currentVals[key] = input ? parse(input.value) : NaN;
    }

    const PRESET_LABELS = {
        sniper: ['🎯 스나이퍼', 'text-yellow-300 border-yellow-500/50 bg-yellow-500/10'],
        trend_rider: ['🌊 트렌드라이더', 'text-blue-300 border-blue-500/50 bg-blue-500/10'],
        scalper: ['⚡ 스캘퍼', 'text-neon-green border-neon-green/50 bg-neon-green/10'],
        iron_dome: ['🛡️ 아이언돔', 'text-orange-300 border-orange-500/50 bg-orange-500/10'],
        factory_reset: ['🏭 팩토리', 'text-gray-300 border-gray-500/50 bg-gray-500/10'],
        micro_seed: ['💎 마이크로', 'text-emerald-300 border-emerald-500/50 bg-emerald-500/10'],
    };

    let matchedLabel = null;
    let matchedClass = null;

    for (const [presetName, presetVals] of Object.entries(PRESET_CONFIGS)) {
        const keys = Object.keys(TUNING_INPUT_MAP);
        const isMatch = keys.every(key => {
            const { parse } = TUNING_INPUT_MAP[key];
            const cur = currentVals[key];
            const pre = presetVals[key];
            if (pre === undefined) return true; // 프리셋에 없는 키는 무시
            // 정수형(parseInt) 비교: 정수 비교, 부동소수점(parseFloat): 소수 오차 허용
            if (parse === parseInt) return Math.round(cur) === Math.round(pre);
            return Math.abs(cur - pre) < 0.00001;
        });
        if (isMatch && PRESET_LABELS[presetName]) {
            [matchedLabel, matchedClass] = PRESET_LABELS[presetName];
            break;
        }
    }

    if (matchedLabel) {
        badge.textContent = matchedLabel;
        badge.className = `px-1.5 py-0.5 rounded font-mono text-[9px] border transition-all ${matchedClass}`;
    } else {
        badge.textContent = '🛠️ 커스텀';
        badge.className = 'px-1.5 py-0.5 rounded font-mono text-[9px] border text-purple-300 border-purple-500/50 bg-purple-500/10 transition-all';
    }
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

// 원클릭 전술 프리셋 정의 — 5가지 매매 스타일별 10개 파라미터 완전 매핑
const PRESET_CONFIGS = {
    sniper: {
        adx_threshold: 30.0, adx_max: 45.0, chop_threshold: 55.0,
        volume_surge_multiplier: 2.0, fee_margin: 0.002,
        hard_stop_loss_rate: 0.008, trailing_stop_activation: 0.005,
        trailing_stop_rate: 0.003, cooldown_losses_trigger: 2, cooldown_duration_sec: 1800,
    },
    trend_rider: {
        adx_threshold: 25.0, adx_max: 60.0, chop_threshold: 58.0,
        volume_surge_multiplier: 1.3, fee_margin: 0.001,
        hard_stop_loss_rate: 0.010, trailing_stop_activation: 0.005,
        trailing_stop_rate: 0.004, cooldown_losses_trigger: 4, cooldown_duration_sec: 600,
    },
    scalper: {
        adx_threshold: 20.0, adx_max: 50.0, chop_threshold: 65.0,
        volume_surge_multiplier: 1.2, fee_margin: 0.002,
        hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.002,
        trailing_stop_rate: 0.001, cooldown_losses_trigger: 5, cooldown_duration_sec: 300,
    },
    iron_dome: {
        adx_threshold: 28.0, adx_max: 42.0, chop_threshold: 50.0,
        volume_surge_multiplier: 2.5, fee_margin: 0.002,
        hard_stop_loss_rate: 0.004, trailing_stop_activation: 0.004,
        trailing_stop_rate: 0.002, cooldown_losses_trigger: 2, cooldown_duration_sec: 3600,
    },
    factory_reset: {
        adx_threshold: 25.0, adx_max: 40.0, chop_threshold: 61.8,
        volume_surge_multiplier: 1.5, fee_margin: 0.0015,
        hard_stop_loss_rate: 0.005, trailing_stop_activation: 0.003,
        trailing_stop_rate: 0.002, cooldown_losses_trigger: 3, cooldown_duration_sec: 900,
    },
    // [Phase 14.3] 초단타 광기 모드 — 모든 방어 관문 해제 + 틱 단위 익절
    // [Phase 18.1] risk_per_trade / leverage 는 시드 보호 설정으로 프리셋에서 완전 제거 (PROTECTED_KEYS)
    frenzy: {
        adx_threshold: 15.0,            // 추세 기준 대폭 완화 (낮은 ADX도 진입 허용)
        chop_threshold: 60.0,           // 횡보 허용치 증가
        volume_surge_multiplier: 1.2,   // 거래량 기준 완화
        disparity_threshold: 3.0,       // 이격도 한계치 3% (UI 슬라이더 % 단위)
        hard_stop_loss_rate: 0.005,     // 0.5% 칼손절 (비율: 0.005)
        trailing_stop_activation: 0.003, // 0.3% 수익 시 트레일링 즉시 ON (비율: 0.003)
        trailing_stop_rate: 0.001,      // 고점 대비 0.1% 낙폭 시 틱 익절 (비율: 0.001)
        cooldown_losses_trigger: 3,     // 3연패 시 쿨다운
        cooldown_duration_sec: 300,     // 5분 휴식 (초단타 특성상 짧게)
        // ── Gate Bypass: 3개 방어 관문 전면 해제 ──
        bypass_macro: 'true',
        bypass_disparity: 'true',
        bypass_indicator: 'true',
    },
    // [Phase 24] 마이크로 시드 — $10~100 소액 계좌 최적화 (R:R 1:2 강제, 저빈도 고확률)
    micro_seed: {
        adx_threshold: 28.0,             // 강한 추세에서만 진입 (노이즈 제거)
        adx_max: 50.0,                   // 강추세 허용 범위 확대
        chop_threshold: 55.0,            // 횡보장 필터 강화 (명확한 추세만)
        volume_surge_multiplier: 1.8,    // 볼륨 확인 강화
        fee_margin: 0.002,               // 수수료 버퍼 확대 (소액 수수료 비중 높음)
        hard_stop_loss_rate: 0.005,      // 0.5% SL 유지 (자본 보호)
        trailing_stop_activation: 0.01,  // 1.0% 수익 후 트레일링 시작 (수익 충분히 성장)
        trailing_stop_rate: 0.005,       // 0.5% 트레일링 거리 (넓은 호흡)
        min_take_profit_rate: 0.01,      // 1.0% 최소 익절 목표 (R:R 1:2 강제)
        cooldown_losses_trigger: 2,      // 2연패 시 쿨다운 (빠른 방어)
        cooldown_duration_sec: 1800,     // 30분 쿨다운 (충분한 냉각)
    },
};

// 리스크 온도계 — risk_per_trade 입력값에 따른 실시간 위험도 안내
function updateRiskThermometer(value) {
    const el = document.getElementById('risk-thermometer-text');
    if (!el) return;
    const v = parseFloat(value);
    if (isNaN(v) || String(value).trim() === '') {
        el.className = 'text-[10px] font-mono mt-1.5 transition-colors duration-300 text-gray-500';
        el.textContent = '리스크 비율을 입력하면 AI가 위험도를 분석합니다.';
        return;
    }
    if (v <= 2) {
        el.className = 'text-[10px] font-mono mt-1.5 transition-colors duration-300 text-neon-green';
        el.textContent = '🛡️ 방어력 극대화 모드. 안전한 복리 우상향을 지향합니다.';
    } else if (v <= 5) {
        el.className = 'text-[10px] font-mono mt-1.5 transition-colors duration-300 text-yellow-400';
        el.textContent = '⚖️ 표준 밸런스 모드. 적절한 수익과 리스크를 동반합니다.';
    } else {
        el.className = 'text-[10px] font-mono mt-1.5 transition-colors duration-300 text-orange-500 font-bold animate-pulse';
        el.textContent = '⚠️ 초고위험 세팅! 단 1번의 손절로 시드의 큰 비중이 증발할 수 있습니다.';
    }
}

// [Phase 18.1] 프리셋이 절대 변경해서는 안 되는 시드 보호 설정 키 집합
const PRESET_PROTECTED_KEYS = new Set(['risk_per_trade', 'leverage']);

// 튜닝 파라미터 맵 — syncConfig() 와 saveTuningConfig() 공유 단일 진실 소스
const TUNING_INPUT_MAP = {
    'leverage': { id: 'config-leverage', parse: parseInt },  // [Phase 18.1] 모달 Section 1으로 이관
    'risk_per_trade': { id: 'config-risk_per_trade', parse: v => parseFloat(v) / 100 },
    'adx_threshold': { id: 'tuning-adx-threshold', parse: parseFloat },
    'adx_max': { id: 'tuning-adx-max', parse: parseFloat },
    'chop_threshold': { id: 'tuning-chop-threshold', parse: parseFloat },
    'volume_surge_multiplier': { id: 'tuning-volume-surge', parse: parseFloat },
    'fee_margin': { id: 'tuning-fee-margin', parse: parseFloat },
    'hard_stop_loss_rate': { id: 'tuning-hard-stop-loss', parse: parseFloat },
    'trailing_stop_activation': { id: 'tuning-trailing-activation', parse: parseFloat },
    'trailing_stop_rate': { id: 'tuning-trailing-rate', parse: parseFloat },
    'cooldown_losses_trigger': { id: 'tuning-cooldown-losses', parse: parseInt },
    'cooldown_duration_sec': { id: 'tuning-cooldown-duration', parse: parseInt },
    'disparity_threshold': { id: 'config-disparity_threshold', parse: parseFloat },  // [Phase 14.2] DB: % 단위 저장
    'min_take_profit_rate': { id: 'tuning-min-tp-rate', parse: parseFloat },  // [Phase 24] 최소 익절 목표율
};

// --- Config Sync ---
// [Phase 18.1] symbol 파라미터 지원: 심볼 전용 설정을 로드하여 모달 입력창 일괄 갱신
async function syncConfig(symbol = null) {
    try {
        const url = symbol ? `${API_URL}/config?symbol=${encodeURIComponent(symbol)}` : `${API_URL}/config`;
        const response = await fetch(url);
        const configs = await response.json();
        for (const [key, val] of Object.entries(configs)) {
            if (key === 'risk_per_trade') {
                const tuningInput = document.getElementById('config-risk_per_trade');
                const v = parseFloat(val) * 100;
                if (tuningInput) { tuningInput.value = v.toFixed(1); updateRiskThermometer(v); }
                updateText('risk-val-display', v.toFixed(1) + '%', false);
                // [Phase 18.1] 좌측 패널 리스크 배지 갱신
                const leftRiskBadge = document.getElementById('left-panel-risk-badge');
                if (leftRiskBadge) leftRiskBadge.textContent = v.toFixed(1) + '%';
            } else if (key === 'leverage') {
                const input = document.getElementById('config-leverage');
                if (input) { input.value = parseInt(val); input.dispatchEvent(new Event('input')); }
                updateText('lev-val-display', parseInt(val) + 'x', false);
                // [Phase 18.1] 좌측 패널 레버리지 배지 갱신
                const leftLevBadge = document.getElementById('left-panel-lev-badge');
                if (leftLevBadge) leftLevBadge.textContent = parseInt(val) + 'x';
            } else if (key === 'direction_mode') {
                // [Phase 18.1] 방향 모드 버튼 UI 동기화
                _applyDirectionModeUI(String(val).toUpperCase());
            } else if (key === 'symbols') {
                const activeSymbol = Array.isArray(val) && val.length > 0 ? val[0] : null;
                if (activeSymbol) currentSymbol = activeSymbol;
                // [Phase 18.1] 좌측 패널 심볼 배지 갱신
                const leftSymBadge = document.getElementById('left-panel-symbol-badge');
                if (leftSymBadge && activeSymbol) leftSymBadge.textContent = activeSymbol.split(':')[0];
                // [Phase 18.1] 모달 심볼 드롭다운 동기화
                const modalSymSel = document.getElementById('modal-target-symbol');
                if (modalSymSel && activeSymbol) modalSymSel.value = activeSymbol;
                // 타겟 그리드 버튼 활성 상태 동기화
                document.querySelectorAll('.target-coin-btn').forEach(btn => {
                    if (btn.dataset.symbol === activeSymbol) {
                        btn.className = 'target-coin-btn text-xs py-2 rounded font-mono font-bold transition-all flex items-center justify-center border border-neon-green text-neon-green bg-neon-green/10';
                    } else {
                        btn.className = 'target-coin-btn text-xs py-2 rounded font-mono font-bold transition-all flex items-center justify-center border border-navy-border/50 bg-navy-900/40 text-gray-500 hover:text-gray-300';
                    }
                });
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
                // 서버 상태와 시각적 경계 모드를 항상 일치시킴 (페이지 로드/30초 주기 동기화)
                toggleOverrideVisuals(enabled);
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
            } else if (key === 'auto_scan_enabled') {
                const toggle = document.getElementById('auto-scan-toggle');
                const track = document.getElementById('auto-scan-track');
                const thumb = document.getElementById('auto-scan-thumb');
                const enabled = val === true || val === 'true';
                if (toggle) toggle.checked = enabled;
                if (track) track.className = `block w-8 h-4 rounded-full border transition-colors ${enabled ? 'bg-neon-green/30 border-neon-green' : 'bg-navy-900 border-navy-border'}`;
                if (thumb) thumb.className = `absolute top-0.5 w-3 h-3 rounded-full transition-all ${enabled ? 'bg-neon-green left-4' : 'bg-gray-500 left-0.5'}`;
            } else if (key === 'SHADOW_MODE_ENABLED') {
                const toggle = document.getElementById('shadow-mode-toggle');
                const enabled = val === true || val === 'true';
                if (toggle) toggle.checked = enabled;
                applyShadowModeVisuals(enabled);
            } else if (key in TUNING_INPUT_MAP) {
                const { id, parse } = TUNING_INPUT_MAP[key];
                const input = document.getElementById(id);
                if (input) input.value = parse(val);
            } else if (key === 'disparity_threshold') {
                // [Phase 14.2] 이격도 슬라이더 + 표시 스팬 동시 갱신
                const slider = document.getElementById('config-disparity_threshold');
                const span = document.getElementById('val-disparity');
                const v = parseFloat(val);
                if (slider) slider.value = v;
                if (span) span.textContent = v.toFixed(1) + '%';
            } else if (['bypass_macro', 'bypass_disparity', 'bypass_indicator', 'exit_only_mode', 'shadow_hunting_enabled', 'auto_preset_enabled'].includes(key)) {
                // [Phase 14.1] Gate Bypass 체크박스 동기화 + [Phase 23] Shadow Hunting + [Phase 25] Adaptive Shield
                const el = document.getElementById(`config-${key}`);
                if (el) el.checked = (val === true || val === 'true');
            }
        }
        updateActiveTuningBadge();
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncConfig 실패 (엔드포인트: /api/v1/config GET):", error);
    }
}

// --- Engine Tuning Modal ---
async function applyPreset(presetName) {
    const config = PRESET_CONFIGS[presetName];
    if (!config) return;

    // 1. 숫자/범위 인풋: TUNING_INPUT_MAP 기준으로 ID 해석 → 값 주입 + 애니메이션 + input 이벤트
    // [Phase 18.1] PRESET_PROTECTED_KEYS(risk_per_trade, leverage)는 절대 건드리지 않음
    for (const [key, { id }] of Object.entries(TUNING_INPUT_MAP)) {
        if (PRESET_PROTECTED_KEYS.has(key)) continue;  // 시드 보호 설정 격리
        if (!(key in config)) continue;
        const input = document.getElementById(id);
        if (!input) continue;
        input.value = config[key];
        // reflow trick: 연속 클릭 시에도 애니메이션 재시작 보장
        input.classList.remove('preset-flash');
        void input.offsetWidth;
        input.classList.add('preset-flash');
        // oninput 연결 UI(온도계, val-disparity 스팬 등) 즉각 갱신
        input.dispatchEvent(new Event('input'));
    }

    // 2. [Phase 14.1/14.3] Gate Bypass 체크박스: 프리셋에 포함된 경우 강제 동기화
    for (const bkey of ['bypass_macro', 'bypass_disparity', 'bypass_indicator', 'exit_only_mode', 'shadow_hunting_enabled', 'auto_preset_enabled']) {
        if (!(bkey in config)) continue;
        const el = document.getElementById(`config-${bkey}`);
        if (!el) continue;
        el.checked = (config[bkey] === 'true' || config[bkey] === true);
        el.dispatchEvent(new Event('input'));
    }

    // 3. 값 주입 직후 서버에 즉시 일괄 저장
    await saveTuningConfig();
    // 4. 프리셋 적용 직후 뱃지 즉시 갱신
    updateActiveTuningBadge();
}

async function openTuningModal() {
    const modal = document.getElementById('tuning-modal');
    if (modal) modal.classList.remove('hidden');
    // [Phase 18.1] 현재 심볼의 전용 설정값을 즉시 로드하여 입력창 갱신
    await syncConfig(currentSymbol);
}

// [Phase 18.1] 방향 모드 UI 적용 헬퍼 (DRY)
function _applyDirectionModeUI(mode) {
    document.querySelectorAll('.direction-mode-btn').forEach(btn => {
        const isActive = btn.dataset.mode === mode;
        if (isActive) {
            btn.classList.add('dir-active');
            if (mode === 'LONG') {
                btn.className = 'direction-mode-btn dir-active flex-1 py-2 text-[10px] font-mono font-bold rounded border border-neon-green text-neon-green bg-neon-green/10 transition';
            } else if (mode === 'SHORT') {
                btn.className = 'direction-mode-btn dir-active flex-1 py-2 text-[10px] font-mono font-bold rounded border border-neon-red text-neon-red bg-neon-red/10 transition';
            } else {
                btn.className = 'direction-mode-btn dir-active flex-1 py-2 text-[10px] font-mono font-bold rounded border border-neon-green text-neon-green bg-neon-green/10 transition';
            }
        } else {
            btn.classList.remove('dir-active');
            btn.className = 'direction-mode-btn flex-1 py-2 text-[10px] font-mono font-bold rounded border border-navy-border text-gray-500 transition hover:border-gray-400 hover:text-gray-300';
        }
    });
}

// [Phase 18.1] 방향 모드 버튼 클릭 핸들러 — UI 즉시 반영 + 백엔드 저장 (코인별)
async function setDirectionMode(mode) {
    _applyDirectionModeUI(mode);
    try {
        const saveSymbol = currentSymbol || 'GLOBAL';
        await fetch(`${API_URL}/config?key=direction_mode&value=${encodeURIComponent(mode)}&symbol=${encodeURIComponent(saveSymbol)}`, { method: 'POST' });
    } catch (e) {
        console.error('[ANTIGRAVITY] setDirectionMode 실패:', e);
    }
}

// [Phase 23] 그림자 사냥(Shadow Hunting) 모드 토글 — 백엔드 즉시 저장
async function toggleShadowHunting(enabled) {
    try {
        await fetch(`${API_URL}/config?key=shadow_hunting_enabled&value=${encodeURIComponent(enabled ? 'true' : 'false')}`, { method: 'POST' });
    } catch (e) {
        console.error('[ANTIGRAVITY] toggleShadowHunting 실패:', e);
    }
}

// [Phase 25] Adaptive Shield 토글 — 잔고 기반 자동 방어 프리셋 ON/OFF
async function toggleAutoPreset(enabled) {
    try {
        await fetch(`${API_URL}/config?key=auto_preset_enabled&value=${encodeURIComponent(enabled ? 'true' : 'false')}`, { method: 'POST' });
        const label = enabled ? '활성화' : '비활성화';
        showToast('Adaptive Shield', `자동 방어 프리셋이 ${label}되었습니다.`, 'SUCCESS');
    } catch (e) {
        console.error('[ANTIGRAVITY] toggleAutoPreset 실패:', e);
    }
}

// [Phase 27] 전체 서브시스템 자가 진단 (Full Diagnostic)
async function runDiagnostic() {
    const btn = document.getElementById('diagnostic-btn');
    const modal = document.getElementById('diagnostic-modal');
    const container = document.getElementById('diag-results-container');
    const summary = document.getElementById('diag-summary');
    const tsEl = document.getElementById('diag-timestamp');

    // 버튼 로딩 상태
    if (btn) { btn.textContent = '⏳ 진단 중...'; btn.disabled = true; }

    // 모달 열기
    if (modal) modal.classList.remove('hidden');
    if (container) container.innerHTML = '<div class="text-center text-gray-500 font-mono text-xs py-8 animate-pulse">🔍 10개 서브시스템 점검 중...</div>';

    try {
        const resp = await fetch(`${API_URL}/diagnostic`);
        const data = await resp.json();
        const items = data.diagnostic || [];

        // 아이콘 매핑
        const iconMap = { PASS: '🟢', FAIL: '🔴', WARN: '🟡', INFO: '🔵' };
        const bgMap = {
            PASS: 'border-emerald-500/30 bg-emerald-500/5',
            FAIL: 'border-red-500/30 bg-red-500/5',
            WARN: 'border-yellow-500/30 bg-yellow-500/5',
            INFO: 'border-blue-500/30 bg-blue-500/5'
        };

        // 결과 렌더링
        let html = '';
        items.forEach((item, idx) => {
            const icon = iconMap[item.status] || '⚪';
            const bg = bgMap[item.status] || 'border-gray-600/30 bg-gray-800/30';
            const hasDetails = item.details && Object.keys(item.details).length > 0;
            html += `<div class="border ${bg} rounded-lg p-3 transition-all">`;
            html += `<div class="flex items-start gap-2 ${hasDetails ? 'cursor-pointer' : ''}" ${hasDetails ? `onclick="toggleDiagDetail(${idx})"` : ''}>`;
            html += `<span class="text-sm flex-shrink-0 mt-0.5">${icon}</span>`;
            html += `<div class="flex-1 min-w-0">`;
            html += `<div class="flex items-center gap-2">`;
            html += `<span class="text-[11px] font-mono font-bold text-text-main">${item.name}</span>`;
            html += `<span class="text-[9px] font-mono px-1.5 py-0.5 rounded ${item.status === 'PASS' ? 'bg-emerald-500/20 text-emerald-400' : item.status === 'FAIL' ? 'bg-red-500/20 text-red-400' : item.status === 'WARN' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-blue-500/20 text-blue-400'}">${item.status}</span>`;
            if (hasDetails) html += `<span class="text-[9px] text-gray-600 font-mono" id="diag-arrow-${idx}">▸ 상세</span>`;
            html += `</div>`;
            html += `<p class="text-[10px] font-mono text-gray-400 mt-1 break-all">${item.message}</p>`;
            html += `</div></div>`;
            // 접히는 상세 영역
            if (hasDetails) {
                html += `<div id="diag-detail-${idx}" class="hidden mt-2 ml-6 p-2 rounded bg-black/30 border border-gray-700/40">`;
                html += `<pre class="text-[9px] font-mono text-gray-500 whitespace-pre-wrap break-all">${JSON.stringify(item.details, null, 2)}</pre>`;
                html += `</div>`;
            }
            html += `</div>`;
        });
        if (container) container.innerHTML = html;

        // 요약 표시
        const s = data.summary || {};
        if (summary) summary.innerHTML = `<span class="text-emerald-400">${s.pass || 0} PASS</span> · <span class="text-red-400">${s.fail || 0} FAIL</span> · <span class="text-yellow-400">${s.warn || 0} WARN</span> · <span class="text-blue-400">${s.info || 0} INFO</span>`;
        if (tsEl) tsEl.textContent = data.timestamp ? `진단 시각: ${new Date(data.timestamp).toLocaleString('ko-KR')}` : '';

        // 토스트
        const totalFail = s.fail || 0;
        if (totalFail === 0) {
            showToast('시스템 진단', `전체 ${s.total || items.length}개 항목 점검 완료 — 이상 없음`, 'SUCCESS');
        } else {
            showToast('시스템 진단', `${totalFail}개 항목에서 문제 발견`, 'ERROR');
        }
    } catch (e) {
        console.error('[ANTIGRAVITY] diagnostic 실패:', e);
        if (container) container.innerHTML = `<div class="text-center text-red-400 font-mono text-xs py-8">진단 실패: ${e.message}</div>`;
        showToast('시스템 진단', '진단 요청 실패', 'ERROR');
    } finally {
        if (btn) { btn.textContent = '🔍 시스템 진단'; btn.disabled = false; }
    }
}

// [Phase 27] 진단 상세 접기/펼치기
function toggleDiagDetail(idx) {
    const el = document.getElementById(`diag-detail-${idx}`);
    const arrow = document.getElementById(`diag-arrow-${idx}`);
    if (el) {
        const isHidden = el.classList.contains('hidden');
        el.classList.toggle('hidden');
        if (arrow) arrow.textContent = isHidden ? '▾ 접기' : '▸ 상세';
    }
}

// ════════════ [Phase 33] 연결 상태 종합 점검 (Health Check Dashboard) ════════════

async function runHealthCheck() {
    const btn = document.getElementById('health-check-btn');
    const modal = document.getElementById('health-modal');
    const body = document.getElementById('hc-body');
    const summaryEl = document.getElementById('hc-summary');
    const tsEl = document.getElementById('hc-timestamp');
    const progressEl = document.getElementById('hc-progress');

    if (btn) { btn.textContent = '⏳ 점검 중...'; btn.disabled = true; }
    if (modal) modal.classList.remove('hidden');
    if (body) body.innerHTML = '<div class="text-center text-gray-500 font-mono text-xs py-8 animate-pulse">🔗 전체 연결 상태 종합 점검 중...</div>';

    let totalOk = 0, totalFail = 0, totalWarn = 0, totalChecks = 0;

    try {
        if (progressEl) progressEl.textContent = '백엔드 인프라 점검 중...';
        const [backendResult, pingResults, wsResult] = await Promise.all([
            _hcFetchBackend(),
            _hcPingSweep(progressEl),
            _hcTestWebSocket()
        ]);

        let html = '';
        html += _hcRenderSection('🏗️ 백엔드 인프라', backendResult.checks);
        html += _hcRenderEndpointSection('📡 API 엔드포인트 연결', pingResults);
        html += _hcRenderSection('🔌 WebSocket 연결', wsResult);
        html += _hcRenderButtonMap(backendResult.endpoints, pingResults);

        if (body) body.innerHTML = html;

        const allChecks = [...backendResult.checks, ...pingResults, ...wsResult];
        allChecks.forEach(c => {
            totalChecks++;
            if (c.status === 'OK') totalOk++;
            else if (c.status === 'FAIL') totalFail++;
            else totalWarn++;
        });

        if (summaryEl) {
            summaryEl.innerHTML = `<span class="text-emerald-400">${totalOk} OK</span> · <span class="text-red-400">${totalFail} FAIL</span> · <span class="text-yellow-400">${totalWarn} WARN</span>`;
        }
        if (tsEl) tsEl.textContent = `점검 시각: ${new Date().toLocaleString('ko-KR')}`;
        if (progressEl) progressEl.textContent = '';

        if (totalFail === 0) {
            showToast('연결 점검', `전체 ${totalChecks}개 항목 정상`, 'SUCCESS');
        } else {
            showToast('연결 점검', `${totalFail}개 연결 실패 감지`, 'ERROR');
        }
    } catch (e) {
        console.error('[ANTIGRAVITY] health check error:', e);
        if (body) body.innerHTML = `<div class="text-center text-red-400 font-mono text-xs py-8">점검 실패: ${e.message}</div>`;
        showToast('연결 점검', '점검 요청 실패', 'ERROR');
    } finally {
        if (btn) { btn.textContent = '🔗 연결 점검'; btn.disabled = false; }
    }
}

async function _hcFetchBackend() {
    try {
        const resp = await fetch(`${API_URL}/health_check`);
        return await resp.json();
    } catch (e) {
        return {
            checks: [{ id: 'backend_unreachable', name: '백엔드 서버', status: 'FAIL', latency_ms: 0, details: `서버 응답 없음: ${e.message}` }],
            endpoints: [],
            summary: { ok: 0, fail: 1, warn: 0, total: 1 }
        };
    }
}

async function _hcPingSweep(progressEl) {
    const getEndpoints = [
        { path: '/status', name: '봇 상태 (status)' },
        { path: '/brain', name: 'AI 뇌 (brain)' },
        { path: '/config', name: '설정 (config)' },
        { path: '/trades', name: '거래 내역 (trades)' },
        { path: '/stats', name: '성과 통계 (stats)' },
        { path: '/logs?limit=1', name: '로그 (logs)' },
        { path: '/symbols', name: '심볼 목록 (symbols)' },
        { path: '/system_health', name: '시스템 헬스 (system_health)' },
        { path: '/ohlcv?symbol=BTC/USDT:USDT&limit=1', name: '차트 데이터 (ohlcv)' },
        { path: '/stress_bypass', name: '바이패스 (stress_bypass)' },
        { path: '/history_stats', name: '기간별 통계 (history_stats)' },
    ];

    const results = [];
    let completed = 0;

    const promises = getEndpoints.map(async (ep) => {
        const t0 = performance.now();
        try {
            const resp = await fetch(`${API_URL}${ep.path}`);
            const latency = Math.round(performance.now() - t0);
            completed++;
            if (progressEl) progressEl.textContent = `엔드포인트 점검 ${completed}/${getEndpoints.length}`;
            return {
                id: `ping_${ep.path.split('?')[0].replace(/\//g, '')}`,
                name: ep.name,
                status: resp.ok ? 'OK' : 'WARN',
                latency_ms: latency,
                details: resp.ok ? `HTTP ${resp.status} (${latency}ms)` : `HTTP ${resp.status} 응답 오류 (${latency}ms)`
            };
        } catch (e) {
            completed++;
            if (progressEl) progressEl.textContent = `엔드포인트 점검 ${completed}/${getEndpoints.length}`;
            return {
                id: `ping_${ep.path.split('?')[0].replace(/\//g, '')}`,
                name: ep.name,
                status: 'FAIL',
                latency_ms: Math.round(performance.now() - t0),
                details: `연결 실패: ${e.message}`
            };
        }
    });

    const settled = await Promise.allSettled(promises);
    settled.forEach(r => { if (r.status === 'fulfilled') results.push(r.value); });
    return results;
}

async function _hcTestWebSocket() {
    const results = [];

    // Test 1: Dashboard WebSocket
    try {
        const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProto}//${location.host}/ws/dashboard`;
        const result = await new Promise((resolve) => {
            const t0 = performance.now();
            const ws = new WebSocket(wsUrl);
            const timeout = setTimeout(() => {
                ws.close();
                resolve({ id: 'ws_dashboard', name: '대시보드 WebSocket (/ws/dashboard)', status: 'FAIL', latency_ms: 5000, details: '연결 시간 초과 (5초)' });
            }, 5000);
            ws.onopen = () => {
                const latency = Math.round(performance.now() - t0);
                clearTimeout(timeout);
                ws.close();
                resolve({ id: 'ws_dashboard', name: '대시보드 WebSocket (/ws/dashboard)', status: 'OK', latency_ms: latency, details: `연결 성공 (${latency}ms)` });
            };
            ws.onerror = () => {
                const latency = Math.round(performance.now() - t0);
                clearTimeout(timeout);
                resolve({ id: 'ws_dashboard', name: '대시보드 WebSocket (/ws/dashboard)', status: 'FAIL', latency_ms: latency, details: `연결 실패 (${latency}ms)` });
            };
        });
        results.push(result);
    } catch (e) {
        results.push({ id: 'ws_dashboard', name: '대시보드 WebSocket (/ws/dashboard)', status: 'FAIL', latency_ms: 0, details: `오류: ${e.message}` });
    }

    // Test 2: OKX Public WebSocket (기존 연결 상태 확인)
    const okxWsOk = typeof priceWs !== 'undefined' && priceWs && priceWs.readyState === WebSocket.OPEN;
    results.push({
        id: 'ws_okx_public', name: 'OKX Public WebSocket (시세 수신)',
        status: okxWsOk ? 'OK' : 'WARN',
        latency_ms: 0,
        details: okxWsOk ? '실시간 시세 수신 중' : 'WebSocket 미연결 (자동 재연결 대기)'
    });

    return results;
}

function _hcRenderSection(title, checks) {
    if (!checks || checks.length === 0) return '';
    const iconMap = { OK: '🟢', FAIL: '🔴', WARN: '🟡' };
    const bgMap = {
        OK: 'border-emerald-500/30 bg-emerald-500/5',
        FAIL: 'border-red-500/30 bg-red-500/5',
        WARN: 'border-yellow-500/30 bg-yellow-500/5'
    };
    let html = `<div>`;
    html += `<h3 class="text-[11px] font-mono font-bold text-gray-300 tracking-widest uppercase mb-2">${title}</h3>`;
    html += `<div class="space-y-1.5">`;
    checks.forEach(c => {
        const icon = iconMap[c.status] || '⚪';
        const bg = bgMap[c.status] || 'border-gray-600/30 bg-gray-800/30';
        const latencyTag = c.latency_ms > 0
            ? `<span class="text-[9px] font-mono ${c.latency_ms > 1000 ? 'text-red-400' : c.latency_ms > 300 ? 'text-yellow-400' : 'text-gray-500'}">${c.latency_ms}ms</span>`
            : '';
        html += `<div class="border ${bg} rounded-lg px-3 py-2 flex items-center gap-2">`;
        html += `<span class="text-sm flex-shrink-0">${icon}</span>`;
        html += `<div class="flex-1 min-w-0">`;
        html += `<div class="flex items-center gap-2">`;
        html += `<span class="text-[10px] font-mono font-bold text-text-main">${c.name}</span>`;
        html += `<span class="text-[9px] font-mono px-1.5 py-0.5 rounded ${c.status === 'OK' ? 'bg-emerald-500/20 text-emerald-400' : c.status === 'FAIL' ? 'bg-red-500/20 text-red-400' : 'bg-yellow-500/20 text-yellow-400'}">${c.status}</span>`;
        html += latencyTag;
        html += `</div>`;
        html += `<p class="text-[9px] font-mono text-gray-500 mt-0.5 truncate">${c.details}</p>`;
        html += `</div></div>`;
    });
    html += `</div></div>`;
    return html;
}

function _hcRenderEndpointSection(title, checks) {
    if (!checks || checks.length === 0) return '';
    const iconMap = { OK: '🟢', FAIL: '🔴', WARN: '🟡' };
    const bgMap = {
        OK: 'border-emerald-500/30 bg-emerald-500/5',
        FAIL: 'border-red-500/30 bg-red-500/5',
        WARN: 'border-yellow-500/30 bg-yellow-500/5'
    };
    let html = `<div>`;
    html += `<h3 class="text-[11px] font-mono font-bold text-gray-300 tracking-widest uppercase mb-2">${title}</h3>`;
    html += `<div class="grid grid-cols-1 md:grid-cols-2 gap-1.5">`;
    checks.forEach(c => {
        const icon = iconMap[c.status] || '⚪';
        const bg = bgMap[c.status] || 'border-gray-600/30 bg-gray-800/30';
        const barPct = Math.min(100, (c.latency_ms / 2000) * 100);
        const barColor = c.latency_ms > 1000 ? 'bg-red-500/40' : c.latency_ms > 300 ? 'bg-yellow-500/40' : 'bg-emerald-500/40';
        html += `<div class="border ${bg} rounded px-2.5 py-1.5 relative overflow-hidden">`;
        html += `<div class="absolute inset-y-0 left-0 ${barColor} transition-all" style="width:${barPct}%"></div>`;
        html += `<div class="relative flex items-center gap-1.5">`;
        html += `<span class="text-xs flex-shrink-0">${icon}</span>`;
        html += `<span class="text-[10px] font-mono text-text-main flex-1 truncate">${c.name}</span>`;
        html += `<span class="text-[9px] font-mono ${c.latency_ms > 1000 ? 'text-red-400' : c.latency_ms > 300 ? 'text-yellow-400' : 'text-emerald-400'} flex-shrink-0">${c.latency_ms}ms</span>`;
        html += `</div></div>`;
    });
    html += `</div></div>`;
    return html;
}

function _hcRenderButtonMap(endpoints, pingResults) {
    if (!endpoints || endpoints.length === 0) return '';
    const pingMap = {};
    pingResults.forEach(p => {
        const pathKey = p.id.replace('ping_', '');
        pingMap[pathKey] = p;
    });

    const buttonMap = [
        { button: '▶ 시작/중지', endpoint: 'POST /api/v1/toggle' },
        { button: '🔍 시스템 진단', endpoint: 'GET /api/v1/diagnostic' },
        { button: '🔗 연결 점검', endpoint: 'GET /api/v1/health_check' },
        { button: '⚙ 마스터 튜닝', endpoint: 'POST /api/v1/config' },
        { button: '📈 LONG 진입', endpoint: 'POST /api/v1/test_order' },
        { button: '📉 SHORT 진입', endpoint: 'POST /api/v1/test_order' },
        { button: '👻 Paper 청산', endpoint: 'POST /api/v1/close_paper' },
        { button: '🚫 매복 철거', endpoint: 'POST /api/v1/cancel_pending' },
        { button: '💾 설정 저장', endpoint: 'POST /api/v1/config' },
        { button: '🤖 AUTO 리셋', endpoint: 'POST /api/v1/tuning/reset' },
        { button: '📊 기간 통계', endpoint: 'GET /api/v1/history_stats' },
        { button: '📥 CSV 내보내기', endpoint: 'GET /api/v1/export_csv' },
        { button: '🗑 DB 초기화', endpoint: 'POST /api/v1/wipe_db' },
    ];

    const registeredPaths = new Set(endpoints.map(ep => `${ep.method} ${ep.path}`));

    let html = `<div>`;
    html += `<h3 class="text-[11px] font-mono font-bold text-gray-300 tracking-widest uppercase mb-2">🗺️ 버튼-API 매핑</h3>`;
    html += `<div class="overflow-x-auto"><table class="w-full text-[10px] font-mono">`;
    html += `<thead><tr class="text-gray-500 border-b border-navy-border">`;
    html += `<th class="text-left py-1.5 px-2">버튼</th>`;
    html += `<th class="text-left py-1.5 px-2">엔드포인트</th>`;
    html += `<th class="text-center py-1.5 px-2">상태</th>`;
    html += `</tr></thead><tbody>`;

    buttonMap.forEach(bm => {
        const method = bm.endpoint.split(' ')[0];
        const path = bm.endpoint.split(' ')[1];
        let statusIcon = '⚪';
        let statusText = '미확인';

        if (method === 'GET') {
            const shortPath = path.replace('/api/v1/', '');
            const ping = pingMap[shortPath];
            if (ping) {
                statusIcon = ping.status === 'OK' ? '🟢' : ping.status === 'FAIL' ? '🔴' : '🟡';
                statusText = `${ping.status} (${ping.latency_ms}ms)`;
            }
        } else {
            const registered = registeredPaths.has(bm.endpoint);
            statusIcon = registered ? '🟢' : '🔴';
            statusText = registered ? '등록됨' : '미등록';
        }

        html += `<tr class="border-b border-navy-border/30 hover:bg-navy-800/30">`;
        html += `<td class="py-1.5 px-2 text-gray-300">${bm.button}</td>`;
        html += `<td class="py-1.5 px-2 text-gray-500">${bm.endpoint}</td>`;
        html += `<td class="py-1.5 px-2 text-center">${statusIcon} <span class="text-[9px]">${statusText}</span></td>`;
        html += `</tr>`;
    });

    html += `</tbody></table></div></div>`;
    return html;
}

// [Phase 24] 매복 주문 수동 철수 — 사령관 즉시 개입
async function abortPendingOrder() {
    if (!confirm("대기 중인 매복 주문을 즉시 취소하시겠습니까?")) return;
    try {
        const response = await fetch(`${API_URL}/cancel_pending`, { method: 'POST' });
        const result = await response.json();
        if (result.status === 'success') {
            showToast('매복 철거 성공', '대기 주문이 정상적으로 취소되었습니다.', 'SUCCESS');
            syncBotStatus();
        } else {
            showToast('철거 실패', result.message, 'ERROR');
        }
    } catch (error) {
        console.error('[ANTIGRAVITY] abortPendingOrder 오류:', error);
        showToast('오류', '서버와 통신할 수 없습니다.', 'ERROR');
    }
}

// [Phase 18.1] 모달 심볼 드롭다운 변경 핸들러 — 해당 코인의 과거 기억 즉시 로드
async function onModalSymbolChange(newSymbol) {
    if (!newSymbol) return;
    await setTargetSymbol(newSymbol);
    await syncConfig(newSymbol);
}


function closeTuningModal() {
    const modal = document.getElementById('tuning-modal');
    if (modal) modal.classList.add('hidden');
}

async function saveTuningConfig() {
    const btn = document.querySelector('#tuning-modal button[onclick="saveTuningConfig()"]');
    try {
        // [Phase 18.1] 현재 감시 심볼로 코인별 독립 저장 (GLOBAL Fallback 포함)
        const saveSymbol = currentSymbol || 'GLOBAL';

        // 1. 유효성 검사 & payload 조립 (TUNING_INPUT_MAP 단일 진실 소스 활용)
        const payloads = [];
        for (const [key, { id, parse }] of Object.entries(TUNING_INPUT_MAP)) {
            const input = document.getElementById(id);
            if (!input) continue;
            const value = parse(input.value);
            if (isNaN(value)) throw new Error(`${key}: 유효하지 않은 숫자입니다.`);
            payloads.push({ key, value: String(value) });
        }
        // [Phase 14.1] Gate Bypass 체크박스 추가 저장 (exit_only_mode, shadow_hunting_enabled, auto_preset_enabled 포함)
        for (const bkey of ['bypass_macro', 'bypass_disparity', 'bypass_indicator', 'exit_only_mode', 'shadow_hunting_enabled', 'auto_preset_enabled']) {
            const el = document.getElementById(`config-${bkey}`);
            if (!el) continue;
            payloads.push({ key: bkey, value: el.checked.toString() });
        }
        // [Phase 18.1] 방향 모드 저장
        const activeDir = document.querySelector('.direction-mode-btn.dir-active');
        const dirMode = activeDir ? activeDir.dataset.mode : 'AUTO';
        payloads.push({ key: 'direction_mode', value: dirMode });

        // 2. 병렬 POST (Query Param 방식 + 심볼 전용 저장)
        await Promise.all(payloads.map(payload =>
            fetch(`${API_URL}/config?key=${encodeURIComponent(payload.key)}&value=${encodeURIComponent(payload.value)}&symbol=${encodeURIComponent(saveSymbol)}`, {
                method: 'POST'
            })
        ));
        // 3. 버튼 플래시 피드백
        if (btn) {
            const origText = btn.textContent;
            btn.textContent = '✓ APPLIED';
            btn.classList.add('border-neon-green', 'text-neon-green', 'bg-neon-green/10');
            btn.classList.remove('border-purple-500', 'text-purple-300', 'bg-purple-500/20');
            setTimeout(() => {
                btn.textContent = origText;
                btn.classList.remove('border-neon-green', 'text-neon-green', 'bg-neon-green/10');
                btn.classList.add('border-purple-500', 'text-purple-300', 'bg-purple-500/20');
            }, 2000);
        }
        showToast('엔진 튜닝 적용', '11개 파라미터 저장 완료. 백엔드 매매 엔진에 즉각 반영됩니다.', 'SUCCESS');
        syncBotStatus(); // 런타임 엔진 상태 뱃지 즉시 갱신
    } catch (error) {
        console.error('[ANTIGRAVITY 디버그] saveTuningConfig 실패:', error);
        showToast('저장 실패', error.message || '서버 통신 오류가 발생했습니다.', 'ERROR');
    }
}

async function resetToAuto() {
    // 모달창과 메인화면에 있는 모든 AUTO 버튼을 선택
    const btns = document.querySelectorAll('button[onclick="resetToAuto()"]');
    try {
        btns.forEach(btn => {
            btn.disabled = true;
            btn.dataset.origText = btn.textContent.trim();
            btn.textContent = '⏳ 리셋 중...';
        });
        // 1. 서버: DB 튜닝 키 전체 삭제 + 전략 인스턴스 재생성 딥 리셋
        const res = await fetch(`${API_URL}/tuning/reset`, { method: 'POST' });
        if (!res.ok) throw new Error(`서버 응답 오류 (${res.status})`);
        // 2. 팩토리 리셋 프리셋으로 UI 입력창 값 동기화 및 DB 재저장
        await applyPreset('factory_reset');
        showToast('AI 순정 모드 복귀 완료', '모든 튜닝값이 리셋되고 가장 똑똑한 본래의 뇌로 복귀했습니다.', 'SUCCESS');
    } catch (error) {
        console.error('[ANTIGRAVITY 디버그] resetToAuto 실패:', error);
        showToast('리셋 실패', error.message || '서버 통신 오류가 발생했습니다.', 'ERROR');
    } finally {
        btns.forEach(btn => {
            btn.disabled = false;
            btn.textContent = btn.dataset.origText || '🤖 AUTO RESET';
        });
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

        // Stale Response 방어: 요청 시점의 심볼을 캡처. 응답 도착 시 currentSymbol과 비교하여
        // 그 사이 타겟이 바뀐 경우 구형 데이터를 즉시 폐기한다.
        const requestedSymbol = currentSymbol;

        const response = await fetch(`${API_URL}/ohlcv?symbol=${encodeURIComponent(requestedSymbol)}&limit=60`);
        const ohlcv = await response.json();

        // 1차 Stale Check: OHLCV 응답 도착 후 심볼이 바뀌었으면 폐기
        if (requestedSymbol !== currentSymbol) return;

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

            // 2차 Stale Check: 거래 내역 응답 도착 후 심볼이 바뀌었으면 마커 렌더링 폐기
            if (requestedSymbol !== currentSymbol) return;

            if (Array.isArray(allTrades) && allTrades.length > 0) {
                // 요청 시점 심볼(requestedSymbol)과 일치하는 거래만 필터링
                const symbolTrades = allTrades.filter(t => t.symbol === requestedSymbol);
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
                                time: entryTs,
                                position: posType === 'LONG' ? 'belowBar' : 'aboveBar',
                                color: posType === 'LONG' ? '#00ff88' : '#ff4d4d',
                                shape: posType === 'LONG' ? 'arrowUp' : 'arrowDown',
                                text: posType === 'LONG' ? '🟢 LONG 진입' : '🔴 SHORT 진입',
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
                                time: exitTs,
                                position: posType === 'LONG' ? 'aboveBar' : 'belowBar',
                                color: isProfit ? '#00ff88' : '#ff4d4d',
                                shape: 'circle',
                                text: isProfit ? '✅ 익절' : '💀 손절',
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

// --- Terminal Syntax Highlighter ---
/**
 * formatTerminalMsg(rawMsg)
 * 원시 로그 문자열을 Cyberpunk 구문 강조 HTML로 변환.
 * 처리 순서: ① 뱃지 치환 → ② 가격 → ③ 수익률 → ④ 방향성
 * 각 단계의 치환 결과가 다음 단계의 패턴과 충돌하지 않도록 순서를 고정.
 */
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

// --- Quick Allocator: 현재 잔고의 N% → USDT 증거금 주입 ---
function setQuickAmount(percent) {
    const balanceEl = document.getElementById('current-balance');
    if (!balanceEl) return;
    // current-balance는 "75.43" 형태 (updateNumberText가 toFixed(2)로 렌더링)
    const balance = parseFloat(balanceEl.textContent.replace(/,/g, ''));
    if (!balance || balance <= 0) return;

    const usdt = Math.floor(balance * percent / 100);
    const input = document.getElementById('manual-amount');
    const display = document.getElementById('manual-amount-display');
    if (input) input.value = usdt;
    if (display) display.textContent = usdt;

    // 0.3초 플래시 피드백 (bg-neon-green/20)
    if (input) {
        input.classList.add('bg-neon-green/20');
        setTimeout(() => input.classList.remove('bg-neon-green/20'), 300);
    }
    // 퀵 할당 후 견적서 즉시 갱신
    updateOrderPreview();
}

// --- Live Order Receipt: 실시간 주문 견적서 프리뷰 계산 엔진 ---
function updateOrderPreview() {
    const totalVolumeEl = document.getElementById('preview-total-volume');
    const estQtyEl = document.getElementById('preview-est-qty');
    if (!totalVolumeEl || !estQtyEl) return;

    const amount = parseFloat(document.getElementById('manual-amount')?.value) || 0;
    const leverage = parseFloat(document.getElementById('manual-leverage')?.value) || 1;
    const totalVolume = amount * leverage;

    // 콤마 포함 숫자 문자열 안전 파싱 (예: "95,234.12" → 95234.12)
    const heroPriceRaw = document.getElementById('hero-price')?.textContent.replace(/,/g, '') || '0';
    const currentPrice = parseFloat(heroPriceRaw) || 0;

    // 총 타격 볼륨 렌더링
    totalVolumeEl.textContent = totalVolume.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }) + ' USDT';

    // 예상 확보 수량 렌더링
    const coinName = currentSymbol.split('/')[0]; // "BTC/USDT:USDT" → "BTC"
    if (currentPrice <= 0) {
        estQtyEl.textContent = '대기중...';
    } else {
        const estQty = totalVolume / currentPrice;
        estQtyEl.textContent = `≈ ${estQty.toFixed(4)} ${coinName}`;
    }
}

// --- Override Visual Alert: 수동 오버라이드 ON/OFF 경계 모드 동기화 ---
function toggleOverrideVisuals(isActive) {
    const panel = document.getElementById('manual-override-panel');
    const badge = document.getElementById('override-warning-badge');
    if (!panel || !badge) return;

    if (isActive) {
        panel.classList.remove('border-navy-border/50');
        panel.classList.add('border-orange-500/80', 'shadow-[0_0_15px_rgba(249,115,22,0.15)]');
        badge.classList.remove('hidden');
    } else {
        panel.classList.remove('border-orange-500/80', 'shadow-[0_0_15px_rgba(249,115,22,0.15)]');
        panel.classList.add('border-navy-border/50');
        badge.classList.add('hidden');
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

    // 클릭 즉시(await 전) 비주얼 경계 모드 동기화 — 서버 응답 대기 없이 즉각 반응
    toggleOverrideVisuals(enabled);

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

    priceWs.onopen = () => {
        // async fetch 제거: WebSocket onopen 시점에는 currentSymbol이 이미 executeDeepSync에 의해 최신화됨.
        // /config 재조회는 불필요하며, 타이밍 경쟁 조건(race condition)을 유발할 수 있으므로 제거.
        const symbolRaw = currentSymbol;

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

                // 4. 오버라이드 패널 오픈 상태에서 견적서 실시간 갱신 (가격 연동 출렁임)
                const overridePanel = document.getElementById('manual-override-panel');
                if (overridePanel && !overridePanel.classList.contains('hidden')) {
                    updateOrderPreview();
                }
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

// --- 실시간 등락률 뱃지 (Live Tickers via OKX Public API) ---
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

// --- Init & Intervals (Parallel Optimization) ---
async function initializeApp() {
    // [Phase 18.2] 부팅 시퀀스 교정: 백엔드에서 현재 타겟(Symbol)을 가장 먼저 알아옴
    await syncConfig();

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
    ]);

    // 초기 렌더링 후 타이머 설정
    setInterval(syncBotStatus, 1000);
    setInterval(syncBrain, 3000);
    setInterval(syncChart, 5000);
    setInterval(syncStats, 5000);
    setInterval(updateLogs, 3000);
    setInterval(syncConfig, 30000);
    setInterval(syncSystemHealth, 5000);
    setInterval(fetchAndRenderHeatmap, 60000);
    setInterval(fetchLiveTickers, 5000);
    // [Phase 21.2] 스트레스 바이패스 상태 주기적 갱신 (10초마다 카운트다운 동기화)
    setInterval(refreshStressBypassUI, 10000);
    refreshStressBypassUI();
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
    const losses = Object.values(pnlMap).map(v => v.net_pnl).filter(v => v < 0);
    const maxProfit = profits.length > 0 ? Math.max(...profits) : 1;
    const maxLoss = losses.length > 0 ? Math.abs(Math.min(...losses)) : 1;

    function _cellColor(dateStr) {
        const d = pnlMap[dateStr];
        if (!d || d.net_pnl === 0) return '#161b22'; // 거래 없음
        const pnl = d.net_pnl;
        if (pnl > 0) {
            const r = Math.min(pnl / maxProfit, 1);
            if (r < 0.25) return '#0e4429';
            if (r < 0.5) return '#006d32';
            if (r < 0.75) return '#26a641';
            return '#39d353';
        } else {
            const r = Math.min(Math.abs(pnl) / maxLoss, 1);
            if (r < 0.25) return '#3d0000';
            if (r < 0.5) return '#7a0000';
            if (r < 0.75) return '#b00020';
            return '#ff4d4d';
        }
    }

    // ── 날짜 범위 생성: KST 기준 오늘부터 26주(182일) 전까지 ──
    // KST 오늘 날짜를 UTC Date 객체로 계산
    const kstNow = new Date(Date.now() + 9 * 3600 * 1000);
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
            const color = _cellColor(dateStr);
            const d = pnlMap[dateStr];
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
            tooltip.style.top = (e.clientY - 32) + 'px';
        });
        cell.addEventListener('mouseleave', () => {
            tooltip.classList.add('hidden');
        });
    });
}

/** history_stats를 fetch 후 히트맵 렌더링 (페이지 로드 & 주기적 갱신용) */
async function fetchAndRenderHeatmap() {
    try {
        const res = await fetch(`${API_URL}/history_stats`);
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

// ════════════ [Phase 21.2] 스트레스 테스트 바이패스 ════════════

async function fetchStressBypass() {
    try {
        const res = await fetch(`${API_URL}/stress_bypass`);
        return await res.json();
    } catch { return null; }
}

async function toggleStressBypass(feature, enabled) {
    try {
        await fetch(`${API_URL}/stress_bypass?feature=${feature}&enabled=${enabled}`, { method: 'POST' });
    } catch (e) {
        console.error('[StressBypass] 토글 실패:', e);
    }
    await refreshStressBypassUI();
}

async function refreshStressBypassUI() {
    const data = await fetchStressBypass();
    if (!data) return;
    const features = ['kill_switch', 'cooldown_loss', 'daily_loss', 'reentry_cd', 'stale_price'];
    features.forEach(f => {
        const btn = document.getElementById(`bypass-btn-${f}`);
        const timer = document.getElementById(`bypass-timer-${f}`);
        if (!btn || !timer) return;
        const info = data[f];
        if (info && info.active) {
            btn.classList.add('bypass-active');
            btn.textContent = '✅ 해제 중';
            const h = Math.floor(info.remaining_sec / 3600);
            const m = Math.floor((info.remaining_sec % 3600) / 60);
            const s = Math.floor(info.remaining_sec % 60);
            timer.textContent = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')} 남음`;
        } else {
            btn.classList.remove('bypass-active');
            btn.textContent = '🔒 원래 동작';
            timer.textContent = '';
        }
    });
}

// ══════════════════════════════════════
