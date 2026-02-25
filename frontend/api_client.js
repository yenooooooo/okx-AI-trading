const API_URL = `/api/v1`;
let chart = null;
let candleSeries = null;
let lastLogTimestamp = '';

// --- UI Utilities ---

// CountUp animation utility
function updateNumberText(elementId, newValue, formatCb) {
    const el = document.getElementById(elementId);
    if (!el) return;

    const oldVal = parseFloat(el.dataset.val || 0);
    const newVal = parseFloat(newValue || 0);

    if (oldVal === newVal) {
        if (!el.textContent) el.textContent = formatCb ? formatCb(newVal) : newVal.toFixed(2);
        return;
    }

    // Flash effect
    const parent = el.closest('.flash-target') || el;
    parent.classList.remove('flash');
    void parent.offsetWidth; // trigger reflow
    parent.classList.add('flash');

    // Count up animation (0.2s)
    const duration = 200;
    let startTimestamp = null;
    const step = (timestamp) => {
        if (!startTimestamp) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);
        const current = oldVal + progress * (newVal - oldVal);
        el.textContent = formatCb ? formatCb(current) : current.toFixed(2);
        if (progress < 1) {
            window.requestAnimationFrame(step);
        } else {
            el.textContent = formatCb ? formatCb(newVal) : newVal.toFixed(2);
            el.dataset.val = newVal;
        }
    };
    window.requestAnimationFrame(step);
}

// Simple text update with flash
function updateText(elementId, text, flash = true) {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (el.textContent === text) return;

    el.textContent = text;
    if (flash) {
        const parent = el.closest('.flash-target') || el;
        parent.classList.remove('flash');
        void parent.offsetWidth;
        parent.classList.add('flash');
    }
}

// Tick Flash (Green/Red) for Websocket Ultra-low latency
function updatePriceWithTickFlash(price) {
    const el1 = document.getElementById('current-balance');
    const el2 = document.getElementById('hero-price');
    const oldPrice = parseFloat(el2 ? el2.dataset.val : 0) || price;

    const formattedPrice = price.toFixed(2);
    let flashClass = '';

    if (price > oldPrice) {
        flashClass = 'tick-flash-green';
    } else if (price < oldPrice) {
        flashClass = 'tick-flash-red';
    }

    [el2].forEach(el => {
        if (!el) return;
        el.textContent = formattedPrice;
        el.dataset.val = price;
        if (flashClass) {
            el.classList.remove('tick-flash-green', 'tick-flash-red');
            void el.offsetWidth; // trigger reflow
            el.classList.add(flashClass);

            // cleanup after animation
            setTimeout(() => {
                el.classList.remove(flashClass);
            }, 200);
        }
    });
}

// --- Status Sync ---

