const API_URL = `/api/v1`;
let chart = null;

async function syncBotStatus() {
    try {
        const response = await fetch(`${API_URL}/status`);
        const data = await response.json();

        // 1. 잔고 업데이트
        document.getElementById('current-balance').innerHTML = `${data.balance} <span class="text-lg text-slate-400">USDT</span>`;
        document.getElementById('balance-krw').innerText = `≈ ${(data.balance * 1350).toLocaleString()} 원`;

        // 2. 포지션 업데이트 (첫 번째 심볼 기준, 다중 심볼은 별도 패널에서 표시)
        const symbols = data.symbols || {};
        let firstSymbol = Object.keys(symbols)[0];
        let symbolData = firstSymbol ? symbols[firstSymbol] : null;

        if (symbolData && symbolData.position && symbolData.position !== "NONE") {
            document.getElementById('position-none').classList.add('hidden');
            document.getElementById('position-active').classList.remove('hidden');

            const typeEl = document.getElementById('pos-type');
            typeEl.innerText = symbolData.position;
            typeEl.className = symbolData.position === 'LONG' ? 'text-3xl font-bold text-green-500' : 'text-3xl font-bold text-red-500';

            document.getElementById('pos-entry').innerText = `$${parseFloat(symbolData.entry_price).toLocaleString()}`;
            if (symbolData.current_price) document.getElementById('pos-current').innerText = `$${parseFloat(symbolData.current_price).toLocaleString()}`;

            const roiEl = document.getElementById('pos-roi');
            const roiValue = parseFloat(symbolData.unrealized_pnl_percent || 0).toFixed(2);
            roiEl.innerText = roiValue > 0 ? `+${roiValue}%` : `${roiValue}%`;
            roiEl.className = roiValue > 0 ? 'text-3xl font-black text-green-400' : (roiValue < 0 ? 'text-3xl font-black text-red-400' : 'text-3xl font-black text-gray-400');

            if (symbolData.take_profit_price) document.getElementById('pos-tp').innerText = `$${parseFloat(symbolData.take_profit_price).toLocaleString()}`;
            if (symbolData.stop_loss_price) document.getElementById('pos-sl').innerText = `$${parseFloat(symbolData.stop_loss_price).toLocaleString()}`;
        } else {
            document.getElementById('position-none').classList.remove('hidden');
            document.getElementById('position-active').classList.add('hidden');
        }

        // 3. 로그 업데이트
        const logContainer = document.getElementById('log-container');
        if (data.logs.length > logContainer.childElementCount) {
            logContainer.innerHTML = '';
            data.logs.slice(-20).forEach(logMsg => {
                const logDiv = document.createElement('div');
                logDiv.className = logMsg.includes('[오류]') || logMsg.includes('[긴급]') ? 'text-red-400' :
                    logMsg.includes('[봇]') ? 'text-green-400' : 'text-slate-300';
                logDiv.innerText = logMsg;
                logContainer.appendChild(logDiv);
            });
            logContainer.scrollTop = logContainer.scrollHeight;
        }

    } catch (error) {
        console.log("서버 오프라인 대기 중...");
    }

    await updateBrain();
}

async function toggleBot() {
    try {
        const response = await fetch(`${API_URL}/toggle`, { method: 'POST' });
        const data = await response.json();

        const btn = document.getElementById('toggle-bot-btn');
        const statusText = document.getElementById('bot-status-text');
        const ping = document.getElementById('status-ping');
        const dot = document.getElementById('status-dot');

        if (data.is_running) {
            btn.innerText = 'Stop Bot';
            btn.className = 'px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded shadow transition font-semibold';
            statusText.innerText = 'Running';
            statusText.className = 'text-green-400 font-semibold';
            ping.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75';
            dot.className = 'relative inline-flex rounded-full h-3 w-3 bg-green-500';
        } else {
            btn.innerText = 'Start Bot';
            btn.className = 'px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded shadow transition font-semibold';
            statusText.innerText = 'Offline';
            statusText.className = 'text-slate-300 font-semibold';
            ping.className = 'hidden';
            dot.className = 'relative inline-flex rounded-full h-3 w-3 bg-red-500';
        }
    } catch (error) {
        alert("백엔드 서버가 켜져 있는지 확인해주세요.");
    }
}

// === 신규 함수들 ===

