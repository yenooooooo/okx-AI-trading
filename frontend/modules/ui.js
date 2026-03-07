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
