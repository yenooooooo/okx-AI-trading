const API_URL = `/api/v1`;
let chart = null;

async function syncBotStatus() {
    try {
        const response = await fetch(`${API_URL}/status`);
        const data = await response.json();

        // 1. 잔고 업데이트 (강제 애니메이션 효과)
        const balanceEl = document.getElementById('current-balance');
        const krwEl = document.getElementById('balance-krw');

        const newBalanceHTML = `${data.balance} <span class="text-lg text-slate-400">USDT</span>`;
        if (balanceEl.innerHTML !== newBalanceHTML) {
            balanceEl.innerHTML = newBalanceHTML;
            // 값 변경 시 애니메이션 클래스 토글 (깜빡임 효과)
            balanceEl.classList.remove('text-white');
            balanceEl.classList.add('text-green-300');
            setTimeout(() => {
                balanceEl.classList.remove('text-green-300');
                balanceEl.classList.add('text-white');
            }, 300);

            krwEl.innerText = `≈ ${(data.balance * 1350).toLocaleString()} 원`;
        }

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

            // 실시간 수익률 (색상 동적 변경 및 강제 갱신)
            const roiEl = document.getElementById('pos-roi');
            const roiValue = parseFloat(symbolData.unrealized_pnl_percent || 0).toFixed(2);
            const newRoiText = roiValue > 0 ? `+${roiValue}%` : `${roiValue}%`;

            if (roiEl.innerText !== newRoiText) {
                roiEl.innerText = newRoiText;
                roiEl.className = roiValue > 0 ? 'text-3xl font-black text-green-400 transition-colors' : (roiValue < 0 ? 'text-3xl font-black text-red-400 transition-colors' : 'text-3xl font-black text-gray-400 transition-colors');
                // 폰트 크기를 잠깐 키웠다 줄이는 애니메이션 효과
                roiEl.style.transform = 'scale(1.1)';
                setTimeout(() => {
                    roiEl.style.transform = 'scale(1.0)';
                }, 200);
            }

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

        // 에러 객체가 반환된 경우 처리
        if (ohlcv.error) {
            console.error("차트 데이터 에러(서버):", ohlcv.error);
            return;
        }

        // 배열이 아니거나 비어있는 경우 처리
        if (!Array.isArray(ohlcv) || ohlcv.length === 0) {
            console.warn("차트 데이터가 비어 있습니다 (0건 수신)");
            return;
        }

        const candleSeries = chart.series()[0];
        const data = ohlcv.map(candle => ({
            time: Math.floor(candle.timestamp / 1000),
            open: parseFloat(candle.open),
            high: parseFloat(candle.high),
            low: parseFloat(candle.low),
            close: parseFloat(candle.close),
        }));

        candleSeries.setData(data);
        chart.timeScale().fitContent();
        console.log(`[차트 업데이트] ${data.length}개 캔들 동기화 성공`);
    } catch (error) {
        console.error("차트 통신(fetch) / 동기화 실패:", error);
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

let lastLogTimestamp = '';

async function updateLogs() {
    try {
        const response = await fetch(`${API_URL}/logs?limit=50`);
        const logs = await response.json();

        const logContainer = document.getElementById('system-log-terminal');
        let newLogsAdded = false;

        logs.forEach(log => {
            // 시간순 정렬된 상태에서 최신 타임스탬프보다 이전의 로그는 필터링
            if (!lastLogTimestamp || log.created_at > lastLogTimestamp) {
                const logDiv = document.createElement('div');

                // 로그 레벨 및 내용에 따른 색상 처리
                const msg = log.message || '';
                if (log.level === 'ERROR' || msg.includes('[오류]') || msg.includes('[긴급]')) {
                    logDiv.className = 'text-red-400';
                } else if (msg.includes('[봇]') || msg.includes('[진입 성공]') || msg.includes('청산')) {
                    logDiv.className = 'text-green-400';
                } else {
                    logDiv.className = 'text-gray-300';
                }

                // 타임스탬프 포맷팅 가시성 향상
                const timeStr = log.created_at ? `[${log.created_at.replace('T', ' ').substring(0, 19)}]` : '';
                logDiv.innerText = `${timeStr} ${msg}`;

                logContainer.appendChild(logDiv);
                lastLogTimestamp = log.created_at; // 마지막 타임스탬프 갱신
                newLogsAdded = true;
            }
        });

        // 새 로그가 추가되었으면 자동 스크롤
        if (newLogsAdded && logContainer) {
            logContainer.scrollTop = logContainer.scrollHeight;
        }

    } catch (error) {
        console.warn("시스템 로그 동기화 실패:", error);
    }
}

function clearLogs() {
    document.getElementById('log-container').innerHTML = '';
    const sysLogContainer = document.getElementById('system-log-terminal');
    if (sysLogContainer) {
        sysLogContainer.innerHTML = '<div class="text-gray-500">System Logs Cleared.</div>';
    }
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

// 3초마다 시스템 로그 동기화
setInterval(updateLogs, 3000);
