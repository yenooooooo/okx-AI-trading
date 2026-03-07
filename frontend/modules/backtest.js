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
            // 뇌구조 모달 즉시 동기화 + 배지 갱신 (커스텀 전환)
            await syncConfig(currentSymbol);
        } else {
            alert(`❌ 적용 실패: ${data.message}`);
        }
    } catch (e) {
        console.error("Apply error:", e);
        alert(`오류: ${e.message}`);
    }
}


