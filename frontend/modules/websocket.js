let priceWs = null;
let _wsManualRestart = false; // 수동 재시작 시 onclose 자동 재연결 타이머 중복 방지

function initPriceWebSocket() {
    // [데모 모드] 백엔드 is_demo 상태에 따라 OKX WebSocket URL 분기
    const wsUrl = _isDemoMode
        ? "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"
        : "wss://ws.okx.com:8443/ws/v5/public";

    if (priceWs) {
        _wsManualRestart = true; // onclose에서 5초 타이머 건너뜀
        priceWs.close();
    }

    priceWs = new WebSocket(wsUrl);

    // OKX WebSocket keepalive: 25초마다 ping 전송 (30초 타임아웃 방지)
    let _wsPingInterval = null;
    priceWs.addEventListener('open', () => {
        _wsPingInterval = setInterval(() => {
            if (priceWs && priceWs.readyState === WebSocket.OPEN) {
                priceWs.send('ping');
            } else {
                clearInterval(_wsPingInterval);
            }
        }, 25000);
    });
    priceWs.addEventListener('close', () => clearInterval(_wsPingInterval));

    priceWs.onopen = () => {
        // async fetch 제거: WebSocket onopen 시점에는 currentSymbol이 이미 executeDeepSync에 의해 최신화됨.
        // /config 재조회는 불필요하며, 타이밍 경쟁 조건(race condition)을 유발할 수 있으므로 제거.
        const symbolRaw = currentSymbol;

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
        if (event.data === 'pong') return; // ping 응답 무시
        const data = JSON.parse(event.data);
        if (data && data.data && data.data.length > 0) {
            const ticker = data.data[0];
            const price = parseFloat(ticker.last);
            if (!isNaN(price)) {
                // 1. 메인 패널 현재가 초저지연 업데이트
                updatePriceWithTickFlash(price);

                // 2. 차트 마지막 캔들 실시간 업데이트 (close/high/low)
                if (candleSeries && lastCandleData) {
                    lastCandleData = {
                        ...lastCandleData,
                        close: price,
                        high: Math.max(lastCandleData.high, price),
                        low: Math.min(lastCandleData.low, price)
                    };
                    candleSeries.update(lastCandleData);
                }

                // 3. pos-current 마크가격 표시 (pos-roi는 백엔드 OKX 정확값으로 처리)

                // 4. 오버라이드 패널 오픈 상태에서 견적서 실시간 갱신 (가격 연동 출렁임)
                const overridePanel = document.getElementById('manual-override-panel');
                if (overridePanel && !overridePanel.classList.contains('hidden')) {
                    updateOrderPreview();
                }
            }
        }
    };

    priceWs.onerror = (error) => {
        console.error("WebSocket Error: ", error);
    };

    priceWs.onclose = () => {
        if (_wsManualRestart) {
            _wsManualRestart = false; // 플래그 리셋 (수동 재시작이므로 타이머 없이 종료)
            return;
        }
        console.log("WebSocket disconnected. Auto-reconnecting in 5 seconds...");
        setTimeout(initPriceWebSocket, 5000); // 5초 후 자동 재시도
    };
}



// --- System Health Check (실제 API 핑 기반 — 표면 체크 아님) ---
