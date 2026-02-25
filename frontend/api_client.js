// 배포 환경에서는 서버의 퍼블릭 IP를 입력해야 합니다. 로컬 테스트 시에는 127.0.0.1 유지.
const SERVER_IP = "127.0.0.1"; // TODO: AWS EC2 등 실제 배포 시 여기에 서버의 Public IP를 입력하세요.
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