async function syncBotStatus() {
    try {
        const response = await fetch(`${API_URL}/status`);
        const data = await response.json();

        // 1. Balance (REST API 데이터는 웹소켓 상태와 무관하게 항상 동기화)
        // 웹소켓은 더 이상 잔고를 건드리지 않으므로, 여기서 무조건 업데이트해야 함.
        updateNumberText('current-balance', data.balance);
        updateNumberText('balance-krw', data.balance * 1350, val => `≈ ${Math.floor(val).toLocaleString()} KRW`);

        // 2. Position
        const symbols = data.symbols || {};
        const firstSymbol = Object.keys(symbols)[0];
        const symbolData = firstSymbol ? symbols[firstSymbol] : null;

        const posCard = document.getElementById('active-position-card');
        const posNone = document.getElementById('position-none');
        const posActive = document.getElementById('position-active');

        if (!symbolData || symbolData.position === 'NONE') {
            posNone.classList.remove('hidden');
            posActive.classList.add('hidden');
            posActive.classList.remove('flex');
            posCard.className = "glass-panel p-5 transition-all duration-500 border-navy-border flex-grow max-h-[250px] flex flex-col justify-center relative overflow-hidden";
        } else {
            posNone.classList.add('hidden');
            posActive.classList.remove('hidden');
            posActive.classList.add('flex');

            // 웹소켓(priceWs)이 연결되어 있을 때는 REST API 구형 가격/수익률 데이터 표시는 무시.
            // (단, 포지션 유무, 진입가, 목표가 등 고정데이터는 계속 연동)
            updateText('pos-type', symbolData.position);
            updateNumberText('pos-entry', symbolData.entry_price);
            updateNumberText('pos-tp', symbolData.take_profit_price);
            updateNumberText('pos-sl', symbolData.stop_loss_price);

            // 레버리지값을 pos-entry의 data-leverage 속성에 심어서 웹소켓 ROE 계산 시 사용
            const entryDomEl = document.getElementById('pos-entry');
            if (entryDomEl) {
                entryDomEl.dataset.leverage = symbolData.leverage || 1;
            }

            // 웹소켓이 아직 연결되지 않았을 때만 Fallback으로 렌더링
            if (!priceWs || priceWs.readyState !== WebSocket.OPEN) {
                const pnl = parseFloat(symbolData.unrealized_pnl_percent || 0);
                updateNumberText('pos-roi', pnl, val => (val > 0 ? `+${val.toFixed(2)}%` : `${val.toFixed(2)}%`));

                const roiEl = document.getElementById('pos-roi');
                if (pnl > 0) {
                    roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-neon-green';
                    posCard.className = "glass-panel p-5 transition-all duration-500 flex-grow max-h-[250px] flex flex-col justify-center relative overflow-hidden glow-green";
                } else if (pnl < 0) {
                    roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-neon-red';
                    posCard.className = "glass-panel p-5 transition-all duration-500 flex-grow max-h-[250px] flex flex-col justify-center relative overflow-hidden glow-red";
                } else {
                    roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-gray-400';
                    posCard.className = "glass-panel p-5 transition-all duration-500 border-navy-border flex-grow max-h-[250px] flex flex-col justify-center relative overflow-hidden";
                }
                updateNumberText('pos-current', symbolData.current_price);
            }
        }

        // 3. Status Info
        const statusDot = document.getElementById('status-dot');
        const statusPing = document.getElementById('status-ping');
        const statusText = document.getElementById('bot-status-text');
        const toggleBtn = document.getElementById('toggle-bot-btn');

        if (data.is_running) {
            statusDot.className = 'relative inline-flex rounded-full h-3 w-3 bg-neon-green';
            statusPing.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-green opacity-75';
            statusText.textContent = '🟢 시스템 가동 중';
            statusText.className = 'font-mono text-sm tracking-widest text-neon-green uppercase';
            toggleBtn.textContent = '🛑 시스템 중지';
            toggleBtn.className = 'px-6 py-2 bg-navy-800 border border-neon-red hover:bg-neon-red hover:text-white text-neon-red text-sm font-bold rounded transition-all font-mono tracking-widest';
        } else {
            statusDot.className = 'relative inline-flex rounded-full h-3 w-3 bg-neon-red';
            statusPing.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-red opacity-75';
            statusText.textContent = '🛑 시스템 중지';
            statusText.className = 'font-mono text-sm tracking-widest text-gray-400 uppercase';
            toggleBtn.textContent = '🟢 시스템 가동';
            toggleBtn.className = 'px-6 py-2 bg-navy-800 border border-neon-green hover:bg-neon-green hover:text-navy-900 text-neon-green text-sm font-bold rounded transition-all font-mono tracking-widest';
        }

        // 4. Brain (Indicators)
        const brainRes = await fetch(`${API_URL}/brain`);
        const brainData = await brainRes.json();

        const symbolBrains = brainData.symbols || {};
        const brainState = firstSymbol ? symbolBrains[firstSymbol] : brainData;

        if (brainState) {
            if (brainState.price) {
                updateNumberText('hero-price', brainState.price);
            }
            if (brainState.decision) {
                updateText('brain-decision', brainState.decision, false);
            }
            if (brainState.rsi) {
                const rsi = parseFloat(brainState.rsi);
                updateNumberText('brain-rsi', rsi);
                const rsiEl = document.getElementById('brain-rsi');
                rsiEl.className = rsi <= 30 ? 'font-mono flash-target font-bold text-neon-green' : (rsi >= 70 ? 'font-mono flash-target font-bold text-neon-red' : 'font-mono flash-target font-bold text-text-main');

                // Update marker
                const marker = document.getElementById('rsi-marker');
                if (marker) {
                    marker.style.left = `${Math.max(0, Math.min(100, rsi))}%`;
                }
            }
            if (brainState.macd !== undefined) {
                const macd = parseFloat(brainState.macd);
                updateNumberText('brain-macd', macd);
                const macdEl = document.getElementById('brain-macd');
                macdEl.className = macd > 0 ? 'font-mono flash-target font-bold text-neon-green' : 'font-mono flash-target font-bold text-neon-red';

                // Update MACD Gauges
                const posBar = document.getElementById('macd-bar-pos');
                const negBar = document.getElementById('macd-bar-neg');
                if (posBar && negBar) {
                    if (macd > 0) {
                        negBar.style.width = '0%';
                        posBar.style.width = `${Math.min(100, macd * 2)}%`; // scale factor
                    } else {
                        posBar.style.width = '0%';
                        negBar.style.width = `${Math.min(100, Math.abs(macd) * 2)}%`;
                    }
                }
            }
        }
    } catch (error) {
        console.warn("Status Sync Failed:", error);
    }
}