async function syncStats() {
    try {
        const response = await fetch(`${API_URL}/stats`);
        const stats = await response.json();

        document.getElementById('stats-total-trades').innerText = stats.total_trades || 0;
        document.getElementById('stats-win-rate').innerText = (stats.win_rate || 0).toFixed(2) + '%';
        document.getElementById('stats-total-pnl').innerText = (stats.total_pnl_percent || 0).toFixed(2) + '%';
        document.getElementById('stats-max-dd').innerText = (stats.max_drawdown || 0).toFixed(2) + '%';
        document.getElementById('stats-sharpe').innerText = (stats.sharpe_ratio || 0).toFixed(2);
    } catch (error) {
        console.warn("성과 분석 동기화 실패:", error);
    }
}

async function syncConfig() {
    try {
        const response = await fetch(`${API_URL}/config`);
        const config = await response.json();

        // 설정값 UI에 반영
        const riskRate = config.risk_per_trade || 0.01;
        document.getElementById('config-risk-rate').value = (parseFloat(riskRate) * 100).toFixed(1);
        document.getElementById('config-leverage').value = config.leverage || 1;

        const symbols = config.symbols || ['BTC/USDT:USDT'];
        const symbolsStr = Array.isArray(symbols) ? symbols.join(', ') : symbols;
        document.getElementById('config-symbols').value = symbolsStr;
    } catch (error) {
        console.warn("설정 로드 실패:", error);
    }
}

async function updateConfigValue(key) {
    try {
        let value;
        if (key === 'risk_per_trade') {
            value = parseFloat(document.getElementById('config-risk-rate').value) / 100;
        } else if (key === 'leverage') {
            value = parseInt(document.getElementById('config-leverage').value);
        }

        const response = await fetch(`${API_URL}/config?key=${key}&value=${value}`, {
            method: 'POST'
        });
        const result = await response.json();
        alert(result.message || '설정이 저장되었습니다.');
    } catch (error) {
        alert('설정 변경 실패: ' + error.message);
    }
}

async function updateConfigSymbols() {
    try {
        const symbolsText = document.getElementById('config-symbols').value;
        const symbols = symbolsText.split(',').map(s => s.trim()).filter(s => s);
        const symbolsJson = JSON.stringify(symbols);

        const response = await fetch(`${API_URL}/config?key=symbols&value=${encodeURIComponent(symbolsJson)}`, {
            method: 'POST'
        });
        const result = await response.json();
        alert(result.message || '심볼이 저장되었습니다.');
    } catch (error) {
        alert('심볼 변경 실패: ' + error.message);
    }
}

// Lightweight Charts 초기화
function initChart() {
    const container = document.getElementById('chart-container');
    if (!container || chart) return;

    chart = LightweightCharts.createChart(container, {
        layout: {
            backgroundColor: '#1e293b',
            textColor: '#cbd5e1',
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
        },
    });

    const candleSeries = chart.addCandlestickSeries();
    chart.timeScale().fitContent();
}

async function syncChart() {
    try {
        if (!chart) initChart();

        const response = await fetch(`${API_URL}/ohlcv?symbol=BTC/USDT:USDT&limit=50`);
        const ohlcv = await response.json();

        const candleSeries = chart.series()[0];
        const data = ohlcv.map(candle => ({
            time: Math.floor(candle.timestamp / 1000),
            open: candle.open,
            high: candle.high,
            low: candle.low,
            close: candle.close,
        }));

        candleSeries.setData(data);
        chart.timeScale().fitContent();
    } catch (error) {
        console.warn("차트 동기화 실패:", error);
    }
}

async function syncMultiSymbols() {
    try {
        const response = await fetch(`${API_URL}/status`);
        const data = await response.json();
        const container = document.getElementById('multi-symbol-container');

        const symbols = data.symbols || {};
        if (Object.keys(symbols).length === 0) {
            container.innerHTML = '<p class="text-gray-400 text-sm">활성 심볼 없음</p>';
            return;
        }

        container.innerHTML = '';
        for (const [symbol, symbolData] of Object.entries(symbols)) {
            const card = document.createElement('div');
            card.className = 'bg-gray-900 p-4 rounded-xl border border-gray-700';

            const positionType = symbolData.position === 'NONE' ? '대기' : symbolData.position;
            const posColor = symbolData.position === 'LONG' ? 'text-green-400' : (symbolData.position === 'SHORT' ? 'text-red-400' : 'text-gray-400');

            card.innerHTML = `
                <div class="flex justify-between items-start mb-2">
                    <div>
                        <p class="text-sm font-bold text-cyan-400">${symbol}</p>
                        <p class="text-xs text-gray-400">현재: $${(symbolData.current_price || 0).toLocaleString()}</p>
                    </div>
                    <p class="text-lg font-bold ${posColor}">${positionType}</p>
                </div>
                <div class="text-xs text-gray-400 space-y-1">
                    ${symbolData.position !== 'NONE' ? `
                        <p>진입: $${(symbolData.entry_price || 0).toLocaleString()}</p>
                        <p class="font-bold text-${symbolData.unrealized_pnl_percent > 0 ? 'green' : 'red'}-400">
                            ${symbolData.unrealized_pnl_percent > 0 ? '+' : ''}${(symbolData.unrealized_pnl_percent || 0).toFixed(2)}%
                        </p>
                    ` : '<p>포지션 없음</p>'}
                </div>
            `;
            container.appendChild(card);
        }
    } catch (error) {
        console.warn("다중 심볼 동기화 실패:", error);
    }
}

