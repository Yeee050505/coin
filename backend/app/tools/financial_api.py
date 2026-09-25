
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


# ==================== Extended data sources ====================
# Design: every metric has 2+ independent sources (EM push2 / Sina / THS / datacenter)
# with retry + graceful degradation — domestic endpoints are flaky.

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def _market_of(code: str) -> str:
    if code.startswith(("6", "9", "5")):
        return "sh"
    if code.startswith(("0", "2", "3")):
        return "sz"
    return "bj"


async def _http_json(url: str, params: Dict = None, headers: Dict = None,
                     timeout: float = 6.0, tries: int = 2) -> Any:
    def _do():
        import requests
        last = None
        for i in range(tries):
            try:
                r = requests.get(url, params=params, headers=headers or {}, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                if i < tries - 1:
                    time.sleep(1.2)
        raise last
    return await _run_in_thread(_do)


# --- 实时快照: 新浪行情(主, 轻量稳定) -> 东财 push2(备, 字段更全) ---

async def _sina_quote(code: str) -> Dict[str, Any]:
    sym = _to_sina_code(code)

    def _do():
        import requests
        r = requests.get(
            f"https://hq.sinajs.cn/list={sym}",
            headers={"Referer": "https://finance.sina.com.cn",
                     "User-Agent": _EM_HEADERS["User-Agent"]},
            timeout=6,
        )
        r.encoding = "gbk"
        text = r.text
        payload = text.split('"', 1)[1].split('"')[0].split(",")
        if len(payload) < 32:
            raise ValueError("unexpected sina payload")
        return payload

    p = await _run_in_thread(_do)
    name, open_, prev, last, high, low = p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])
    volume, amount = float(p[8]), float(p[9])
    chg = round((last - prev) / prev * 100, 2) if prev else None
    return {'status': 'success', 'stock': code, 'name': name, 'price': last, 'prev_close': prev,
            'open': open_, 'high': high, 'low': low, 'change_pct': chg,
            'volume': volume, 'amount': amount, 'date': p[30], 'time': p[31], 'source': 'sina_quote'}