async function toggleBot() {
    try {
        const response = await fetch(`${API_URL}/toggle`, { method: 'POST' });
        const result = await response.json();
        syncBotStatus();
    } catch (error) {
        alert('Toggle target failed: ' + error.message);
    }
}

// --- Config Sync ---
async function syncConfig() {
    try {
        const response = await fetch(`${API_URL}/config`);
        const configs = await response.json();
        for (const [key, val] of Object.entries(configs)) {
            if (key === 'risk_per_trade') {
                const input = document.getElementById('config-risk-rate');
                const v = parseFloat(val) * 100;
                if (input) input.value = v;
                updateText('risk-val-display', v.toFixed(1) + '%', false);
            } else if (key === 'leverage') {
                const input = document.getElementById('config-leverage');
                if (input) input.value = val;
                updateText('lev-val-display', val + 'x', false);
            } else if (key === 'symbols') {
                const input = document.getElementById('config-symbols');
                if (input && Array.isArray(val)) input.value = val.join(', ');
            }
        }
    } catch (error) {
        console.warn("Config sync failed:", error);
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
        const response = await fetch(`${API_URL}/config?key=${encodeURIComponent(key)}&value=${encodeURIComponent(value)}`, { method: 'POST' });
        await response.json();
    } catch (error) {
        alert('Config update failed: ' + error.message);
    }
}

async function updateConfigSymbols() {
    try {
        const symbolsText = document.getElementById('config-symbols').value;
        const symbols = symbolsText.split(',').map(s => s.trim()).filter(s => s);
        const symbolsJson = JSON.stringify(symbols);
        const response = await fetch(`${API_URL}/config?key=symbols&value=${encodeURIComponent(symbolsJson)}`, { method: 'POST' });
        await response.json();
    } catch (error) {
        alert('Symbol update failed: ' + error.message);
    }
}

// --- Chart Sync ---
function initChart() {
    const container = document.getElementById('chart-container');
    if (!container || chart) return;

    chart = LightweightCharts.createChart(container, {
        autoSize: true,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#8b949e',
        },
        grid: {
            vertLines: { color: 'rgba(48, 54, 61, 0.5)' },
            horzLines: { color: 'rgba(48, 54, 61, 0.5)' },
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
            borderColor: '#30363d'
        },
        rightPriceScale: {
            borderColor: '#30363d'
        }
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00ff88',
        downColor: '#ff4d4d',
        borderVisible: false,
        wickUpColor: '#00ff88',
        wickDownColor: '#ff4d4d'
    });

    // Add dummy overlay for moving averages (visuals requested)
    const smaSeries = chart.addLineSeries({ color: 'rgba(88, 166, 255, 0.5)', lineWidth: 1 });
}

async function syncChart() {
    try {
        if (!chart) initChart();

        const response = await fetch(`${API_URL}/ohlcv?symbol=${encodeURIComponent('BTC/USDT:USDT')}&limit=60`);
        const ohlcv = await response.json();

        const overlay = document.getElementById('chart-overlay');

        if (ohlcv.error || !Array.isArray(ohlcv) || ohlcv.length === 0) {
            if (overlay) overlay.classList.remove('hidden');
            return;
        }

        if (overlay) overlay.classList.add('hidden');

        if (!candleSeries) return;

        const data = ohlcv.map(candle => ({
            time: Math.floor(candle.timestamp / 1000),
            open: parseFloat(candle.open),
            high: parseFloat(candle.high),
            low: parseFloat(candle.low),
            close: parseFloat(candle.close),
        }));

        candleSeries.setData(data);
    } catch (error) {
        const overlay = document.getElementById('chart-overlay');
        if (overlay) overlay.classList.remove('hidden');
        console.error("Chart Sync Failed:", error);
    }
}

