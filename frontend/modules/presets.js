const PRESET_LABELS = {
    sniper: ['🎯 스나이퍼', 'text-yellow-300 border-yellow-500/50 bg-yellow-500/10'],
    trend_rider: ['🌊 트렌드라이더', 'text-blue-300 border-blue-500/50 bg-blue-500/10'],
    scalper: ['⚡ 스캘퍼', 'text-neon-green border-neon-green/50 bg-neon-green/10'],
    iron_dome: ['🛡️ 아이언돔', 'text-orange-300 border-orange-500/50 bg-orange-500/10'],
    factory_reset: ['🏭 팩토리', 'text-gray-300 border-gray-500/50 bg-gray-500/10'],
    frenzy: ['🔥 FRENZY', 'text-red-400 border-red-500/50 bg-red-500/10'],
    micro_seed: ['💎 마이크로', 'text-emerald-300 border-emerald-500/50 bg-emerald-500/10'],
    scalp_context: ['📡 스캘프CTX', 'text-cyan-300 border-cyan-500/50 bg-cyan-500/10'],
};

// 원클릭 전술 프리셋 정의 — 5가지 매매 스타일별 10개 파라미터 완전 매핑
const PRESET_CONFIGS = {
    sniper: {
        adx_threshold: 30.0, adx_max: 45.0, chop_threshold: 55.0,
        volume_surge_multiplier: 2.0, fee_margin: 0.002,
        hard_stop_loss_rate: 0.006, trailing_stop_activation: 0.005,
        trailing_stop_rate: 0.003, min_take_profit_rate: 0.008,
        cooldown_losses_trigger: 2, cooldown_duration_sec: 1800,
    },
    trend_rider: {
        adx_threshold: 25.0, adx_max: 60.0, chop_threshold: 58.0,
        volume_surge_multiplier: 1.3, fee_margin: 0.001,
        hard_stop_loss_rate: 0.008, trailing_stop_activation: 0.005,
        trailing_stop_rate: 0.004, min_take_profit_rate: 0.008,
        cooldown_losses_trigger: 4, cooldown_duration_sec: 600,
    },
    scalper: {
        adx_threshold: 20.0, adx_max: 50.0, chop_threshold: 65.0,
        volume_surge_multiplier: 1.2, fee_margin: 0.002,
        hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.002,
        trailing_stop_rate: 0.002, min_take_profit_rate: 0.005,
        cooldown_losses_trigger: 5, cooldown_duration_sec: 300,
    },
    iron_dome: {
        adx_threshold: 28.0, adx_max: 42.0, chop_threshold: 50.0,
        volume_surge_multiplier: 2.5, fee_margin: 0.002,
        hard_stop_loss_rate: 0.004, trailing_stop_activation: 0.004,
        trailing_stop_rate: 0.002, min_take_profit_rate: 0.005,
        cooldown_losses_trigger: 2, cooldown_duration_sec: 3600,
    },
    factory_reset: {
        adx_threshold: 25.0, adx_max: 40.0, chop_threshold: 61.8,
        volume_surge_multiplier: 1.5, fee_margin: 0.0015,
        hard_stop_loss_rate: 0.005, trailing_stop_activation: 0.003,  // [Fix] 15m 기준 0.003 (기존 0.005 오류)
        trailing_stop_rate: 0.002, min_take_profit_rate: 0.01,        // [Fix] 15m 기준 1.0% (기존 0.8% 오류)
        cooldown_losses_trigger: 3, cooldown_duration_sec: 900,
        disparity_threshold: 0.8,  // [Fix] 15m 기준 이격도 (TIMEFRAME_PRESETS 동기화)
    },
    // [Phase 14.3] 초단타 광기 모드 — 모든 방어 관문 해제 + 틱 단위 익절
    // [Phase 18.1] risk_per_trade / leverage 는 시드 보호 설정으로 프리셋에서 완전 제거 (PROTECTED_KEYS)
    frenzy: {
        adx_threshold: 15.0,            // 추세 기준 대폭 완화 (낮은 ADX도 진입 허용)
        chop_threshold: 60.0,           // 횡보 허용치 증가
        volume_surge_multiplier: 1.2,   // 거래량 기준 완화
        disparity_threshold: 3.0,       // 이격도 한계치 3% (UI 슬라이더 % 단위)
        hard_stop_loss_rate: 0.005,     // 0.5% 칼손절 (비율: 0.005)
        trailing_stop_activation: 0.003, // 0.3% 수익 시 트레일링 즉시 ON (비율: 0.003)
        trailing_stop_rate: 0.002,      // 고점 대비 0.2% 낙폭 시 익절 (0.1%→0.2%: 거래소 TP 체결 여유)
        min_take_profit_rate: 0.004,    // 0.4% 최소 익절 가드 (광기 모드 빠른 EXIT)
        cooldown_losses_trigger: 3,     // 3연패 시 쿨다운
        cooldown_duration_sec: 300,     // 5분 휴식 (초단타 특성상 짧게)
        // ── Gate Bypass: 3개 방어 관문 전면 해제 ──
        bypass_macro: 'true',
        bypass_disparity: 'true',
        bypass_indicator: 'true',
    },
    // [Phase 24] 마이크로 시드 — $10~100 소액 계좌 최적화 (R:R 1:2 강제, 저빈도 고확률)
    micro_seed: {
        adx_threshold: 28.0,             // 강한 추세에서만 진입 (노이즈 제거)
        adx_max: 50.0,                   // 강추세 허용 범위 확대
        chop_threshold: 55.0,            // 횡보장 필터 강화 (명확한 추세만)
        volume_surge_multiplier: 1.8,    // 볼륨 확인 강화
        fee_margin: 0.002,               // 수수료 버퍼 확대 (소액 수수료 비중 높음)
        hard_stop_loss_rate: 0.005,      // 0.5% SL 유지 (자본 보호)
        trailing_stop_activation: 0.01,  // 1.0% 수익 후 트레일링 시작 (수익 충분히 성장)
        trailing_stop_rate: 0.005,       // 0.5% 트레일링 거리 (넓은 호흡)
        min_take_profit_rate: 0.01,      // 1.0% 최소 익절 목표 (R:R 1:2 강제)
        cooldown_losses_trigger: 2,      // 2연패 시 쿨다운 (빠른 방어)
        cooldown_duration_sec: 1800,     // 30분 쿨다운 (충분한 냉각)
    },
    // [Scalp Context] 스캘핑 적합 구간 전용 — 이격도+RSI 해제, 매크로 유지, SL 타이트
    scalp_context: {
        adx_threshold: 20.0,             // ADX 하한 낮춤 (더 많은 진입 기회)
        adx_max: 50.0,                   // ADX 상한 확대
        chop_threshold: 61.8,            // CHOP 기본 유지
        volume_surge_multiplier: 1.2,    // 볼륨 기준 완화 (스캘핑 빈도 확보)
        fee_margin: 0.0015,              // 수수료 마진 타이트
        hard_stop_loss_rate: 0.003,      // 0.3% SL (스캘핑 타이트)
        trailing_stop_activation: 0.002, // 0.2% 수익 후 트레일링
        trailing_stop_rate: 0.002,       // 0.2% 트레일링 거리 (0.15%→0.2%: 거래소 TP 체결 여유)
        min_take_profit_rate: 0.005,     // 0.5% 최소 익절 가드 (잔여 50% 보호)
        cooldown_losses_trigger: 3,      // 3연패 쿨다운
        cooldown_duration_sec: 900,      // 15분 쿨다운
        bypass_macro: 'false',           // 거시추세 필터 유지 (안전장치)
        bypass_disparity: 'true',        // 이격도 해제 (빠른 진입)
        bypass_indicator: 'true',        // RSI 해제 (빠른 진입)
    },
};

