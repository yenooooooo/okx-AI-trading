// 배포 환경에서는 서버의 퍼블릭 IP를 입력해야 합니다. 로컬 테스트 시에는 127.0.0.1 유지.
// 로컬 환경용 IP
const SERVER_IP = "15.135.78.118"; // TODO: AWS EC2 등 실제 배포 시 여기에 서버의 Public IP를 입력하세요.
const API_URL = `http://${SERVER_IP}:8000/api/v1`;

async function syncBotStatus() {
    try {
        const response = await fetch(`${API_URL}/status`);
        const data = await response.json();

        // 1. 잔고 업데이트
        document.getElementById('current-balance').innerHTML = `${data.balance} <span class="text-lg text-slate-400">USDT</span>`;
        document.getElementById('balance-krw').innerText = `≈ ${(data.balance * 1350).toLocaleString()} 원`; // 임시 환율 적용

        // 2. 포지션 업데이트 (기존 코드 유지)
        const posEl = document.getElementById('current-position');
        posEl.innerText = data.position;
        posEl.className = `text-3xl font-bold mb-1 ${data.position === 'LONG' ? 'text-green-500' : data.position === 'SHORT' ? 'text-red-500' : 'text-slate-500'}`;

        // 이 부분을 data.entryPrice 에서 data.entry_price 로 수정
        document.getElementById('entry-price').innerText = `진입가: ${data.entry_price} USDT`;

        // 3. 로그 업데이트 (최신 로그만 화면에 렌더링)
        const logContainer = document.getElementById('log-container');
        if (data.logs.length > logContainer.childElementCount) {
            logContainer.innerHTML = ''; // 초기화 후 재렌더링
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

    // 뇌 구조 데이터 동기화 함수 호출
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

// 1초마다 백엔드와 동기화 (무한 루프)
setInterval(syncBotStatus, 1000);

// --- [신규 추가] AI 실시간 뇌 구조 및 수익률 연동 로직 ---

async function updateBrain() {
    try {
        // 1. 뇌 구조(시황 판단) 데이터 호출
        const brainRes = await fetch(`http://${SERVER_IP}:8000/api/v1/brain`);
        if (brainRes.ok) {
            const brainData = await brainRes.json();
            // 데이터가 비어있지 않다면 UI 업데이트
            if (brainData.price) document.getElementById('brain-price').innerText = `$${parseFloat(brainData.price).toLocaleString()}`;
            if (brainData.rsi !== null) document.getElementById('brain-indicator').innerText = parseFloat(brainData.rsi).toFixed(2);
            if (brainData.decision) document.getElementById('brain-decision').innerText = `🤖 ${brainData.decision}`;
        }

        // 2. 최근 거래 내역(ROI) 호출
        const tradesRes = await fetch(`http://${SERVER_IP}:8000/api/v1/trades`);
        if (tradesRes.ok) {
            const tradesData = await tradesRes.json();
            const listEl = document.getElementById('trade-history-list');

            if (tradesData.length > 0) {
                listEl.innerHTML = ''; // 기존 내역 비우기
                tradesData.forEach(trade => {
                    // 수익률에 따라 색상 변경 (양수는 빨간색/초록색, 음수는 파란색 등 디자인 취향껏 설정)
                    const roiColor = trade.roi > 0 ? 'text-green-400 font-bold' : (trade.roi < 0 ? 'text-red-400 font-bold' : 'text-gray-300');
                    const row = `
                        <tr class="hover:bg-gray-700/50 transition-colors">
                            <td class="px-3 py-3 text-xs text-gray-400">${trade.time}</td>
                            <td class="px-3 py-3 font-medium">${trade.type}</td>
                            <td class="px-3 py-3 text-right ${roiColor}">${trade.roi > 0 ? '+' : ''}${trade.roi}%</td>
                        </tr>
                    `;
                    listEl.innerHTML += row;
                });
            }
        }
    } catch (error) {
        console.warn("뇌 구조 데이터 동기화 대기 중...", error);
    }
}