// --- Terminal Logs ---
async function updateLogs() {
    try {
        const response = await fetch(`${API_URL}/logs?limit=50`);
        const logs = await response.json();
        const logContainer = document.getElementById('system-log-terminal');
        if (!logContainer) return;

        let newLogsAdded = false;

        logs.forEach(log => {
            if (!lastLogTimestamp || log.created_at > lastLogTimestamp) {
                const logDiv = document.createElement('div');
                const msg = log.message || '';

                let colorClass = 'text-gray-400';
                if (log.level === 'ERROR' || msg.includes('[오류]') || msg.includes('[긴급]')) {
                    colorClass = 'text-neon-red drop-shadow-[0_0_5px_rgba(255,77,77,0.8)]';
                } else if (msg.includes('[봇]') || msg.includes('[진입 성공]') || msg.includes('청산') || msg.includes('[엔진]')) {
                    colorClass = 'text-neon-green drop-shadow-[0_0_5px_rgba(0,255,136,0.8)]';
                }

                const timeStr = log.created_at ? log.created_at.replace('T', ' ').substring(11, 19) : '';
                logDiv.className = colorClass + ' break-words';
                logDiv.innerHTML = `<span class="text-gray-600 mr-2">[${timeStr}]</span><span class="text-gray-500 mr-2">[system@antigravity ~]$</span>${msg}`;

                logContainer.appendChild(logDiv);
                lastLogTimestamp = log.created_at;
                newLogsAdded = true;
            }
        });

        if (newLogsAdded) {
            logContainer.scrollTop = logContainer.scrollHeight;
        }
    } catch (error) {
        console.warn("Log Sync Failed:", error);
    }
}

function clearLogs() {
    const logContainer = document.getElementById('system-log-terminal');
    if (logContainer) {
        logContainer.innerHTML = '<div class="text-gray-500">[system@antigravity ~]$ Buffer cleared.</div>';
    }
}

// --- Stats Tracker ---
async function syncStats() {
    try {
        const response = await fetch(`${API_URL}/stats`);
        const stats = await response.json();

        updateNumberText('stats-total-trades', stats.total_trades || 0, val => Math.floor(val));
        updateNumberText('stats-win-rate', stats.win_rate || 0, val => `${val.toFixed(2)}%`);
        updateNumberText('stats-total-pnl', stats.total_pnl_percent || 0, val => `${val.toFixed(2)}%`);
        updateNumberText('stats-max-dd', stats.max_drawdown || 0, val => `${val.toFixed(2)}%`);
    } catch (e) { }
}

// --- Test Order Function ---
async function testOrder() {
    try {
        const response = await fetch(`${API_URL}/test_order`, { method: 'POST' });
        const result = await response.json();
        if (result.error) throw new Error(result.error);
        alert('테스트 매수 주문이 브레인으로 전송되었습니다. 터미널 로그를 확인하십시오.');
        updateLogs(); // fetch logs immediately
        syncBotStatus(); // fetch pos immediately
    } catch (error) {
        alert('테스트 진입 실패: ' + error.message);
    }
}

// --- WebSocket (0.1s Ultra-low Latency) ---
let priceWs = null;

