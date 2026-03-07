async function toggleTimeframe() {
    const current = window._currentTimeframe || '15m';
    const target = current === '15m' ? '5m' : '15m';

    const confirmed = confirm(
        `타임프레임을 ${current} → ${target}으로 전환합니다.\n\n` +
        `• 매매 파라미터가 ${target} 최적값으로 자동 조정됩니다.\n` +
        `• 차트 및 게이트 필터가 즉시 새 타임프레임으로 전환됩니다.\n\n` +
        `계속하시겠습니까?`
    );
    if (!confirmed) return;

    const container = document.getElementById('tf-toggle-container');
    if (container) container.classList.add('tf-switching');

    try {
        const res = await fetch(`${API_URL}/timeframe/switch?target_tf=${encodeURIComponent(target)}`, {
            method: 'POST'
        });
        const data = await res.json();

        if (!data.success) {
            showToast('타임프레임 전환 차단', data.message, 'ERROR');
            return;
        }

        if (!data.changed) {
            showToast('타임프레임', data.message, 'INFO');
            return;
        }

        // 글로벌 캐시 즉시 갱신
        window._currentTimeframe = target;
        _applyTimeframeToggleUI(target);

        // Deep sync: 차트 + 뇌 + 설정 동시 리프레시
        await Promise.all([syncChart(), syncBrain(), syncConfig(currentSymbol)]);

        showToast(
            '타임프레임 전환 완료',
            `${data.previous} → ${data.current} | 프리셋 자동 적용`,
            'SUCCESS'
        );
    } catch (err) {
        console.error('[ANTIGRAVITY] toggleTimeframe 실패:', err);
        showToast('타임프레임 전환 실패', err.message || '서버 통신 오류', 'ERROR');
    } finally {
        if (container) container.classList.remove('tf-switching');
    }
}

/**
 * _applyTimeframeToggleUI(tf) — 토글 버튼 시각 상태 즉시 갱신
 */
function _applyTimeframeToggleUI(tf) {
    const btn5m = document.getElementById('tf-btn-5m');
    const btn15m = document.getElementById('tf-btn-15m');
    if (btn5m) {
        btn5m.classList.toggle('tf-active', tf === '5m');
    }
    if (btn15m) {
        btn15m.classList.toggle('tf-active', tf === '15m');
    }
}

// --- OKX 수동 매매 싱크 ---
/** OKX 수동 매매 기록을 대시보드로 동기화 */
async function syncOkxTrades() {
    const btn = document.getElementById('okx-sync-btn');
    const origText = btn ? btn.textContent : '';
    if (btn) btn.textContent = '\u23F3';  // ⏳
    try {
        const res = await fetch(`${API_URL}/sync_trades`, { method: 'POST' });
        const data = await res.json();
        if (btn) {
            btn.textContent = data.synced > 0 ? ('\u2705' + data.synced) : '\u2705';
            setTimeout(() => { btn.textContent = origText || '\uD83D\uDD04'; }, 3000);
        }
        // 싱크 건수 있으면 즉시 stats/heatmap 갱신
        if (data.synced > 0) {
            syncStats();
            if (typeof fetchAndRenderHeatmap === 'function') fetchAndRenderHeatmap();
        }
    } catch(e) {
        if (btn) {
            btn.textContent = '\u274C';
            setTimeout(() => { btn.textContent = origText || '\uD83D\uDD04'; }, 3000);
        }
    }
}

// --- Gate 수동 새로고침 ---
/** Entry Readiness 패널 즉시 새로고침 (방식 A: syncBrain 즉시 1회 호출 + 시각 피드백) */
async function forceRefreshGates() {
    const btn = document.getElementById('gate-refresh-btn');
    const label = document.getElementById('gate-candle-label');
    if (btn) {
        btn.style.pointerEvents = 'none';
        btn.style.opacity = '0.4';
        btn.style.transform = 'rotate(360deg)';
        btn.style.transition = 'transform 0.4s ease';
    }
    // 라벨에 갱신 중 표시
    const prevLabel = label ? label.textContent : '';
    if (label) {
        label.textContent = '갱신 중...';
        label.className = 'font-mono text-[10px] text-yellow-400 animate-pulse tracking-wide';
    }
    await syncBrain();
    if (btn) {
        btn.style.pointerEvents = '';
        btn.style.opacity = '';
        setTimeout(() => {
            btn.style.transform = '';
            btn.style.transition = '';
        }, 450);
    }
    // 갱신 완료 피드백 (0.8초 동안 초록색 플래시)
    if (label) {
        label.className = 'font-mono text-[10px] text-neon-green tracking-wide';
        setTimeout(() => {
            label.className = 'font-mono text-[10px] text-cyan-400 tracking-wide';
        }, 800);
    }
}

// --- Modal Scroll Lock ---