async def _em_quote_raw(code: str) -> Dict[str, Any]:
    market = 1 if code.startswith("6") else 0
    params = {
        "fltt": "2", "invt": "2", "secid": f"{market}.{code}",
        "fields": "f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f71,f116,f117,"
                  "f162,f163,f167,f168,f169,f170",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    j = await _http_json("https://push2.eastmoney.com/api/qt/stock/get",
                         params, _EM_HEADERS, timeout=6, tries=3)
    d = (j or {}).get("data") or {}
    if not d or d.get("f43") in (None, "-"):
        raise ValueError("empty quote data")
    return {'status': 'success', 'stock': code, 'name': d.get("f58"), 'price': d.get("f43"),
            'prev_close': d.get("f60"), 'open': d.get("f46"), 'high': d.get("f44"), 'low': d.get("f45"),
            'change_pct': d.get("f170"), 'turnover_rate': d.get("f168"), 'volume_ratio': d.get("f50"),
            'pe': d.get("f162"), 'pe_ttm': d.get("f163"), 'pb': d.get("f167"),
            'market_cap': d.get("f116"), 'float_cap': d.get("f117"),
            'volume': d.get("f47"), 'amount': d.get("f48"), 'source': 'eastmoney_push2'}


async def fetch_stock_snapshot(code: str) -> Dict[str, Any]:
    if not _is_a_share(code):
        return {'status': 'error', 'stock': code, 'message': 'snapshot only supports A-shares',
                'source': 'snapshot'}
    key = f"snap:{code}"

    async def _do():
        try:
            return await _sina_quote(code)
        except Exception as e:
            logger.warning(f"sina quote failed for {code}: {e}")
        return await _em_quote_raw(code)

    return await _cached(key, _do)


# --- 资金流: 东财个股资金流(主) -> 同花顺全市场资金流(备, 首次25s后缓存) ---

def _parse_yi(s) -> float:
    s = str(s).strip()
    try:
        if s.endswith('亿'):
            return float(s[:-1])
        if s.endswith('万'):
            return float(s[:-1]) / 1e4
        return float(s)
    except (TypeError, ValueError):
        return 0.0


async def _ths_fund_flow_all() -> Dict[str, Any]:
    """THS whole-market money flow, cached — first call ~25s, then instant."""
    key = "ths_fflow_all"

    async def _do():
        import akshare as ak
        await _ak_throttle()
        df = await _run_in_thread(lambda: ak.stock_fund_flow_individual(symbol='即时'))
        if df is None or df.empty:
            raise ValueError("empty THS fund flow")
        out = {}
        for _, r in df.iterrows():
            c = str(r.get('股票代码', '')).strip()
            if c.endswith('.0'):
                c = c[:-2]
            if c.isdigit() and len(c) < 6:
                c = c.zfill(6)
            if len(c) != 6 or not c.isdigit():
                continue
            try:
                out[c] = {
                    'price': float(r.get('最新价', 0) or 0),
                    'change_pct': float(str(r.get('涨跌幅', '0')).replace('%', '') or 0),
                    'main_net_yi': _parse_yi(r.get('净额')),
                    'in_yi': _parse_yi(r.get('流入资金')),
                    'out_yi': _parse_yi(r.get('流出资金')),
                }
            except (TypeError, ValueError):
                continue
        if not out:
            raise ValueError("THS fund flow parse failed")
        return out

    return await _cached(key, _do)


async def fetch_fund_flow(code: str, days: int = 30) -> Dict[str, Any]:
    if not _is_a_share(code):
        return {'status': 'error', 'stock': code, 'message': 'fund flow only supports A-shares'}
    key = f"fflow:{code}:{days}"

    async def _do():
        import akshare as ak
        last_err = None
        for attempt in range(2):
            try:
                await _ak_throttle()
                df = await _run_in_thread(
                    lambda: ak.stock_individual_fund_flow(stock=code, market=_market_of(code)))
                if df is None or df.empty:
                    raise ValueError("empty fund flow")
                df = df.tail(days)
                net_col = "主力净流入-净额"
                pct_col = "主力净流入-净占比"
                records = []
                for _, row in df.iterrows():
                    records.append({
                        "date": str(row.get("日期", "")),
                        "change_pct": float(row.get("涨跌幅", 0) or 0),
                        "main_net": round(float(row.get(net_col, 0) or 0) / 1e8, 4),
                        "main_net_pct": float(row.get(pct_col, 0) or 0),
                    })
                totals = {
                    "d5_main_net_yi": round(sum(r["main_net"] for r in records[-5:]), 4),
                    "d10_main_net_yi": round(sum(r["main_net"] for r in records[-10:]), 4),
                    "d20_main_net_yi": round(sum(r["main_net"] for r in records[-20:]), 4),
                }
                return {'status': 'success', 'stock': code, 'unit': '亿元', 'recent': records,
                        'totals': totals, 'source': 'eastmoney_fund_flow'}
            except Exception as e:
                last_err = e
                await asyncio.sleep(1.5)
        logger.warning(f"EM fund flow failed for {code} ({last_err}), falling back to THS")
        # 备用源: 同花顺全市场资金流（无逐日历史，仅当日快照）
        try:
            all_map = await _ths_fund_flow_all()
            item = all_map.get(code)
            if not item:
                raise ValueError(f"{code} not in THS flow data")
            return {'status': 'success', 'stock': code, 'unit': '亿元', 'recent': [],
                    'instant': item, 'history_available': False,
                    'totals': {'d5_main_net_yi': item['main_net_yi'],
                               'd10_main_net_yi': None, 'd20_main_net_yi': None},
                    'source': 'ths_fund_flow_instant'}
        except Exception as e2:
            raise RuntimeError(f"all fund-flow sources failed: {last_err}; {e2}")

    return await _cached(key, _do)


# --- 估值分位: 东财 datacenter stock_value_em (实测稳定) ---

async def fetch_valuation(code: str) -> Dict[str, Any]:
    if not _is_a_share(code):
        return {'status': 'error', 'stock': code, 'message': 'valuation only supports A-shares'}
    key = f"val:{code}"

    async def _do():
        import akshare as ak
        await _ak_throttle()
        df = await _run_in_thread(lambda: ak.stock_value_em(symbol=code))
        if df is None or df.empty or len(df) < 20:
            raise ValueError("empty valuation history")

        def col(*keywords):
            for c in df.columns:
                if all(k in str(c) for k in keywords):
                    return c
            return None

        date_col = df.columns[0]
        pe_c, pb_c, ps_c = col("PE", "TTM"), col("市净率"), col("市销率")
        out = {'status': 'success', 'stock': code, 'as_of': str(df.iloc[-1][date_col])[:10]}

        def add_pct(label, c):
            if c is None:
                return
            cur = df[c].iloc[-1]
            hist = df[c].dropna()
            if cur is None or hist.empty:
                return
            try:
                cur = float(cur)
                pct = round(float((hist < cur).mean()) * 100, 1)
                out[label] = round(cur, 2)
                out[label + "_percentile"] = pct
            except (TypeError, ValueError):
                pass

        add_pct("pe_ttm", pe_c)
        add_pct("pb", pb_c)
        add_pct("ps", ps_c)
        if "pe_ttm" not in out:
            raise ValueError("no valuation columns matched")
        out['source'] = 'eastmoney_value'
        return out

    return await _cached(key, _do)


# --- 所属板块 + 板块行情: push2 f127 (重试) -> 研报行业字段(备) ---

async def fetch_industry_info(code: str) -> Dict[str, Any]:
    if not _is_a_share(code):
        return {'status': 'error', 'stock': code, 'message': 'industry only supports A-shares'}
    key = f"ind:{code}"

    async def _do():
        industry = None
        try:
            j = await _http_json(
                "https://push2.eastmoney.com/api/qt/stock/get",
                {"fltt": "2", "invt": "2", "secid": f"{'1' if code.startswith('6') else '0'}.{code}",
                 "fields": "f57,f58,f127",
                 "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
                _EM_HEADERS, timeout=6, tries=3)
            industry = ((j or {}).get("data") or {}).get("f127")
        except Exception as e:
            logger.warning(f"push2 industry lookup failed for {code}: {e}")
        if not industry:
            # 备用源: 研报数据中的行业字段
            import akshare as ak
            await _ak_throttle()
            df = await _run_in_thread(lambda: ak.stock_research_report_em(symbol=code))
            if df is not None and not df.empty and "行业" in df.columns:
                vals = df["行业"].dropna()
                if not vals.empty:
                    industry = str(vals.mode().iloc[0])
        if not industry:
            raise ValueError("industry lookup failed")
        out = {'status': 'success', 'stock': code, 'industry': industry}
        # 板块近期行情（失败仅缺失，不阻断）: 东财板块 -> 同花顺板块指数
        try:
            import akshare as ak
            end = datetime.now().strftime('%Y%m%d')
            start = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
            await _ak_throttle()
            bdf = await _run_in_thread(lambda: ak.stock_board_industry_hist_em(
                symbol=industry, start_date=start, end_date=end, period='日k', adjust='qfq'))
            if bdf is None or bdf.empty:
                raise ValueError("empty EM board hist")
            latest = bdf.iloc[-1]
            closes = [float(x) for x in bdf['收盘'].tail(30)]
            chg = latest.get('涨跌幅')
            if chg is None or str(chg) == 'nan':
                chg = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
            out['board_history'] = {
                'recent_30d_close': [round(c, 2) for c in closes[-10:]],
                'latest_close': round(float(latest.get('收盘', 0)), 2),
                'latest_change_pct': round(float(chg), 2),
                'source': 'eastmoney_board',
            }
        except Exception as e:
            logger.warning(f"EM board history failed for {industry}: {e}, trying THS index")
            try:
                import akshare as ak
                import re as _re
                # 同花顺板块命名无罗马数字后缀（白酒Ⅱ -> 白酒）
                ths_name = _re.sub(r'[ⅠⅡⅢⅣⅤIVX]+$', '', str(industry)).strip()
                end = datetime.now().strftime('%Y%m%d')
                start = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
                await _ak_throttle()
                bdf = await _run_in_thread(lambda: ak.stock_board_industry_index_ths(
                    symbol=ths_name, start_date=start, end_date=end))
                if bdf is None or bdf.empty:
                    raise ValueError("empty THS board index")
                closes = [float(x) for x in bdf['收盘价'].tail(30)]
                last_c, prev_c = closes[-1], closes[-2] if len(closes) >= 2 else closes[-1]
                out['board_history'] = {
                    'recent_30d_close': [round(c, 2) for c in closes[-10:]],
                    'latest_close': round(last_c, 2),
                    'latest_change_pct': round((last_c - prev_c) / prev_c * 100, 2) if prev_c else 0.0,
                    'source': 'ths_board_index',
                }
            except Exception as e2:
                logger.warning(f"THS board index failed for {industry}: {e2}")
        out['source'] = 'eastmoney'
        return out

    return await _cached(key, _do)


# --- 板块资金流: 同花顺(主, 独立源实测0.4s) -> 东财(备) ---

async def fetch_sector_fund_flow() -> Dict[str, Any]:
    key = "sector_fflow"

    async def _do():
        try:
            import akshare as ak
            await _ak_throttle()
            df = await _run_in_thread(lambda: ak.stock_fund_flow_industry(symbol='即时'))
            if df is not None and not df.empty:
                rows = []
                for _, r in df.head(15).iterrows():
                    rows.append({
                        'industry': str(r.get('行业', '')),
                        'change_pct': float(r.get('行业-涨跌幅', 0) or 0),
                        'net_yi': float(r.get('净额', 0) or 0),
                        'leader': str(r.get('领涨股', '')),
                        'leader_pct': float(r.get('领涨股-涨跌幅', 0) or 0),
                    })
                return {'status': 'success', 'unit': '亿元', 'rank': rows, 'source': 'ths_sector_flow'}
        except Exception as e:
            logger.warning(f"THS sector flow failed: {e}")
        try:
            import akshare as ak
            await _ak_throttle()
            df = await _run_in_thread(lambda: ak.stock_sector_fund_flow_rank(
                indicator='今日', sector_type='行业资金流'))
            if df is None or df.empty:
                raise ValueError("empty")

            def pick(*kw):
                for c in df.columns:
                    if all(k in str(c) for k in kw):
                        return c
                return None
            name_c, pct_c, net_c = pick('名称'), pick('涨跌幅'), pick('主力', '净额')
            if name_c is None or net_c is None:
                raise ValueError("unexpected columns")
            rows = []
            for _, r in df.head(15).iterrows():
                try:
                    rows.append({
                        'industry': str(r[name_c]),
                        'change_pct': float(r[pct_c]) if pct_c else 0.0,
                        'net_yi': round(float(r[net_c]) / 1e8, 4),
                    })
                except (TypeError, ValueError):
                    continue
            return {'status': 'success', 'unit': '亿元', 'rank': rows, 'source': 'eastmoney_sector_flow'}
        except Exception as e:
            logger.warning(f"EM sector flow failed: {e}")
            return {'status': 'error', 'message': str(e)[:200], 'source': 'sector_flow'}

    return await _cached(key, _do)


# --- 券商研报 + 评级 + 盈利预测: 东财 datacenter (实测稳定) ---

async def fetch_research_report(code: str, top: int = 8) -> Dict[str, Any]:
    if not _is_a_share(code):
        return {'status': 'error', 'stock': code, 'message': 'research report only supports A-shares'}
    key = f"rpt:{code}"

    async def _do():
        import akshare as ak
        await _ak_throttle()
        df = await _run_in_thread(lambda: ak.stock_research_report_em(symbol=code))
        if df is None or df.empty:
            raise ValueError("empty reports")
        date_c = next((c for c in df.columns if '日期' in str(c)), None)
        if date_c:
            df = df.sort_values(date_c, ascending=False, kind='stable')
        reports = []
        cols = [c for c in df.columns if any(k in str(c) for k in
                ('报告名称', '东财评级', '机构', '盈利预测', '日期'))][:10]
        for _, r in df.head(top).iterrows():
            item = {}
            for c in cols:
                v = r.get(c)
                if v is not None and str(v) != 'nan':
                    item[str(c)] = round(float(v), 2) if isinstance(v, float) and abs(v) > 1 else str(v)
            if item:
                reports.append(item)
        out = {'status': 'success', 'stock': code, 'reports': reports}
        if '东财评级' in df.columns:
            vc = df['东财评级'].value_counts().head(5)
            out['rating_counts'] = {str(k): int(v) for k, v in vc.items()}
        out['report_count'] = int(len(df))
        out['source'] = 'eastmoney_research'
        return out

    return await _cached(key, _do)


# --- 财经快讯: 东财全球快讯 (实测稳定) , 关键词匹配 ---

async def fetch_finance_news(keyword: str = "", limit: int = 12) -> Dict[str, Any]:
    key = f"gnews:{keyword}:{limit}"

    async def _do():
        import akshare as ak
        await _ak_throttle()
        df = await _run_in_thread(lambda: ak.stock_info_global_em())
        if df is None or df.empty:
            raise ValueError("empty news")
        kw = (keyword or "").strip()
        matched = []
        if kw:
            for _, r in df.iterrows():
                text = f"{r.get('标题', '')} {r.get('摘要', '')}"
                if kw in text:
                    matched.append(r)
        rows = matched if len(matched) >= 3 else [r for _, r in df.iterrows()]
        results = []
        for r in rows[:limit]:
            results.append({
                'title': str(r.get('标题', ''))[:120],
                'summary': str(r.get('摘要', ''))[:300],
                'time': str(r.get('发布时间', '')),
                'url': str(r.get('链接', '')),
            })
        return {'status': 'success', 'keyword': kw, 'matched': len(matched),
                'results': results, 'source': 'eastmoney_news'}

    return await _cached(key, _do)


# --- 港股历史: 东财 stock_hk_hist(主) -> 新浪 stock_hk_daily(备) ---

async def fetch_hk_history(code: str, days: int = 90) -> Dict[str, Any]:
    digits = ''.join(ch for ch in str(code) if ch.isdigit())
    if len(digits) < 4:
        return {'status': 'error', 'stock': code, 'message': 'invalid HK code'}
    sym = digits.zfill(5)
    key = f"hk:{sym}:{days}"

    async def _do():
        import akshare as ak
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
        # 主: 东财
        last_err = None
        for attempt in range(2):
            try:
                await _ak_throttle()
                df = await _run_in_thread(lambda: ak.stock_hk_hist(
                    symbol=sym, period='daily', start_date=start, end_date=end, adjust='qfq'))
                if df is None or df.empty:
                    raise ValueError("empty HK history")
                latest = df.iloc[-1]
                return {
                    'status': 'success', 'stock': sym, 'price': float(latest.get('收盘', 0)),
                    'change_pct': float(latest.get('涨跌幅', 0) or 0),
                    'history': df.tail(30)[['日期', '开盘', '收盘', '最高', '最低', '成交量', '成交额', '涨跌幅']]
                              .to_dict(orient='records'),
                    'source': 'eastmoney_hk',
                }
            except Exception as e:
                last_err = e
                await asyncio.sleep(1.0)
        logger.warning(f"EM HK history failed for {sym} ({last_err}), falling back to Sina")
        # 备: 新浪港股 (全量日线, 过滤窗口)
        await _ak_throttle()
        df = await _run_in_thread(lambda: ak.stock_hk_daily(symbol=sym, adjust='qfq'))
        if df is None or df.empty:
            raise RuntimeError(f"HK history failed on all sources: {last_err}")
        cutoff = datetime.now() - timedelta(days=days)
        df = df.copy()
        df['date'] = __import__('pandas').to_datetime(df['date'])
        df = df[df['date'] >= cutoff]
        if df.empty:
            raise ValueError(f"no HK data within last {days} days")
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else None
        chg = 0.0
        if prev is not None and float(prev.get('close', 0)):
            chg = round((float(latest['close']) - float(prev['close'])) / float(prev['close']) * 100, 2)
        records = []
        for _, r in df.tail(30).iterrows():
            records.append({
                '日期': str(r['date'])[:10], '开盘': float(r['open']), '收盘': float(r['close']),
                '最高': float(r['high']), '最低': float(r['low']), '成交量': float(r['volume']),
                '成交额': float(r['amount']), '涨跌幅': chg,
            })
        return {'status': 'success', 'stock': sym, 'price': float(latest['close']),
                'change_pct': chg, 'history': records, 'source': 'sina_hk'}

    return await _cached(key, _do)


# --- 财务指标明细: 东财杜邦/盈利指标 (实测稳定) ---

async def fetch_financial_ratio(code: str) -> Dict[str, Any]:
    if not _is_a_share(code):
        return {'status': 'error', 'stock': code, 'message': 'ratio only supports A-shares'}
    key = f"ratio:{code}"

    async def _do():
        import akshare as ak
        start_year = str(datetime.now().year - 2)
        await _ak_throttle()
        df = await _run_in_thread(lambda: ak.stock_financial_analysis_indicator(
            symbol=code, start_year=start_year))
        if df is None or df.empty:
            raise ValueError("empty ratio data")
        wanted = ['净资产收益率', '销售毛利率', '销售净利率', '主营业务收入增长率',
                  '净利润增长率', '资产负债率', '流动比率', '总资产周转率']
        cols = [c for c in df.columns if any(w in str(c) for w in wanted)]
        date_c = next((c for c in df.columns if '日期' in str(c)), None)
        periods = []
        for _, r in df.head(4).iterrows():
            item = {}
            if date_c:
                item['period'] = str(r.get(date_c, ''))[:10]
            for c in cols:
                v = r.get(c)
                if v is not None and str(v) != 'nan':
                    try:
                        item[str(c)] = round(float(v), 2)
                    except (TypeError, ValueError):
                        pass
            if item:
                periods.append(item)
        if not periods:
            raise ValueError("no ratio rows")
        return {'status': 'success', 'stock': code, 'periods': periods, 'source': 'eastmoney_ratio'}

    return await _cached(key, _do)
