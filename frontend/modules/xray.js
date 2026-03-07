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
