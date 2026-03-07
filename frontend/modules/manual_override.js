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
