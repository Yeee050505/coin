
"""Financial data API - parallel multi-source (no cascade, no degradation chain)"""
import asyncio
import concurrent.futures
import logging
import time
from typing import Any, Dict, List
from datetime import datetime, timedelta
import yfinance as yf

_CACHE: Dict[str, tuple[Any, float]] = {}
_CACHE_TTL = 300
_AK_SEM = asyncio.Semaphore(3)
_AK_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="akshare")

async def _cached(key: str, fn, *args, **kwargs):
    now = time.time()
    if key in _CACHE and now - _CACHE[key][1] < _CACHE_TTL:
        return _CACHE[key][0]
    result = await fn(*args, **kwargs)
    _CACHE[key] = (result, now)
    return result

logger = logging.getLogger(__name__)

_TIMEOUT = 8
_AK_DELAY = 0.3      # tiny stagger so they don't all hit at the exact same ms
_last_ak_call = 0.0

# Helpers - throttling and thread execution

import socket as _socket

import concurrent.futures as _cf
import time as _time

_single_executor = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="fin_sync")

async def _run_in_thread(fn, *args, **kwargs):
    def _wrapped():
        _log_fin(f"_wrapped start: {fn.__name__}")
        old_to = _socket.getdefaulttimeout()
        _socket.setdefaulttimeout(6)
        try:
            result = fn(*args, **kwargs)
            _log_fin(f"_wrapped done: {fn.__name__}")
            return result
        except Exception as e:
            _log_fin(f"_wrapped error: {fn.__name__}: {e}")
            raise
        finally:
            _socket.setdefaulttimeout(old_to)
    loop = asyncio.get_event_loop()
    _log_fin(f"run_in_thread submitting: {fn.__name__}")
    result = await loop.run_in_executor(_single_executor, _wrapped)
    _log_fin(f"run_in_thread returned: {fn.__name__}")
    return result

def _log_fin(msg):
    import builtins
    with builtins.open("D:/py/fastapi_demo/coin/backend/logs/trace_fin.txt", "a", 1) as f:
        f.write(f"[{_time.time():.0f}] {msg}\n")

async def _ak_throttle():
    global _last_ak_call
    elapsed = time.time() - _last_ak_call
    if elapsed < _AK_DELAY:
        await asyncio.sleep(_AK_DELAY - elapsed)
    _last_ak_call = time.time()


def _is_a_share(code: str) -> bool:
    return code.isdigit() and len(code) == 6

def _to_sina_code(code: str) -> str:
    return ('sz' if code.startswith(('0','2','3')) else 'sh') + code

def _to_tx_code(code: str) -> str:
    return _to_sina_code(code)


# yfinance (US / international) data sources

async def _yf_overview(code: str) -> Dict[str, Any]:
    try:
        t = yf.Ticker(code)
        info = await _run_in_thread(lambda: t.info or {})
        keys = ['marketCap','trailingPE','forwardPE','priceToBook',
                'returnOnEquity','revenueGrowth','profitMargins',
                'debtToEquity','beta','dividendYield']
        data = {k: info.get(k) for k in keys if k in info}
        price = info.get('currentPrice') or info.get('regularMarketPrice') or 0
        return {'status': 'success', 'stock': code, 'price': price,
                'data': data, 'name': info.get('longName', code), 'source': 'yfinance'}
    except Exception as e:
        return {'status': 'error', 'stock': code, 'message': str(e)[:200], 'source': 'yfinance'}

async def _yf_financials(code: str, stmt_type: str, years: int = 3) -> Dict[str, Any]:
    try:
        t = yf.Ticker(code)
        attr_map = {'income': 'financials', 'balance': 'balance_sheet', 'cashflow': 'cashflow'}
        attr = attr_map.get(stmt_type, 'financials')
        df = await _run_in_thread(lambda: getattr(t, attr))
        if df is None or df.empty:
            return {'status': 'empty', 'stock': code, 'source': 'yfinance'}
        result = {}
        for col in df.columns[:years]:
            yr = str(col.year)
            result[yr] = {str(k): round(float(v), 2) for k, v in df[col].head(10).items() if isinstance(v, (int, float))}
        return {'status': 'success', 'stock': code, 'indicator': stmt_type, 'data': result, 'source': 'yfinance'}
    except Exception as e:
        return {'status': 'error', 'stock': code, 'message': str(e)[:200], 'source': 'yfinance'}


# 东方财富 A股行情数据

async def _em_stock_history(code: str, days: int = 90) -> Dict[str, Any]:
    import akshare as ak
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    await _ak_throttle()
    df = await _run_in_thread(ak.stock_zh_a_hist, symbol=code, period='daily',
                               start_date=start, end_date=end, adjust='qfq')
    if df.empty:
        raise ValueError("empty response")
    latest = df.iloc[-1]
    return {
        'status': 'success', 'stock': code,
        'price': float(latest.get('收盘', 0)),
        'volume': int(latest.get('成交量', 0)),
        'history': df.tail(30)[['日期','开盘','收盘','最高','最低','成交量','成交额']].to_dict(orient='records'),
        'source': 'eastmoney'
    }


# 新浪 A股行情数据

