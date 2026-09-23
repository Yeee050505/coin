import logging
import asyncio
from typing import Any, Dict, List, Optional, Callable
from app.core.config import settings

logger = logging.getLogger(__name__)

_tool_registry: Dict[str, Dict[str, Any]] = {}


def register_tool(name: str, description: str, parameters: dict):
    def decorator(func: Callable):
        _tool_registry[name] = {"name": name, "description": description, "parameters": parameters, "function": func}
        return func
    return decorator


def get_tool_definitions() -> List[Dict]:
    return [{"type": "function", "function": {"name": info["name"], "description": info["description"], "parameters": info["parameters"]}} for info in _tool_registry.values()]


async def call_tool(name: str, **kwargs) -> Any:
    import asyncio
    tool = _tool_registry.get(name)
    if not tool:
        return {"status": "error", "message": f"Tool '{name}' not found"}
    fn = tool["function"]
    if asyncio.iscoroutinefunction(fn):
        return await fn(**kwargs)
    return fn(**kwargs)


# ?? Web Search ??

@register_tool(name="web_search", description="Search the web for recent information",
               parameters={"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]})
async def web_search(query: str, max_results: int = 5):
    key = settings.tavily_api_key
    if key:
        import httpx
        try:
            r = await httpx.AsyncClient().post("https://api.tavily.com/search", json={"api_key": key, "query": query, "max_results": max_results}, timeout=15)
            data = r.json()
            return {"status": "success", "results": [{"title": x["title"], "url": x["url"], "content": x["content"][:500]} for x in data.get("results", [])], "source": "tavily"}
        except Exception as e:
            logger.warning(f"Tavily failed: {e}")

    # 国内可用：必应国内版 (cn.bing.com) - 无需 API key，相对稳定
    try:
        import httpx
        from bs4 import BeautifulSoup
        import urllib.parse

        async def _bing_cn_search():
            url = "https://cn.bing.com/search"
            params = {"q": query, "count": max_results}
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params, headers=headers)
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for item in soup.select("li.b_algo"):
                title_elem = item.select_one("h2 a")
                content_elem = item.select_one(".b_caption p")
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    url = title_elem.get("href", "")
                    content = content_elem.get_text(strip=True) if content_elem else ""
                    results.append({"title": title, "url": url, "content": content[:500]})
                    if len(results) >= max_results:
                        break
            return results

        rlist = await asyncio.wait_for(_bing_cn_search(), timeout=15.0)
        if rlist:
            return {"status": "success", "results": rlist, "source": "bing_cn"}
    except asyncio.TimeoutError:
        logger.warning(f"Bing CN search timed out for: {query}")
    except Exception as e:
        logger.warning(f"Bing CN search failed: {e}")

    # 备用：ddgs (保留但不作为首选)
    try:
        from ddgs import DDGS

        async def _ddgs_search():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, lambda: list(DDGS().text(query, max_results=max_results, backend="yandex")))
        rlist = await asyncio.wait_for(_ddgs_search(), timeout=15.0)
        if rlist:
            return {"status": "success", "results": [{"title": r.get("title",""), "url": r.get("href",""), "content": r.get("body","")[:500]} for r in rlist], "source": "ddgs_yandex"}
    except asyncio.TimeoutError:
        logger.warning(f"DDGS search timed out for: {query}")
    except Exception as e:
        logger.warning(f"DDGS search failed: {e}")

    return {"status": "unavailable", "results": [], "source": "none", "message": "No search engine available"}

# 个股行情数据源 (直连, 不进行竞速)

@register_tool(name="stock_em", description="东方财富 A-share stock history (direct)",
               parameters={"type": "object", "properties": {"stock_code": {"type": "string"}, "days": {"type": "integer"}}, "required": ["stock_code"]})
async def stock_em(stock_code: str, days: int = 90):
    from app.tools.financial_api import _em_stock_history
    return await _em_stock_history(stock_code, days)

@register_tool(name="stock_sina", description="新浪 A-share stock history (direct)",
               parameters={"type": "object", "properties": {"stock_code": {"type": "string"}, "days": {"type": "integer"}}, "required": ["stock_code"]})
async def stock_sina(stock_code: str, days: int = 90):
    from app.tools.financial_api import _sina_stock_history
    return await _sina_stock_history(stock_code, days)

@register_tool(name="stock_ths", description="同花顺 A-share stock history (direct)",
               parameters={"type": "object", "properties": {"stock_code": {"type": "string"}, "days": {"type": "integer"}}, "required": ["stock_code"]})
async def stock_ths(stock_code: str, days: int = 90):
    from app.tools.financial_api import _tx_stock_history
    return await _tx_stock_history(stock_code, days)


# Financial Data

@register_tool(name="fetch_financial_data", description="Fetch real financial data via AKShare (A-shares) or yfinance (US/international): overview, income, balance, cashflow",
               parameters={"type": "object", "properties": {"stock_code": {"type": "string"}, "indicator": {"type": "string", "enum": ["income", "balance", "cashflow", "overview"]}, "years": {"type": "integer"}}, "required": ["stock_code", "indicator"]})
async def fetch_financial_data(stock_code: str, indicator: str = "overview", years: int = 3):
    from app.tools.financial_api import fetch_stock_overview, fetch_stock_financials
    if indicator == "overview":
        return await fetch_stock_overview(stock_code)
    return await fetch_stock_financials(stock_code, indicator, years)


# ?? Text2SQL ??

@register_tool(name="text2sql", description="Search research projects by keyword",
               parameters={"type": "object", "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]})
async def text2sql(keyword: str):
    try:
        from app.models import get_session
        from sqlalchemy import text
        db = get_session()
        sql = text("SELECT id, title, scenario, status, created_at FROM research_projects WHERE title LIKE :kw LIMIT 5")
        result = list(db.execute(sql, {"kw": f"%{keyword[:20]}%"}).fetchall())
        rows = [dict(r._mapping) for r in result] if result else []
        db.close()
        return {"status": "success", "results": rows, "source": "text2sql"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:100], "source": "text2sql"}


@register_tool(name="run_chart_code", description="Generate a financial chart from data series",
               parameters={"type": "object", "properties": {
                   "chart_type": {"type": "string", "enum": ["bar", "line", "pie"]},
                   "title": {"type": "string"},
                   "labels": {"type": "array", "items": {"type": "string"}},
                   "values": {"type": "array", "items": {"type": "number"}},
               }, "required": ["chart_type", "title", "labels", "values"]})
async def run_chart_code(chart_type: str = "bar", title: str = "", labels: list = None, values: list = None):
    import base64, io, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    labels = labels or []
    values = values or []
    plt.figure(figsize=(8, 5))
    if chart_type == "bar":
        plt.bar(labels, values, color="#1A7DFF")
    elif chart_type == "line":
        plt.plot(labels, values, marker="o", color="#1A7DFF")
    elif chart_type == "pie":
        plt.pie(values, labels=labels, autopct="%1.1f%%")
    plt.title(title)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="svg")
    buf.seek(0)
    svg_data = base64.b64encode(buf.read()).decode()
    plt.close()
    return {"status": "success", "chart_data": f"data:image/svg+xml;base64,{svg_data}", "source": "chart_template"}