async function executeDeepSync(newSymbol) {
    // 1. 글로벌 심볼 즉각 갱신
    currentSymbol = newSymbol;

    // 2. 조준경 뱃지 갱신
    const targetBadge = document.getElementById('hero-target-badge');
    if (targetBadge) targetBadge.textContent = newSymbol;
    // [Phase 18.1] 좌측 패널 심볼 배지 즉시 갱신
    const leftSymBadge = document.getElementById('left-panel-symbol-badge');
    if (leftSymBadge) leftSymBadge.textContent = newSymbol.split(':')[0];
    // [Phase 18.1] 모달 심볼 드롭다운 동기화
    const modalSymSel = document.getElementById('modal-target-symbol');
    if (modalSymSel) modalSymSel.value = newSymbol;

    // 3. Ghost Data 방지 — 가격 및 차트 즉시 초기화
    const heroPriceEl = document.getElementById('hero-price');
    if (heroPriceEl) heroPriceEl.textContent = '---';
    if (candleSeries) candleSeries.setData([]);

    // 4. 혼잣말 리셋
    const feed = document.getElementById('monologue-feed');
    if (feed) feed.innerHTML = `<div class="text-[11px] font-mono text-neon-green italic animate-pulse">🎯 [${newSymbol}] 조준 완료. 데이터 딥싱크(Deep Sync) 중...</div>`;

    // 5. 그리드 버튼 UI 활성 상태 갱신
    document.querySelectorAll('.target-coin-btn').forEach(btn => {
        if (btn.dataset.symbol === newSymbol) {
            btn.className = 'target-coin-btn flex items-center justify-center text-xs py-2 rounded font-mono font-bold transition-all border border-neon-green text-neon-green bg-neon-green/10';
        } else {
            btn.className = 'target-coin-btn flex items-center justify-center text-xs py-2 rounded font-mono font-bold transition-all border border-navy-border/50 bg-navy-900/40 text-gray-500 hover:text-gray-300';
        }
    });

    // 6. 신경망 재연결 — 웹소켓 즉각 연결 후 차트·뇌 병렬 완료 대기
    // initPriceWebSocket은 동기식이므로 먼저 실행, syncChart·syncBrain은 Promise.all로 병렬 처리
    initPriceWebSocket();
    await Promise.all([syncChart(), syncBrain()]);
}

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
        // Chimera 버그 수정: Object.keys()[0]은 메모리 순서 기반이라 타겟 변경 후에도 구형 심볼을 가리킴.
        // active_target은 서버가 get_config('symbols')[0]을 직접 읽어 반환하므로 항상 최신 타겟이 보장됨.
        const activeTarget = data.active_target || Object.keys(symbols)[0];
        const symbolData = activeTarget ? symbols[activeTarget] : null;

        // --- Auto-Tracking: 백엔드 타겟 변경 자동 감지 → Deep Sync 트리거 ---
        if (activeTarget && activeTarget !== currentSymbol) {
            const posTypeEl = document.getElementById('pos-type');
            const posType = posTypeEl ? posTypeEl.textContent.trim() : 'NONE';
            if (!posType || posType === 'NONE') {
                executeDeepSync(activeTarget);
                return; // Deep Sync 후 이 사이클의 나머지 UI 업데이트 스킵 (다음 폴링에서 정상 처리)
            }
        }

        const posCard = document.getElementById('active-position-card');
        const posNone = document.getElementById('position-none');
        const posActive = document.getElementById('position-active');

        const posSymbolEl = document.getElementById('pos-symbol');

        if (!symbolData || symbolData.position === 'NONE') {
            posNone.classList.remove('hidden');
            posActive.classList.add('hidden');
            posActive.classList.remove('flex');
            posCard.className = "glass-panel p-5 transition-all duration-500 border-navy-border flex-grow flex flex-col relative overflow-hidden";
            if (posSymbolEl) posSymbolEl.classList.add('hidden');
        } else {
            posNone.classList.add('hidden');
            posActive.classList.remove('hidden');
            posActive.classList.add('flex');
            if (posSymbolEl) {
                posSymbolEl.textContent = activeTarget.split(':')[0];
                posSymbolEl.classList.remove('hidden');
            }

            // [Phase 24] PENDING 상태 시 철거 버튼 노출 / 아닐 시 숨김
            const _isPending = symbolData.position && symbolData.position.startsWith('PENDING');
            const _abortBtn = document.getElementById('btn-abort-pending');
            if (_abortBtn) _abortBtn.classList.toggle('hidden', !_isPending);

            // 웹소켓(priceWs)이 연결되어 있을 때는 REST API 구형 가격/수익률 데이터 표시는 무시.
            // (단, 포지션 유무, 진입가, 목표가 등 고정데이터는 계속 연동)
            updateText('pos-type', symbolData.position);
            // [UI Overhaul] 방향 배지 색상 동적 적용
            const posTypeEl2 = document.getElementById('pos-type');
            if (posTypeEl2) {
                const posStr = String(symbolData.position).toUpperCase();
                if (posStr.includes('LONG')) {
                    posTypeEl2.className = 'text-sm font-black font-mono tracking-tight flash-target text-white px-3 py-1.5 rounded-lg bg-gradient-to-r from-emerald-600 to-green-500 border border-emerald-400/30 shadow-lg';
                } else if (posStr.includes('SHORT')) {
                    posTypeEl2.className = 'text-sm font-black font-mono tracking-tight flash-target text-white px-3 py-1.5 rounded-lg bg-gradient-to-r from-red-600 to-rose-500 border border-red-400/30 shadow-lg';
                } else {
                    posTypeEl2.className = 'text-sm font-black font-mono tracking-tight flash-target text-white px-3 py-1.5 rounded-lg bg-gradient-to-r from-gray-700 to-gray-600 border border-gray-500/30 shadow-lg';
                }
            }
            updateNumberText('pos-entry', symbolData.entry_price);
            // TP/SL 상태 동기화 (백엔드 실시간 계산값 기반)
            const realSl = parseFloat(symbolData.real_sl || 0);

            // [Phase 16] 다이내믹 목표가 렌더링 (백엔드에서 완성된 문자열이 넘어옴)
            const posTpEl = document.getElementById('pos-tp');
            if (posTpEl) {
                const tpVal = symbolData.take_profit_price;
                if (tpVal && tpVal !== 0 && tpVal !== '0.0') {
                    posTpEl.textContent = tpVal;
                } else {
                    posTpEl.textContent = '대기중';
                }
            }
            // pos-tp-expect는 trailing 상태에 맞게 유지
            const trailingActive = symbolData.trailing_active === true;
            const trailingTarget = parseFloat(symbolData.trailing_target || 0);
            const posContracts = parseInt(symbolData.contracts || 1);
            if (trailingActive && trailingTarget > 0) {
                updateText('pos-tp-expect', 'Trailing Active 🎯');
            } else if (posContracts <= 1) {
                // [Phase TF] 1계약: TP 미등록 → 트레일링 전용 모드 표시
                updateText('pos-tp-expect', '트레일링 전용 모드 ⏳');
            } else {
                updateText('pos-tp-expect', '1차 익절 대기 중 ⏳');
            }

            updateNumberText('pos-sl', realSl > 0 ? realSl : 0);
            // [Breakeven Stop] SL 상태 라벨: TRAILING > BREAKEVEN > PROTECTED > Dynamic
            const beActive = symbolData.breakeven_stop_active === true;
            const ptpDone = symbolData.partial_tp_executed === true;
            if (trailingActive && trailingTarget > 0) {
                updateText('pos-sl-expect', '(TRAILING)');
            } else if (beActive) {
                updateText('pos-sl-expect', '(BREAKEVEN)');
            } else if (ptpDone) {
                updateText('pos-sl-expect', '(PROTECTED)');
            } else {
                updateText('pos-sl-expect', realSl > 0 ? '(Dynamic)' : '');
            }

            // [거래소 실제 주문가] Exchange Pending Order Prices
            const exchangeTp = parseFloat(symbolData.last_placed_tp_price || 0);
            const exchangeSl = parseFloat(symbolData.last_placed_sl_price || 0);
            const exchangeOrdersRow = document.getElementById('exchange-orders-row');
            if (exchangeOrdersRow) {
                const isPaper = symbolData.is_paper === true;
                if (!isPaper && (exchangeTp > 0 || exchangeSl > 0)) {
                    exchangeOrdersRow.classList.remove('hidden');
                    const tpDecimals = exchangeTp > 0 && exchangeTp < 100 ? 4 : 2;
                    const slDecimals = exchangeSl > 0 && exchangeSl < 100 ? 4 : 2;
                    updateText('pos-exchange-tp', exchangeTp > 0 ? `$${exchangeTp.toFixed(tpDecimals)}` : '미등록');
                    updateText('pos-exchange-sl', exchangeSl > 0 ? `$${exchangeSl.toFixed(slDecimals)}` : '미등록');
                } else {
                    exchangeOrdersRow.classList.add('hidden');
                }
            }

            // PnL(%) 및 USDT 수익금 동기화
            const pnl = parseFloat(symbolData.unrealized_pnl_percent || 0);
            const pnlUsdt = parseFloat(symbolData.unrealized_pnl || 0);
            const pnlSign = pnl >= 0 ? '+' : '';

            updateNumberText('pos-roi', pnl, val => `${pnlSign}${val.toFixed(2)}%`);
            updateNumberText('pos-pnl-usdt', pnlUsdt, val => `${pnlSign}${val.toFixed(2)} USDT`);
            // [최적화] 현재가는 카운트업 애니메이션 제거하고 즉각 반영 (지연시간 0)
            const posCurrentEl = document.getElementById('pos-current');
            if (posCurrentEl) {
                const p = parseFloat(symbolData.current_price);
                const decimals = p < 100 ? 4 : 2;
                const newText = p.toFixed(decimals);
                if (posCurrentEl.textContent !== newText) {
                    posCurrentEl.textContent = newText;
                    // 미세 깜빡임 효과만 유지
                    posCurrentEl.classList.remove('flash');
                    void posCurrentEl.offsetWidth;
                    posCurrentEl.classList.add('flash');
                }
            }

            const roiEl = document.getElementById('pos-roi');
            const pnlUsdtEl = document.getElementById('pos-pnl-usdt');

            // 색상 및 글로우 동적 적용 (숏/롱 관계없이 수익 여부에 따름)
            if (pnl > 0) {
                roiEl.className = 'text-3xl font-mono font-bold leading-none flash-target text-neon-green block';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono font-bold block mt-0.5 flash-target text-neon-green';
                posCard.className = "glass-panel p-5 transition-all duration-500 flex flex-col relative overflow-hidden glow-green";
            } else if (pnl < 0) {
                roiEl.className = 'text-3xl font-mono font-bold leading-none flash-target text-neon-red block';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono font-bold block mt-0.5 flash-target text-neon-red';
                posCard.className = "glass-panel p-5 transition-all duration-500 flex flex-col relative overflow-hidden glow-red";
            } else {
                roiEl.className = 'text-3xl font-mono font-bold leading-none flash-target text-gray-400 block';
                if (pnlUsdtEl) pnlUsdtEl.className = 'text-xs font-mono font-bold block mt-0.5 flash-target text-gray-400';
                posCard.className = "glass-panel p-5 transition-all duration-500 border-navy-border flex flex-col relative overflow-hidden";
            }

            // [UI Overhaul] TP/SL 프로그레스 바 업데이트
            const _entry = parseFloat(symbolData.entry_price || 0);
            const _current = parseFloat(symbolData.current_price || 0);
            const _tp = parseFloat(symbolData.take_profit_price || 0);
            if (_entry > 0 && _current > 0 && realSl > 0 && _tp > 0) {
                const priceMarker = document.getElementById('pos-price-marker');
                const tpSlBar = document.getElementById('pos-tp-sl-bar');
                if (priceMarker && tpSlBar) {
                    const range = _tp - realSl;
                    if (range > 0) {
                        const progress = Math.max(0, Math.min(100, ((_current - realSl) / range) * 100));
                        priceMarker.style.left = progress + '%';
                        // 바 색상: SL쪽(왼쪽)은 적색, TP쪽(오른쪽)은 녹색
                        tpSlBar.style.width = '100%';
                        tpSlBar.style.transform = 'none';
                    }
                }
            }
        }

        // --- NEW: Market Radar ---
        if (data.symbols) {
            const radarContainer = document.getElementById('market-radar-list');
            if (radarContainer) {
                let radarHtml = '';
                const symKeys = Object.keys(data.symbols);
                symKeys.slice(0, 3).forEach(sym => {
                    const symData = data.symbols[sym];
                    const priceStr = symData.current_price ? parseFloat(symData.current_price).toFixed(4) : "0.00";
                    const pnl = parseFloat(symData.unrealized_pnl_percent || 0);
                    let colorObj = "text-gray-500";
                    let valStr = `$${priceStr}`;

                    if (symData.position !== "NONE") {
                        colorObj = pnl >= 0 ? "text-neon-green" : "text-neon-red";
                        const sign = pnl > 0 ? "+" : "";
                        valStr = `${sign}${pnl.toFixed(2)}%`;
                    } else if (symData.current_price !== undefined && symData.current_price > 0) {
                        colorObj = "text-text-main";
                    }
                    const shortSym = sym.split(':')[0];

                    radarHtml += `
                        <div class="flex justify-between items-center text-[11px] bg-navy-900/40 p-1.5 rounded border border-navy-border/50">
                            <span class="font-mono text-gray-300 font-bold">${shortSym}</span>
                            <span class="font-mono ${colorObj}">${valStr}</span>
                        </div>
                    `;
                });
                if (radarHtml) radarContainer.innerHTML = radarHtml;
            }
        }

        // 3. Status Info
        // [데모 모드] 백엔드에서 is_demo 동기화
        if (typeof data.is_demo !== 'undefined') _isDemoMode = data.is_demo;
        const _demoTag = _isDemoMode ? '🧪 DEMO | ' : '';

        const statusDot = document.getElementById('status-dot');
        const statusPing = document.getElementById('status-ping');
        const statusText = document.getElementById('bot-status-text');
        const toggleBtn = document.getElementById('toggle-bot-btn');

        if (data.is_running) {
            statusDot.className = 'relative inline-flex rounded-full h-3 w-3 bg-neon-green';
            statusPing.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-green opacity-75';
            statusText.textContent = `${_demoTag}🟢 시스템 가동 중`;
            statusText.className = 'font-mono text-sm tracking-widest text-neon-green uppercase';
            toggleBtn.textContent = '🛑 시스템 중지';
            toggleBtn.className = 'px-6 py-2 bg-navy-800 border border-neon-red hover:bg-neon-red hover:text-white text-neon-red text-sm font-bold rounded transition-all font-mono tracking-widest';
        } else {
            statusDot.className = 'relative inline-flex rounded-full h-3 w-3 bg-neon-red';
            statusPing.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-neon-red opacity-75';
            statusText.textContent = `${_demoTag}🛑 시스템 중지`;
            statusText.className = 'font-mono text-sm tracking-widest text-gray-400 uppercase';
            toggleBtn.textContent = '🟢 시스템 가동';
            toggleBtn.className = 'px-6 py-2 bg-navy-800 border border-neon-green hover:bg-neon-green hover:text-navy-900 text-neon-green text-sm font-bold rounded transition-all font-mono tracking-widest';
        }

        // 3.5 [Phase TF] 타임프레임 토글 포지션 기반 활성/비활성
        const tfContainer = document.getElementById('tf-toggle-container');
        if (tfContainer) {
            const anyPositionOpen = Object.values(data.symbols || {}).some(
                s => s.position && s.position !== 'NONE'
            );
            tfContainer.classList.toggle('tf-disabled', anyPositionOpen);
            tfContainer.title = anyPositionOpen
                ? '포지션 보유 중 — 타임프레임 변경 불가'
                : '클릭하여 타임프레임 전환 (5m ↔ 15m)';
        }

        // 4. Engine Live Status Badge
        if (data.engine_status) {
            const badgeEl = document.getElementById('engine-live-badge');
            if (badgeEl) {
                if (data.engine_status.mode === 'AUTO') {
                    badgeEl.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold border flex items-center gap-1.5 transition-all bg-blue-500/10 border-blue-500/50 text-blue-400';
                    badgeEl.innerHTML = `<span class="animate-pulse">🤖</span> 순정 AI 다이내믹 연산 중`;
                } else {
                    badgeEl.className = 'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold border flex items-center gap-1.5 transition-all bg-orange-500/10 border-orange-500/50 text-orange-400';
                    badgeEl.innerHTML = `<span class="animate-pulse">⚙️</span> 수동 통제 중 (Risk: ${data.engine_status.risk}%)`;
                }
            }
        }

        // 5. [Phase 25] Adaptive Shield 방어 티어 배지 실시간 렌더링
        const tierBadge = document.getElementById('adaptive-tier-badge');
        if (tierBadge) {
            const tierName = data.adaptive_tier || '';
            const tierMap = {
                'CRITICAL': { emoji: '🔴', cls: 'border-red-500/50 bg-red-500/10 text-red-400', label: 'CRITICAL — 긴급 방어' },
                'MICRO':    { emoji: '🟡', cls: 'border-yellow-500/50 bg-yellow-500/10 text-yellow-400', label: 'MICRO — 소액 보호' },
                'STANDARD': { emoji: '🟢', cls: 'border-green-500/50 bg-green-500/10 text-green-400', label: 'STANDARD — 표준 운용' },
                'GROWTH':   { emoji: '🔵', cls: 'border-blue-500/50 bg-blue-500/10 text-blue-400', label: 'GROWTH — 성장 추종' },
            };
            const t = tierMap[tierName];
            if (t) {
                tierBadge.textContent = `${t.emoji} ${t.label}`;
                tierBadge.className = `inline-block px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold tracking-wider border ${t.cls}`;
            } else {
                tierBadge.textContent = '🛡️ OFF — 수동 모드';
                tierBadge.className = 'inline-block px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold tracking-wider border border-gray-600/50 bg-gray-800/50 text-gray-500';
            }
        }

        // 6. [UI Overhaul] Command Bar 미러링 — 핵심 데이터를 상단 바에 실시간 반영
        const cmdBalMirror = document.getElementById('cmd-balance-mirror');
        if (cmdBalMirror) cmdBalMirror.textContent = '$' + parseFloat(data.balance || 0).toFixed(2);

        if (symbolData && symbolData.position !== 'NONE') {
            const _pnl = parseFloat(symbolData.unrealized_pnl_percent || 0);
            const _pnlSign = _pnl >= 0 ? '+' : '';
            const cmdPnlMirror = document.getElementById('cmd-pnl-mirror');
            if (cmdPnlMirror) {
                cmdPnlMirror.textContent = `${_pnlSign}${_pnl.toFixed(2)}%`;
                cmdPnlMirror.className = `font-mono text-xs font-bold ${_pnl >= 0 ? 'text-neon-green' : 'text-neon-red'}`;
            }
            const cmdPriceMirror = document.getElementById('cmd-price-mirror');
            if (cmdPriceMirror) {
                const _p = parseFloat(symbolData.current_price || 0);
                cmdPriceMirror.textContent = '$' + (_p < 100 ? _p.toFixed(4) : _p.toFixed(2));
            }
        } else {
            const cmdPnlMirror = document.getElementById('cmd-pnl-mirror');
            if (cmdPnlMirror) { cmdPnlMirror.textContent = '--'; cmdPnlMirror.className = 'font-mono text-xs font-bold text-gray-500'; }
            const cmdPriceMirror = document.getElementById('cmd-price-mirror');
            if (cmdPriceMirror) cmdPriceMirror.textContent = '--';
        }

        // 7. [Margin Guard] 증거금 사전 경고 렌더링
        if (data.margin_guard) {
            window._marginGuardData = data.margin_guard;

            // [Bug Fix] 적용 직후 grace period (5초) — 백엔드 갱신 전 배지 재표시 방지
            const _mgInGrace = window._mgAppliedAt && (Date.now() - window._mgAppliedAt < 5000);
            if (!_mgInGrace) {
                const mgBadge = document.getElementById('margin-guard-badge');
                const cmdMgWarn = document.getElementById('cmd-margin-warn');
                let _mgHasWarn = false;
                let _mgSym = '', _mgCurLev = 0, _mgRecLev = 0;

                // 현재 감시 타겟(active_target)만 체크 — 타겟 전환 시 해당 코인 상태만 반영
                // (다른 심볼에 문제가 있어도 현재 보고 있는 코인이 괜찮으면 배지 숨김)
                const _mgActiveSym = data.active_target || currentSymbol;
                const _mgActive = data.margin_guard[_mgActiveSym];
                if (_mgActive && _mgActive.needs_change) {
                    _mgHasWarn = true;
                    _mgSym = _mgActiveSym.split(':')[0];
                    _mgCurLev = _mgActive.current_leverage;
                    _mgRecLev = _mgActive.recommended_leverage;
                }

                if (_mgHasWarn && mgBadge) {
                    // applyRecommendedLeverage()가 정확한 심볼을 알 수 있게 캐싱
                    window._lastActiveMgSym = _mgActiveSym;
                    mgBadge.classList.remove('hidden');
                    const mgSymEl = document.getElementById('mg-symbol');
                    const mgCurEl = document.getElementById('mg-current-lev');
                    const mgRecEl = document.getElementById('mg-rec-lev');
                    if (mgSymEl) mgSymEl.textContent = _mgSym;
                    if (mgCurEl) mgCurEl.textContent = _mgCurLev + 'x';
                    if (mgRecEl) mgRecEl.textContent = _mgRecLev + 'x';
                    // 토스트: 5분 쿨다운
                    if (!window._mgLastToast || Date.now() - window._mgLastToast > 300000) {
                        window._mgLastToast = Date.now();
                        showToast('Margin Guard', `${_mgSym} 증거금 부족 — ${_mgRecLev}x 추천`, 'ERROR');
                    }
                } else if (mgBadge) {
                    mgBadge.classList.add('hidden');
                }

                if (cmdMgWarn) cmdMgWarn.classList.toggle('hidden', !_mgHasWarn);
            }
        }

    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncBotStatus 실패 (엔드포인트: /api/v1/status):", error);
    }
}

