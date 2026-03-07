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