async def _sina_stock_history(code: str, days: int = 90) -> Dict[str, Any]:
    import akshare as ak
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    await _ak_throttle()
    df = await _run_in_thread(ak.stock_zh_a_daily, symbol=_to_sina_code(code),
                               start_date=start, end_date=end, adjust='qfq')
    if df.empty:
        raise ValueError("empty response")
    latest = df.iloc[-1]
    return {
        'status': 'success', 'stock': code,
        'price': float(latest.get('close', 0)),
        'volume': int(latest.get('volume', 0)),
        'history': df.tail(30).to_dict(orient='records'),
        'source': 'sina'
    }


# 同花顺 A股行情数据

async def _tx_stock_history(code: str, days: int = 90) -> Dict[str, Any]:
    import akshare as ak
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    await _ak_throttle()
    df = await _run_in_thread(ak.stock_zh_a_hist_tx, symbol=_to_tx_code(code),
                               start_date=start, end_date=end, adjust='qfq')
    if df.empty:
        raise ValueError("empty response")
    latest = df.iloc[-1]
    return {
        'status': 'success', 'stock': code,
        'price': float(latest.get('close', 0)),
        'volume': int(latest.get('amount', 0)),
        'history': df.tail(30).to_dict(orient='records'),
        'source': 'tonghuashun'
    }


# Financial data (single-source, most reliable for each)

async def _ths_financial_benefit(code: str, quarterly: bool = False) -> Dict[str, Any]:
    import akshare as ak
    indicator = '按季度' if quarterly else '按报告期'
    await _ak_throttle()
    df = await _run_in_thread(ak.stock_financial_benefit_ths, symbol=code, indicator=indicator)
    if df.empty:
        raise ValueError("empty")
    return {'status': 'success', 'stock': code, 'data': df.head(6).to_dict(orient='records'),
            'source': 'tonghuashun', 'period': indicator}

async def _ths_financial_abstract(code: str) -> Dict[str, Any]:
    import akshare as ak
    await _ak_throttle()
    df = await _run_in_thread(ak.stock_financial_abstract_ths, symbol=code, indicator='主要指标')
    if df.empty:
        raise ValueError("empty")
    return {'status': 'success', 'stock': code, 'data': df.head(6).to_dict(orient='records'), 'source': 'tonghuashun'}

async def _sina_financials(code: str, stmt_type: str) -> Dict[str, Any]:
    import akshare as ak
    symbol_map = {'balance': '资产负债表', 'income': '利润表', 'cashflow': '现金流量表'}
    symbol = symbol_map.get(stmt_type, '资产负债表')
    await _ak_throttle()
    df = await _run_in_thread(ak.stock_financial_report_sina, stock=_to_sina_code(code), symbol=symbol)
    if df.empty:
        raise ValueError("empty")
    return {'status': 'success', 'stock': code, 'data': df.head(6).to_dict(orient='records'), 'source': 'sina'}


# Parallel-first unified API

async def _race(*coros):
    """Fire all coroutines in parallel, return first successful result.
    If all fail, return the last error dict. Overall timeout 15s."""
    tasks = [asyncio.ensure_future(c()) if callable(c) else asyncio.ensure_future(c) for c in coros]
    last_error = None
    pending = set(tasks)
    deadline = time.time() + 15
    while pending and time.time() < deadline:
        remain = max(1, deadline - time.time())
        done, pending = await asyncio.wait(pending, timeout=remain, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            try:
                result = t.result()
                if result.get('status') == 'success':
                    for p in pending:
                        p.cancel()
                    return result
                if result.get('status') == 'error':
                    last_error = result
            except Exception as e:
                last_error = {'status': 'error', 'message': str(e)[:200]}
    for p in pending:
        p.cancel()
    return last_error or {'status': 'error', 'message': 'all sources failed or timed out'}


async def fetch_stock_overview(code: str) -> Dict[str, Any]:
    """Fetch overview from THS first, fallback to EM."""
    key = f"overview:{code}"
    async def _do():
        if _is_a_share(code):
            try:
                return await _tx_stock_history(code)
            except:
                try:
                    return await _em_stock_history(code)
                except:
                    return await _sina_stock_history(code)
        return await _yf_overview(code)
    return await _cached(key, _do)


async def fetch_stock_financials(code: str, stmt_type: str = 'income', years: int = 3) -> Dict[str, Any]:
    """Financial statements ? Sina (quarterly) primary, THS (annual) fallback."""
    key = f"fin:{code}:{stmt_type}"
    async def _do():
        if _is_a_share(code):
            r = await _sina_financials(code, stmt_type)
            if r['status'] == 'success':
                return r
            if stmt_type == 'income':
                return await _ths_financial_benefit(code, quarterly=True)
            return await _ths_financial_abstract(code)
        return await _yf_financials(code, stmt_type, years)
    return await _cached(key, _do)


# Backward-compat
fetch_yfinance_income = lambda code, years=3: fetch_stock_financials(code, 'income', years)
fetch_yfinance_balance = lambda code, years=3: fetch_stock_financials(code, 'balance', years)
fetch_yfinance_cashflow = lambda code, years=3: fetch_stock_financials(code, 'cashflow', years)
fetch_yfinance_overview = fetch_stock_overview