// --- Brain Sync (3초 인터벌 - status와 분리) ---

async function syncBrain() {
    try {
        const brainRes = await fetch(`${API_URL}/brain`);
        const brainData = await brainRes.json();

        const symbolBrains = brainData.symbols || {};
        // active_target을 서버로부터 직접 수신해 brainState 조회 키로 사용
        // (currentSymbol은 executeDeepSync 호출 직후 갱신 전 구형 심볼일 수 있으므로 신뢰도 낮음)
        const activeTarget = brainData.active_target || currentSymbol;
        const brainState = symbolBrains[activeTarget] || Object.values(symbolBrains)[0];

        if (!brainState) return;

        // [확정봉 메타데이터] 카운트다운 + 봉 라벨 갱신
        if (brainState.confirmed_candle_ts) {
            window._confirmedCandleTs = brainState.confirmed_candle_ts;
            window._currentTimeframe = brainState.timeframe || '15m';
            _applyTimeframeToggleUI(window._currentTimeframe);
            // 확정봉 시각 라벨 표시 — 캔들 종료 시각 = 시작 + 타임프레임
            // 예: 02:15 시작 + 15분 = "02:30 봉 기준" (02:30에 확정된 데이터 기준)
            const tfMs = parseTimeframeMs(window._currentTimeframe);
            const candleCloseDate = new Date(brainState.confirmed_candle_ts + tfMs);
            const hh = String(candleCloseDate.getHours()).padStart(2, '0');
            const mm = String(candleCloseDate.getMinutes()).padStart(2, '0');
            const candleLabel = document.getElementById('gate-candle-label');
            if (candleLabel) candleLabel.textContent = `${hh}:${mm} 봉 기준`;
        }

        // [A] 진입 관문 체크리스트
        if (brainState.gates) {
            renderGates(brainState.gates, brainState.gates_passed || 0, brainState.live_gates || null);
        }
        // [B] 봇 혼잣말 피드
        if (brainState.monologue) {
            renderMonologue(brainState.monologue);
        }
        // [C] Guard Wall 진입 방벽 실시간 상태 (Flight Recorder)
        if (brainState.entry_guards) {
            renderEntryGuards(brainState.entry_guards);
        }
        // [D] Decision Trail 진입 파이프라인 (Flight Recorder)
        if (brainState.latest_decision_trail) {
            renderDecisionTrail(brainState.latest_decision_trail);
        }
        // [E] Config Snapshot Diff 설정 불일치 감지 (Flight Recorder)
        if (brainState.active_config) {
            checkConfigMismatch(brainState.active_config);
        }

        // WebSocket 연결 중엔 REST가 hero-price를 덮어쓰지 않음 (실시간 보호)
        if (brainState.price && (!priceWs || priceWs.readyState !== WebSocket.OPEN)) {
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
            const marker = document.getElementById('rsi-marker');
            if (marker) marker.style.left = `${Math.max(0, Math.min(100, rsi))}%`;

            // --- AI Confidence Matrix (RSI 50% + MACD 50% 복합 지표) ---
            // RSI 컴포넌트: 낮을수록 LONG 신호 (과매도 = 반등 압력)
            const rsiLongScore = Math.max(0, Math.min(100, 100 - rsi));
            // MACD 컴포넌트: 양수면 LONG 신호, 음수면 SHORT 신호 (±100 정규화)
            const macdRaw = parseFloat(brainState.macd) || 0;
            const macdAbs = Math.max(Math.abs(macdRaw), 0.0001);
            const macdLongScore = Math.max(0, Math.min(100, 50 + (macdRaw / macdAbs) * 50));
            // RSI 50% + MACD 50% 가중 합산
            const longProb = Math.round(rsiLongScore * 0.5 + macdLongScore * 0.5);
            const shortProb = 100 - longProb;

            const longProbEl = document.getElementById('ai-long-prob');
            const longBarEl = document.getElementById('ai-long-bar');
            if (longProbEl && longBarEl) {
                longProbEl.textContent = `${longProb}%`;
                longBarEl.style.width = `${longProb}%`;
                longProbEl.className = longProb >= 50 ? 'text-neon-green font-bold text-[10px]' : 'text-gray-500 font-bold text-[10px]';
            }

            const shortProbEl = document.getElementById('ai-short-prob');
            const shortBarEl = document.getElementById('ai-short-bar');
            if (shortProbEl && shortBarEl) {
                shortProbEl.textContent = `${shortProb}%`;
                shortBarEl.style.width = `${shortProb}%`;
                shortProbEl.className = shortProb >= 50 ? 'text-neon-red font-bold text-[10px]' : 'text-gray-500 font-bold text-[10px]';
            }
        }
        // --- CHOP Index 렌더링 (횡보장 탐지) ---
        if (brainState.chop !== undefined) {
            const chop = parseFloat(brainState.chop) || 0;
            const chopEl = document.getElementById('brain-chop');
            const chopBar = document.getElementById('chop-bar');
            const chopStatus = document.getElementById('chop-status');
            if (chopEl) chopEl.textContent = chop.toFixed(1);
            if (chopBar) {
                const pct = Math.max(0, Math.min(100, chop));
                chopBar.style.width = `${pct}%`;
                if (chop >= 61.8) {
                    // 횡보장 — 빨간색
                    chopBar.style.background = '#ff4d4d';
                    chopBar.style.boxShadow = '0 0 8px rgba(255,77,77,0.7)';
                } else if (chop <= 38.2) {
                    // 추세장 — 녹색
                    chopBar.style.background = '#00ff88';
                    chopBar.style.boxShadow = '0 0 8px rgba(0,255,136,0.7)';
                } else {
                    // 중립
                    chopBar.style.background = '#aaa';
                    chopBar.style.boxShadow = '0 0 4px #aaa';
                }
            }
            if (chopStatus) {
                if (chop >= 61.8) {
                    chopStatus.textContent = '🔴 횡보장 (진입 차단)';
                    chopStatus.className = 'font-bold text-neon-red text-[10px]';
                } else if (chop <= 38.2) {
                    chopStatus.textContent = '🟢 추세장 (진입 가능)';
                    chopStatus.className = 'font-bold text-neon-green text-[10px]';
                } else {
                    chopStatus.textContent = '🟡 중립';
                    chopStatus.className = 'font-bold text-yellow-400 text-[10px]';
                }
            }
        }

        if (brainState.macd !== undefined) {
            const macd = parseFloat(brainState.macd);
            updateNumberText('brain-macd', macd);
            const macdEl = document.getElementById('brain-macd');
            // MACD >= 0 green (0 포함 중립), < 0 red
            macdEl.className = macd >= 0 ? 'font-mono flash-target font-bold text-neon-green' : 'font-mono flash-target font-bold text-neon-red';

            // MACD 게이지 동적 스케일
            const posBar = document.getElementById('macd-bar-pos');
            const negBar = document.getElementById('macd-bar-neg');
            if (posBar && negBar) {
                const absMaxMacd = Math.max(Math.abs(macd), parseFloat(posBar.dataset.maxMacd || 1));
                posBar.dataset.maxMacd = absMaxMacd;
                negBar.dataset.maxMacd = absMaxMacd;
                const pct = Math.min(100, (Math.abs(macd) / absMaxMacd) * 100);
                if (macd >= 0) {
                    negBar.style.width = '0%';
                    posBar.style.width = `${pct}%`;
                } else {
                    posBar.style.width = '0%';
                    negBar.style.width = `${pct}%`;
                }
            }
        }

        // ── 프리셋 추천 배지 ──
        if (brainState.recommended_preset !== undefined) {
            const recPreset = brainState.recommended_preset;
            const recScore = parseInt(brainState.recommended_preset_score) || 0;
            const recIcon = brainState.recommended_preset_icon || '';
            const recLabel = brainState.recommended_preset_label || '';
            const sfBadge = document.getElementById('scalp-fitness-badge');
            const sfScoreEl = document.getElementById('scalp-fitness-score');
            const sfBar = document.getElementById('scalp-fitness-bar');

            if (sfScoreEl) sfScoreEl.textContent = recScore;
            if (sfBar) sfBar.style.width = `${Math.round((recScore / 8) * 100)}%`;
            if (sfBadge) {
                const presetStyle = PRESET_LABELS[recPreset];
                if (recScore >= 6 && presetStyle) {
                    sfBadge.textContent = `${recIcon} ${recLabel} (${recScore}/8)`;
                    const colorClass = presetStyle[1];
                    sfBadge.className = `px-2 py-1 rounded font-mono text-[10px] font-bold ${colorClass} shadow-[0_0_8px_rgba(255,255,255,0.15)] transition-all`;
                } else {
                    sfBadge.textContent = `대기 (${recScore}/8)`;
                    sfBadge.className = 'px-2 py-1 rounded font-mono text-[10px] font-bold text-gray-500 border border-gray-600/50 bg-gray-600/10 transition-all';
                }
            }

            // 프리셋 버튼 그리드에 추천 하이라이트
            document.querySelectorAll('.preset-card').forEach(card => {
                card.classList.remove('ring-2', 'ring-neon-green/50');
            });
            if (recScore >= 6) {
                const recCard = document.querySelector(`.preset-card[data-preset="${recPreset}"]`);
                if (recCard) recCard.classList.add('ring-2', 'ring-neon-green/50');
            }
        } else if (brainState.scalp_fitness !== undefined) {
            // 하위호환: 백엔드 미업데이트 시 기존 scalp_fitness 표시
            const sfScore = parseInt(brainState.scalp_fitness) || 0;
            const sfLabel = brainState.scalp_fitness_label || '대기';
            const sfBadge = document.getElementById('scalp-fitness-badge');
            const sfScoreEl = document.getElementById('scalp-fitness-score');
            const sfBar = document.getElementById('scalp-fitness-bar');
            if (sfScoreEl) sfScoreEl.textContent = sfScore;
            if (sfBar) sfBar.style.width = `${Math.round((sfScore / 8) * 100)}%`;
            if (sfBadge) {
                if (sfScore >= 6) {
                    sfBadge.textContent = `⚡ ${sfLabel} (${sfScore}/8)`;
                    sfBadge.className = 'px-2 py-1 rounded font-mono text-[10px] font-bold text-neon-green border border-neon-green/50 bg-neon-green/10 shadow-[0_0_8px_rgba(0,255,136,0.3)] transition-all';
                } else {
                    sfBadge.textContent = `${sfLabel} (${sfScore}/8)`;
                    sfBadge.className = 'px-2 py-1 rounded font-mono text-[10px] font-bold text-gray-500 border border-gray-600/50 bg-gray-600/10 transition-all';
                }
            }
        }
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncBrain 실패 (엔드포인트: /api/v1/brain):", error);
    }
}


