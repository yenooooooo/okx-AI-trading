function initChart() {
    const container = document.getElementById('chart-container');
    if (!container || chart) return;

    // ① 메인 차트 (캔들)
    chart = LightweightCharts.createChart(container, {
        autoSize: true,
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e' },
        grid: {
            vertLines: { color: 'rgba(48,54,61,0.5)' },
            horzLines: { color: 'rgba(48,54,61,0.5)' },
        },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#30363d' },
        rightPriceScale: { borderColor: '#30363d' },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#00ff88', downColor: '#ff4d4d',
        borderVisible: false, wickUpColor: '#00ff88', wickDownColor: '#ff4d4d',
    });

    // ② 볼륨 히스토그램 (메인 차트 하단, 별도 스케일)
    volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        scaleMargins: { top: 0.85, bottom: 0 },
    });

    // ③ EMA20 라인 (전략 핵심 지표)
    ema20Series = chart.addLineSeries({
        color: 'rgba(88,166,255,0.85)', lineWidth: 1,
        title: 'EMA20', priceLineVisible: false, lastValueVisible: false,
    });

    // ④ EMA200 1h 라인 (거시 추세 — 단일 수평선, Dashed)
    ema200Series = chart.addLineSeries({
        color: 'rgba(255,200,80,0.7)', lineWidth: 1, lineStyle: 2,
        title: 'EMA200(1h)', priceLineVisible: false, lastValueVisible: true,
    });

    // ⑤ RSI 서브 차트
    const rsiContainer = document.getElementById('rsi-chart-container');
    if (rsiContainer) {
        rsiChart = LightweightCharts.createChart(rsiContainer, {
            autoSize: true,
            layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e', fontSize: 9 },
            grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(48,54,61,0.3)' } },
            timeScale: { visible: false, borderColor: '#30363d' },
            rightPriceScale: { borderColor: '#30363d', scaleMargins: { top: 0.1, bottom: 0.1 } },
            crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
        });
        rsiSeries = rsiChart.addLineSeries({
            color: '#c084fc', lineWidth: 1,
            priceLineVisible: false, lastValueVisible: true,
        });
        [30, 55, 70].forEach(v => rsiSeries.createPriceLine({
            price: v, color: 'rgba(255,255,255,0.15)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false,
        }));
    }

    // ⑥ MACD 서브 차트
    const macdContainer = document.getElementById('macd-chart-container');
    if (macdContainer) {
        macdChart = LightweightCharts.createChart(macdContainer, {
            autoSize: true,
            layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8b949e', fontSize: 9 },
            grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(48,54,61,0.3)' } },
            timeScale: { visible: false, borderColor: '#30363d' },
            rightPriceScale: { borderColor: '#30363d', scaleMargins: { top: 0.15, bottom: 0.15 } },
            crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
        });
        macdHistSeries = macdChart.addHistogramSeries({
            priceLineVisible: false, lastValueVisible: false,
        });
        macdSignalSeries = macdChart.addLineSeries({
            color: 'rgba(255,165,0,0.7)', lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false,
        });
    }
}