async function runBacktest() {
    try {
        const symbol = document.getElementById('backtest-symbol').value;
        const timeframe = document.getElementById('backtest-timeframe').value;
        const limit = parseInt(document.getElementById('backtest-limit').value);

        const response = await fetch(`${API_URL}/backtest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, timeframe, limit })
        });

        const result = await response.json();

        if (result.error) {
            alert('백테스팅 실패: ' + result.error);
            return;
        }

        // 결과 표시
        const resultsDiv = document.getElementById('backtest-results');
        resultsDiv.classList.remove('hidden');

        document.getElementById('backtest-total-trades').innerText = result.total_trades || 0;
        document.getElementById('backtest-win-rate').innerText = (result.win_rate || 0).toFixed(2) + '%';
        document.getElementById('backtest-pnl').innerText = (result.total_pnl_percent || 0).toFixed(2) + '%';

        alert(`백테스팅 완료!\n거래: ${result.total_trades}건\n승률: ${result.win_rate.toFixed(2)}%\n수익: ${result.total_pnl_percent.toFixed(2)}%`);
    } catch (error) {
        alert('백테스팅 실행 중 오류: ' + error.message);
    }
}

async function updateBrain() {
    try {
        const brainRes = await fetch(`/api/v1/brain`);
        if (brainRes.ok) {
            const brainData = await brainRes.json();

            // 다중 심볼 뇌 상태 처리
            const symbolBrains = brainData.symbols || {};
            const firstSymbol = Object.keys(symbolBrains)[0];
            const brainState = firstSymbol ? symbolBrains[firstSymbol] : brainData;

            if (brainState.price) document.getElementById('brain-price').innerText = `$${parseFloat(brainState.price).toLocaleString()}`;
            if (brainState.decision) document.getElementById('brain-decision').innerText = `🤖 ${brainState.decision}`;

            if (brainState.rsi) {
                const rsiEl = document.getElementById('brain-rsi');
                const rsiVal = parseFloat(brainState.rsi);
                rsiEl.innerText = rsiVal.toFixed(2);
                rsiEl.className = rsiVal <= 40 ? 'text-xl font-mono text-green-400' : (rsiVal >= 60 ? 'text-xl font-mono text-red-400' : 'text-xl font-mono text-purple-400');
            }

            if (brainState.macd !== undefined) {
                const macdEl = document.getElementById('brain-macd');
                const macdVal = parseFloat(brainState.macd).toFixed(1);
                macdEl.innerText = macdVal;
                macdEl.className = parseFloat(brainState.macd) > 0 ? 'text-xl font-mono text-green-400' : 'text-xl font-mono text-red-400';
            }

            if (brainState.bb_upper && brainState.bb_lower) {
                document.getElementById('brain-bb').innerHTML = `상: $${parseFloat(brainState.bb_upper).toLocaleString()}<br>하: $${parseFloat(brainState.bb_lower).toLocaleString()}`;
            }
        }
    } catch (error) {
        console.warn("뇌 구조 데이터 동기화 실패:", error);
    }
}

function clearLogs() {
    document.getElementById('log-container').innerHTML = '';
}

// === 초기화 및 setInterval 설정 ===

// 초기 설정 로드
syncConfig();
initChart();

// 1초마다 봇 상태 동기화
setInterval(syncBotStatus, 1000);

// 10초마다 차트 동기화
setInterval(syncChart, 10000);

// 30초마다 성과 분석 및 다중 심볼 업데이트
setInterval(syncStats, 30000);
setInterval(syncMultiSymbols, 30000);