function initPriceWebSocket() {
    // OKX Public Demo Trading WebSocket URL
    const wsUrl = "wss://wspap.okx.com:8443/ws/v5/public";

    if (priceWs) {
        priceWs.close();
    }

    priceWs = new WebSocket(wsUrl);

    priceWs.onopen = async () => {
        // Fetch current symbol to subscribe
        const response = await fetch(`${API_URL}/config`);
        const config = await response.json();
        const symbolRaw = Array.isArray(config.symbols) ? config.symbols[0] : 'BTC/USDT:USDT';

        // Convert symbol format. Ex: "BTC/USDT:USDT" -> "BTC-USDT-SWAP"
        let okxSymbol = symbolRaw.split(':')[0].replace('/', '-');
        if (symbolRaw.includes('USDT')) okxSymbol += '-SWAP';

        const subscribeMsg = {
            op: "subscribe",
            args: [{
                channel: "tickers",
                instId: okxSymbol
            }]
        };
        if (priceWs.readyState === WebSocket.OPEN) {
            priceWs.send(JSON.stringify(subscribeMsg));
            console.log("WebSocket connected. Subscribed strictly to: " + okxSymbol);
        }
    };

    priceWs.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data && data.data && data.data.length > 0) {
            const ticker = data.data[0];
            const price = parseFloat(ticker.last);
            if (!isNaN(price)) {
                // 1. 메인 패널 현재가 초저지연 업데이트
                updatePriceWithTickFlash(price);

                // 2. Active Deployment 실시간 라이브 PnL (OKX 선물 ROE 공식)
                const posActive = document.getElementById('position-active');
                if (posActive && !posActive.classList.contains('hidden')) {
                    const posCard = document.getElementById('active-position-card');
                    const posType = document.getElementById('pos-type')?.textContent?.trim();
                    const entryPriceEl = document.getElementById('pos-entry');

                    if (entryPriceEl && entryPriceEl.dataset.val) {
                        const entryPrice = parseFloat(entryPriceEl.dataset.val);
                        // 레버리지는 서버에서 dataset.leverage로 심어둔 값 사용 (없으면 1)
                        const leverage = parseFloat(entryPriceEl.dataset.leverage || '1');

                        if (entryPrice > 0) {
                            let pnl = 0;
                            // 선물 ROE 공식: ((Mark - Entry) / Entry) * 100 * Leverage
                            if (posType === 'LONG') {
                                pnl = ((price - entryPrice) / entryPrice) * 100 * leverage;
                            } else if (posType === 'SHORT') {
                                pnl = ((entryPrice - price) / entryPrice) * 100 * leverage;
                            }
                            pnl = Math.round(pnl * 100) / 100;

                            // 현재가 및 ROE 렌더링
                            updateNumberText('pos-current', price);
                            updateNumberText('pos-roi', pnl, val => (val > 0 ? `+${val.toFixed(2)}%` : `${val.toFixed(2)}%`));

                            // 카드 Glow 이펙트 실시간 연동
                            const roiEl = document.getElementById('pos-roi');
                            if (pnl > 0) {
                                roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-neon-green';
                                posCard.className = "glass-panel p-5 transition-all duration-500 flex-grow max-h-[250px] flex flex-col justify-center relative overflow-hidden glow-green";
                            } else if (pnl < 0) {
                                roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-neon-red';
                                posCard.className = "glass-panel p-5 transition-all duration-500 flex-grow max-h-[250px] flex flex-col justify-center relative overflow-hidden glow-red";
                            } else {
                                roiEl.className = 'text-2xl font-mono font-bold leading-none flash-target text-gray-400';
                                posCard.className = "glass-panel p-5 transition-all duration-500 border-navy-border flex-grow max-h-[250px] flex flex-col justify-center relative overflow-hidden";
                            }
                        }
                    }
                }
            }
        }
    };

    priceWs.onerror = (error) => {
        console.error("WebSocket Error: ", error);
    };

    priceWs.onclose = () => {
        console.log("WebSocket disconnected. Auto-reconnecting in 5 seconds...");
        setTimeout(initPriceWebSocket, 5000); // 5초 후 자동 재시도
    };
}


// --- Init & Intervals (Parallel Optimization) ---
async function initializeApp() {
    // 순차적 페칭 대신 Promise.all을 활용해 병렬 스레드로 대기 시간 단축
    initPriceWebSocket(); // 웹소켓 즉각 연결
    initChart();
    await Promise.all([
        syncConfig(),
        syncBotStatus(),
        syncStats(),
        syncChart(),
        updateLogs()
    ]);

    // 초기 렌더링 후 타이머 설정
    setInterval(syncBotStatus, 1000);
    setInterval(syncChart, 5000);       // Optimized to 5s
    setInterval(syncStats, 30000);
    setInterval(updateLogs, 3000);
}

// Start
initializeApp();
