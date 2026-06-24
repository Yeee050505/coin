import logging
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

    try:
        from ddgs import DDGS
        import asyncio
        async def _ddgs_search():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, lambda: list(DDGS().text(query, max_results=max_results)))
        rlist = await asyncio.wait_for(_ddgs_search(), timeout=5.0)
        if rlist:
            return {"status": "success", "results": [{"title": r.get("title",""), "url": r.get("href",""), "content": r.get("body","")[:500]} for r in rlist], "source": "ddgs"}
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


# ?? Code Sandbox ??

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


# ?? RAG Retrieval ??

@register_tool(name="retrieve_knowledge", description="Retrieve relevant financial knowledge from knowledge base",
               parameters={"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]})
async def retrieve_knowledge(query: str, top_k: int = 5):
    knowledge = {
        "market_size": "中国A股市场总市值约80万亿元，日均成交额约8000亿元。市场由散户和机构投资者共同主导，近年来机构化趋势明显。",
        "financial_report": "财务报表包括资产负债表、利润表和现金流量表。核心指标包括营收增长率、毛利率、净利率、ROE、ROA、资产负债率、经营性现金流等。",
        "competitor": "竞争分析框架：波特五力模型（供应商议价能力、买方议价能力、新进入者威胁、替代品威胁、同业竞争）。核心竞争要素包括技术壁垒、品牌优势、成本优势、渠道优势等。",
        "risk": "投资风险包括市场风险（系统性风险）、信用风险、流动性风险、操作风险、政策风险等。常用风险指标包括Beta、VaR、最大回撤、夏普比率等。",
        "valuation": "估值方法包括DCF现金流折现法、PE市盈率、PB市净率、PS市销率、EV/EBITDA企业价值倍数、PEG等。不同行业适用不同估值方法。",
        "investment": "投资策略包括价值投资、成长投资、趋势投资、量化投资等。需结合宏观经济、行业周期、公司基本面和技术面综合判断。",
        "industry": "行业分析框架：PEST分析（政策Policy、经济Economic、社会Social、技术Technology）、生命周期理论（导入期、成长期、成熟期、衰退期）。",
        "economy": "宏观经济指标包括GDP增速、CPI通胀率、PPI工业品价格、PMI采购经理人指数、M2货币供应量、利率、汇率、社融数据等。",
    }
    try:
        q = query.lower()
        keywords = {"market_size": ["市场", "规模", "市值", "成交"],
                     "financial_report": ["财务", "报表", "营收", "利润", "ROE"],
                     "competitor": ["竞争", "波特", "五力", "对手"],
                     "risk": ["风险", "Beta", "VaR", "回撤"],
                     "valuation": ["估值", "PE", "PB", "DCF", "市盈率"],
                     "investment": ["投资", "策略", "价值投资"],
                     "industry": ["行业", "PEST", "生命周期"],
                     "economy": ["经济", "GDP", "CPI", "PPI", "PMI"]}
        scored = []
        for key, kws in keywords.items():
            score = sum(2 for kw in kws if kw in q) + (1 if key.replace("_","") in q.replace(" ","") else 0)
            if score > 0:
                scored.append((key, knowledge[key], score))
        scored.sort(key=lambda x: x[2], reverse=True)
        results = [{"content": text, "score": s, "source": f"knowledge_base:{key}"} for key, text, s in scored[:top_k]]
        if not results:
            results = [{"content": knowledge[k], "score": 1, "source": f"knowledge_base:{k}"} for k in list(knowledge)[:top_k]]
        return {"status": "success", "results": results, "source": "keyword_rag"}
    except Exception as e:
        logger.warning(f"RAG retrieval failed: {e}")
        return {"status": "error", "results": [], "message": "RAG retrieval unavailable", "source": "keyword_rag"}
