const API_URL = `/api/v1`;
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
function parseTimeframeMs(tf) {
    if (!tf) return 900000; // 기본 15m
    const num = parseInt(tf) || 15;
    if (tf.endsWith('d')) return num * 86400000;
    if (tf.endsWith('h')) return num * 3600000;
    if (tf.endsWith('m')) return num * 60000;
    return 900000; // fallback 15m
}

// ════════════ [Mobile] 하단 탭바 섹션 스크롤 ════════════
function mobileScrollTo(id) {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ════════════ [Phase TF] 원클릭 타임프레임 전환 ════════════

/**
 * toggleTimeframe() — 5m ↔ 15m 원클릭 타임프레임 전환
 * 포지션 보유 시 백엔드에서 차단, 확인 다이얼로그, 토스트 피드백
 */
async function toggleTimeframe() {
    const current = window._currentTimeframe || '15m';
    const target = current === '15m' ? '5m' : '15m';

    const confirmed = confirm(
        `타임프레임을 ${current} → ${target}으로 전환합니다.\n\n` +
        `• 매매 파라미터가 ${target} 최적값으로 자동 조정됩니다.\n` +
        `• 차트 및 게이트 필터가 즉시 새 타임프레임으로 전환됩니다.\n\n` +
        `계속하시겠습니까?`
    );
    if (!confirmed) return;

    const container = document.getElementById('tf-toggle-container');
    if (container) container.classList.add('tf-switching');

    try {
        const res = await fetch(`${API_URL}/timeframe/switch?target_tf=${encodeURIComponent(target)}`, {
            method: 'POST'
        });
        const data = await res.json();

        if (!data.success) {
            showToast('타임프레임 전환 차단', data.message, 'ERROR');
            return;
        }

        if (!data.changed) {
            showToast('타임프레임', data.message, 'INFO');
            return;
        }

        // 글로벌 캐시 즉시 갱신
        window._currentTimeframe = target;
        _applyTimeframeToggleUI(target);

        // Deep sync: 차트 + 뇌 + 설정 동시 리프레시
        await Promise.all([syncChart(), syncBrain(), syncConfig(currentSymbol)]);

        showToast(
            '타임프레임 전환 완료',
            `${data.previous} → ${data.current} | 프리셋 자동 적용`,
            'SUCCESS'
        );
    } catch (err) {
        console.error('[ANTIGRAVITY] toggleTimeframe 실패:', err);
        showToast('타임프레임 전환 실패', err.message || '서버 통신 오류', 'ERROR');
    } finally {
        if (container) container.classList.remove('tf-switching');
    }
}

/**
 * _applyTimeframeToggleUI(tf) — 토글 버튼 시각 상태 즉시 갱신
 */
function _applyTimeframeToggleUI(tf) {
    const btn5m = document.getElementById('tf-btn-5m');
    const btn15m = document.getElementById('tf-btn-15m');
    if (btn5m) {
        btn5m.classList.toggle('tf-active', tf === '5m');
    }
    if (btn15m) {
        btn15m.classList.toggle('tf-active', tf === '15m');
    }
}

// --- OKX 수동 매매 싱크 ---
/** OKX 수동 매매 기록을 대시보드로 동기화 */
async function syncOkxTrades() {
    const btn = document.getElementById('okx-sync-btn');
    const origText = btn ? btn.textContent : '';
    if (btn) btn.textContent = '\u23F3';  // ⏳
    try {
        const res = await fetch(`${API_URL}/sync_trades`, { method: 'POST' });
        const data = await res.json();
        if (btn) {
            btn.textContent = data.synced > 0 ? ('\u2705' + data.synced) : '\u2705';
            setTimeout(() => { btn.textContent = origText || '\uD83D\uDD04'; }, 3000);
        }
        // 싱크 건수 있으면 즉시 stats/heatmap 갱신
        if (data.synced > 0) {
            syncStats();
            if (typeof fetchAndRenderHeatmap === 'function') fetchAndRenderHeatmap();
        }
    } catch(e) {
        if (btn) {
            btn.textContent = '\u274C';
            setTimeout(() => { btn.textContent = origText || '\uD83D\uDD04'; }, 3000);
        }
    }
}

// --- Gate 수동 새로고침 ---
/** Entry Readiness 패널 즉시 새로고침 (방식 A: syncBrain 즉시 1회 호출 + 시각 피드백) */
async function forceRefreshGates() {
    const btn = document.getElementById('gate-refresh-btn');
    const label = document.getElementById('gate-candle-label');
    if (btn) {
        btn.style.pointerEvents = 'none';
        btn.style.opacity = '0.4';
        btn.style.transform = 'rotate(360deg)';
        btn.style.transition = 'transform 0.4s ease';
    }
    // 라벨에 갱신 중 표시
    const prevLabel = label ? label.textContent : '';
    if (label) {
        label.textContent = '갱신 중...';
        label.className = 'font-mono text-[10px] text-yellow-400 animate-pulse tracking-wide';
    }
    await syncBrain();
    if (btn) {
        btn.style.pointerEvents = '';
        btn.style.opacity = '';
        setTimeout(() => {
            btn.style.transform = '';
            btn.style.transition = '';
        }, 450);
    }
    // 갱신 완료 피드백 (0.8초 동안 초록색 플래시)
    if (label) {
        label.className = 'font-mono text-[10px] text-neon-green tracking-wide';
        setTimeout(() => {
            label.className = 'font-mono text-[10px] text-cyan-400 tracking-wide';
        }, 800);
    }
}

// --- Modal Scroll Lock ---
/** 모달 열릴 때 배경 스크롤 차단 */
function lockBodyScroll() { document.body.style.overflow = 'hidden'; }
/** 모달 닫힐 때 배경 스크롤 복원 */
function unlockBodyScroll() { document.body.style.overflow = ''; }

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
            // [UI Overhaul] 방향 배지 색상 동적 적용
            const posTypeEl2 = document.getElementById('pos-type');
            if (posTypeEl2) {
                const posStr = String(symbolData.position).toUpperCase();
                if (posStr.includes('LONG')) {
                    posTypeEl2.className = 'text-sm font-black font-mono tracking-tight flash-target text-white px-3 py-1.5 rounded-lg bg-gradient-to-r from-emerald-600 to-green-500 border border-emerald-400/30 shadow-lg';
                } else if (posStr.includes('SHORT')) {
                    posTypeEl2.className = 'text-sm font-black font-mono tracking-tight flash-target text-white px-3 py-1.5 rounded-lg bg-gradient-to-r from-red-600 to-rose-500 border border-red-400/30 shadow-lg';
                } else {
                    posTypeEl2.className = 'text-sm font-black font-mono tracking-tight flash-target text-white px-3 py-1.5 rounded-lg bg-gradient-to-r from-gray-700 to-gray-600 border border-gray-500/30 shadow-lg';
                }
            }
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
            const posContracts = parseInt(symbolData.contracts || 1);
            if (trailingActive && trailingTarget > 0) {
                updateText('pos-tp-expect', 'Trailing Active 🎯');
            } else if (posContracts <= 1) {
                // [Phase TF] 1계약: TP 미등록 → 트레일링 전용 모드 표시
                updateText('pos-tp-expect', '트레일링 전용 모드 ⏳');
            } else {
                updateText('pos-tp-expect', '1차 익절 대기 중 ⏳');
            }

            updateNumberText('pos-sl', realSl > 0 ? realSl : 0);
            // [Breakeven Stop] SL 상태 라벨: TRAILING > BREAKEVEN > PROTECTED > Dynamic
            const beActive = symbolData.breakeven_stop_active === true;
            const ptpDone = symbolData.partial_tp_executed === true;
            if (trailingActive && trailingTarget > 0) {
                updateText('pos-sl-expect', '(TRAILING)');
            } else if (beActive) {
                updateText('pos-sl-expect', '(BREAKEVEN)');
            } else if (ptpDone) {
                updateText('pos-sl-expect', '(PROTECTED)');
            } else {
                updateText('pos-sl-expect', realSl > 0 ? '(Dynamic)' : '');
            }

            // [거래소 실제 주문가] Exchange Pending Order Prices
            const exchangeTp = parseFloat(symbolData.last_placed_tp_price || 0);
            const exchangeSl = parseFloat(symbolData.last_placed_sl_price || 0);
            const exchangeOrdersRow = document.getElementById('exchange-orders-row');
            if (exchangeOrdersRow) {
                const isPaper = symbolData.is_paper === true;
                if (!isPaper && (exchangeTp > 0 || exchangeSl > 0)) {
                    exchangeOrdersRow.classList.remove('hidden');
                    const tpDecimals = exchangeTp > 0 && exchangeTp < 100 ? 4 : 2;
                    const slDecimals = exchangeSl > 0 && exchangeSl < 100 ? 4 : 2;
                    updateText('pos-exchange-tp', exchangeTp > 0 ? `$${exchangeTp.toFixed(tpDecimals)}` : '미등록');
                    updateText('pos-exchange-sl', exchangeSl > 0 ? `$${exchangeSl.toFixed(slDecimals)}` : '미등록');
                } else {
                    exchangeOrdersRow.classList.add('hidden');
                }
            }

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
                roiEl.className = 'text-3xl font-mono font-bold leading-none flash-target text-neon-green block';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono font-bold block mt-0.5 flash-target text-neon-green';
                posCard.className = "glass-panel p-5 transition-all duration-500 flex flex-col relative overflow-hidden glow-green";
            } else if (pnl < 0) {
                roiEl.className = 'text-3xl font-mono font-bold leading-none flash-target text-neon-red block';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono font-bold block mt-0.5 flash-target text-neon-red';
                posCard.className = "glass-panel p-5 transition-all duration-500 flex flex-col relative overflow-hidden glow-red";
            } else {
                roiEl.className = 'text-3xl font-mono font-bold leading-none flash-target text-gray-400 block';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono font-bold block mt-0.5 flash-target text-gray-400';
                posCard.className = "glass-panel p-5 transition-all duration-500 border-navy-border flex flex-col relative overflow-hidden";
            }

            // [UI Overhaul] TP/SL 프로그레스 바 업데이트
            const _entry = parseFloat(symbolData.entry_price || 0);
            const _current = parseFloat(symbolData.current_price || 0);
            const _tp = parseFloat(symbolData.take_profit_price || 0);
            if (_entry > 0 && _current > 0 && realSl > 0 && _tp > 0) {
                const priceMarker = document.getElementById('pos-price-marker');
                const tpSlBar = document.getElementById('pos-tp-sl-bar');
                if (priceMarker && tpSlBar) {
                    const range = _tp - realSl;
                    if (range > 0) {
                        const progress = Math.max(0, Math.min(100, ((_current - realSl) / range) * 100));
                        priceMarker.style.left = progress + '%';
                        // 바 색상: SL쪽(왼쪽)은 적색, TP쪽(오른쪽)은 녹색
                        tpSlBar.style.width = '100%';
                        tpSlBar.style.transform = 'none';
                    }
                }
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

        // 3.5 [Phase TF] 타임프레임 토글 포지션 기반 활성/비활성
        const tfContainer = document.getElementById('tf-toggle-container');
        if (tfContainer) {
            const anyPositionOpen = Object.values(data.symbols || {}).some(
                s => s.position && s.position !== 'NONE'
            );
            tfContainer.classList.toggle('tf-disabled', anyPositionOpen);
            tfContainer.title = anyPositionOpen
                ? '포지션 보유 중 — 타임프레임 변경 불가'
                : '클릭하여 타임프레임 전환 (5m ↔ 15m)';
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

        // 6. [UI Overhaul] Command Bar 미러링 — 핵심 데이터를 상단 바에 실시간 반영
        const cmdBalMirror = document.getElementById('cmd-balance-mirror');
        if (cmdBalMirror) cmdBalMirror.textContent = '$' + parseFloat(data.balance || 0).toFixed(2);

        if (symbolData && symbolData.position !== 'NONE') {
            const _pnl = parseFloat(symbolData.unrealized_pnl_percent || 0);
            const _pnlSign = _pnl >= 0 ? '+' : '';
            const cmdPnlMirror = document.getElementById('cmd-pnl-mirror');
            if (cmdPnlMirror) {
                cmdPnlMirror.textContent = `${_pnlSign}${_pnl.toFixed(2)}%`;
                cmdPnlMirror.className = `font-mono text-xs font-bold ${_pnl >= 0 ? 'text-neon-green' : 'text-neon-red'}`;
            }
            const cmdPriceMirror = document.getElementById('cmd-price-mirror');
            if (cmdPriceMirror) {
                const _p = parseFloat(symbolData.current_price || 0);
                cmdPriceMirror.textContent = '$' + (_p < 100 ? _p.toFixed(4) : _p.toFixed(2));
            }
        } else {
            const cmdPnlMirror = document.getElementById('cmd-pnl-mirror');
            if (cmdPnlMirror) { cmdPnlMirror.textContent = '--'; cmdPnlMirror.className = 'font-mono text-xs font-bold text-gray-500'; }
            const cmdPriceMirror = document.getElementById('cmd-price-mirror');
            if (cmdPriceMirror) cmdPriceMirror.textContent = '--';
        }

        // 7. [Margin Guard] 증거금 사전 경고 렌더링
        if (data.margin_guard) {
            window._marginGuardData = data.margin_guard;

            // [Bug Fix] 적용 직후 grace period (5초) — 백엔드 갱신 전 배지 재표시 방지
            const _mgInGrace = window._mgAppliedAt && (Date.now() - window._mgAppliedAt < 5000);
            if (!_mgInGrace) {
                const mgBadge = document.getElementById('margin-guard-badge');
                const cmdMgWarn = document.getElementById('cmd-margin-warn');
                let _mgHasWarn = false;
                let _mgSym = '', _mgCurLev = 0, _mgRecLev = 0;

                // 현재 감시 타겟(active_target)만 체크 — 타겟 전환 시 해당 코인 상태만 반영
                // (다른 심볼에 문제가 있어도 현재 보고 있는 코인이 괜찮으면 배지 숨김)
                const _mgActiveSym = data.active_target || currentSymbol;
                const _mgActive = data.margin_guard[_mgActiveSym];
                if (_mgActive && _mgActive.needs_change) {
                    _mgHasWarn = true;
                    _mgSym = _mgActiveSym.split(':')[0];
                    _mgCurLev = _mgActive.current_leverage;
                    _mgRecLev = _mgActive.recommended_leverage;
                }

                if (_mgHasWarn && mgBadge) {
                    // applyRecommendedLeverage()가 정확한 심볼을 알 수 있게 캐싱
                    window._lastActiveMgSym = _mgActiveSym;
                    mgBadge.classList.remove('hidden');
                    const mgSymEl = document.getElementById('mg-symbol');
                    const mgCurEl = document.getElementById('mg-current-lev');
                    const mgRecEl = document.getElementById('mg-rec-lev');
                    if (mgSymEl) mgSymEl.textContent = _mgSym;
                    if (mgCurEl) mgCurEl.textContent = _mgCurLev + 'x';
                    if (mgRecEl) mgRecEl.textContent = _mgRecLev + 'x';
                    // 토스트: 5분 쿨다운
                    if (!window._mgLastToast || Date.now() - window._mgLastToast > 300000) {
                        window._mgLastToast = Date.now();
                        showToast('Margin Guard', `${_mgSym} 증거금 부족 — ${_mgRecLev}x 추천`, 'ERROR');
                    }
                } else if (mgBadge) {
                    mgBadge.classList.add('hidden');
                }

                if (cmdMgWarn) cmdMgWarn.classList.toggle('hidden', !_mgHasWarn);
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

        // [확정봉 메타데이터] 카운트다운 + 봉 라벨 갱신
        if (brainState.confirmed_candle_ts) {
            window._confirmedCandleTs = brainState.confirmed_candle_ts;
            window._currentTimeframe = brainState.timeframe || '15m';
            _applyTimeframeToggleUI(window._currentTimeframe);
            // 확정봉 시각 라벨 표시 — 캔들 종료 시각 = 시작 + 타임프레임
            // 예: 02:15 시작 + 15분 = "02:30 봉 기준" (02:30에 확정된 데이터 기준)
            const tfMs = parseTimeframeMs(window._currentTimeframe);
            const candleCloseDate = new Date(brainState.confirmed_candle_ts + tfMs);
            const hh = String(candleCloseDate.getHours()).padStart(2, '0');
            const mm = String(candleCloseDate.getMinutes()).padStart(2, '0');
            const candleLabel = document.getElementById('gate-candle-label');
            if (candleLabel) candleLabel.textContent = `${hh}:${mm} 봉 기준`;
        }

        // [A] 진입 관문 체크리스트
        if (brainState.gates) {
            renderGates(brainState.gates, brainState.gates_passed || 0, brainState.live_gates || null);
        }
        // [B] 봇 혼잣말 피드
        if (brainState.monologue) {
            renderMonologue(brainState.monologue);
        }
        // [C] Guard Wall 진입 방벽 실시간 상태 (Flight Recorder)
        if (brainState.entry_guards) {
            renderEntryGuards(brainState.entry_guards);
        }
        // [D] Decision Trail 진입 파이프라인 (Flight Recorder)
        if (brainState.latest_decision_trail) {
            renderDecisionTrail(brainState.latest_decision_trail);
        }
        // [E] Config Snapshot Diff 설정 불일치 감지 (Flight Recorder)
        if (brainState.active_config) {
            checkConfigMismatch(brainState.active_config);
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

        // ── Scalp Fitness 적합도 배지 ──
        if (brainState.scalp_fitness !== undefined) {
            const sfScore = parseInt(brainState.scalp_fitness) || 0;
            const sfLabel = brainState.scalp_fitness_label || '대기';
            const sfBadge = document.getElementById('scalp-fitness-badge');
            const sfScoreEl = document.getElementById('scalp-fitness-score');
            const sfBar = document.getElementById('scalp-fitness-bar');
            if (sfScoreEl) sfScoreEl.textContent = sfScore;
            if (sfBar) sfBar.style.width = `${Math.round((sfScore / 8) * 100)}%`;
            if (sfBadge) {
                if (sfScore >= 6) {
                    sfBadge.textContent = `⚡ ${sfLabel} (${sfScore}/8)`;
                    sfBadge.className = 'px-2 py-1 rounded font-mono text-[10px] font-bold text-neon-green border border-neon-green/50 bg-neon-green/10 shadow-[0_0_8px_rgba(0,255,136,0.3)] transition-all';
                } else {
                    sfBadge.textContent = `${sfLabel} (${sfScore}/8)`;
                    sfBadge.className = 'px-2 py-1 rounded font-mono text-[10px] font-bold text-gray-500 border border-gray-600/50 bg-gray-600/10 transition-all';
                }
            }
        }
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncBrain 실패 (엔드포인트: /api/v1/brain):", error);
    }
}

// --- [A] 진입 관문 체크리스트 렌더링 ---
function renderGates(gates, passed, liveGates) {
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
    // 게이트 이름 → data-gate 속성 매핑
    const gateAttrMap = { adx: 'adx', chop: 'chop', volume: 'volume', disparity: 'disparity', macd_rsi: 'macd-rsi', macro: 'macro' };

    for (const [key, elId] of Object.entries(gateMap)) {
        const el = document.getElementById(elId);
        if (!el || !gates[key]) continue;
        const g = gates[key];

        // UI 개선: 세로 구분선(border-l)과 '목표:' 레이블을 추가하여 가독성 극대화
        const targetHtml = g.target ? `<span class="text-[9px] text-gray-500 ml-1.5 border-l border-gray-600/50 pl-1.5 tracking-wider">목표: ${g.target}</span>` : '';

        let baseHtml = '';
        if (g.pass) {
            baseHtml = `<span class="text-neon-green text-[10px] mr-1">✅</span><span class="text-neon-green font-bold text-[11px]">${g.value}</span>${targetHtml}`;
        } else {
            baseHtml = `<span class="text-neon-red text-[10px] mr-1">❌</span><span class="text-gray-400 font-bold text-[11px]">${g.value}</span>${targetHtml}`;
        }

        // ── 라이브 값 + 게이지바 (현재 캔들 실시간 수치) ──
        let liveHtml = '';
        if (liveGates && liveGates[key]) {
            const live = liveGates[key];
            const lv = live.value;
            const gauge = Math.max(0, Math.min(100, live.gauge || 0));

            // 확정봉 값 파싱 (비교용)
            let confirmed = 0;
            if (key === 'macd_rsi') {
                const m = g.value.match(/[\d.]+/);
                confirmed = m ? parseFloat(m[0]) : 0;
            } else if (key === 'macro') {
                confirmed = null; // 추세 라벨이라 수치 비교 불가
            } else {
                confirmed = parseFloat(g.value) || 0;
            }

            // 화살표 방향
            let arrow = '\u2192'; // →
            let arrowCls = 'text-gray-500';
            if (confirmed !== null) {
                if (lv > confirmed) { arrow = '\u2191'; arrowCls = 'text-neon-green'; }  // ↑
                else if (lv < confirmed) { arrow = '\u2193'; arrowCls = 'text-neon-red'; } // ↓
            } else {
                // macro: 양수면 상승, 음수면 하락
                if (lv > 0) { arrow = '\u2191'; arrowCls = 'text-neon-green'; }
                else if (lv < 0) { arrow = '\u2193'; arrowCls = 'text-neon-red'; }
            }

            // 라이브 값 포맷팅
            let liveStr = '';
            if (key === 'volume') liveStr = `${lv}x`;
            else if (key === 'disparity') liveStr = `${lv}%`;
            else if (key === 'macd_rsi') liveStr = `RSI ${lv}`;
            else if (key === 'macro') liveStr = `${lv > 0 ? '+' : ''}${lv}%`;
            else liveStr = `${lv}`;

            // 게이지 색상
            const gColor = gauge >= 70 ? '#00ff88' : gauge >= 40 ? '#facc15' : '#ff4d4d';

            liveHtml = `<span class="flex items-center gap-0.5 mt-0.5"><span class="text-[8px] text-gray-600">\u279C</span><span class="text-[9px] ${arrowCls} font-mono">${liveStr}</span><span class="text-[8px] ${arrowCls}">${arrow}</span></span><span class="block w-full h-[2px] rounded-full mt-0.5 overflow-hidden" style="background:rgba(15,23,42,0.6)"><span class="block h-full rounded-full" style="width:${gauge}%;background:${gColor};transition:width .5s ease,background .3s ease"></span></span>`;
        }

        el.innerHTML = baseHtml + liveHtml;

        // [UI Overhaul] 파이프라인 노드 시각 업데이트 (데스크톱 원형 + 커넥터)
        const gateAttr = gateAttrMap[key];
        const pipeNodes = document.querySelectorAll(`.gate-node[data-gate="${gateAttr}"]`);
        pipeNodes.forEach(node => {
            node.classList.toggle('gate-pass', !!g.pass);
            node.classList.toggle('gate-fail', !g.pass);
            // 원형 아이콘 색상 변경
            const circle = node.querySelector('.gate-circle');
            if (circle) {
                if (g.pass) {
                    circle.className = 'w-8 h-8 rounded-full border-2 border-neon-green bg-neon-green/15 flex items-center justify-center text-[10px] font-bold text-neon-green transition-all mb-1 gate-circle shadow-[0_0_8px_rgba(0,255,136,0.3)]';
                } else {
                    circle.className = 'w-8 h-8 rounded-full border-2 border-neon-red/60 bg-neon-red/10 flex items-center justify-center text-[10px] font-bold text-neon-red/70 transition-all mb-1 gate-circle';
                }
            }
            // 모바일 미러 값 동기화
            const mirrorVal = node.querySelector('.gate-val-mirror');
            if (mirrorVal) {
                mirrorVal.innerHTML = el.innerHTML;
            }
        });
    }
}

// --- [B] Bot Consciousness — 의식의 흐름 렌더링 ---
let _lastMonologueLatest = '';
function renderMonologue(lines) {
    if (!lines || lines.length === 0) return;
    const latest = lines[lines.length - 1];
    if (latest === _lastMonologueLatest) return; // 최신 메시지 동일하면 스킵
    _lastMonologueLatest = latest;

    const feed = document.getElementById('monologue-feed');
    if (!feed) return;

    // 최신 20개 표시 (위에서 아래로 최신 → 오래된 순)
    const recent = lines.slice(-20).reverse();
    feed.innerHTML = recent.map((line, i) => {
        const isLatest = i === 0;
        // 카테고리 기반 컬러 코딩
        let cls = 'text-[11px] font-mono py-0.5 px-1 rounded transition-all';
        if (line.includes('🟢') || line.includes('🔴') || line.includes('🎯'))
            cls += ' text-neon-green bg-neon-green/10 font-bold animate-pulse';
        else if (line.includes('🚨') || line.includes('⚠️'))
            cls += ' text-red-400 bg-red-500/10';
        else if (line.includes('💰') || line.includes('🔥'))
            cls += ' text-yellow-400';
        else if (line.includes('🔍') || line.includes('📊'))
            cls += ' text-blue-400';
        else if (line.includes('🛡️') || line.includes('❄️'))
            cls += ' text-cyan-400';
        else if (line.includes('🕯️') || line.includes('✅'))
            cls += ' text-purple-400';
        else if (line.includes('💤'))
            cls += ' text-gray-600 italic';
        else if (isLatest)
            cls += ' text-gray-300';
        else
            cls += ' text-gray-500';
        return `<div class="${cls}">${line}</div>`;
    }).join('');
}

// ═══════════════════════════════════════════════════════════════
// [Flight Recorder] Guard Wall — 진입 방벽 실시간 렌더링
// ═══════════════════════════════════════════════════════════════
function renderEntryGuards(guards) {
    const container = document.getElementById('guard-wall-list');
    if (!container || !guards) return;

    const guardMeta = {
        candle_lock:    { label: '캔들 잠금',      icon: '🔒' },
        exit_only:      { label: '퇴근 모드',      icon: '🛏️' },
        reentry_cd:     { label: '재진입 쿨다운',   icon: '⏳' },
        other_position: { label: '타 포지션',       icon: '🔗' },
        kill_switch:    { label: '킬스위치',        icon: '🚨' },
        loss_cooldown:  { label: '연패 쿨다운',     icon: '❄️' },
        active_target:  { label: '활성 타겟',       icon: '🎯' },
        direction_mode: { label: '방향 모드',       icon: '🧭' },
        micro_account:  { label: '소액 방어',       icon: '🛡️' },
        margin_check:   { label: '증거금 검증',     icon: '💰' },
    };

    let clearCount = 0;
    let blockCount = 0;
    const total = Object.keys(guards).length;

    let html = '';
    for (const [key, g] of Object.entries(guards)) {
        const meta = guardMeta[key] || { label: key, icon: '?' };
        const isClear = g.status === 'CLEAR';
        const isBlocking = g.status === 'BLOCKING';
        if (isClear) clearCount++;
        if (isBlocking) blockCount++;

        const dotColor = isClear ? 'bg-neon-green' : (isBlocking ? 'bg-neon-red animate-pulse' : 'bg-gray-600');
        const textColor = isClear ? 'text-neon-green' : (isBlocking ? 'text-neon-red' : 'text-gray-600');

        html += `<div class="flex items-center justify-between text-[10px] font-mono py-0.5">
            <span class="flex items-center gap-1.5">
                <span class="w-1.5 h-1.5 rounded-full ${dotColor} flex-shrink-0"></span>
                <span class="text-gray-400">${meta.icon} ${meta.label}</span>
            </span>
            <span class="${textColor} text-[9px] truncate max-w-[140px]" title="${g.detail || ''}">${g.detail || ''}</span>
        </div>`;
    }
    container.innerHTML = html;

    const badge = document.getElementById('guard-wall-badge');
    if (badge) {
        if (blockCount > 0) {
            badge.textContent = `${blockCount} 차단`;
            badge.className = 'text-neon-red font-bold text-[10px] animate-pulse';
        } else {
            badge.textContent = `${clearCount}/${total} 통과`;
            badge.className = 'text-neon-green font-bold text-[10px]';
        }
    }
}

// ═══════════════════════════════════════════════════════════════
// [Flight Recorder] Decision Trail — 진입 파이프라인 렌더링
// ═══════════════════════════════════════════════════════════════
const _PIPELINE_STEP_META = {
    active_target:   { label: '활성 타겟',    order: 1 },
    direction_mode:  { label: '방향 모드',    order: 2 },
    position_sizing: { label: '포지션 사이징', order: 3 },
    micro_account:   { label: '소액 방어',    order: 4 },
    margin_check:    { label: '증거금 검증',  order: 5 },
    order_execution: { label: '주문 실행',    order: 6 },
};

let _lastTrailTimestamp = '';
function renderDecisionTrail(trail) {
    if (!trail || trail.timestamp === _lastTrailTimestamp) return;
    _lastTrailTimestamp = trail.timestamp;

    const container = document.getElementById('decision-trail-container');
    if (!container) return;

    const sigColor = trail.signal === 'LONG' ? 'text-emerald-400' : (trail.signal === 'SHORT' ? 'text-red-400' : 'text-gray-500');
    const resultColor = trail.result === 'SUCCESS' ? 'text-neon-green' : 'text-neon-red';
    const sym = (trail.symbol || '').split('/')[0] || '';
    const ts = trail.timestamp ? new Date(trail.timestamp).toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '';

    let html = `<div class="flex items-center justify-between mb-2 text-[10px] font-mono">
        <span class="text-gray-400">${ts}</span>
        <span class="text-gray-500">${sym}</span>
        <span class="${sigColor} font-bold">${trail.signal}</span>
        <span class="${resultColor} font-bold">${trail.result}</span>
    </div>`;

    const steps = (trail.pipeline || []).sort((a, b) => (_PIPELINE_STEP_META[a.step]?.order || 99) - (_PIPELINE_STEP_META[b.step]?.order || 99));

    html += '<div class="flex flex-col gap-0">';
    steps.forEach((step, idx) => {
        const meta = _PIPELINE_STEP_META[step.step] || { label: step.step };
        let dotClass, textClass;
        if (step.status === 'PASS') {
            dotClass = 'bg-neon-green'; textClass = 'text-neon-green';
        } else if (step.status === 'BLOCKED' || step.status === 'FAILED') {
            dotClass = 'bg-neon-red'; textClass = 'text-neon-red font-bold';
        } else {
            dotClass = 'bg-gray-700'; textClass = 'text-gray-600';
        }

        const isLast = idx === steps.length - 1;
        const connector = !isLast ? `<div class="w-px h-2 ${step.status === 'PASS' ? 'bg-neon-green/30' : 'bg-gray-700'} ml-[3px]"></div>` : '';

        html += `<div class="flex items-start gap-2">
            <div class="flex flex-col items-center flex-shrink-0">
                <div class="w-[7px] h-[7px] rounded-full ${dotClass} mt-[3px]"></div>
                ${connector}
            </div>
            <div class="flex items-center justify-between flex-1 text-[9px] font-mono min-w-0 pb-0.5">
                <span class="text-gray-400 flex-shrink-0 w-20">${meta.label}</span>
                <span class="${textClass} truncate max-w-[120px]" title="${step.detail || ''}">${step.status === 'SKIPPED' ? '—' : (step.detail || step.status)}</span>
            </div>
        </div>`;
    });
    html += '</div>';

    container.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════════
// [Flight Recorder] Config Snapshot Diff — 설정 불일치 감지
// ═══════════════════════════════════════════════════════════════
let _lastMismatchReport = '';
function checkConfigMismatch(activeConfig) {
    const mismatches = [];

    // 레버리지 비교
    const levEl = document.getElementById('config-leverage');
    const frontLev = levEl ? parseInt(levEl.value || 0) : 0;
    if (frontLev > 0 && frontLev !== activeConfig.leverage) {
        mismatches.push({ key: '레버리지', front: `${frontLev}x`, back: `${activeConfig.leverage}x` });
    }

    // 리스크 비교 (both in %)
    const riskEl = document.getElementById('config-risk_per_trade');
    const frontRisk = riskEl ? parseFloat(riskEl.value || 0) : 0;
    if (frontRisk > 0 && Math.abs(frontRisk - activeConfig.risk_per_trade) > 0.05) {
        mismatches.push({ key: '리스크', front: `${frontRisk}%`, back: `${activeConfig.risk_per_trade}%` });
    }

    // ADX 비교
    const adxEl = document.getElementById('config-adx_threshold');
    const frontAdx = adxEl ? parseFloat(adxEl.value || 0) : 0;
    if (frontAdx > 0 && Math.abs(frontAdx - activeConfig.adx_threshold) > 0.1) {
        mismatches.push({ key: 'ADX', front: `${frontAdx}`, back: `${activeConfig.adx_threshold}` });
    }

    // 불일치 표시
    const indicator = document.getElementById('config-mismatch-indicator');
    if (!indicator) return;

    const reportKey = mismatches.map(m => `${m.key}:${m.front}/${m.back}`).join('|');
    if (reportKey === _lastMismatchReport) return;
    _lastMismatchReport = reportKey;

    if (mismatches.length === 0) {
        indicator.classList.add('hidden');
        return;
    }

    indicator.classList.remove('hidden');
    let html = '<div class="text-[9px] text-yellow-400 font-mono uppercase tracking-wider mb-1 flex items-center gap-1"><span class="animate-pulse">⚠</span> 설정 불일치 감지</div>';
    mismatches.forEach(m => {
        html += `<div class="flex items-center justify-between text-[9px] font-mono py-0.5">
            <span class="text-yellow-400">${m.key}</span>
            <span class="text-gray-400">표시: <span class="text-white">${m.front}</span> · 실제: <span class="text-neon-red">${m.back}</span></span>
        </div>`;
    });
    indicator.innerHTML = html;

    // 토스트 1회만 (키 변경 시에만)
    if (typeof showToast === 'function') {
        showToast('설정 불일치', `${mismatches.map(m => m.key).join(', ')} 값이 다릅니다`, 'ERROR');
    }
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
        frenzy: ['🔥 FRENZY', 'text-red-400 border-red-500/50 bg-red-500/10'],
        micro_seed: ['💎 마이크로', 'text-emerald-300 border-emerald-500/50 bg-emerald-500/10'],
        scalp_context: ['📡 스캘프CTX', 'text-cyan-300 border-cyan-500/50 bg-cyan-500/10'],
    };

    let matchedLabel = null;
    let matchedClass = null;

    for (const [presetName] of Object.entries(PRESET_CONFIGS)) {
        const effectivePreset = _getEffectivePreset(presetName);
        if (!effectivePreset) continue;
        const keys = Object.keys(TUNING_INPUT_MAP);
        const isMatch = keys.every(key => {
            const { parse } = TUNING_INPUT_MAP[key];
            const cur = currentVals[key];
            const pre = effectivePreset[key];
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

    // [UI Overhaul] Command Bar 프리셋 배지 미러 (색상도 Entry Readiness 뱃지와 동기화)
    const cmdPresetBadge = document.getElementById('cmd-preset-badge');
    if (cmdPresetBadge) {
        cmdPresetBadge.textContent = matchedLabel || '🛠️ 커스텀';
        cmdPresetBadge.className = matchedClass
            ? `font-mono text-[9px] border px-1.5 py-0.5 rounded transition-all ${matchedClass}`
            : 'font-mono text-[9px] text-purple-300 bg-purple-500/10 border border-purple-500/30 px-1.5 py-0.5 rounded';
    }

    // [UI Overhaul] Preset Card 활성 하이라이트 (타임프레임 인식)
    let matchedPresetName = null;
    for (const [presetName] of Object.entries(PRESET_CONFIGS)) {
        const effectivePreset2 = _getEffectivePreset(presetName);
        if (!effectivePreset2) continue;
        const keys = Object.keys(TUNING_INPUT_MAP);
        const isMatch = keys.every(key => {
            const { parse } = TUNING_INPUT_MAP[key];
            const cur = currentVals[key];
            const pre = effectivePreset2[key];
            if (pre === undefined) return true;
            if (parse === parseInt) return Math.round(cur) === Math.round(pre);
            return Math.abs(cur - pre) < 0.00001;
        });
        if (isMatch) { matchedPresetName = presetName; break; }
    }
    document.querySelectorAll('.preset-card').forEach(card => {
        card.classList.toggle('preset-active', card.dataset.preset === matchedPresetName);
    });
}

async function toggleBot() {
    const confirmed = confirm('⚠️ 봇 상태 변경\n\n시스템 가동/중지 상태를 전환합니다.\n계속하시겠습니까?');
    if (!confirmed) return;
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
        hard_stop_loss_rate: 0.006, trailing_stop_activation: 0.005,
        trailing_stop_rate: 0.003, min_take_profit_rate: 0.008,
        cooldown_losses_trigger: 2, cooldown_duration_sec: 1800,
    },
    trend_rider: {
        adx_threshold: 25.0, adx_max: 60.0, chop_threshold: 58.0,
        volume_surge_multiplier: 1.3, fee_margin: 0.001,
        hard_stop_loss_rate: 0.008, trailing_stop_activation: 0.005,
        trailing_stop_rate: 0.004, min_take_profit_rate: 0.008,
        cooldown_losses_trigger: 4, cooldown_duration_sec: 600,
    },
    scalper: {
        adx_threshold: 20.0, adx_max: 50.0, chop_threshold: 65.0,
        volume_surge_multiplier: 1.2, fee_margin: 0.002,
        hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.002,
        trailing_stop_rate: 0.002, min_take_profit_rate: 0.005,
        cooldown_losses_trigger: 5, cooldown_duration_sec: 300,
    },
    iron_dome: {
        adx_threshold: 28.0, adx_max: 42.0, chop_threshold: 50.0,
        volume_surge_multiplier: 2.5, fee_margin: 0.002,
        hard_stop_loss_rate: 0.004, trailing_stop_activation: 0.004,
        trailing_stop_rate: 0.002, min_take_profit_rate: 0.005,
        cooldown_losses_trigger: 2, cooldown_duration_sec: 3600,
    },
    factory_reset: {
        adx_threshold: 25.0, adx_max: 40.0, chop_threshold: 61.8,
        volume_surge_multiplier: 1.5, fee_margin: 0.0015,
        hard_stop_loss_rate: 0.005, trailing_stop_activation: 0.003,  // [Fix] 15m 기준 0.003 (기존 0.005 오류)
        trailing_stop_rate: 0.002, min_take_profit_rate: 0.01,        // [Fix] 15m 기준 1.0% (기존 0.8% 오류)
        cooldown_losses_trigger: 3, cooldown_duration_sec: 900,
        disparity_threshold: 0.8,  // [Fix] 15m 기준 이격도 (TIMEFRAME_PRESETS 동기화)
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
        trailing_stop_rate: 0.002,      // 고점 대비 0.2% 낙폭 시 익절 (0.1%→0.2%: 거래소 TP 체결 여유)
        min_take_profit_rate: 0.004,    // 0.4% 최소 익절 가드 (광기 모드 빠른 EXIT)
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
    // [Scalp Context] 스캘핑 적합 구간 전용 — 이격도+RSI 해제, 매크로 유지, SL 타이트
    scalp_context: {
        adx_threshold: 20.0,             // ADX 하한 낮춤 (더 많은 진입 기회)
        adx_max: 50.0,                   // ADX 상한 확대
        chop_threshold: 61.8,            // CHOP 기본 유지
        volume_surge_multiplier: 1.2,    // 볼륨 기준 완화 (스캘핑 빈도 확보)
        fee_margin: 0.0015,              // 수수료 마진 타이트
        hard_stop_loss_rate: 0.003,      // 0.3% SL (스캘핑 타이트)
        trailing_stop_activation: 0.002, // 0.2% 수익 후 트레일링
        trailing_stop_rate: 0.002,       // 0.2% 트레일링 거리 (0.15%→0.2%: 거래소 TP 체결 여유)
        min_take_profit_rate: 0.005,     // 0.5% 최소 익절 가드 (잔여 50% 보호)
        cooldown_losses_trigger: 3,      // 3연패 쿨다운
        cooldown_duration_sec: 900,      // 15분 쿨다운
        bypass_macro: 'false',           // 거시추세 필터 유지 (안전장치)
        bypass_disparity: 'true',        // 이격도 해제 (빠른 진입)
        bypass_indicator: 'true',        // RSI 해제 (빠른 진입)
    },
};

// ════════════ [Phase TF] 타임프레임별 프리셋 오버레이 ════════════
// 5분봉 전환 시 각 프리셋의 SL/TP/필터 값을 5분봉에 최적화된 값으로 오버라이드
// 15분봉은 PRESET_CONFIGS 원본값 그대로 사용 (기본값)
const PRESET_TF_OVERLAY = {
    '5m': {
        sniper: {
            hard_stop_loss_rate: 0.004, trailing_stop_activation: 0.003,
            trailing_stop_rate: 0.002, min_take_profit_rate: 0.005,
            adx_threshold: 33.0, chop_threshold: 50.0,
            volume_surge_multiplier: 2.2, cooldown_duration_sec: 600,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        trend_rider: {
            hard_stop_loss_rate: 0.005, trailing_stop_activation: 0.003,
            trailing_stop_rate: 0.0025, min_take_profit_rate: 0.005,
            adx_threshold: 28.0, chop_threshold: 53.0,
            volume_surge_multiplier: 1.5, cooldown_duration_sec: 300,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        scalper: {
            hard_stop_loss_rate: 0.002, trailing_stop_activation: 0.0015,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.003,
            adx_threshold: 22.0, chop_threshold: 60.0,
            volume_surge_multiplier: 1.4, cooldown_duration_sec: 180,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        iron_dome: {
            hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.003,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.004,
            adx_threshold: 30.0, chop_threshold: 48.0,
            volume_surge_multiplier: 2.8, cooldown_duration_sec: 1800,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        factory_reset: {
            hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.002,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.005,
            adx_threshold: 28.0, chop_threshold: 55.0,
            volume_surge_multiplier: 1.8, cooldown_duration_sec: 300,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        frenzy: {
            hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.002,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.003,
            chop_threshold: 55.0, cooldown_duration_sec: 180,
            // bypass_disparity: 'true' 상속 → disparity_threshold 무효 (오버라이드 불필요)
        },
        micro_seed: {
            hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.005,
            trailing_stop_rate: 0.003, min_take_profit_rate: 0.006,
            adx_threshold: 30.0, chop_threshold: 50.0,
            volume_surge_multiplier: 2.0, cooldown_duration_sec: 900,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        scalp_context: {
            hard_stop_loss_rate: 0.002, trailing_stop_activation: 0.0015,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.003,
            adx_threshold: 22.0, volume_surge_multiplier: 1.4,
            cooldown_duration_sec: 300,
            // bypass_disparity: 'true' 상속 → disparity_threshold 무효 (오버라이드 불필요)
        },
    },
};

/**
 * _getEffectivePreset(presetName) — 현재 타임프레임에 맞는 프리셋 값 반환
 * 15m: PRESET_CONFIGS 원본 그대로
 * 5m: PRESET_CONFIGS + PRESET_TF_OVERLAY['5m'] 머지 (오버레이 우선)
 */
function _getEffectivePreset(presetName) {
    const base = PRESET_CONFIGS[presetName];
    if (!base) return null;
    const tf = window._currentTimeframe || '15m';
    const overlay = (PRESET_TF_OVERLAY[tf] || {})[presetName];
    if (!overlay) return { ...base };
    return { ...base, ...overlay };
}

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
        // [방어막] 튜닝 모달이 열려있는 상태에서 주기적 syncConfig가 실행되면
        // 사용자가 입력 중인 값을 덮어쓰지 않도록 TUNING_INPUT_MAP 업데이트를 건너뜀
        // (openTuningModal에서 명시적으로 호출된 syncConfig는 symbol이 있으므로 항상 통과)
        const _tuningModal = document.getElementById('tuning-modal');
        const _isTuningOpen = _tuningModal && !_tuningModal.classList.contains('hidden');
        const _isPeriodicCall = (symbol === null);
        const _skipTuningInputs = _isTuningOpen && _isPeriodicCall;
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
                // [UI Overhaul] Command Bar 레버리지 미러
                const cmdLevBadge = document.getElementById('cmd-lev-badge');
                if (cmdLevBadge) cmdLevBadge.textContent = parseInt(val) + 'x';
            } else if (key === 'direction_mode') {
                // [Phase 18.1] 방향 모드 버튼 UI 동기화
                _applyDirectionModeUI(String(val).toUpperCase());
            } else if (key === 'symbols') {
                const activeSymbol = Array.isArray(val) && val.length > 0 ? val[0] : null;
                if (activeSymbol) currentSymbol = activeSymbol;
                // 차트 상단 조준경 배지 갱신
                const targetBadge = document.getElementById('hero-target-badge');
                if (targetBadge && activeSymbol) targetBadge.textContent = activeSymbol;
                // [Phase 18.1] 좌측 패널 심볼 배지 갱신
                const leftSymBadge = document.getElementById('left-panel-symbol-badge');
                if (leftSymBadge && activeSymbol) leftSymBadge.textContent = activeSymbol.split(':')[0];
                // [UI Overhaul] Command Bar 심볼 미러
                const cmdSymMirror = document.getElementById('cmd-symbol-mirror');
                if (cmdSymMirror && activeSymbol) cmdSymMirror.textContent = activeSymbol.split(':')[0];
                // [Phase 18.1] 모달 심볼 드롭다운 동기화
                const modalSymSel = document.getElementById('modal-target-symbol');
                if (modalSymSel && activeSymbol) modalSymSel.value = activeSymbol;
                // [Dynamic Symbol] 스캐너가 찾은 코인을 드롭다운에 동적 추가
                if (Array.isArray(val)) _syncSymbolDropdowns(val, activeSymbol);
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
                // [방어막] 모달 열려있고 주기적 호출이면 입력창 업데이트 건너뜀 (사용자 입력 보호)
                if (_skipTuningInputs) continue;
                const { id, parse } = TUNING_INPUT_MAP[key];
                const input = document.getElementById(id);
                if (input) {
                    input.value = parse(val);
                    // disparity_threshold 슬라이더: 표시 스팬도 동시 갱신 (dead code 제거 후 통합)
                    if (key === 'disparity_threshold') {
                        const span = document.getElementById('val-disparity');
                        if (span) span.textContent = parseFloat(val).toFixed(1) + '%';
                    }
                }
            } else if (['bypass_macro', 'bypass_disparity', 'bypass_indicator', 'exit_only_mode', 'shadow_hunting_enabled', 'auto_preset_enabled'].includes(key)) {
                // [Phase 14.1] Gate Bypass 체크박스 동기화 + [Phase 23] Shadow Hunting + [Phase 25] Adaptive Shield
                const el = document.getElementById(`config-${key}`);
                if (el) el.checked = (val === true || val === 'true');
            } else if (key === 'timeframe') {
                // [Phase TF] 타임프레임 토글 UI 동기화
                window._currentTimeframe = String(val);
                _applyTimeframeToggleUI(String(val));
            }
        }
        updateActiveTuningBadge();
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncConfig 실패 (엔드포인트: /api/v1/config GET):", error);
    }
}

// --- Engine Tuning Modal ---
async function applyPreset(presetName) {
    const config = _getEffectivePreset(presetName);
    if (!config) return;

    // [BUGFIX] 프리셋 저장 전 보호 키(leverage, risk_per_trade)를 백엔드에서 강제 동기화
    // openTuningModal()의 syncConfig()가 완료되기 전 프리셋 클릭 시 슬라이더가 HTML 기본값
    // (value="1")인 채로 saveTuningConfig()가 실행되어 leverage=1이 저장되는 레이스 컨디션 방지
    await syncConfig(currentSymbol);

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

    // 2. [Phase 14.1/14.3] Gate Bypass 체크박스 동기화
    // [Bugfix] bypass 3개는 프리셋에 명시 없으면 항상 false로 강제 초기화
    // → 이전에 수동으로 켠 bypass가 다른 프리셋 적용 후에도 남아있는 문제 방지 (5m/15m 공통)
    const _BYPASS_KEYS = new Set(['bypass_macro', 'bypass_disparity', 'bypass_indicator']);
    for (const bkey of ['bypass_macro', 'bypass_disparity', 'bypass_indicator', 'exit_only_mode', 'shadow_hunting_enabled', 'auto_preset_enabled']) {
        const el = document.getElementById(`config-${bkey}`);
        if (!el) continue;
        if (bkey in config) {
            // 프리셋에 명시된 값 적용
            el.checked = (config[bkey] === 'true' || config[bkey] === true);
        } else if (_BYPASS_KEYS.has(bkey)) {
            // bypass 3개: 프리셋에 없으면 항상 false 강제 초기화 (방어코드)
            el.checked = false;
        } else {
            // exit_only_mode, shadow_hunting_enabled, auto_preset_enabled: 프리셋에 없으면 건드리지 않음
            continue;
        }
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
    lockBodyScroll();
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

function closeDiagnosticModal() {
    const modal = document.getElementById('diagnostic-modal');
    if (modal) modal.classList.add('hidden');
    unlockBodyScroll();
}

function closeHealthModal() {
    const modal = document.getElementById('health-modal');
    if (modal) modal.classList.add('hidden');
    unlockBodyScroll();
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
    lockBodyScroll();
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
    lockBodyScroll();
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
        // [Fix] /status, /diagnostic, /health_check 는 외부 API 호출(OKX+TG) 포함 → ping sweep 제외
        // /status: fetch_balance + fetch_positions (OKX 2회)
        // /diagnostic: 거래소 API 다수 호출
        // /health_check: fetch_balance + TG getMe + 자기 자신 재귀 호출 위험
        // 이 3개는 _hcFetchBackend()에서 이미 개별 점검됨 → 중복 제거
        { path: '/export_csv', name: 'CSV 내보내기 (export_csv)' },
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
    // Vercel 환경에서는 HTTP 프록시만 지원 (/api/v1/*) — /ws/dashboard는 프록시 불가
    // 현재 접속 호스트가 Vercel 도메인이면 직접 WS 테스트 대신 안내 메시지 표시
    const isVercel = location.host.includes('vercel.app') || location.host.includes('vercel.com');
    if (isVercel) {
        results.push({
            id: 'ws_dashboard', name: '대시보드 WebSocket (/ws/dashboard)',
            status: 'WARN', latency_ms: 0,
            details: 'Vercel 프록시 환경 — WS 직접 테스트 불가 (HTTP 프록시만 지원). 백엔드 Private WS 상태로 대체 확인.'
        });
    } else {
        // 직접 접속 환경 (AWS IP 등): 실제 WS 연결 테스트
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
    unlockBodyScroll();
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

    // ① 메인 차트 (캔들)
    chart = LightweightCharts.createChart(container, {
        autoSize: true,
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e' },
        grid: {
            vertLines: { color: 'rgba(48,54,61,0.5)' },
            horzLines: { color: 'rgba(48,54,61,0.5)' },
        },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#30363d' },
        rightPriceScale: { borderColor: '#30363d' },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00ff88', downColor: '#ff4d4d',
        borderVisible: false, wickUpColor: '#00ff88', wickDownColor: '#ff4d4d',
    });

    // ② 볼륨 히스토그램 (메인 차트 하단, 별도 스케일)
    volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        scaleMargins: { top: 0.85, bottom: 0 },
    });

    // ③ EMA20 라인 (전략 핵심 지표)
    ema20Series = chart.addLineSeries({
        color: 'rgba(88,166,255,0.85)', lineWidth: 1,
        title: 'EMA20', priceLineVisible: false, lastValueVisible: false,
    });

    // ④ EMA200 1h 라인 (거시 추세 — 단일 수평선, Dashed)
    ema200Series = chart.addLineSeries({
        color: 'rgba(255,200,80,0.7)', lineWidth: 1, lineStyle: 2,
        title: 'EMA200(1h)', priceLineVisible: false, lastValueVisible: true,
    });

    // ⑤ RSI 서브 차트
    const rsiContainer = document.getElementById('rsi-chart-container');
    if (rsiContainer) {
        rsiChart = LightweightCharts.createChart(rsiContainer, {
            autoSize: true,
            layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e', fontSize: 9 },
            grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(48,54,61,0.3)' } },
            timeScale: { visible: false, borderColor: '#30363d' },
            rightPriceScale: { borderColor: '#30363d', scaleMargins: { top: 0.1, bottom: 0.1 } },
            crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
        });
        rsiSeries = rsiChart.addLineSeries({
            color: '#c084fc', lineWidth: 1,
            priceLineVisible: false, lastValueVisible: true,
        });
        [30, 55, 70].forEach(v => rsiSeries.createPriceLine({
            price: v, color: 'rgba(255,255,255,0.15)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false,
        }));
    }

    // ⑥ MACD 서브 차트
    const macdContainer = document.getElementById('macd-chart-container');
    if (macdContainer) {
        macdChart = LightweightCharts.createChart(macdContainer, {
            autoSize: true,
            layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e', fontSize: 9 },
            grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(48,54,61,0.3)' } },
            timeScale: { visible: false, borderColor: '#30363d' },
            rightPriceScale: { borderColor: '#30363d', scaleMargins: { top: 0.15, bottom: 0.15 } },
            crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
        });
        macdHistSeries = macdChart.addHistogramSeries({
            priceLineVisible: false, lastValueVisible: false,
        });
        macdSignalSeries = macdChart.addLineSeries({
            color: 'rgba(255,165,0,0.7)', lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false,
        });
    }
}

async function syncChart() {
    try {
        if (!chart) initChart();

        // Stale Response 방어: 요청 시점의 심볼을 캡처
        const requestedSymbol = currentSymbol;

        // ── OHLCV (지표 포함) ────────────────────────────────────────────────
        const response = await fetch(`${API_URL}/ohlcv?symbol=${encodeURIComponent(requestedSymbol)}&limit=60`);
        const ohlcv = await response.json();
        if (requestedSymbol !== currentSymbol) return;

        const overlay = document.getElementById('chart-overlay');
        if (ohlcv.error || !Array.isArray(ohlcv) || ohlcv.length === 0) {
            if (overlay) overlay.classList.remove('hidden');
            return;
        }
        if (overlay) overlay.classList.add('hidden');
        if (!candleSeries) return;

        const KST = 9 * 3600; // KST = UTC+9 (초 단위)

        // ① 캔들 데이터 세팅
        const candles = ohlcv.map(c => ({
            time: Math.floor(c.timestamp / 1000) + KST,
            open:  parseFloat(c.open),
            high:  parseFloat(c.high),
            low:   parseFloat(c.low),
            close: parseFloat(c.close),
        }));
        candleSeries.setData(candles);
        lastCandleData = candles[candles.length - 1];

        // ② 볼륨 바
        if (volumeSeries) {
            const volumes = ohlcv.map(c => ({
                time:  Math.floor(c.timestamp / 1000) + KST,
                value: parseFloat(c.volume || 0),
                color: parseFloat(c.close) >= parseFloat(c.open)
                    ? 'rgba(0,255,136,0.25)' : 'rgba(255,77,77,0.25)',
            }));
            volumeSeries.setData(volumes);
        }

        // ③ EMA20 라인
        if (ema20Series) {
            const ema20Data = ohlcv
                .filter(c => c.ema_20 != null)
                .map(c => ({ time: Math.floor(c.timestamp / 1000) + KST, value: parseFloat(c.ema_20) }));
            ema20Series.setData(ema20Data);
        }

        // ④ RSI 서브 패널
        if (rsiSeries) {
            const rsiData = ohlcv
                .filter(c => c.rsi != null)
                .map(c => ({ time: Math.floor(c.timestamp / 1000) + KST, value: parseFloat(c.rsi) }));
            rsiSeries.setData(rsiData);
        }

        // ⑤ MACD 서브 패널 (히스토그램 + 시그널)
        if (macdHistSeries) {
            const macdData = ohlcv
                .filter(c => c.macd != null)
                .map(c => {
                    const v = parseFloat(c.macd);
                    return {
                        time:  Math.floor(c.timestamp / 1000) + KST,
                        value: v,
                        color: v >= 0 ? 'rgba(0,255,136,0.6)' : 'rgba(255,77,77,0.6)',
                    };
                });
            macdHistSeries.setData(macdData);
        }
        if (macdSignalSeries) {
            const sigData = ohlcv
                .filter(c => c.macd_signal != null)
                .map(c => ({ time: Math.floor(c.timestamp / 1000) + KST, value: parseFloat(c.macd_signal) }));
            macdSignalSeries.setData(sigData);
        }

        // ── ⑥ EMA200(1h 거시) — /api/v1/brain 에서 단일값 수평선 ─────────────
        try {
            const brainRes = await fetch(`${API_URL}/brain`);
            const brain = await brainRes.json();
            if (requestedSymbol !== currentSymbol) return;
            const macroEma200 = brain?.symbols?.[requestedSymbol]?.macro_ema_200;
            if (ema200Series && macroEma200) {
                const ema200Data = candles.map(c => ({ time: c.time, value: parseFloat(macroEma200) }));
                ema200Series.setData(ema200Data);
            }
        } catch(e) { /* EMA200 실패 무시 */ }

        // ── ⑦ TP/SL/진입가 수평선 — /api/v1/status 에서 ──────────────────────
        try {
            const statusRes = await fetch(`${API_URL}/status`);
            const status = await statusRes.json();
            if (requestedSymbol !== currentSymbol) return;
            const sym = status?.symbols?.[requestedSymbol];

            // 기존 수평선 제거
            if (entryPriceLine) { try { candleSeries.removePriceLine(entryPriceLine); } catch(e){} entryPriceLine = null; }
            if (tpPriceLine)    { try { candleSeries.removePriceLine(tpPriceLine);    } catch(e){} tpPriceLine    = null; }
            if (slPriceLine)    { try { candleSeries.removePriceLine(slPriceLine);    } catch(e){} slPriceLine    = null; }

            if (sym && sym.position && sym.position !== 'NONE' && !sym.position.startsWith('PENDING')) {
                const entryP = parseFloat(sym.entry_price || 0);
                const realSl = parseFloat(sym.real_sl || 0);
                const realTp = parseFloat(sym.last_placed_tp_price || 0);
                const isLong = sym.position === 'LONG';

                if (entryP > 0) entryPriceLine = candleSeries.createPriceLine({
                    price: entryP, color: '#8b949e', lineWidth: 1, lineStyle: 2,
                    axisLabelVisible: true, title: '진입',
                });
                if (realSl > 0) slPriceLine = candleSeries.createPriceLine({
                    price: realSl, color: '#ff4d4d', lineWidth: 1, lineStyle: 1,
                    axisLabelVisible: true, title: 'SL',
                });
                if (realTp > 0) tpPriceLine = candleSeries.createPriceLine({
                    price: realTp, color: '#00ff88', lineWidth: 1, lineStyle: 1,
                    axisLabelVisible: true, title: 'TP',
                });

                // ⑧ 포지션 배경 틴트
                const tint = document.getElementById('chart-pos-tint');
                if (tint) {
                    tint.style.backgroundColor = isLong ? 'rgba(0,255,136,0.04)' : 'rgba(255,77,77,0.04)';
                    tint.style.opacity = '1';
                }

                // ⑧ 포지션 배지
                const badge = document.getElementById('chart-position-badge');
                if (badge) {
                    badge.textContent = isLong ? '🟢 LONG' : '🔴 SHORT';
                    badge.className = `text-[10px] font-mono px-1.5 py-0.5 rounded ${
                        isLong
                            ? 'bg-green-500/20 text-green-400 border border-green-500/40'
                            : 'bg-red-500/20 text-red-400 border border-red-500/40'
                    }`;
                    badge.classList.remove('hidden');
                }
            } else {
                const tint = document.getElementById('chart-pos-tint');
                if (tint) tint.style.opacity = '0';
                const badge = document.getElementById('chart-position-badge');
                if (badge) badge.classList.add('hidden');
            }
        } catch(e) { /* status 실패 무시 */ }

        // ── ⑨ 차트 헤더 업데이트 (우상단 오버레이) ──────────────────────────
        // 다음 캔들 카운트다운
        if (candles.length > 1) {
            const tfSec = candles[candles.length - 1].time - candles[candles.length - 2].time;
            const nextCandleTime = lastCandleData.time + tfSec;
            const nowKst = Math.floor(Date.now() / 1000) + KST;
            const remaining = nextCandleTime - nowKst;
            const countdown = document.getElementById('chart-candle-countdown');
            if (countdown) {
                if (remaining > 0) {
                    const m = Math.floor(remaining / 60);
                    const s = remaining % 60;
                    countdown.textContent = `다음 캔들 ${m}:${String(s).padStart(2, '0')}`;
                } else {
                    countdown.textContent = '';
                }
            }
        }

        // ── ④ 온차트 매매 마커 (시간 필터링 적용) ────────────────────────────
        try {
            const tradesRes = await fetch(`${API_URL}/trades`);
            const allTrades = await tradesRes.json();
            if (requestedSymbol !== currentSymbol) return;

            // 차트 가시 범위 밖(첫 캔들 이전)의 마커는 제거
            const minChartTime = candles.length > 0 ? candles[0].time : 0;

            if (Array.isArray(allTrades) && allTrades.length > 0) {
                const symbolTrades = allTrades.filter(t => t.symbol === requestedSymbol);
                const markers = [];

                symbolTrades.forEach(trade => {
                    const posType = (trade.position_type || '').toUpperCase();
                    const pnl = parseFloat(trade.pnl ?? 0);

                    if (trade.entry_time) {
                        const entryTs = Math.floor(
                            new Date(String(trade.entry_time).replace(' ', 'T') + 'Z').getTime() / 1000
                        ) + KST;
                        if (!isNaN(entryTs) && entryTs >= minChartTime) {
                            markers.push({
                                time: entryTs,
                                position: posType === 'LONG' ? 'belowBar' : 'aboveBar',
                                color: posType === 'LONG' ? '#00ff88' : '#ff4d4d',
                                shape: posType === 'LONG' ? 'arrowUp' : 'arrowDown',
                                text: posType === 'LONG' ? '🟢 LONG 진입' : '🔴 SHORT 진입',
                            });
                        }
                    }

                    if (trade.exit_time) {
                        const exitTs = Math.floor(
                            new Date(String(trade.exit_time).replace(' ', 'T') + 'Z').getTime() / 1000
                        ) + KST;
                        if (!isNaN(exitTs) && exitTs >= minChartTime) {
                            markers.push({
                                time: exitTs,
                                position: posType === 'LONG' ? 'aboveBar' : 'belowBar',
                                color: pnl >= 0 ? '#00ff88' : '#ff4d4d',
                                shape: 'circle',
                                text: pnl >= 0 ? '✅ 익절' : '💀 손절',
                            });
                        }
                    }
                });

                markers.sort((a, b) => a.time - b.time);
                candleSeries.setMarkers(markers);
            } else {
                candleSeries.setMarkers([]);
            }
        } catch (markerErr) {
            console.warn("Marker Sync Failed:", markerErr);
        }

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

        // ── Hero PnL ──────────────────────────────────────────────────────────
        const totalNetVal = parseFloat(stats.total_net_pnl || 0);
        const dailyNetVal = parseFloat(stats.daily_net_pnl || 0);

        const totalNetEl = document.getElementById('stats-total-net');
        if (totalNetEl) {
            totalNetEl.textContent = (totalNetVal >= 0 ? '+' : '') + totalNetVal.toFixed(2);
            totalNetEl.className = totalNetEl.className.replace(/text-neon-(green|red)/g, '') + (totalNetVal >= 0 ? ' text-neon-green' : ' text-neon-red');
        }
        const dailyNetEl = document.getElementById('stats-daily-net');
        if (dailyNetEl) {
            dailyNetEl.textContent = (dailyNetVal >= 0 ? '+' : '') + dailyNetVal.toFixed(2);
            dailyNetEl.className = dailyNetEl.className.replace(/text-neon-(green|red)/g, '') + (dailyNetVal >= 0 ? ' text-neon-green' : ' text-neon-red');
        }
        const dailySub = document.getElementById('stats-daily-sub');
        if (dailySub) {
            const dt = stats.daily_trades || 0;
            const dw = stats.daily_wins || 0;
            dailySub.textContent = `USDT 오늘 ${dt}회 (${dw}승)`;
        }

        // ── Win/Loss 프로그레스 바 ────────────────────────────────────────────
        const winTrades = stats.win_trades || 0;
        const lossTrades = stats.loss_trades || 0;
        const totalT = stats.total_trades || 0;
        const winPct = totalT > 0 ? (winTrades / totalT * 100) : 0;

        const winBar = document.getElementById('stats-win-bar');
        if (winBar) winBar.style.width = winPct.toFixed(1) + '%';
        const wlLabel = document.getElementById('stats-wl-label');
        if (wlLabel) wlLabel.textContent = `${winTrades}W · ${lossTrades}L`;

        // ── 4-stat 그리드 ─────────────────────────────────────────────────────
        updateNumberText('stats-total-trades', totalT, val => Math.floor(val));
        updateNumberText('stats-win-rate', stats.win_rate || 0, val => `${val.toFixed(2)}%`);

        const maxDdEl = document.getElementById('stats-max-dd');
        if (maxDdEl) maxDdEl.textContent = (stats.max_drawdown || 0).toFixed(2) + '%';

        const avgEl = document.getElementById('stats-avg-trade');
        if (avgEl) {
            const avg = parseFloat(stats.avg_net_pnl || 0);
            avgEl.textContent = (avg >= 0 ? '+' : '') + avg.toFixed(2);
            avgEl.className = avgEl.className.replace(/text-neon-(green|red)/g, '') + (avg >= 0 ? ' text-neon-green' : ' text-neon-red');
        }

        // ── Best / Worst ──────────────────────────────────────────────────────
        const bestEl = document.getElementById('stats-best');
        if (bestEl) {
            const v = parseFloat(stats.best_trade || 0);
            bestEl.textContent = (v >= 0 ? '+' : '') + v.toFixed(2) + ' U';
        }
        const worstEl = document.getElementById('stats-worst');
        if (worstEl) {
            const v = parseFloat(stats.worst_trade || 0);
            worstEl.textContent = (v >= 0 ? '+' : '') + v.toFixed(2) + ' U';
        }

        // ── Sharpe Ratio ──────────────────────────────────────────────────────
        const sharpeEl = document.getElementById('stats-sharpe');
        if (sharpeEl) {
            const s = parseFloat(stats.sharpe_ratio || 0);
            sharpeEl.textContent = s.toFixed(2);
            sharpeEl.className = sharpeEl.className.replace(/text-neon-(green|red)/g, '') + (s >= 1 ? ' text-neon-green' : s < 0 ? ' text-neon-red' : '');
        }

        // ── Streak ────────────────────────────────────────────────────────────
        const streakEl = document.getElementById('stats-streak');
        const streakIcon = document.getElementById('stats-streak-icon');
        if (streakEl) {
            const sc = stats.streak_count || 0;
            const st = stats.streak_type || 'W';
            if (sc === 0) {
                streakEl.textContent = '—';
                streakEl.className = streakEl.className.replace(/text-neon-(green|red)/g, '');
                if (streakIcon) streakIcon.textContent = '➖';
            } else if (st === 'W') {
                streakEl.textContent = `${sc}연승`;
                streakEl.className = streakEl.className.replace(/text-neon-(green|red)/g, '') + ' text-neon-green';
                if (streakIcon) streakIcon.textContent = sc >= 3 ? '🔥' : '✅';
            } else {
                streakEl.textContent = `${sc}연패`;
                streakEl.className = streakEl.className.replace(/text-neon-(green|red)/g, '') + ' text-neon-red';
                if (streakIcon) streakIcon.textContent = sc >= 3 ? '❄️' : '⚠️';
            }
        }

        // ── Recent Executions ─────────────────────────────────────────────────
        try {
            const tradesRes = await fetch(`${API_URL}/trades`);
            const trades = await tradesRes.json();

            const historyContainer = document.getElementById('recent-executions-list');
            if (historyContainer && trades && Array.isArray(trades) && trades.length > 0) {
                let histHtml = '';
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

// ═══════════ [Advanced Analytics] 심볼별 / 시간대별 / 방향별 분석 ═══════════
async function syncAdvancedStats() {
    try {
        const res = await fetch(`${API_URL}/stats/advanced`);
        if (!res.ok) return;
        const data = await res.json();

        // ── 분석 건수 ──
        const countEl = document.getElementById('adv-stats-count');
        if (countEl) countEl.textContent = `${data.total_analyzed || 0} trades analyzed`;

        // ── 방향별 LONG / SHORT ──
        (data.by_direction || []).forEach(d => {
            const dir = d.direction.toLowerCase();
            const wrEl = document.getElementById(`adv-${dir}-wr`);
            const trEl = document.getElementById(`adv-${dir}-trades`);
            const pnlEl = document.getElementById(`adv-${dir}-pnl`);
            if (wrEl) wrEl.textContent = `${d.win_rate}%`;
            if (trEl) trEl.textContent = `${d.total}회`;
            if (pnlEl) {
                const v = d.net_pnl;
                pnlEl.textContent = (v >= 0 ? '+' : '') + v.toFixed(2) + ' U';
                pnlEl.className = pnlEl.className.replace(/text-neon-(green|red)/g, '') + (v >= 0 ? ' text-neon-green' : ' text-neon-red');
            }
        });

        // ── 심볼별 테이블 ──
        const tbody = document.getElementById('adv-symbol-tbody');
        if (tbody) {
            let html = '';
            (data.by_symbol || []).forEach(s => {
                const pnlColor = s.net_pnl >= 0 ? 'text-neon-green' : 'text-neon-red';
                const pnlSign = s.net_pnl >= 0 ? '+' : '';
                const wrColor = s.win_rate >= 60 ? 'text-neon-green' : s.win_rate < 40 ? 'text-neon-red' : 'text-text-main';
                const holdStr = s.avg_hold_min > 0 ? `${s.avg_hold_min}m` : '-';
                html += `<tr class="border-b border-navy-border/20 hover:bg-navy-800/30 transition-colors">
                    <td class="py-1.5 px-1 text-left font-semibold text-text-main">${s.symbol}</td>
                    <td class="py-1.5 px-1 text-center text-gray-400">${s.total} <span class="text-gray-600">(${s.wins}W)</span></td>
                    <td class="py-1.5 px-1 text-center ${wrColor} font-bold">${s.win_rate}%</td>
                    <td class="py-1.5 px-1 text-right ${pnlColor} font-bold">${pnlSign}${s.net_pnl.toFixed(2)}</td>
                    <td class="py-1.5 px-1 text-right text-gray-400">${holdStr}</td>
                </tr>`;
            });
            tbody.innerHTML = html || '<tr><td colspan="5" class="py-3 text-center text-gray-600">데이터 없음</td></tr>';
        }

        // ── 시간대별 히트맵 ──
        const heatmapEl = document.getElementById('adv-hour-heatmap');
        if (heatmapEl) {
            let html = '';
            const hours = data.by_hour || [];
            const maxTrades = Math.max(1, ...hours.map(h => h.total));
            hours.forEach(h => {
                let bgColor = '#161b22';  // 거래 없음
                let borderStyle = 'border:1px solid #30363d;';
                if (h.total > 0) {
                    const intensity = Math.min(1, h.total / maxTrades);
                    if (h.net_pnl > 0) {
                        const g = Math.round(50 + intensity * 150);
                        bgColor = `rgb(0, ${g}, ${Math.round(g * 0.3)})`;
                    } else if (h.net_pnl < 0) {
                        const r = Math.round(80 + intensity * 175);
                        bgColor = `rgb(${r}, 0, ${Math.round(r * 0.15)})`;
                    } else {
                        bgColor = '#1c2128';
                    }
                    borderStyle = 'border:1px solid transparent;';
                }
                const tooltip = h.total > 0 ? `${h.label} | ${h.total}회 (${h.wins}W) | ${h.net_pnl >= 0 ? '+' : ''}${h.net_pnl.toFixed(2)} U` : `${h.label} | 거래 없음`;
                html += `<div class="rounded-sm aspect-square flex items-center justify-center text-[7px] font-mono text-gray-400/60 cursor-default"
                    style="background:${bgColor};${borderStyle}min-height:20px;" title="${tooltip}">
                    ${h.hour % 3 === 0 ? h.hour : ''}
                </div>`;
            });
            heatmapEl.innerHTML = html;
        }

        // ── 요일별 바 차트 ──
        const weekdayEl = document.getElementById('adv-weekday-chart');
        if (weekdayEl) {
            const days = data.by_weekday || [];
            const maxPnl = Math.max(0.01, ...days.map(d => Math.abs(d.net_pnl)));
            let html = '';
            days.forEach(d => {
                const isProfit = d.net_pnl >= 0;
                const barH = Math.max(4, Math.round((Math.abs(d.net_pnl) / maxPnl) * 100));
                const bgClass = isProfit ? 'bg-neon-green' : 'bg-neon-red';
                const textColor = isProfit ? 'text-neon-green' : 'text-neon-red';
                const pnlStr = (isProfit ? '+' : '') + d.net_pnl.toFixed(1);
                html += `<div class="flex-1 flex flex-col items-center gap-0.5" title="${d.day} | ${d.total}회 (WR:${d.win_rate}%) | ${pnlStr} U">
                    <span class="text-[7px] font-mono ${textColor} font-bold">${pnlStr}</span>
                    <div class="${bgClass} rounded-t-sm w-full transition-all duration-500" style="height:${barH}%;opacity:${d.total > 0 ? 0.8 : 0.15};min-height:2px;"></div>
                    <span class="text-[8px] font-mono text-gray-500">${d.day}</span>
                </div>`;
            });
            weekdayEl.innerHTML = html;
        }

    } catch (e) {
        console.warn("Advanced Stats Sync Failed:", e);
    }
}

// ═══════════ [Backtest Visualizer] 백테스트 실행 + 차트 렌더링 ═══════════
let _btChart = null;
let _btEquityChart = null;

async function runBacktestVisualizer() {
    const statusEl = document.getElementById('bt-status');
    if (statusEl) statusEl.textContent = '실행 중...';

    const symbol = document.getElementById('bt-symbol')?.value || 'BTC/USDT:USDT';
    const timeframe = document.getElementById('bt-timeframe')?.value || '15m';
    const limit = parseInt(document.getElementById('bt-limit')?.value || '500');
    const slippage = parseFloat(document.getElementById('bt-slippage')?.value || '5');

    try {
        const res = await fetch(`${API_URL}/backtest?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}&slippage_bps=${slippage}`, {
            method: 'POST',
        });
        const data = await res.json();

        if (data.error) {
            if (statusEl) statusEl.textContent = `오류: ${data.error}`;
            return;
        }

        // ── 요약 통계 ──
        const summaryEl = document.getElementById('bt-summary');
        if (summaryEl) summaryEl.classList.remove('hidden');
        const totalEl = document.getElementById('bt-total');
        if (totalEl) totalEl.textContent = data.total_trades;
        const wrEl = document.getElementById('bt-winrate');
        if (wrEl) {
            wrEl.textContent = `${data.win_rate}%`;
            wrEl.className = wrEl.className.replace(/text-neon-(green|red)/g, '') + (data.win_rate >= 50 ? ' text-neon-green' : ' text-neon-red');
        }
        const pnlEl = document.getElementById('bt-pnl');
        if (pnlEl) {
            const pv = data.total_pnl_percent;
            pnlEl.textContent = `${pv >= 0 ? '+' : ''}${pv}%`;
            pnlEl.className = pnlEl.className.replace(/text-neon-(green|red)/g, '') + (pv >= 0 ? ' text-neon-green' : ' text-neon-red');
        }
        const mddEl = document.getElementById('bt-mdd');
        if (mddEl) mddEl.textContent = `${data.max_drawdown}%`;
        const sharpeEl = document.getElementById('bt-sharpe');
        if (sharpeEl) sharpeEl.textContent = data.sharpe_ratio;

        // ── 캔들 차트 + 마커 ──
        const chartContainer = document.getElementById('bt-chart-container');
        if (chartContainer && data.candles && data.candles.length > 0) {
            chartContainer.classList.remove('hidden');
            chartContainer.innerHTML = '';

            if (_btChart) { _btChart.remove(); _btChart = null; }

            _btChart = LightweightCharts.createChart(chartContainer, {
                autoSize: true,
                layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e', fontSize: 10 },
                grid: { vertLines: { color: 'rgba(48,54,61,0.3)' }, horzLines: { color: 'rgba(48,54,61,0.3)' } },
                crosshair: { mode: 0 },
                timeScale: { borderColor: '#30363d', timeVisible: true },
                rightPriceScale: { borderColor: '#30363d' },
            });

            const candleSeries = _btChart.addCandlestickSeries({
                upColor: '#00ff88', downColor: '#ff4d4d',
                borderUpColor: '#00ff88', borderDownColor: '#ff4d4d',
                wickUpColor: '#00ff88', wickDownColor: '#ff4d4d',
            });
            candleSeries.setData(data.candles);

            if (data.markers && data.markers.length > 0) {
                candleSeries.setMarkers(data.markers);
            }

            _btChart.timeScale().fitContent();
        }

        // ── 잔고 곡선 ──
        const equityContainer = document.getElementById('bt-equity-container');
        if (equityContainer && data.equity_curve && data.equity_curve.length > 0) {
            equityContainer.classList.remove('hidden');
            equityContainer.innerHTML = '';

            if (_btEquityChart) { _btEquityChart.remove(); _btEquityChart = null; }

            _btEquityChart = LightweightCharts.createChart(equityContainer, {
                autoSize: true,
                layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e', fontSize: 9 },
                grid: { vertLines: { color: 'rgba(48,54,61,0.2)' }, horzLines: { color: 'rgba(48,54,61,0.2)' } },
                crosshair: { mode: 0 },
                timeScale: { borderColor: '#30363d', timeVisible: true },
                rightPriceScale: { borderColor: '#30363d' },
            });

            const equitySeries = _btEquityChart.addAreaSeries({
                topColor: 'rgba(0, 255, 136, 0.3)',
                bottomColor: 'rgba(0, 255, 136, 0.02)',
                lineColor: '#00ff88',
                lineWidth: 2,
            });
            equitySeries.setData(data.equity_curve);
            _btEquityChart.timeScale().fitContent();
        }

        // ── 거래 로그 테이블 ──
        const tradesListEl = document.getElementById('bt-trades-list');
        if (tradesListEl && data.trades_log && data.trades_log.length > 0) {
            tradesListEl.classList.remove('hidden');
            let html = '<table class="w-full text-[9px] font-mono"><thead><tr class="text-gray-500 border-b border-navy-border/40">' +
                '<th class="text-left py-1 px-1">방향</th><th class="text-right py-1 px-1">진입가</th>' +
                '<th class="text-right py-1 px-1">청산가</th><th class="text-right py-1 px-1">PnL%</th>' +
                '<th class="text-right py-1 px-1">사유</th></tr></thead><tbody>';
            data.trades_log.forEach(t => {
                const pnlColor = t.pnl_percent >= 0 ? 'text-neon-green' : 'text-neon-red';
                const pnlSign = t.pnl_percent >= 0 ? '+' : '';
                const dirIcon = t.position === 'LONG' ? '📈' : '📉';
                const reasonMap = { 'STOP_LOSS': 'SL', 'TRAILING_STOP_EXIT': 'TS', 'END_OF_DATA': 'EOD' };
                const reason = reasonMap[t.exit_reason] || t.exit_reason;
                html += `<tr class="border-b border-navy-border/10 hover:bg-navy-800/20">` +
                    `<td class="py-1 px-1">${dirIcon} ${t.position}</td>` +
                    `<td class="py-1 px-1 text-right text-gray-400">$${t.entry_price.toFixed(2)}</td>` +
                    `<td class="py-1 px-1 text-right text-gray-400">$${t.exit_price.toFixed(2)}</td>` +
                    `<td class="py-1 px-1 text-right ${pnlColor} font-bold">${pnlSign}${t.pnl_percent.toFixed(2)}%</td>` +
                    `<td class="py-1 px-1 text-right text-gray-500">${reason}</td></tr>`;
            });
            html += '</tbody></table>';
            tradesListEl.innerHTML = html;
        }

        if (statusEl) statusEl.textContent = `완료 — ${data.total_trades}거래 (${symbol.split('/')[0]} ${timeframe})`;

    } catch (e) {
        console.error("Backtest error:", e);
        if (statusEl) statusEl.textContent = `오류: ${e.message}`;
    }
}


// ═══════════════════════════════════════════════════════
// [Parameter Auto-Optimizer] 그리드 서치 최적화 UI
// ═══════════════════════════════════════════════════════

async function runOptimizer() {
    const statusEl = document.getElementById('opt-status');
    const resultsEl = document.getElementById('opt-results');
    const cardsEl = document.getElementById('opt-cards');
    const compEl = document.getElementById('opt-comparison');
    const metaEl = document.getElementById('opt-meta');

    const symbol = document.getElementById('opt-symbol').value;
    const timeframe = document.getElementById('opt-timeframe').value;
    const limit = parseInt(document.getElementById('opt-limit').value);
    const slippage = parseFloat(document.getElementById('opt-slippage').value);

    if (statusEl) statusEl.textContent = '최적화 실행 중... (30초~2분 소요)';
    if (resultsEl) resultsEl.classList.add('hidden');

    try {
        const res = await fetch(
            `${API_URL}/optimize?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}&slippage_bps=${slippage}`,
            { method: 'POST' }
        );
        const data = await res.json();

        if (data.status === 'cooldown') {
            if (statusEl) statusEl.textContent = data.message || '쿨다운 중';
            return;
        }
        if (data.status === 'error') {
            if (statusEl) statusEl.textContent = `오류: ${data.message}`;
            return;
        }

        const recs = data.recommendations || [];
        if (recs.length === 0) {
            if (statusEl) statusEl.textContent = '유효한 추천안 없음 (과적합 필터 통과 조합 없음)';
            return;
        }

        // ── 현재 설정 vs TOP1 비교 테이블 ──
        const top1 = recs[0];
        let compHTML = `<div class="overflow-x-auto"><table class="w-full text-xs">
            <thead><tr class="text-gray-500 border-b border-navy-border">
                <th class="text-left py-1 px-2">파라미터</th>
                <th class="text-right py-1 px-2">현재값</th>
                <th class="text-right py-1 px-2">추천값</th>
                <th class="text-center py-1 px-2">변경</th>
            </tr></thead><tbody>`;

        for (const [pName, diff] of Object.entries(top1.diffs || {})) {
            const curVal = diff.current !== null && diff.current !== undefined ? parseFloat(diff.current) : '—';
            const recVal = diff.recommended;
            const dir = diff.direction;
            const dirColor = dir === 'UP' ? 'text-green-400' : dir === 'DOWN' ? 'text-red-400' : 'text-yellow-400';
            const dirIcon = dir === 'UP' ? '▲' : dir === 'DOWN' ? '▼' : '●';
            compHTML += `<tr class="border-b border-navy-border/30 hover:bg-navy-800/50">
                <td class="py-1 px-2 text-gray-300 font-mono">${pName}</td>
                <td class="py-1 px-2 text-right text-gray-400">${typeof curVal === 'number' ? curVal.toFixed(4) : curVal}</td>
                <td class="py-1 px-2 text-right text-white font-bold">${parseFloat(recVal).toFixed(4)}</td>
                <td class="py-1 px-2 text-center ${dirColor} font-bold">${dirIcon} ${dir}</td>
            </tr>`;
        }
        compHTML += '</tbody></table></div>';
        if (compEl) compEl.innerHTML = compHTML;

        // ── TOP 3 추천 카드 ──
        const rankColors = ['border-yellow-500/50 bg-yellow-500/5', 'border-gray-400/40 bg-gray-400/5', 'border-amber-700/40 bg-amber-700/5'];
        const rankLabels = ['🥇 1위', '🥈 2위', '🥉 3위'];

        let cardsHTML = '';
        recs.forEach((rec, i) => {
            const pnlColor = rec.total_pnl_percent >= 0 ? 'text-green-400' : 'text-red-400';
            const paramsJSON = JSON.stringify(rec.params).replace(/"/g, '&quot;');
            cardsHTML += `
            <div class="rounded-lg border ${rankColors[i] || 'border-navy-border'} p-3">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-sm font-bold text-white">${rankLabels[i] || `#${rec.rank}`}</span>
                    <span class="text-[10px] text-gray-500">Score ${rec.score}</span>
                </div>
                <div class="grid grid-cols-2 gap-y-1 text-xs mb-3">
                    <div class="text-gray-400">거래수</div><div class="text-right text-white">${rec.total_trades}</div>
                    <div class="text-gray-400">승률</div><div class="text-right text-white">${rec.win_rate}%</div>
                    <div class="text-gray-400">수익률</div><div class="text-right ${pnlColor} font-bold">${rec.total_pnl_percent > 0 ? '+' : ''}${rec.total_pnl_percent}%</div>
                    <div class="text-gray-400">MDD</div><div class="text-right text-orange-400">${rec.max_drawdown}%</div>
                    <div class="text-gray-400">Sharpe</div><div class="text-right text-cyan-400">${rec.sharpe_ratio}</div>
                </div>
                <button onclick="applyOptimization(${rec.rank}, '${paramsJSON}')"
                    class="w-full py-1.5 bg-purple-600/80 hover:bg-purple-500 text-white text-[11px] font-bold rounded transition-colors">
                    적용하기
                </button>
            </div>`;
        });
        if (cardsEl) cardsEl.innerHTML = cardsHTML;

        // ── 메타 정보 ──
        if (metaEl) {
            metaEl.textContent = `${data.total_tested}개 조합 테스트 | ${data.total_valid}개 유효 | ${data.elapsed_sec}초 소요 | ${symbol} ${timeframe} ${limit}봉`;
        }

        if (resultsEl) resultsEl.classList.remove('hidden');
        if (statusEl) statusEl.textContent = `완료 — TOP ${recs.length} 추천안 생성`;

    } catch (e) {
        console.error("Optimizer error:", e);
        if (statusEl) statusEl.textContent = `오류: ${e.message}`;
    }
}

async function applyOptimization(rank, paramsJSON) {
    if (!confirm(`추천안 #${rank}을 라이브에 적용하시겠습니까?\n\n⚠️ 포지션 보유 중이면 자동 차단됩니다.`)) return;

    try {
        const res = await fetch(
            `${API_URL}/optimize/apply?rank=${rank}&params=${encodeURIComponent(paramsJSON)}`,
            { method: 'POST' }
        );
        const data = await res.json();

        if (data.success) {
            alert(`✅ ${data.count}개 파라미터 적용 완료!\n\n적용 항목:\n${Object.entries(data.applied).map(([k, v]) => `  ${k}: ${v}`).join('\n')}`);
        } else {
            alert(`❌ 적용 실패: ${data.message}`);
        }
    } catch (e) {
        console.error("Apply error:", e);
        alert(`오류: ${e.message}`);
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

// ════════════ Config History (설정 변경 이력) ════════════

async function loadConfigHistory() {
    const container = document.getElementById('config-history-table');
    if (!container) return;
    container.textContent = '로딩 중...';

    try {
        const res = await fetch(`${API_URL}/config/history?limit=50`);
        const data = await res.json();
        const history = data.history || [];

        if (history.length === 0) {
            container.textContent = '변경 이력이 없습니다.';
            return;
        }

        let html = `<table class="w-full"><thead>
            <tr class="text-gray-500 border-b border-navy-border">
                <th class="text-left py-1 px-2">시간</th>
                <th class="text-left py-1 px-2">설정 키</th>
                <th class="text-right py-1 px-2">이전값</th>
                <th class="text-center py-1 px-1">→</th>
                <th class="text-right py-1 px-2">새값</th>
            </tr></thead><tbody>`;

        history.forEach(h => {
            const time = h.changed_at ? h.changed_at.replace('T', ' ').substring(0, 19) : '—';
            const oldVal = h.old_value !== null && h.old_value !== undefined ? h.old_value : '(신규)';
            const newVal = h.new_value || '—';
            // 키 이름에서 SYMBOL:: 접두사 분리
            const keyDisplay = h.key || '—';

            html += `<tr class="border-b border-navy-border/20 hover:bg-navy-800/50">
                <td class="py-1 px-2 text-gray-500 whitespace-nowrap">${time}</td>
                <td class="py-1 px-2 text-cyan-300">${keyDisplay}</td>
                <td class="py-1 px-2 text-right text-gray-400">${oldVal}</td>
                <td class="py-1 px-1 text-center text-gray-600">→</td>
                <td class="py-1 px-2 text-right text-white font-bold">${newVal}</td>
            </tr>`;
        });

        html += '</tbody></table>';
        container.innerHTML = html;

    } catch (e) {
        console.error('Config history error:', e);
        container.textContent = `오류: ${e.message}`;
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
    lockBodyScroll();
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
    unlockBodyScroll();
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

    // ── PnL 맵 구성 (win_rate, gross_pnl 포함) ──
    const pnlMap = {};
    if (Array.isArray(dailyData)) {
        dailyData.forEach(d => {
            pnlMap[d.date] = {
                net_pnl: parseFloat(d.net_pnl || 0),
                gross_pnl: parseFloat(d.gross_pnl || 0),
                total_trades: d.total_trades || 0,
                win_rate: parseFloat(d.win_rate || 0),
            };
        });
    }

    // ── 색상 스케일 (sqrt 정규화 — 소액 거래 색상 구분력 향상) ──
    const profits = Object.values(pnlMap).map(v => v.net_pnl).filter(v => v > 0);
    const losses  = Object.values(pnlMap).map(v => v.net_pnl).filter(v => v < 0);
    const maxProfit = profits.length > 0 ? Math.max(...profits) : 1;
    const maxLoss   = losses.length  > 0 ? Math.abs(Math.min(...losses)) : 1;

    function _cellColor(dateStr) {
        const d = pnlMap[dateStr];
        if (!d || d.total_trades === 0) return '#161b22';
        const pnl = d.net_pnl;
        if (pnl >= 0) {
            if (pnl === 0) return '#1c2128';
            const r = Math.sqrt(Math.min(pnl / maxProfit, 1));
            if (r < 0.25) return '#0e4429';
            if (r < 0.5)  return '#006d32';
            if (r < 0.75) return '#26a641';
            return '#39d353';
        } else {
            const r = Math.sqrt(Math.min(Math.abs(pnl) / maxLoss, 1));
            if (r < 0.25) return '#3d0000';
            if (r < 0.5)  return '#7a0000';
            if (r < 0.75) return '#b00020';
            return '#ff4d4d';
        }
    }

    // ── KST 기준 오늘 날짜 ──
    const kstNow  = new Date(Date.now() + 9 * 3600 * 1000);
    const todayKst = new Date(Date.UTC(kstNow.getUTCFullYear(), kstNow.getUTCMonth(), kstNow.getUTCDate()));
    const todayStr = todayKst.toISOString().split('T')[0];

    // ── 26주 전 일요일부터 시작 ──
    const startDate = new Date(todayKst);
    startDate.setUTCDate(startDate.getUTCDate() - 26 * 7);
    startDate.setUTCDate(startDate.getUTCDate() - startDate.getUTCDay());
    const startStr = startDate.toISOString().split('T')[0];

    // ── 주 단위 배열 생성 ──
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

    // ── 월 레이블 행 렌더링 (JS — heatmap-month-labels) ──
    const MONTH_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const monthLabelsEl = document.getElementById('heatmap-month-labels');
    if (monthLabelsEl) {
        let lastMonth = -1;
        let mHtml = '';
        weeks.forEach(week => {
            const m = parseInt(week[0].split('-')[1]) - 1;
            const show = (m !== lastMonth);
            if (show) lastMonth = m;
            mHtml += `<div style="width:12px;height:12px;flex-shrink:0;font-size:8px;font-family:monospace;color:${show ? '#6e7681' : 'transparent'};overflow:visible;white-space:nowrap;">${MONTH_ABBR[m]}</div>`;
        });
        monthLabelsEl.innerHTML = mHtml;
    }

    // ── 셀 HTML 생성 ──
    let html = '';
    weeks.forEach(week => {
        html += `<div class="flex flex-col shrink-0" style="gap:2px;">`;
        week.forEach(dateStr => {
            const isFuture = dateStr > todayStr;
            const isToday  = dateStr === todayStr;
            if (isFuture) {
                html += `<div style="width:12px;height:12px;border-radius:2px;background:transparent;"></div>`;
                return;
            }
            const color = _cellColor(dateStr);
            const d = pnlMap[dateStr];
            const pnlVal  = d ? d.net_pnl    : 0;
            const grossVal = d ? d.gross_pnl  : 0;
            const trades  = d ? d.total_trades : 0;
            const wr      = d ? d.win_rate    : 0;
            const todayStyle = isToday ? 'outline:1.5px solid #58a6ff;outline-offset:-1px;' : '';
            html += `<div
                class="heatmap-cell"
                style="width:12px;height:12px;border-radius:2px;background:${color};cursor:default;${todayStyle}"
                data-date="${dateStr}"
                data-pnl="${pnlVal}"
                data-gross="${grossVal}"
                data-trades="${trades}"
                data-wr="${wr}"
            ></div>`;
        });
        html += `</div>`;
    });
    container.innerHTML = html;

    // ── 26주 통계 바 렌더링 ──
    const statsEl = document.getElementById('heatmap-stats-bar');
    if (statsEl) {
        const rangeData = Array.isArray(dailyData) ? dailyData.filter(d => d.date >= startStr) : [];
        if (rangeData.length > 0) {
            const totalPnl    = rangeData.reduce((s, d) => s + parseFloat(d.net_pnl || 0), 0);
            const totalTrades = rangeData.reduce((s, d) => s + (d.total_trades || 0), 0);
            const totalWins   = rangeData.reduce((s, d) => s + Math.round(parseFloat(d.win_rate || 0) / 100 * (d.total_trades || 0)), 0);
            const overallWR   = totalTrades > 0 ? (totalWins / totalTrades * 100) : 0;
            const activeDays  = rangeData.filter(d => d.total_trades > 0).length;
            const sorted      = [...rangeData].sort((a, b) => parseFloat(b.net_pnl) - parseFloat(a.net_pnl));
            const bestDay     = sorted[0];
            const worstDay    = sorted[sorted.length - 1];
            const pnlColor    = totalPnl >= 0 ? '#39d353' : '#ff4d4d';
            const pnlSign     = totalPnl >= 0 ? '+' : '';
            const bestPnl     = parseFloat(bestDay.net_pnl);
            const worstPnl    = parseFloat(worstDay.net_pnl);

            statsEl.innerHTML = `
                <div class="flex items-center gap-4 flex-wrap">
                    <div class="flex flex-col">
                        <span class="text-[8px] font-mono text-gray-600 uppercase tracking-wider">26주 누적 PnL</span>
                        <span class="text-[13px] font-mono font-bold leading-tight" style="color:${pnlColor}">${pnlSign}${totalPnl.toFixed(2)} <span class="text-[9px] font-normal text-gray-600">USDT</span></span>
                    </div>
                    <div class="w-px self-stretch bg-navy-border/50 shrink-0"></div>
                    <div class="flex flex-col">
                        <span class="text-[8px] font-mono text-gray-600 uppercase tracking-wider">총 거래</span>
                        <span class="text-[13px] font-mono font-bold text-gray-300 leading-tight">${totalTrades}<span class="text-[9px] font-normal text-gray-600"> 건</span></span>
                    </div>
                    <div class="flex flex-col">
                        <span class="text-[8px] font-mono text-gray-600 uppercase tracking-wider">활성일</span>
                        <span class="text-[13px] font-mono font-bold text-gray-400 leading-tight">${activeDays}<span class="text-[9px] font-normal text-gray-600"> 일</span></span>
                    </div>
                    <div class="flex flex-col">
                        <span class="text-[8px] font-mono text-gray-600 uppercase tracking-wider">승률</span>
                        <span class="text-[13px] font-mono font-bold text-blue-400 leading-tight">${overallWR.toFixed(1)}<span class="text-[9px] font-normal text-gray-600"> %</span></span>
                    </div>
                    <div class="w-px self-stretch bg-navy-border/50 shrink-0"></div>
                    <div class="flex flex-col">
                        <span class="text-[8px] font-mono text-gray-600 uppercase tracking-wider">최고일</span>
                        <span class="text-[11px] font-mono font-bold text-neon-green leading-tight">${bestPnl >= 0 ? '+' : ''}${bestPnl.toFixed(2)}</span>
                        <span class="text-[8px] font-mono text-gray-600">${bestDay.date}</span>
                    </div>
                    <div class="flex flex-col">
                        <span class="text-[8px] font-mono text-gray-600 uppercase tracking-wider">최악일</span>
                        <span class="text-[11px] font-mono font-bold text-red-400 leading-tight">${worstPnl.toFixed(2)}</span>
                        <span class="text-[8px] font-mono text-gray-600">${worstDay.date}</span>
                    </div>
                </div>`;
        } else {
            statsEl.innerHTML = `<div class="text-[10px] font-mono text-gray-600">실전 거래 데이터가 쌓이면 26주 통계가 표시됩니다.</div>`;
        }
    }

    // ── 리치 툴팁 이벤트 바인딩 ──
    const tooltip = document.getElementById('heatmap-tooltip');
    if (!tooltip) return;
    container.querySelectorAll('.heatmap-cell').forEach(cell => {
        cell.addEventListener('mousemove', (e) => {
            const trades  = parseInt(cell.dataset.trades || 0);
            const pnl     = parseFloat(cell.dataset.pnl   || 0);
            const gross   = parseFloat(cell.dataset.gross  || 0);
            const wr      = parseFloat(cell.dataset.wr    || 0);
            const date    = cell.dataset.date;
            const pnlColor = pnl >= 0 ? '#39d353' : '#ff4d4d';
            const pnlSign  = pnl >= 0 ? '+' : '';
            const gSign    = gross >= 0 ? '+' : '';
            if (trades > 0) {
                tooltip.innerHTML = `
                    <div style="color:#8b949e;font-size:9px;margin-bottom:4px;">📅 ${date}</div>
                    <div style="color:${pnlColor};font-weight:bold;font-size:12px;">💰 ${pnlSign}${pnl.toFixed(2)} USDT</div>
                    <div style="color:#6e7681;font-size:9px;margin-top:1px;">Gross ${gSign}${gross.toFixed(2)} USDT</div>
                    <div style="border-top:1px solid #21262d;margin:4px 0;"></div>
                    <div style="color:#c9d1d9;font-size:9px;">거래 ${trades}건 &nbsp;·&nbsp; 승률 ${wr.toFixed(0)}%</div>`;
            } else {
                tooltip.innerHTML = `
                    <div style="color:#8b949e;font-size:9px;">📅 ${date}</div>
                    <div style="color:#484f58;font-size:9px;margin-top:2px;">거래없음</div>`;
            }
            tooltip.classList.remove('hidden');
            tooltip.style.left = (e.clientX + 14) + 'px';
            tooltip.style.top  = (e.clientY - 75) + 'px';
        });
        cell.addEventListener('mouseleave', () => tooltip.classList.add('hidden'));
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

    // ── 바이패스 토글 버튼 갱신 ──
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

    // ── 방어 상태 모니터 렌더링 ──
    const ds = data._defense_state;
    if (!ds) return;

    // Kill Switch 카드
    const ksCard  = document.getElementById('def-ks-card');
    const ksDot   = document.getElementById('def-ks-dot');
    const ksLabel = document.getElementById('def-ks-label');
    const ksTimer = document.getElementById('def-ks-timer');
    if (ksCard && ksDot && ksLabel && ksTimer) {
        if (ds.kill_switch_active) {
            ksCard.className  = 'rounded-lg p-2.5 border border-red-500/50 bg-red-900/20 shadow-[0_0_10px_rgba(239,68,68,0.15)] transition-all duration-500';
            ksDot.className   = 'w-2 h-2 rounded-full bg-red-500 animate-pulse transition-all duration-300';
            ksLabel.className = 'text-[11px] font-mono font-bold text-red-400';
            ksLabel.textContent = '🔴 ACTIVE';
            const rem = ds.kill_switch_remaining_sec;
            const kh = Math.floor(rem / 3600), km = Math.floor((rem % 3600) / 60);
            ksTimer.textContent = `${kh}시간 ${km}분 남음`;
        } else {
            ksCard.className  = 'rounded-lg p-2.5 border border-gray-700/50 bg-gray-900/60 transition-all duration-500';
            ksDot.className   = 'w-2 h-2 rounded-full bg-emerald-500 transition-all duration-300';
            ksLabel.className = 'text-[11px] font-mono font-bold text-emerald-400';
            ksLabel.textContent = '🟢 정상';
            ksTimer.textContent = '';
        }
    }

    // 연패 쿨다운 카드
    const cdCard  = document.getElementById('def-cd-card');
    const cdDot   = document.getElementById('def-cd-dot');
    const cdLabel = document.getElementById('def-cd-label');
    const cdCount = document.getElementById('def-cd-count');
    const cdTimer = document.getElementById('def-cd-timer');
    if (cdCard && cdDot && cdLabel && cdTimer) {
        const losses  = ds.consecutive_losses || 0;
        const trigger = ds.cd_trigger || 3;
        const countTxt = `${losses}/${trigger}`;
        if (cdCount) cdCount.textContent = countTxt;

        if (ds.cooldown_active) {
            cdCard.className  = 'rounded-lg p-2.5 border border-orange-500/50 bg-orange-900/20 shadow-[0_0_10px_rgba(249,115,22,0.15)] transition-all duration-500';
            cdDot.className   = 'w-2 h-2 rounded-full bg-orange-400 animate-pulse transition-all duration-300';
            cdLabel.className = 'text-[11px] font-mono font-bold text-orange-400';
            cdLabel.innerHTML = `🟡 COOLING <span id="def-cd-count" class="text-[9px] font-normal">${countTxt}</span>`;
            const rem = ds.cooldown_remaining_sec;
            const cm = Math.floor(rem / 60), cs = Math.floor(rem % 60);
            cdTimer.textContent = `${cm}분 ${cs}초 남음`;
        } else if (losses > 0) {
            cdCard.className  = 'rounded-lg p-2.5 border border-yellow-700/40 bg-yellow-900/10 transition-all duration-500';
            cdDot.className   = 'w-2 h-2 rounded-full bg-yellow-500 transition-all duration-300';
            cdLabel.className = 'text-[11px] font-mono font-bold text-yellow-500';
            cdLabel.innerHTML = `⚠️ ${losses}연패 <span id="def-cd-count" class="text-[9px] font-normal">${countTxt}</span>`;
            cdTimer.textContent = '';
        } else {
            cdCard.className  = 'rounded-lg p-2.5 border border-gray-700/50 bg-gray-900/60 transition-all duration-500';
            cdDot.className   = 'w-2 h-2 rounded-full bg-emerald-500 transition-all duration-300';
            cdLabel.className = 'text-[11px] font-mono font-bold text-emerald-400';
            cdLabel.innerHTML = `🟢 정상 <span id="def-cd-count" class="text-[9px] text-gray-700 font-normal">${countTxt}</span>`;
            cdTimer.textContent = '';
        }
    }

    // 일일 누적 PnL 미니바
    const pnlEl  = document.getElementById('def-daily-pnl');
    const barEl  = document.getElementById('def-daily-bar');
    if (pnlEl && barEl) {
        const pct    = ds.daily_pnl_pct || 0;
        const maxPct = ds.daily_max_pct || 7;
        const sign   = pct >= 0 ? '+' : '';
        pnlEl.textContent = `${sign}${pct.toFixed(2)}%`;
        if (pct >= 0) {
            pnlEl.className = 'text-[9px] font-mono font-bold text-neon-green';
            barEl.className = 'h-full transition-all duration-700 rounded-full bg-neon-green';
        } else {
            pnlEl.className = 'text-[9px] font-mono font-bold text-red-400';
            barEl.className = 'h-full transition-all duration-700 rounded-full bg-red-500';
        }
        barEl.style.width = `${Math.min(100, Math.abs(pct) / maxPct * 100).toFixed(1)}%`;
    }
}

// ════════════════════════════════════════════════════════════════════════════
// [X-Ray] 매매 진단 시스템 — 5탭 모달
// ════════════════════════════════════════════════════════════════════════════

let _currentXrayTab = 'loop';

function openXrayModal() {
    document.getElementById('xray-modal').classList.remove('hidden');
    lockBodyScroll();
    switchXrayTab('loop');
}

function closeXrayModal() {
    document.getElementById('xray-modal').classList.add('hidden');
    unlockBodyScroll();
}

function switchXrayTab(tab) {
    _currentXrayTab = tab;
    document.querySelectorAll('.xray-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });
    refreshCurrentXrayTab();
}

async function refreshCurrentXrayTab() {
    const content = document.getElementById('xray-content');
    const tsEl = document.getElementById('xray-timestamp');
    content.innerHTML = '<div class="text-center text-gray-500 font-mono text-xs py-8 animate-pulse">로딩 중...</div>';
    try {
        switch (_currentXrayTab) {
            case 'loop': await _xrayLoadLoopState(content); break;
            case 'blocker': await _xrayRunBlockerWizard(content); break;
            case 'attempts': await _xrayLoadTradeAttempts(content); break;
            case 'gates': await _xrayLoadGateScoreboard(content); break;
            case 'okx': await _xrayLoadOkxDeepVerify(content); break;
        }
        if (tsEl) tsEl.textContent = `점검 시각: ${new Date().toLocaleString('ko-KR')}`;
    } catch (e) {
        content.innerHTML = `<div class="text-center text-red-400 font-mono text-xs py-8">로드 실패: ${e.message}</div>`;
    }
}

// ── Tab 1: 루프 상태 ──
async function _xrayLoadLoopState(container) {
    const resp = await fetch(`${API_URL}/xray/loop_state`);
    const d = await resp.json();

    const _badge = (ok, label, detail) => {
        const color = ok ? 'emerald' : 'red';
        return `<div class="rounded-lg border border-${color}-500/30 bg-${color}-500/5 p-3">
            <div class="flex items-center gap-2 mb-1">
                <span class="w-2 h-2 rounded-full bg-${color}-400"></span>
                <span class="text-[10px] font-mono font-bold text-${color}-400">${label}</span>
            </div>
            <div class="text-[9px] font-mono text-gray-400">${detail}</div>
        </div>`;
    };

    let html = '<div class="space-y-4">';

    // 상태 카드 3개
    html += '<div class="grid grid-cols-3 gap-3">';
    html += _badge(d.is_running, '엔진 상태', d.is_running ? '가동 중' : '중지됨');
    html += _badge(!d.kill_switch.active,
        '킬스위치',
        d.kill_switch.active ? `발동 중 | 일일: ${d.kill_switch.daily_pnl_pct}% | ${d.kill_switch.remaining_text}` : `비활성 | 일일: ${d.kill_switch.daily_pnl_pct}%`
    );
    html += _badge(!d.cooldown.active,
        '연패 쿨다운',
        d.cooldown.active ? `${d.cooldown.consecutive_losses}연패 | ${d.cooldown.remaining_text}` : `${d.cooldown.consecutive_losses}연패 / ${d.cooldown.trigger_threshold} 트리거`
    );
    html += '</div>';

    // 루프 정보
    html += `<div class="rounded-lg border border-navy-border bg-navy-card/30 p-3">
        <div class="text-[10px] font-mono font-bold text-gray-300 mb-2">루프 정보</div>
        <div class="grid grid-cols-2 gap-2 text-[9px] font-mono text-gray-400">
            <div>사이클 카운트: <span class="text-text-main">${d.loop_cycle_count.toLocaleString()}</span></div>
            <div>활성 심볼: <span class="text-text-main">${d.active_symbols_count}개</span></div>
            <div>마지막 스캔: <span class="text-text-main">${d.last_scan_time_text}</span></div>
            <div>태스크 상태: <span class="${d.trading_task_alive ? 'text-emerald-400' : 'text-red-400'}">${d.trading_task_alive ? 'ALIVE' : 'DEAD'}</span></div>
        </div>
    </div>`;

    // 심볼 테이블
    if (d.symbols.length > 0) {
        html += `<div class="rounded-lg border border-navy-border bg-navy-card/30 p-3">
            <div class="text-[10px] font-mono font-bold text-gray-300 mb-2">심볼별 상태</div>
            <table class="w-full text-[9px] font-mono">
                <thead><tr class="text-gray-500 border-b border-navy-border">
                    <th class="text-left py-1">심볼</th>
                    <th class="text-center py-1">방향</th>
                    <th class="text-center py-1">Exit-Only</th>
                    <th class="text-center py-1">포지션</th>
                    <th class="text-center py-1">게이트</th>
                </tr></thead><tbody>`;
        d.symbols.forEach(s => {
            const posColor = s.position === 'NONE' ? 'text-gray-500' : (s.position === 'LONG' ? 'text-emerald-400' : 'text-red-400');
            const gateColor = s.gates_passed >= 6 ? 'text-emerald-400' : (s.gates_passed >= 4 ? 'text-yellow-400' : 'text-gray-500');
            html += `<tr class="border-b border-navy-border/50">
                <td class="py-1.5 text-text-main">${s.symbol_short}</td>
                <td class="text-center py-1.5 ${s.direction_mode === 'AUTO' ? 'text-gray-400' : 'text-yellow-400'}">${s.direction_mode}</td>
                <td class="text-center py-1.5 ${s.exit_only ? 'text-red-400' : 'text-gray-500'}">${s.exit_only ? 'ON' : 'OFF'}</td>
                <td class="text-center py-1.5 ${posColor}">${s.position}</td>
                <td class="text-center py-1.5 ${gateColor}">${s.gates_passed}/6</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
    }

    // 마지막 시도/성공
    html += `<div class="grid grid-cols-2 gap-3">
        <div class="rounded-lg border border-navy-border bg-navy-card/30 p-3">
            <div class="text-[10px] font-mono font-bold text-gray-300 mb-1">마지막 진입 시도</div>
            <div class="text-[9px] font-mono text-gray-400">
                ${d.last_entry_attempt.time_text !== '없음'
                    ? `${d.last_entry_attempt.time_text}<br>
                       <span class="text-text-main">${d.last_entry_attempt.symbol}</span>
                       <span class="${d.last_entry_attempt.result === 'SUCCESS' ? 'text-emerald-400' : (d.last_entry_attempt.result === 'BLOCKED' ? 'text-yellow-400' : 'text-red-400')}">${d.last_entry_attempt.result}</span>
                       ${d.last_entry_attempt.reason ? `<br><span class="text-gray-500">${d.last_entry_attempt.reason}</span>` : ''}`
                    : '아직 시도 기록 없음'}
            </div>
        </div>
        <div class="rounded-lg border border-navy-border bg-navy-card/30 p-3">
            <div class="text-[10px] font-mono font-bold text-gray-300 mb-1">마지막 진입 성공</div>
            <div class="text-[9px] font-mono text-gray-400">
                ${d.last_successful_entry.time_text !== '없음'
                    ? `${d.last_successful_entry.time_text}<br><span class="text-emerald-400">${d.last_successful_entry.symbol}</span>`
                    : '아직 성공 기록 없음'}
            </div>
        </div>
    </div>`;

    html += '</div>';
    container.innerHTML = html;
}

// ── Tab 2: 차단 마법사 ──
async function _xrayRunBlockerWizard(container) {
    const resp = await fetch(`${API_URL}/xray/blocker_wizard`);
    const d = await resp.json();

    let html = '<div class="space-y-2">';

    d.steps.forEach((step, i) => {
        const isLast = d.stopped_at_step === step.step;
        const isPending = d.stopped_at_step !== null && step.step > d.stopped_at_step;
        let borderColor, bgColor, iconColor, icon;

        if (isPending) {
            borderColor = 'border-gray-700/30'; bgColor = 'bg-gray-800/20'; iconColor = 'text-gray-600'; icon = '○';
        } else if (step.pass) {
            borderColor = 'border-emerald-500/30'; bgColor = 'bg-emerald-500/5'; iconColor = 'text-emerald-400'; icon = '✓';
        } else {
            borderColor = 'border-red-500/30'; bgColor = 'bg-red-500/5'; iconColor = 'text-red-400'; icon = '✕';
        }

        html += `<div class="rounded-lg border ${borderColor} ${bgColor} p-3 ${isPending ? 'opacity-40' : ''}">
            <div class="flex items-center gap-3">
                <div class="flex-shrink-0 w-6 h-6 rounded-full border ${borderColor} flex items-center justify-center">
                    <span class="${iconColor} text-xs font-bold">${icon}</span>
                </div>
                <div class="flex-grow">
                    <div class="flex items-center gap-2">
                        <span class="text-[10px] font-mono font-bold ${isPending ? 'text-gray-600' : 'text-gray-300'}">Step ${step.step}. ${step.name}</span>
                    </div>
                    <div class="text-[9px] font-mono ${isPending ? 'text-gray-700' : 'text-gray-400'} mt-0.5">${step.detail}</div>
                </div>
            </div>
            ${(!step.pass && !isPending && step.fix) ? `<div class="mt-2 ml-9 text-[9px] font-mono text-yellow-400/80 border-l-2 border-yellow-500/30 pl-2">${step.fix}</div>` : ''}
        </div>`;
    });

    // 결론
    const vColor = d.all_clear ? 'emerald' : 'red';
    html += `<div class="rounded-lg border border-${vColor}-500/40 bg-${vColor}-500/10 p-4 mt-3">
        <div class="text-[11px] font-mono font-bold text-${vColor}-400">${d.all_clear ? '✓ 전체 정상' : `✕ Step ${d.stopped_at_step}에서 차단`}</div>
        <div class="text-[10px] font-mono text-gray-300 mt-1">${d.verdict}</div>
    </div>`;

    html += '</div>';
    container.innerHTML = html;
}

// ── Tab 3: 시도 이력 ──
async function _xrayLoadTradeAttempts(container) {
    const resp = await fetch(`${API_URL}/xray/trade_attempts`);
    const d = await resp.json();

    let html = '<div class="space-y-3">';

    // 요약
    html += `<div class="flex gap-3 text-[10px] font-mono">
        <span class="text-gray-400">총 ${d.summary.total}건</span>
        <span class="text-emerald-400">${d.summary.success} 성공</span>
        <span class="text-yellow-400">${d.summary.blocked} 차단</span>
        <span class="text-red-400">${d.summary.failed} 실패</span>
    </div>`;

    if (d.attempts.length === 0) {
        html += '<div class="text-center text-gray-500 font-mono text-xs py-8">매매 시도 기록이 없습니다. 봇 가동 후 기록됩니다.</div>';
    } else {
        html += '<div class="space-y-1.5">';
        d.attempts.forEach(a => {
            const colorMap = { emerald: 'emerald', yellow: 'yellow', red: 'red', gray: 'gray' };
            const c = colorMap[a.result_color] || 'gray';
            const sigColor = a.signal === 'LONG' ? 'text-emerald-400' : (a.signal === 'SHORT' ? 'text-red-400' : 'text-gray-500');
            const ts = a.timestamp ? new Date(a.timestamp).toLocaleTimeString('ko-KR') : '';

            html += `<div class="rounded border border-${c}-500/20 bg-${c}-500/5 px-3 py-2 flex items-center gap-3">
                <span class="text-[8px] font-mono text-gray-500 flex-shrink-0 w-16">${ts}</span>
                <span class="text-[9px] font-mono text-text-main flex-shrink-0 w-10">${a.symbol_short}</span>
                <span class="text-[9px] font-mono ${sigColor} flex-shrink-0 w-12">${a.signal}</span>
                <span class="text-[9px] font-mono font-bold text-${c}-400 flex-shrink-0 w-14">${a.result}</span>
                <span class="text-[8px] font-mono text-gray-400 truncate">${a.result_text}</span>
            </div>`;
        });
        html += '</div>';
    }

    html += '</div>';
    container.innerHTML = html;
}

// ── Tab 4: 7게이트 스코어보드 ──
async function _xrayLoadGateScoreboard(container) {
    const resp = await fetch(`${API_URL}/xray/gate_scoreboard`);
    const d = await resp.json();

    const gateKeys = ['adx', 'chop', 'volume', 'disparity', 'macd_rsi', 'macro'];

    let html = '<div class="space-y-3">';

    if (d.symbols.length === 0) {
        html += '<div class="text-center text-gray-500 font-mono text-xs py-8">분석된 심볼이 없습니다. 봇 가동 후 표시됩니다.</div>';
    } else {
        html += `<div class="overflow-x-auto"><table class="w-full text-[9px] font-mono">
            <thead><tr class="text-gray-500 border-b border-navy-border">
                <th class="text-left py-2 pr-2">심볼</th>`;
        gateKeys.forEach(gk => {
            const labelMap = { adx: 'ADX', chop: 'CHOP', volume: 'VOL', disparity: 'DISP', macd_rsi: 'MACD/RSI', macro: 'EMA200' };
            html += `<th class="text-center py-2 px-1">${labelMap[gk] || gk}</th>`;
        });
        html += `<th class="text-center py-2">결과</th></tr></thead><tbody>`;

        d.symbols.forEach(sym => {
            html += `<tr class="border-b border-navy-border/50">
                <td class="py-2 pr-2">
                    <div class="text-text-main font-bold">${sym.symbol_short}</div>
                    <div class="text-gray-600 text-[8px]">$${sym.price ? sym.price.toLocaleString() : 'N/A'}</div>
                </td>`;

            gateKeys.forEach(gk => {
                const gate = sym.gates[gk];
                if (gate) {
                    const gc = gate.pass ? 'emerald' : 'red';
                    html += `<td class="text-center py-2 px-1">
                        <div class="rounded px-1.5 py-0.5 bg-${gc}-500/10 border border-${gc}-500/20">
                            <span class="text-${gc}-400">${gate.pass ? '✓' : '✕'}</span>
                            <div class="text-[7px] text-gray-500 mt-0.5">${gate.value}</div>
                        </div>
                    </td>`;
                } else {
                    html += `<td class="text-center py-2 px-1 text-gray-600">—</td>`;
                }
            });

            const gpc = sym.gates_passed >= 6 ? 'emerald' : (sym.gates_passed >= 4 ? 'yellow' : 'red');
            html += `<td class="text-center py-2">
                <span class="text-${gpc}-400 font-bold">${sym.gates_passed}/${sym.gates_total}</span>
            </td></tr>`;

            // 결정 요약 행
            html += `<tr class="border-b border-navy-border/30">
                <td colspan="${gateKeys.length + 2}" class="py-1 px-2 text-[8px] text-gray-500 italic">${sym.decision}</td>
            </tr>`;
        });

        html += '</tbody></table></div>';
    }

    html += '</div>';
    container.innerHTML = html;
}

// ── Tab 5: OKX 딥검증 ──
async function _xrayLoadOkxDeepVerify(container) {
    const resp = await fetch(`${API_URL}/xray/okx_deep_verify`);
    const d = await resp.json();

    let html = '<div class="space-y-4">';

    // API 상태 카드
    const ac = d.api_status.connected ? 'emerald' : 'red';
    html += `<div class="rounded-lg border border-${ac}-500/30 bg-${ac}-500/5 p-4">
        <div class="flex items-center gap-2 mb-2">
            <span class="w-2.5 h-2.5 rounded-full bg-${ac}-400"></span>
            <span class="text-[11px] font-mono font-bold text-${ac}-400">OKX API ${d.api_status.connected ? '연결됨' : '연결 실패'}</span>
        </div>
        <div class="grid grid-cols-3 gap-3 text-[9px] font-mono text-gray-400">
            <div>잔고: <span class="text-text-main">${d.api_status.balance_text}</span></div>
            <div>응답속도: <span class="text-text-main">${d.api_status.latency_ms}ms</span></div>
            <div>전체: <span class="${d.overall_feasible ? 'text-emerald-400' : 'text-yellow-400'}">${d.overall_feasible ? '매매 가능' : '일부 제한'}</span></div>
        </div>
    </div>`;

    // 심볼별 테이블
    if (d.symbols.length > 0) {
        html += `<div class="rounded-lg border border-navy-border bg-navy-card/30 p-3">
            <div class="text-[10px] font-mono font-bold text-gray-300 mb-2">심볼별 매매 가능성</div>
            <table class="w-full text-[9px] font-mono">
                <thead><tr class="text-gray-500 border-b border-navy-border">
                    <th class="text-left py-1">심볼</th>
                    <th class="text-right py-1">현재가</th>
                    <th class="text-center py-1">레버리지</th>
                    <th class="text-right py-1">계약당증거금</th>
                    <th class="text-center py-1">최대계약</th>
                    <th class="text-center py-1">상태</th>
                </tr></thead><tbody>`;

        d.symbols.forEach(s => {
            const fc = s.feasible ? 'emerald' : 'red';
            html += `<tr class="border-b border-navy-border/50">
                <td class="py-1.5 text-text-main font-bold">${s.symbol_short}</td>
                <td class="text-right py-1.5 text-gray-400">$${s.current_price.toLocaleString()}</td>
                <td class="text-center py-1.5 text-gray-400">${s.leverage}x</td>
                <td class="text-right py-1.5 text-gray-400">$${s.margin_per_contract.toFixed(2)}</td>
                <td class="text-center py-1.5 text-text-main">${s.max_contracts}</td>
                <td class="text-center py-1.5">
                    <span class="text-${fc}-400 font-bold">${s.feasible ? '가능' : '불가'}</span>
                </td>
            </tr>`;
        });

        html += '</tbody></table></div>';
    } else {
        html += '<div class="text-center text-gray-500 font-mono text-xs py-8">등록된 심볼이 없습니다.</div>';
    }

    html += '</div>';
    container.innerHTML = html;
}

// ════════════════════════════════════════════════════════════════════════════
// [UI Overhaul] Tab System, FAB, Theme System
// ════════════════════════════════════════════════════════════════════════════

// --- Tab Switching ---
function switchMainTab(tabName) {
    ['trading', 'analytics', 'settings'].forEach(t => {
        const panel = document.getElementById(`tab-${t}`);
        if (panel) panel.classList.toggle('hidden', t !== tabName);
    });
    document.querySelectorAll('.main-tab-btn').forEach(btn => {
        btn.classList.toggle('tab-active', btn.dataset.tab === tabName);
    });
    // Analytics 탭 전환 시 데이터 새로고침
    if (tabName === 'analytics') {
        syncStats();
        fetchAndRenderHeatmap();
        syncAdvancedStats();
    }
}

// --- FAB (Floating Action Button) ---
function toggleFAB() {
    const menu = document.getElementById('fab-menu');
    const mainBtn = document.getElementById('fab-main-btn');
    if (!menu || !mainBtn) return;
    const isOpen = !menu.classList.contains('hidden');
    menu.classList.toggle('hidden', isOpen);
    mainBtn.style.transform = isOpen ? 'rotate(0deg)' : 'rotate(45deg)';
}

async function emergencyCloseAll() {
    const confirmed = confirm('🚨 긴급 전량 청산\n\n현재 보유 중인 모든 포지션을 즉시 시장가로 청산합니다.\n정말 실행하시겠습니까?');
    if (!confirmed) return;
    try {
        const response = await fetch(`${API_URL}/close-position`, { method: 'POST' });
        const result = await response.json();
        if (result.status === 'success' || result.message) {
            showToast('긴급 청산', '전량 청산 요청이 전송되었습니다.', 'SUCCESS');
        } else {
            showToast('청산 실패', result.error || '알 수 없는 오류', 'ERROR');
        }
    } catch (error) {
        showToast('청산 오류', error.message, 'ERROR');
    }
    toggleFAB(); // 메뉴 닫기
}

// --- Theme System ---
function getThemeColor(varName) {
    return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

function toggleTheme() {
    const html = document.documentElement;
    const isLight = html.getAttribute('data-theme') === 'light';
    if (isLight) {
        html.removeAttribute('data-theme');
        localStorage.setItem('ag-theme', 'dark');
    } else {
        html.setAttribute('data-theme', 'light');
        localStorage.setItem('ag-theme', 'light');
    }
    updateThemeIcon();
    updateChartTheme();
}

function initTheme() {
    const saved = localStorage.getItem('ag-theme');
    if (saved === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    }
    updateThemeIcon();
}

function updateThemeIcon() {
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    btn.innerHTML = isLight ? '🌙' : '☀️';
    btn.title = isLight ? 'Switch to Dark Mode' : 'Switch to Light Mode';
}

function updateChartTheme() {
    const textColor = getThemeColor('--text-secondary');
    const borderColor = getThemeColor('--border-primary');
    const gridColor = getThemeColor('--border-subtle') || 'rgba(48,54,61,0.5)';
    const greenColor = getThemeColor('--neon-green');
    const redColor = getThemeColor('--neon-red');

    const layoutOpts = {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: textColor },
        grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
        timeScale: { borderColor: borderColor },
        rightPriceScale: { borderColor: borderColor },
    };

    if (chart) {
        chart.applyOptions(layoutOpts);
        if (candleSeries) {
            candleSeries.applyOptions({
                upColor: greenColor, downColor: redColor,
                wickUpColor: greenColor, wickDownColor: redColor,
            });
        }
    }

    const subChartOpts = {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: textColor },
        grid: { vertLines: { visible: false }, horzLines: { color: gridColor } },
        timeScale: { borderColor: borderColor },
        rightPriceScale: { borderColor: borderColor },
    };

    if (rsiChart) rsiChart.applyOptions(subChartOpts);
    if (macdChart) macdChart.applyOptions(subChartOpts);
}

// --- Margin Guard: 원클릭 추천 레버리지 적용 ---
async function applyRecommendedLeverage() {
    const mg = window._marginGuardData;
    if (!mg) return;

    // 현재 감시 타겟 기준으로 적용 (타겟 전환 후 적용 시 해당 코인에만 반영)
    const _applyActiveSym = window._lastActiveMgSym || currentSymbol;
    const d = mg[_applyActiveSym];
    if (!d || !d.needs_change || !d.recommended_leverage) return;

    try {
        // 심볼 전용으로만 저장 (GLOBAL 저장 제거 — 타 심볼 레버리지 오염 방지)
        await fetch(`${API_URL}/config?key=leverage&value=${d.recommended_leverage}&symbol=${encodeURIComponent(_applyActiveSym)}`, { method: 'POST' });
        showToast('Margin Guard', `[${_applyActiveSym.split(':')[0]}] 레버리지 ${d.current_leverage}x → ${d.recommended_leverage}x 적용 완료`, 'SUCCESS');

        // UI 즉시 갱신
        const levInput = document.getElementById('config-leverage');
        if (levInput) { levInput.value = d.recommended_leverage; levInput.dispatchEvent(new Event('input')); }
        const leftLev = document.getElementById('left-panel-lev-badge');
        if (leftLev) leftLev.textContent = d.recommended_leverage + 'x';
        const cmdLev = document.getElementById('cmd-lev-badge');
        if (cmdLev) cmdLev.textContent = d.recommended_leverage + 'x';

        // grace period 설정 — syncBotStatus 재실행 시 배지 즉시 재표시 방지
        window._mgAppliedAt = Date.now();

        // 경고 배지 숨김
        const badge = document.getElementById('margin-guard-badge');
        if (badge) badge.classList.add('hidden');
        const cmdWarn = document.getElementById('cmd-margin-warn');
        if (cmdWarn) cmdWarn.classList.add('hidden');

        await syncConfig(_applyActiveSym);  // [Fix] 심볼 전용 leverage가 GLOBAL로 덮어쓰이는 버그 수정
    } catch (err) {
        showToast('Margin Guard 오류', err.message, 'ERROR');
    }
}
