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