// ════════════ [Phase TF] 타임프레임별 프리셋 오버레이 ════════════
// 5분봉 전환 시 각 프리셋의 SL/TP/필터 값을 5분봉에 최적화된 값으로 오버라이드
// 15분봉은 PRESET_CONFIGS 원본값 그대로 사용 (기본값)
const PRESET_TF_OVERLAY = {
    '5m': {
        sniper: {
            hard_stop_loss_rate: 0.004, trailing_stop_activation: 0.003,
            trailing_stop_rate: 0.002, min_take_profit_rate: 0.005,
            adx_threshold: 33.0, chop_threshold: 50.0,
            volume_surge_multiplier: 2.2, cooldown_duration_sec: 600,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        trend_rider: {
            hard_stop_loss_rate: 0.005, trailing_stop_activation: 0.003,
            trailing_stop_rate: 0.0025, min_take_profit_rate: 0.005,
            adx_threshold: 28.0, chop_threshold: 53.0,
            volume_surge_multiplier: 1.5, cooldown_duration_sec: 300,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        scalper: {
            hard_stop_loss_rate: 0.002, trailing_stop_activation: 0.0015,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.003,
            adx_threshold: 22.0, chop_threshold: 60.0,
            volume_surge_multiplier: 1.4, cooldown_duration_sec: 180,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        iron_dome: {
            hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.003,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.004,
            adx_threshold: 30.0, chop_threshold: 48.0,
            volume_surge_multiplier: 2.8, cooldown_duration_sec: 1800,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        factory_reset: {
            hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.002,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.005,
            adx_threshold: 28.0, chop_threshold: 55.0,
            volume_surge_multiplier: 1.8, cooldown_duration_sec: 300,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        frenzy: {
            hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.002,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.003,
            chop_threshold: 55.0, cooldown_duration_sec: 180,
            // bypass_disparity: 'true' 상속 → disparity_threshold 무효 (오버라이드 불필요)
        },
        micro_seed: {
            hard_stop_loss_rate: 0.003, trailing_stop_activation: 0.005,
            trailing_stop_rate: 0.003, min_take_profit_rate: 0.006,
            adx_threshold: 30.0, chop_threshold: 50.0,
            volume_surge_multiplier: 2.0, cooldown_duration_sec: 900,
            disparity_threshold: 1.0,  // [Fix] 5m: 15m(0.8%) 대비 노이즈 허용치 확대
        },
        scalp_context: {
            hard_stop_loss_rate: 0.002, trailing_stop_activation: 0.0015,
            trailing_stop_rate: 0.0015, min_take_profit_rate: 0.003,
            adx_threshold: 22.0, volume_surge_multiplier: 1.4,
            cooldown_duration_sec: 300,
            // bypass_disparity: 'true' 상속 → disparity_threshold 무효 (오버라이드 불필요)
        },
    },
};

/**
 * _getEffectivePreset(presetName) — 현재 타임프레임에 맞는 프리셋 값 반환
 * 15m: PRESET_CONFIGS 원본 그대로
 * 5m: PRESET_CONFIGS + PRESET_TF_OVERLAY['5m'] 머지 (오버레이 우선)
 */
function _getEffectivePreset(presetName) {
    const base = PRESET_CONFIGS[presetName];
    if (!base) return null;
    const tf = window._currentTimeframe || '15m';
    const overlay = (PRESET_TF_OVERLAY[tf] || {})[presetName];
    if (!overlay) return { ...base };
    return { ...base, ...overlay };
}

// 리스크 온도계 — risk_per_trade 입력값에 따른 실시간 위험도 안내
function updateRiskThermometer(value) {
    const el = document.getElementById('risk-thermometer-text');
    if (!el) return;
    const v = parseFloat(value);
    if (isNaN(v) || String(value).trim() === '') {
        el.className = 'text-[10px] font-mono mt-1.5 transition-colors duration-300 text-gray-500';
        el.textContent = '리스크 비율을 입력하면 AI가 위험도를 분석합니다.';
        return;
    }
    if (v <= 2) {
        el.className = 'text-[10px] font-mono mt-1.5 transition-colors duration-300 text-neon-green';
        el.textContent = '🛡️ 방어력 극대화 모드. 안전한 복리 우상향을 지향합니다.';
    } else if (v <= 5) {
        el.className = 'text-[10px] font-mono mt-1.5 transition-colors duration-300 text-yellow-400';
        el.textContent = '⚖️ 표준 밸런스 모드. 적절한 수익과 리스크를 동반합니다.';
    } else {
        el.className = 'text-[10px] font-mono mt-1.5 transition-colors duration-300 text-orange-500 font-bold animate-pulse';
        el.textContent = '⚠️ 초고위험 세팅! 단 1번의 손절로 시드의 큰 비중이 증발할 수 있습니다.';
    }
}

// [Phase 18.1] 프리셋이 절대 변경해서는 안 되는 시드 보호 설정 키 집합
const PRESET_PROTECTED_KEYS = new Set(['risk_per_trade', 'leverage']);

// 튜닝 파라미터 맵 — syncConfig() 와 saveTuningConfig() 공유 단일 진실 소스
const TUNING_INPUT_MAP = {
    'leverage': { id: 'config-leverage', parse: parseInt },  // [Phase 18.1] 모달 Section 1으로 이관
    'risk_per_trade': { id: 'config-risk_per_trade', parse: v => parseFloat(v) / 100 },
    'adx_threshold': { id: 'tuning-adx-threshold', parse: parseFloat },
    'adx_max': { id: 'tuning-adx-max', parse: parseFloat },
    'chop_threshold': { id: 'tuning-chop-threshold', parse: parseFloat },
    'volume_surge_multiplier': { id: 'tuning-volume-surge', parse: parseFloat },
    'fee_margin': { id: 'tuning-fee-margin', parse: parseFloat },
    'hard_stop_loss_rate': { id: 'tuning-hard-stop-loss', parse: parseFloat },
    'trailing_stop_activation': { id: 'tuning-trailing-activation', parse: parseFloat },
    'trailing_stop_rate': { id: 'tuning-trailing-rate', parse: parseFloat },
    'cooldown_losses_trigger': { id: 'tuning-cooldown-losses', parse: parseInt },
    'cooldown_duration_sec': { id: 'tuning-cooldown-duration', parse: parseInt },
    'disparity_threshold': { id: 'config-disparity_threshold', parse: parseFloat },  // [Phase 14.2] DB: % 단위 저장
    'min_take_profit_rate': { id: 'tuning-min-tp-rate', parse: parseFloat },  // [Phase 24] 최소 익절 목표율
};

// --- Config Sync ---
// [Phase 18.1] symbol 파라미터 지원: 심볼 전용 설정을 로드하여 모달 입력창 일괄 갱신

async function applyPreset(presetName) {
    const config = _getEffectivePreset(presetName);
    if (!config) return;

    // [BUGFIX] 프리셋 저장 전 보호 키(leverage, risk_per_trade)를 백엔드에서 강제 동기화
    // openTuningModal()의 syncConfig()가 완료되기 전 프리셋 클릭 시 슬라이더가 HTML 기본값
    // (value="1")인 채로 saveTuningConfig()가 실행되어 leverage=1이 저장되는 레이스 컨디션 방지
    await syncConfig(currentSymbol);

    // 1. 숫자/범위 인풋: TUNING_INPUT_MAP 기준으로 ID 해석 → 값 주입 + 애니메이션 + input 이벤트
    // [Phase 18.1] PRESET_PROTECTED_KEYS(risk_per_trade, leverage)는 절대 건드리지 않음
    for (const [key, { id }] of Object.entries(TUNING_INPUT_MAP)) {
        if (PRESET_PROTECTED_KEYS.has(key)) continue;  // 시드 보호 설정 격리
        if (!(key in config)) continue;
        const input = document.getElementById(id);
        if (!input) continue;
        input.value = config[key];
        // reflow trick: 연속 클릭 시에도 애니메이션 재시작 보장
        input.classList.remove('preset-flash');
        void input.offsetWidth;
        input.classList.add('preset-flash');
        // oninput 연결 UI(온도계, val-disparity 스팬 등) 즉각 갱신
        input.dispatchEvent(new Event('input'));
    }

    // 2. [Phase 14.1/14.3] Gate Bypass 체크박스 동기화
    // [Bugfix] bypass 3개는 프리셋에 명시 없으면 항상 false로 강제 초기화
    // → 이전에 수동으로 켠 bypass가 다른 프리셋 적용 후에도 남아있는 문제 방지 (5m/15m 공통)
    const _BYPASS_KEYS = new Set(['bypass_macro', 'bypass_disparity', 'bypass_indicator']);
    for (const bkey of ['bypass_macro', 'bypass_disparity', 'bypass_indicator', 'exit_only_mode', 'shadow_hunting_enabled', 'auto_preset_enabled']) {
        const el = document.getElementById(`config-${bkey}`);
        if (!el) continue;
        if (bkey in config) {
            // 프리셋에 명시된 값 적용
            el.checked = (config[bkey] === 'true' || config[bkey] === true);
        } else if (_BYPASS_KEYS.has(bkey)) {
            // bypass 3개: 프리셋에 없으면 항상 false 강제 초기화 (방어코드)
            el.checked = false;
        } else {
            // exit_only_mode, shadow_hunting_enabled, auto_preset_enabled: 프리셋에 없으면 건드리지 않음
            continue;
        }
        el.dispatchEvent(new Event('input'));
    }

    // 3. 값 주입 직후 서버에 즉시 일괄 저장
    await saveTuningConfig();
    // 4. 프리셋 적용 직후 뱃지 즉시 갱신
    updateActiveTuningBadge();
}

// ── 섹션 1 접기/펴기 토글 ──
