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

