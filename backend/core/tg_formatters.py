"""
core/tg_formatters.py — 텔레그램 HTML 메시지 포맷터 (순수 함수, 사이드 이펙트 없음)
"""
from database import get_config

_TG_LINE = "─" * 24


def _sym_short(symbol: str) -> str:
    return symbol.split(':')[0]


def _tg_entry(symbol: str, direction: str, price: float, amount: int, leverage: int, payload: dict = None, is_test: bool = False) -> str:
    d_emoji = "📈" if direction == "LONG" else "📉"
    header = "👻 <b>PAPER TRADING</b>  |  가상 진입" if is_test else "⚡ <b>ANTIGRAVITY (LIVE)</b>  |  실전 진입"
    _tf_label = str(get_config('timeframe') or '15m')
    msg = (
        f"{header}\n"
        f"{_TG_LINE}\n"
        f"{d_emoji} <b>{direction}</b>  ·  <code>{_sym_short(symbol)}</code>  ·  ⏱️ <code>{_tf_label}</code>\n"
        f"{_TG_LINE}\n"
        f"가격   │  <code>${price:,.2f}</code>\n"
        f"수량   │  <code>{amount}계약  ·  {leverage}x</code>\n"
    )
    if payload:
        _ema = str(payload.get('ema_status', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
        _vol = str(payload.get('vol_multiplier', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
        _atr = str(payload.get('atr_sl_margin', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
        msg += (
            f"{_TG_LINE}\n"
            f"[진입 근거 데이터]\n"
            f"📈 1h 추세: {_ema}\n"
            f"🔥 거래량 폭발: {_vol}\n"
            f"🛡️ ATR 방어선: {_atr}\n"
        )
        gates = payload.get('gates')
        if gates and isinstance(gates, dict):
            _gate_icons = {
                'ADX': '📊', 'CHOP': '🔀', 'Volume': '🔥',
                'Disparity': '📏', 'Macro': '🌍', 'RSI': '📉', 'MACD': '📈',
            }
            msg += f"{_TG_LINE}\n"
            msg += f"[7-Gate Scoreboard]\n"
            for gate_name, gate_val in gates.items():
                _icon = _gate_icons.get(gate_name, '✅')
                _safe_val = str(gate_val).replace('<', '&lt;').replace('>', '&gt;')
                msg += f"{_icon} {gate_name}: {_safe_val}\n"
    msg += f"{_TG_LINE}"
    return msg


def _tg_pending(symbol: str, direction: str, price: float, amount: int, leverage: int, is_test: bool = False) -> str:
    header = "👻 <b>PAPER TRADING</b>  |  가상 지정가" if is_test else "⚡ <b>ANTIGRAVITY (LIVE)</b>  |  실전 지정가"
    return (
        f"{header}\n"
        f"{_TG_LINE}\n"
        f"⏳ <b>PENDING {direction}</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"목표가 │  <code>${price:,.2f}</code>\n"
        f"수량   │  <code>{amount}계약  ·  {leverage}x</code>\n"
        f"상태   │  5분 내 미체결 시 자동 취소\n"
        f"{_TG_LINE}"
    )


def _tg_exit(symbol: str, direction: str, avg_price: float, gross_pnl: float, fee: float, net_pnl: float, pnl_pct: float, reason: str, is_test: bool = False) -> str:
    is_profit = pnl_pct >= 0
    result_emoji = "✅" if is_profit else "🔴"
    result_label = "익절" if is_profit else "손절"
    sign_net = "+" if net_pnl >= 0 else ""
    sign_gross = "+" if gross_pnl >= 0 else ""
    _reason_ko = {"STOP_LOSS": "하드 손절", "TRAILING_STOP_EXIT": "트레일링 익절"}
    reason_ko = _reason_ko.get(reason, reason)
    header = "👻 <b>PAPER TRADING</b>  |  가상 청산" if is_test else "⚡ <b>ANTIGRAVITY (LIVE)</b>  |  실전 청산"
    _tf_label = str(get_config('timeframe') or '15m')
    return (
        f"{header}\n"
        f"{_TG_LINE}\n"
        f"{result_emoji} <b>{direction} {result_label}</b>  ·  <code>{_sym_short(symbol)}</code>  ·  ⏱️ <code>{_tf_label}</code>\n"
        f"{_TG_LINE}\n"
        f"청산가  │  <code>${avg_price:,.2f}</code>\n"
        f"총수익  │  <code>{sign_gross}{gross_pnl:.4f} USDT</code>\n"
        f"수수료  │  <code>{fee:.4f} USDT</code>\n"
        f"순수익  │  <b><code>{sign_net}{net_pnl:.4f} USDT</code></b>\n"
        f"수익률  │  <b><code>{sign_net}{pnl_pct:.2f}%</code></b>\n"
        f"청산 이유 │  <code>{reason_ko}</code>\n"
        f"{_TG_LINE}"
    )


def _tg_manual_exit(symbol: str, direction: str, avg_price: float, gross_pnl: float, fee: float, net_pnl: float, pnl_pct: float) -> str:
    is_profit = pnl_pct >= 0
    result_emoji = "✅" if is_profit else "🔴"
    sign_net = "+" if net_pnl >= 0 else ""
    sign_gross = "+" if gross_pnl >= 0 else ""
    return (
        f"🖐️ <b>ANTIGRAVITY (LIVE)</b>  |  수동 청산 감지\n"
        f"{_TG_LINE}\n"
        f"{result_emoji} <b>{direction} 수동 청산</b>  ·  <code>{_sym_short(symbol)}</code>\n"
        f"{_TG_LINE}\n"
        f"청산가  │  <code>${avg_price:,.2f}</code>\n"
        f"총수익  │  <code>{sign_gross}{gross_pnl:.4f} USDT</code>\n"
        f"수수료  │  <code>{fee:.4f} USDT</code>\n"
        f"순수익  │  <b><code>{sign_net}{net_pnl:.4f} USDT</code></b>\n"
        f"수익률  │  <b><code>{sign_net}{pnl_pct:.2f}%</code></b>\n"
        f"{_TG_LINE}"
    )


def _tg_scanner(symbols: list) -> str:
    _sym_list = '\n'.join([f"  • <code>{s}</code>" for s in symbols])
    return (
        f"🔭 <b>ANTIGRAVITY</b>  |  오토 스캐너\n"
        f"{_TG_LINE}\n"
        f"📊 고볼륨 탐지 완료\n"
        f"{_TG_LINE}\n"
        f"{_sym_list}\n"
        f"{_TG_LINE}"
    )


def _tg_volume_spike(spikes: list) -> str:
    _lines = '\n'.join([
        f"  • <code>{s.get('symbol','?')}</code>  {s.get('ratio','?')}x  (${s.get('volume_usd',0)/1e6:.1f}M)"
        for s in spikes[:3]
    ])
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  Volume Spike\n"
        f"{_TG_LINE}\n"
        f"🔥 거래량 급등 감지\n"
        f"{_TG_LINE}\n"
        f"{_lines}\n"
        f"{_TG_LINE}"
    )


def _tg_margin_guard(symbol: str, current_lev: int, rec_lev: int, balance: float, margin_needed: float) -> str:
    return (
        f"⚡ <b>ANTIGRAVITY</b>  |  증거금 경고\n"
        f"{_TG_LINE}\n"
        f"⚠️ <b>Margin Guard 발동</b>\n"
        f"{_TG_LINE}\n"
        f"심볼      │  <code>{_sym_short(symbol)}</code>\n"
        f"현재 레버 │  <code>{current_lev}x</code>\n"
        f"추천 레버 │  <b><code>{rec_lev}x</code></b>\n"
        f"잔고      │  <code>${balance:.2f}</code>\n"
        f"필요 증거금 │  <code>${margin_needed:.2f}</code>\n"
        f"{_TG_LINE}"
    )


def _tg_circuit_breaker(symbol: str, balance: float) -> str:
    return (
        f"🚨 <b>ANTIGRAVITY</b>  |  Circuit Breaker\n"
        f"{_TG_LINE}\n"
        f"🔴 <b>서킷 브레이커 발동</b>\n"
        f"{_TG_LINE}\n"
        f"심볼  │  <code>{_sym_short(symbol)}</code>\n"
        f"잔고  │  <code>${balance:.2f}</code>\n"
        f"조치  │  매매 일시 중단\n"
        f"{_TG_LINE}"
    )


def _tg_system(is_running: bool) -> str:
    if is_running:
        return (
            f"🟢 <b>ANTIGRAVITY</b>  |  시스템 시작\n"
            f"{_TG_LINE}\n"
            f"자동매매 루프가 가동되었습니다.\n"
            f"{_TG_LINE}"
        )
    else:
        return (
            f"🛑 <b>ANTIGRAVITY</b>  |  시스템 중지\n"
            f"{_TG_LINE}\n"
            f"자동매매 루프가 중지되었습니다.\n"
            f"{_TG_LINE}"
        )