async function syncChart() {
    try {
        if (!chart) initChart();

        // Stale Response 방어: 요청 시점의 심볼을 캡처
        const requestedSymbol = currentSymbol;

        // ── OHLCV (지표 포함) ────────────────────────────────────────────────
        const response = await fetch(`${API_URL}/ohlcv?symbol=${encodeURIComponent(requestedSymbol)}&limit=60`);
        const ohlcv = await response.json();
        if (requestedSymbol !== currentSymbol) return;

        const overlay = document.getElementById('chart-overlay');
        if (ohlcv.error || !Array.isArray(ohlcv) || ohlcv.length === 0) {
            if (overlay) overlay.classList.remove('hidden');
            return;
        }
        if (overlay) overlay.classList.add('hidden');
        if (!candleSeries) return;

        const KST = 9 * 3600; // KST = UTC+9 (초 단위)

        // ① 캔들 데이터 세팅
        const candles = ohlcv.map(c => ({
            time: Math.floor(c.timestamp / 1000) + KST,
            open:  parseFloat(c.open),
            high:  parseFloat(c.high),
            low:   parseFloat(c.low),
            close: parseFloat(c.close),
        }));
        candleSeries.setData(candles);
        lastCandleData = candles[candles.length - 1];

        // ② 볼륨 바
        if (volumeSeries) {
            const volumes = ohlcv.map(c => ({
                time:  Math.floor(c.timestamp / 1000) + KST,
                value: parseFloat(c.volume || 0),
                color: parseFloat(c.close) >= parseFloat(c.open)
                    ? 'rgba(0,255,136,0.25)' : 'rgba(255,77,77,0.25)',
            }));
            volumeSeries.setData(volumes);
        }

        // ③ EMA20 라인
        if (ema20Series) {
            const ema20Data = ohlcv
                .filter(c => c.ema_20 != null)
                .map(c => ({ time: Math.floor(c.timestamp / 1000) + KST, value: parseFloat(c.ema_20) }));
            ema20Series.setData(ema20Data);
        }

        // ④ RSI 서브 패널
        if (rsiSeries) {
            const rsiData = ohlcv
                .filter(c => c.rsi != null)
                .map(c => ({ time: Math.floor(c.timestamp / 1000) + KST, value: parseFloat(c.rsi) }));
            rsiSeries.setData(rsiData);
        }

        // ⑤ MACD 서브 패널 (히스토그램 + 시그널)
        if (macdHistSeries) {
            const macdData = ohlcv
                .filter(c => c.macd != null)
                .map(c => {
                    const v = parseFloat(c.macd);
                    return {
                        time:  Math.floor(c.timestamp / 1000) + KST,
                        value: v,
                        color: v >= 0 ? 'rgba(0,255,136,0.6)' : 'rgba(255,77,77,0.6)',
                    };
                });
            macdHistSeries.setData(macdData);
        }
        if (macdSignalSeries) {
            const sigData = ohlcv
                .filter(c => c.macd_signal != null)
                .map(c => ({ time: Math.floor(c.timestamp / 1000) + KST, value: parseFloat(c.macd_signal) }));
            macdSignalSeries.setData(sigData);
        }

        // ── ⑥ EMA200(1h 거시) — /api/v1/brain 에서 단일값 수평선 ─────────────
        try {
            const brainRes = await fetch(`${API_URL}/brain`);
            const brain = await brainRes.json();
            if (requestedSymbol !== currentSymbol) return;
            const macroEma200 = brain?.symbols?.[requestedSymbol]?.macro_ema_200;
            if (ema200Series && macroEma200) {
                const ema200Data = candles.map(c => ({ time: c.time, value: parseFloat(macroEma200) }));
                ema200Series.setData(ema200Data);
            }
        } catch(e) { /* EMA200 실패 무시 */ }

        // ── ⑦ TP/SL/진입가 수평선 — /api/v1/status 에서 ──────────────────────
        try {
            const statusRes = await fetch(`${API_URL}/status`);
            const status = await statusRes.json();
            if (requestedSymbol !== currentSymbol) return;
            const sym = status?.symbols?.[requestedSymbol];

            // 기존 수평선 제거
            if (entryPriceLine) { try { candleSeries.removePriceLine(entryPriceLine); } catch(e){} entryPriceLine = null; }
            if (tpPriceLine)    { try { candleSeries.removePriceLine(tpPriceLine);    } catch(e){} tpPriceLine    = null; }
            if (slPriceLine)    { try { candleSeries.removePriceLine(slPriceLine);    } catch(e){} slPriceLine    = null; }

            if (sym && sym.position && sym.position !== 'NONE' && !sym.position.startsWith('PENDING')) {
                const entryP = parseFloat(sym.entry_price || 0);
                const realSl = parseFloat(sym.real_sl || 0);
                const realTp = parseFloat(sym.last_placed_tp_price || 0);
                const isLong = sym.position === 'LONG';

                if (entryP > 0) entryPriceLine = candleSeries.createPriceLine({
                    price: entryP, color: '#8b949e', lineWidth: 1, lineStyle: 2,
                    axisLabelVisible: true, title: '진입',
                });
                if (realSl > 0) slPriceLine = candleSeries.createPriceLine({
                    price: realSl, color: '#ff4d4d', lineWidth: 1, lineStyle: 1,
                    axisLabelVisible: true, title: 'SL',
                });
                if (realTp > 0) tpPriceLine = candleSeries.createPriceLine({
                    price: realTp, color: '#00ff88', lineWidth: 1, lineStyle: 1,
                    axisLabelVisible: true, title: 'TP',
                });

                // ⑧ 포지션 배경 틴트
                const tint = document.getElementById('chart-pos-tint');
                if (tint) {
                    tint.style.backgroundColor = isLong ? 'rgba(0,255,136,0.04)' : 'rgba(255,77,77,0.04)';
                    tint.style.opacity = '1';
                }

                // ⑧ 포지션 배지
                const badge = document.getElementById('chart-position-badge');
                if (badge) {
                    badge.textContent = isLong ? '🟢 LONG' : '🔴 SHORT';
                    badge.className = `text-[10px] font-mono px-1.5 py-0.5 rounded ${
                        isLong
                            ? 'bg-green-500/20 text-green-400 border border-green-500/40'
                            : 'bg-red-500/20 text-red-400 border border-red-500/40'
                    }`;
                    badge.classList.remove('hidden');
                }
            } else {
                const tint = document.getElementById('chart-pos-tint');
                if (tint) tint.style.opacity = '0';
                const badge = document.getElementById('chart-position-badge');
                if (badge) badge.classList.add('hidden');
            }
        } catch(e) { /* status 실패 무시 */ }

        // ── ⑨ 차트 헤더 업데이트 (우상단 오버레이) ──────────────────────────
        // 다음 캔들 카운트다운
        if (candles.length > 1) {
            const tfSec = candles[candles.length - 1].time - candles[candles.length - 2].time;
            const nextCandleTime = lastCandleData.time + tfSec;
            const nowKst = Math.floor(Date.now() / 1000) + KST;
            const remaining = nextCandleTime - nowKst;
            const countdown = document.getElementById('chart-candle-countdown');
            if (countdown) {
                if (remaining > 0) {
                    const m = Math.floor(remaining / 60);
                    const s = remaining % 60;
                    countdown.textContent = `다음 캔들 ${m}:${String(s).padStart(2, '0')}`;
                } else {
                    countdown.textContent = '';
                }
            }
        }

        // ── ④ 온차트 매매 마커 (시간 필터링 적용) ────────────────────────────
        try {
            const tradesRes = await fetch(`${API_URL}/trades`);
            const allTrades = await tradesRes.json();
            if (requestedSymbol !== currentSymbol) return;

            // 차트 가시 범위 밖(첫 캔들 이전)의 마커는 제거
            const minChartTime = candles.length > 0 ? candles[0].time : 0;

            if (Array.isArray(allTrades) && allTrades.length > 0) {
                const symbolTrades = allTrades.filter(t => t.symbol === requestedSymbol);
                const markers = [];

                symbolTrades.forEach(trade => {
                    const posType = (trade.position_type || '').toUpperCase();
                    const pnl = parseFloat(trade.pnl ?? 0);

                    if (trade.entry_time) {
                        const entryTs = Math.floor(
                            new Date(String(trade.entry_time).replace(' ', 'T') + 'Z').getTime() / 1000
                        ) + KST;
                        if (!isNaN(entryTs) && entryTs >= minChartTime) {
                            markers.push({
                                time: entryTs,
                                position: posType === 'LONG' ? 'belowBar' : 'aboveBar',
                                color: posType === 'LONG' ? '#00ff88' : '#ff4d4d',
                                shape: posType === 'LONG' ? 'arrowUp' : 'arrowDown',
                                text: posType === 'LONG' ? '🟢 LONG 진입' : '🔴 SHORT 진입',
                            });
                        }
                    }

                    if (trade.exit_time) {
                        const exitTs = Math.floor(
                            new Date(String(trade.exit_time).replace(' ', 'T') + 'Z').getTime() / 1000
                        ) + KST;
                        if (!isNaN(exitTs) && exitTs >= minChartTime) {
                            markers.push({
                                time: exitTs,
                                position: posType === 'LONG' ? 'aboveBar' : 'belowBar',
                                color: pnl >= 0 ? '#00ff88' : '#ff4d4d',
                                shape: 'circle',
                                text: pnl >= 0 ? '✅ 익절' : '💀 손절',
                            });
                        }
                    }
                });

                markers.sort((a, b) => a.time - b.time);
                candleSeries.setMarkers(markers);
            } else {
                candleSeries.setMarkers([]);
            }
        } catch (markerErr) {
            console.warn("Marker Sync Failed:", markerErr);
        }

    } catch (error) {
        const overlay = document.getElementById('chart-overlay');
        if (overlay) overlay.classList.remove('hidden');
        console.error("Chart Sync Failed:", error);
    }
}

// --- Terminal Syntax Highlighter ---
/**
 * formatTerminalMsg(rawMsg)
 * 원시 로그 문자열을 Cyberpunk 구문 강조 HTML로 변환.
 * 처리 순서: ① 뱃지 치환 → ② 가격 → ③ 수익률 → ④ 방향성
 * 각 단계의 치환 결과가 다음 단계의 패턴과 충돌하지 않도록 순서를 고정.
 */