async function toggleBot() {
    const confirmed = confirm('⚠️ 봇 상태 변경\n\n시스템 가동/중지 상태를 전환합니다.\n계속하시겠습니까?');
    if (!confirmed) return;
    try {
        const response = await fetch(`${API_URL}/toggle`, { method: 'POST' });
        const result = await response.json();
        syncBotStatus();
    } catch (error) {
        alert('Toggle target failed: ' + error.message);
    }
}

// 프리셋 라벨 + 스타일 (syncBrain, renderPresets 공용)

async function syncConfig(symbol = null) {
    try {
        const url = symbol ? `${API_URL}/config?symbol=${encodeURIComponent(symbol)}` : `${API_URL}/config`;
        const response = await fetch(url);
        const configs = await response.json();
        // [방어막] 튜닝 모달이 열려있는 상태에서 주기적 syncConfig가 실행되면
        // 사용자가 입력 중인 값을 덮어쓰지 않도록 TUNING_INPUT_MAP 업데이트를 건너뜀
        // (openTuningModal에서 명시적으로 호출된 syncConfig는 symbol이 있으므로 항상 통과)
        const _tuningModal = document.getElementById('tuning-modal');
        const _isTuningOpen = _tuningModal && !_tuningModal.classList.contains('hidden');
        const _isPeriodicCall = (symbol === null);
        const _skipTuningInputs = _isTuningOpen && _isPeriodicCall;
        for (const [key, val] of Object.entries(configs)) {
            if (key === 'risk_per_trade') {
                const tuningInput = document.getElementById('config-risk_per_trade');
                const v = parseFloat(val) * 100;
                if (tuningInput) { tuningInput.value = v.toFixed(1); updateRiskThermometer(v); }
                updateText('risk-val-display', v.toFixed(1) + '%', false);
                // [Phase 18.1] 좌측 패널 리스크 배지 갱신
                const leftRiskBadge = document.getElementById('left-panel-risk-badge');
                if (leftRiskBadge) leftRiskBadge.textContent = v.toFixed(1) + '%';
            } else if (key === 'leverage') {
                const input = document.getElementById('config-leverage');
                if (input) { input.value = parseInt(val); input.dispatchEvent(new Event('input')); }
                updateText('lev-val-display', parseInt(val) + 'x', false);
                // [Phase 18.1] 좌측 패널 레버리지 배지 갱신
                const leftLevBadge = document.getElementById('left-panel-lev-badge');
                if (leftLevBadge) leftLevBadge.textContent = parseInt(val) + 'x';
                // [UI Overhaul] Command Bar 레버리지 미러
                const cmdLevBadge = document.getElementById('cmd-lev-badge');
                if (cmdLevBadge) cmdLevBadge.textContent = parseInt(val) + 'x';
            } else if (key === 'direction_mode') {
                // [Phase 18.1] 방향 모드 버튼 UI 동기화
                _applyDirectionModeUI(String(val).toUpperCase());
            } else if (key === 'symbols') {
                const activeSymbol = Array.isArray(val) && val.length > 0 ? val[0] : null;
                if (activeSymbol) currentSymbol = activeSymbol;
                // 차트 상단 조준경 배지 갱신
                const targetBadge = document.getElementById('hero-target-badge');
                if (targetBadge && activeSymbol) targetBadge.textContent = activeSymbol;
                // [Phase 18.1] 좌측 패널 심볼 배지 갱신
                const leftSymBadge = document.getElementById('left-panel-symbol-badge');
                if (leftSymBadge && activeSymbol) leftSymBadge.textContent = activeSymbol.split(':')[0];
                // [UI Overhaul] Command Bar 심볼 미러
                const cmdSymMirror = document.getElementById('cmd-symbol-mirror');
                if (cmdSymMirror && activeSymbol) cmdSymMirror.textContent = activeSymbol.split(':')[0];
                // [Phase 18.1] 모달 심볼 드롭다운 동기화
                const modalSymSel = document.getElementById('modal-target-symbol');
                if (modalSymSel && activeSymbol) modalSymSel.value = activeSymbol;
                // [Dynamic Symbol] 스캐너가 찾은 코인을 드롭다운에 동적 추가
                if (Array.isArray(val)) _syncSymbolDropdowns(val, activeSymbol);
                // 타겟 그리드 버튼 활성 상태 동기화
                document.querySelectorAll('.target-coin-btn').forEach(btn => {
                    if (btn.dataset.symbol === activeSymbol) {
                        btn.className = 'target-coin-btn text-xs py-2 rounded font-mono font-bold transition-all flex items-center justify-center border border-neon-green text-neon-green bg-neon-green/10';
                    } else {
                        btn.className = 'target-coin-btn text-xs py-2 rounded font-mono font-bold transition-all flex items-center justify-center border border-navy-border/50 bg-navy-900/40 text-gray-500 hover:text-gray-300';
                    }
                });
            } else if (key === 'ENTRY_ORDER_TYPE') {
                const btnMarket = document.getElementById('btn-market-type');
                const btnLimit = document.getElementById('btn-limit-type');
                if (btnMarket && btnLimit) {
                    if (val === 'Smart Limit') {
                        btnLimit.className = 'flex-1 py-1.5 rounded transition bg-neon-green text-navy-900 font-bold';
                        btnMarket.className = 'flex-1 py-1.5 rounded transition text-gray-400 hover:text-white';
                    } else {
                        btnMarket.className = 'flex-1 py-1.5 rounded transition bg-neon-green text-navy-900 font-bold';
                        btnLimit.className = 'flex-1 py-1.5 rounded transition text-gray-400 hover:text-white';
                    }
                }
            } else if (key === 'manual_override_enabled') {
                const toggle = document.getElementById('manual-override-toggle');
                const panel = document.getElementById('manual-override-panel');
                const status = document.getElementById('override-status');
                const enabled = val === true || val === 'true';
                if (toggle) toggle.checked = enabled;
                if (panel) panel.classList.toggle('hidden', !enabled);
                if (status) status.textContent = enabled ? '활성 — 아래 설정값으로 자동매매' : '해제 — 잔고 비율 자동 계산';
                // 서버 상태와 시각적 경계 모드를 항상 일치시킴 (페이지 로드/30초 주기 동기화)
                toggleOverrideVisuals(enabled);
            } else if (key === 'manual_amount') {
                const input = document.getElementById('manual-amount');
                const display = document.getElementById('manual-amount-display');
                if (input) input.value = val;
                if (display) display.textContent = val;
            } else if (key === 'manual_leverage') {
                const input = document.getElementById('manual-leverage');
                const display = document.getElementById('manual-lev-display');
                if (input) input.value = val;
                if (display) display.textContent = val + 'x';
            } else if (key === 'auto_scan_enabled') {
                const toggle = document.getElementById('auto-scan-toggle');
                const track = document.getElementById('auto-scan-track');
                const thumb = document.getElementById('auto-scan-thumb');
                const enabled = val === true || val === 'true';
                if (toggle) toggle.checked = enabled;
                if (track) track.className = `block w-8 h-4 rounded-full border transition-colors ${enabled ? 'bg-neon-green/30 border-neon-green' : 'bg-navy-900 border-navy-border'}`;
                if (thumb) thumb.className = `absolute top-0.5 w-3 h-3 rounded-full transition-all ${enabled ? 'bg-neon-green left-4' : 'bg-gray-500 left-0.5'}`;
            } else if (key === 'spike_auto_switch') {
                const toggle = document.getElementById('spike-auto-switch-toggle');
                const track = document.getElementById('spike-switch-track');
                const thumb = document.getElementById('spike-switch-thumb');
                const enabled = val === true || val === 'true';
                if (toggle) toggle.checked = enabled;
                if (track) track.className = `block w-8 h-4 rounded-full border transition-colors ${enabled ? 'bg-orange-500/30 border-orange-500' : 'bg-navy-900 border-navy-border'}`;
                if (thumb) thumb.className = `absolute top-0.5 w-3 h-3 rounded-full transition-all ${enabled ? 'bg-orange-500 left-4' : 'bg-gray-500 left-0.5'}`;
            } else if (key === 'SHADOW_MODE_ENABLED') {
                const toggle = document.getElementById('shadow-mode-toggle');
                const enabled = val === true || val === 'true';
                if (toggle) toggle.checked = enabled;
                applyShadowModeVisuals(enabled);
            } else if (key in TUNING_INPUT_MAP) {
                // [방어막] 모달 열려있고 주기적 호출이면 입력창 업데이트 건너뜀 (사용자 입력 보호)
                if (_skipTuningInputs) continue;
                const { id, parse } = TUNING_INPUT_MAP[key];
                const input = document.getElementById(id);
                if (input) {
                    input.value = parse(val);
                    // disparity_threshold 슬라이더: 표시 스팬도 동시 갱신 (dead code 제거 후 통합)
                    if (key === 'disparity_threshold') {
                        const span = document.getElementById('val-disparity');
                        if (span) span.textContent = parseFloat(val).toFixed(1) + '%';
                    }
                }
            } else if (['bypass_macro', 'bypass_disparity', 'bypass_indicator', 'exit_only_mode', 'shadow_hunting_enabled', 'auto_preset_enabled'].includes(key)) {
                // [Phase 14.1] Gate Bypass 체크박스 동기화 + [Phase 23] Shadow Hunting + [Phase 25] Adaptive Shield
                const el = document.getElementById(`config-${key}`);
                if (el) el.checked = (val === true || val === 'true');
            } else if (key === 'timeframe') {
                // [Phase TF] 타임프레임 토글 UI 동기화
                window._currentTimeframe = String(val);
                _applyTimeframeToggleUI(String(val));
            }
        }
        updateActiveTuningBadge();
    } catch (error) {
        console.error("[ANTIGRAVITY 디버그] syncConfig 실패 (엔드포인트: /api/v1/config GET):", error);
    }
}

