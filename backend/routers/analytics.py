"""
routers/analytics.py — 통계, 히스토리, 고급 분석, CSV 내보내기
Routes: /stats, /history_stats, /stats/advanced, /export_csv
"""
import asyncio
import csv
import io
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter
from fastapi.responses import Response as FastAPIResponse

from database import get_trades, get_config, save_trade, trade_exists_by_okx_id
from logger import get_logger
from core.state import bot_global_state, _g
from core.background import _sync_okx_trades

router = APIRouter()
logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# POST /api/v1/sync_trades — OKX 매매 기록 수동 싱크
# ════════════════════════════════════════════════════════════════════════════
@router.post("/sync_trades")
async def trigger_trade_sync():
    """OKX 매매 기록 수동 싱크 트리거 — 대시보드 버튼용"""
    if not _g["engine"] or not _g["engine"].exchange:
        return {"success": False, "error": "OKX 연결 안됨", "synced": 0}
    try:
        count = await _sync_okx_trades(_g["engine"])
        return {"success": True, "synced": count, "message": f"{count}건 싱크 완료"}
    except Exception as e:
        logger.warning(f"[OKX Sync] 수동 트리거 실패: {e}")
        return {"success": False, "error": str(e), "synced": 0}


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/stats — 성과 통계 (KST 기준 일일 지표 포함)
# ════════════════════════════════════════════════════════════════════════════
@router.get("/stats")
async def fetch_statistics():
    """성과 분석 통계 — KST 기준 오늘 일일 지표 포함"""
    trades = get_trades(limit=1000)

    _season_start = get_config('season_start_date')
    if _season_start:
        trades = [t for t in trades if (t.get('created_at') or '') >= str(_season_start)]

    total_trades = len(trades)
    win_trades = len([t for t in trades if (t.get('pnl_percent') or 0) > 0])
    loss_trades = total_trades - win_trades
    win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0

    total_pnl_percent = sum([(t.get('pnl_percent') or 0) for t in trades])

    max_drawdown = 0
    if trades:
        sorted_trades = list(reversed(trades))
        first_pnl = sorted_trades[0].get('pnl') or 0
        initial_balance = max(1.0, abs(first_pnl) * 100 if first_pnl else 100.0)
        running_balance = initial_balance
        running_max = initial_balance
        for trade in sorted_trades:
            pnl = trade.get('pnl') or 0
            running_balance += pnl
            running_max = max(running_max, running_balance)
            drawdown = (running_max - running_balance) / running_max if running_max > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

    sharpe_ratio = 0
    if total_trades > 1:
        import statistics
        pnl_percent_list = [(t.get('pnl_percent') or 0) for t in trades]
        mean_pnl = statistics.mean(pnl_percent_list)
        std_pnl = statistics.stdev(pnl_percent_list) if len(pnl_percent_list) > 1 else 1
        if std_pnl > 0:
            sharpe_ratio = mean_pnl / std_pnl

    KST = timezone(timedelta(hours=9))
    today_kst = datetime.now(KST).strftime("%Y-%m-%d")

    def _parse_kst_date(created_at_str):
        if not created_at_str:
            return ""
        try:
            dt = datetime.fromisoformat(str(created_at_str).replace(' ', 'T'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(KST).strftime("%Y-%m-%d")
        except Exception:
            return ""

    today_trades_list = [t for t in trades if _parse_kst_date(t.get('created_at')) == today_kst]
    daily_trades = len(today_trades_list)
    daily_wins = len([t for t in today_trades_list if (t.get('pnl_percent') or 0) > 0])
    daily_net_pnl = sum((t.get('pnl') or 0) for t in today_trades_list)
    total_net_pnl = sum((t.get('pnl') or 0) for t in trades)

    avg_net_pnl = total_net_pnl / total_trades if total_trades > 0 else 0
    pnl_values = [(t.get('pnl') or 0) for t in trades]
    best_trade = max(pnl_values) if pnl_values else 0
    worst_trade = min(pnl_values) if pnl_values else 0

    streak_count = 0
    streak_type = 'W'
    if trades:
        first_pct = (trades[0].get('pnl_percent') or 0)
        streak_type = 'W' if first_pct > 0 else 'L'
        for t in trades:
            pct = t.get('pnl_percent') or 0
            if (streak_type == 'W' and pct > 0) or (streak_type == 'L' and pct <= 0):
                streak_count += 1
            else:
                break

    return {
        'total_trades': total_trades,
        'win_trades': win_trades,
        'loss_trades': loss_trades,
        'win_rate': round(win_rate, 2),
        'total_pnl_percent': round(total_pnl_percent, 2),
        'max_drawdown': round(max_drawdown * 100, 2),
        'sharpe_ratio': round(sharpe_ratio, 2),
        'daily_net_pnl': round(daily_net_pnl, 4),
        'daily_trades': daily_trades,
        'daily_wins': daily_wins,
        'total_net_pnl': round(total_net_pnl, 4),
        'avg_net_pnl': round(avg_net_pnl, 4),
        'best_trade': round(best_trade, 4),
        'worst_trade': round(worst_trade, 4),
        'streak_count': streak_count,
        'streak_type': streak_type,
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/history_stats — 일별/월별 누적 통계
# ════════════════════════════════════════════════════════════════════════════
@router.get("/history_stats")
async def fetch_history_stats():
    """KST 기준 일별/월별 누적 거래 통계"""
    trades = get_trades(limit=99999)

    _season_start = get_config('season_start_date')
    if _season_start:
        trades = [t for t in trades if (t.get('created_at') or '') >= str(_season_start)]
    KST = timezone(timedelta(hours=9))

    daily_map = defaultdict(lambda: {'total': 0, 'wins': 0, 'gross_pnl': 0.0, 'net_pnl': 0.0})
    monthly_map = defaultdict(lambda: {'total': 0, 'wins': 0, 'gross_pnl': 0.0, 'net_pnl': 0.0})

    for t in trades:
        created_at_str = t.get('created_at')
        if not created_at_str:
            continue
        try:
            dt = datetime.fromisoformat(str(created_at_str).replace(' ', 'T'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_kst = dt.astimezone(KST)
        except Exception:
            continue

        date_key = dt_kst.strftime("%Y-%m-%d")
        month_key = dt_kst.strftime("%Y-%m")
        net_pnl = t.get('pnl') or 0
        gross_pnl = t.get('gross_pnl') or 0
        is_win = net_pnl > 0

        for key, mapping in [(date_key, daily_map), (month_key, monthly_map)]:
            mapping[key]['total'] += 1
            mapping[key]['gross_pnl'] += gross_pnl
            mapping[key]['net_pnl'] += net_pnl
            if is_win:
                mapping[key]['wins'] += 1

    def _build_sorted_list(mapping):
        result = []
        for key, data in mapping.items():
            total = data['total']
            win_rate = round(data['wins'] / total * 100, 2) if total > 0 else 0.0
            result.append({
                'date': key,
                'total_trades': total,
                'win_rate': win_rate,
                'gross_pnl': round(data['gross_pnl'], 4),
                'net_pnl': round(data['net_pnl'], 4),
            })
        return sorted(result, key=lambda x: x['date'], reverse=True)

    return {
        'daily': _build_sorted_list(daily_map),
        'monthly': _build_sorted_list(monthly_map),
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/stats/advanced — 심볼별/시간대별/방향별 고급 분석
# ════════════════════════════════════════════════════════════════════════════
@router.get("/stats/advanced")
async def fetch_advanced_stats():
    """심볼별 / 시간대별 / 방향별 / 요일별 분석 (READ-ONLY)"""
    trades = get_trades(limit=99999)

    _season_start = get_config('season_start_date')
    if _season_start:
        trades = [t for t in trades if (t.get('created_at') or '') >= str(_season_start)]

    KST = timezone(timedelta(hours=9))

    symbol_map = defaultdict(lambda: {
        'total': 0, 'wins': 0, 'net_pnl': 0.0, 'gross_pnl': 0.0,
        'total_hold_sec': 0.0, 'hold_count': 0,
    })
    hour_map = defaultdict(lambda: {'total': 0, 'wins': 0, 'net_pnl': 0.0})
    dir_map = defaultdict(lambda: {'total': 0, 'wins': 0, 'net_pnl': 0.0})
    weekday_map = defaultdict(lambda: {'total': 0, 'wins': 0, 'net_pnl': 0.0})
    _weekday_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    for t in trades:
        sym = (t.get('symbol') or 'UNKNOWN').split(':')[0]
        net_pnl = t.get('pnl') or 0
        gross_pnl = t.get('gross_pnl') or 0
        is_win = net_pnl > 0
        direction = (t.get('position_type') or 'UNKNOWN').upper()

        symbol_map[sym]['total'] += 1
        symbol_map[sym]['net_pnl'] += net_pnl
        symbol_map[sym]['gross_pnl'] += gross_pnl
        if is_win:
            symbol_map[sym]['wins'] += 1

        entry_time_str = t.get('entry_time')
        exit_time_str = t.get('exit_time')
        if entry_time_str and exit_time_str:
            try:
                et = datetime.fromisoformat(str(entry_time_str).replace(' ', 'T'))
                xt = datetime.fromisoformat(str(exit_time_str).replace(' ', 'T'))
                hold_sec = max(0, (xt - et).total_seconds())
                symbol_map[sym]['total_hold_sec'] += hold_sec
                symbol_map[sym]['hold_count'] += 1
            except Exception:
                pass

        created_at_str = t.get('created_at')
        dt_kst = None
        if created_at_str:
            try:
                dt = datetime.fromisoformat(str(created_at_str).replace(' ', 'T'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_kst = dt.astimezone(KST)
            except Exception:
                pass

        if dt_kst:
            hour_key = dt_kst.hour
            hour_map[hour_key]['total'] += 1
            hour_map[hour_key]['net_pnl'] += net_pnl
            if is_win:
                hour_map[hour_key]['wins'] += 1

            wd_key = _weekday_names[dt_kst.weekday()]
            weekday_map[wd_key]['total'] += 1
            weekday_map[wd_key]['net_pnl'] += net_pnl
            if is_win:
                weekday_map[wd_key]['wins'] += 1

        if direction in ('LONG', 'SHORT'):
            dir_map[direction]['total'] += 1
            dir_map[direction]['net_pnl'] += net_pnl
            if is_win:
                dir_map[direction]['wins'] += 1

    def _wr(wins, total):
        return round(wins / total * 100, 1) if total > 0 else 0.0

    by_symbol = []
    for sym, d in symbol_map.items():
        avg_hold_min = round(d['total_hold_sec'] / d['hold_count'] / 60, 1) if d['hold_count'] > 0 else 0.0
        by_symbol.append({
            'symbol': sym,
            'total': d['total'],
            'wins': d['wins'],
            'losses': d['total'] - d['wins'],
            'win_rate': _wr(d['wins'], d['total']),
            'net_pnl': round(d['net_pnl'], 4),
            'avg_hold_min': avg_hold_min,
        })
    by_symbol.sort(key=lambda x: x['net_pnl'], reverse=True)

    by_hour = []
    for h in range(24):
        d = hour_map[h]
        by_hour.append({
            'hour': h,
            'label': f"{h:02d}:00",
            'total': d['total'],
            'wins': d['wins'],
            'win_rate': _wr(d['wins'], d['total']),
            'net_pnl': round(d['net_pnl'], 4),
        })

    by_direction = []
    for dir_name in ['LONG', 'SHORT']:
        d = dir_map[dir_name]
        by_direction.append({
            'direction': dir_name,
            'total': d['total'],
            'wins': d['wins'],
            'win_rate': _wr(d['wins'], d['total']),
            'net_pnl': round(d['net_pnl'], 4),
        })

    by_weekday = []
    for wd in _weekday_names:
        d = weekday_map[wd]
        by_weekday.append({
            'day': wd,
            'total': d['total'],
            'wins': d['wins'],
            'win_rate': _wr(d['wins'], d['total']),
            'net_pnl': round(d['net_pnl'], 4),
        })

    return {
        'by_symbol': by_symbol,
        'by_hour': by_hour,
        'by_direction': by_direction,
        'by_weekday': by_weekday,
        'total_analyzed': len(trades),
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/export_csv — 거래 내역 CSV 다운로드
# ════════════════════════════════════════════════════════════════════════════
@router.get("/export_csv")
async def export_csv():
    """전체 거래 내역 CSV 파일 다운로드"""
    trades = get_trades(limit=99999)
    fieldnames = [
        'ID', 'Symbol', 'Position', 'Entry_Price', 'Exit_Price',
        'Amount', 'Leverage', 'Gross_PnL', 'Fee', 'Net_PnL',
        'Entry_Time', 'Exit_Time', 'Exit_Reason',
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for t in trades:
        writer.writerow({
            'ID':          t.get('id', ''),
            'Symbol':      t.get('symbol', ''),
            'Position':    t.get('position_type', ''),
            'Entry_Price': t.get('entry_price', ''),
            'Exit_Price':  t.get('exit_price', ''),
            'Amount':      t.get('amount', ''),
            'Leverage':    t.get('leverage', ''),
            'Gross_PnL':   t.get('gross_pnl', ''),
            'Fee':         t.get('fee', ''),
            'Net_PnL':     t.get('pnl', ''),
            'Entry_Time':  t.get('entry_time', ''),
            'Exit_Time':   t.get('exit_time', ''),
            'Exit_Reason': t.get('exit_reason', ''),
        })
    csv_bytes = output.getvalue().encode('utf-8-sig')
    output.close()
    return FastAPIResponse(
        content=csv_bytes,
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="antigravity_trades.csv"'},
    )
