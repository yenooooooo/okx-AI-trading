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