// --- Engine Tuning Modal ---

async function syncSystemHealth() {
    try {
        const res = await fetch(`${API_URL}/system_health`);
        if (!res.ok) return;
        const data = await res.json();

        function applyBadge(dotId, textId, connected, connectedLabel) {
            const dot = document.getElementById(dotId);
            const text = document.getElementById(textId);
            if (!dot || !text) return;
            if (connected) {
                dot.className = 'w-2 h-2 rounded-full bg-neon-green animate-pulse transition-colors duration-500';
                text.textContent = connectedLabel || 'Connected';
                text.className = 'text-[10px] font-mono text-neon-green';
            } else {
                dot.className = 'w-2 h-2 rounded-full bg-red-500 transition-colors duration-500';
                text.textContent = 'Disconnected';
                text.className = 'text-[10px] font-mono text-red-400';
            }
        }

        applyBadge('badge-okx-dot', 'badge-okx-text', data.okx_connected, 'Connected');
        // Telegram: 실제 봇 이름도 표시 (빈 문자열이면 그냥 Connected)
        const tgLabel = data.telegram_connected
            ? (data.telegram_bot_name ? data.telegram_bot_name : 'Connected')
            : 'Disconnected';
        applyBadge('badge-tg-dot', 'badge-tg-text', data.telegram_connected, tgLabel);
        applyBadge('badge-engine-dot', 'badge-engine-text', data.strategy_engine_running, 'Running');

        const ts = document.getElementById('health-last-checked');
        if (ts) ts.textContent = `Last checked: ${new Date().toLocaleTimeString('ko-KR')}`;
    } catch (e) {
        console.warn('System health check failed:', e);
    }
}

// --- 실시간 등락률 뱃지 (Live Tickers via OKX Public API) ---
