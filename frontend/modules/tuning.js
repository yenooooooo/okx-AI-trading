let _section1Collapsed = false;
function toggleSection1() {
    const content = document.getElementById('section1-content');
    const arrow = document.getElementById('section1-arrow');
    const hint = document.getElementById('section1-collapsed-hint');
    if (!content || !arrow) return;

    _section1Collapsed = !_section1Collapsed;
    if (_section1Collapsed) {
        content.style.maxHeight = '0px';
        content.style.opacity = '0';
        arrow.style.transform = 'rotate(0deg)';
        if (hint) hint.classList.remove('hidden');
    } else {
        content.style.maxHeight = '600px';
        content.style.opacity = '1';
        arrow.style.transform = 'rotate(90deg)';
        if (hint) hint.classList.add('hidden');
    }
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
