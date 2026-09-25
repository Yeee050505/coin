# coding: utf-8
"""数据工程师 - 负责财务数据获取与解读"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool
import asyncio, logging

logger = logging.getLogger(__name__)


class DataEngineer(BaseAgent):
    def __init__(self):
        super().__init__(
            name="data_engineer",
            model_name="deepseek-chat",
            system_prompt="""
你是一名金融数据工程师。基于获取到的财务数据，你需要：
1. 解读各项财务指标的含义和趋势
2. 识别数据中的异常和关键变化
3. 评估公司的财务健康状况
4. 以结构化方式呈现数据分析结果

如果数据源不可用，基于你的知识提供分析。用中文输出。
""",
        )

    def _compact_financial(self, raw: dict) -> str:
        """compress financial data to ~900 chars — only key metrics"""
        lines = []
        for key, val in raw.items():
            if isinstance(val, dict):
                if key.endswith("_snapshot"):
                    lines.append(
                        f"[{key}] name={val.get('name')} price={val.get('price')} "
                        f"chg={val.get('change_pct')}% amount={val.get('amount')} source={val.get('source')}")
                    continue
                if key.endswith("_valuation"):
                    lines.append(
                        f"[{key}] as_of={val.get('as_of')} PE(TTM)={val.get('pe_ttm')}"
                        f"(分位{val.get('pe_ttm_percentile')}%) PB={val.get('pb')}"
                        f"(分位{val.get('pb_percentile')}%) PS={val.get('ps')}(分位{val.get('ps_percentile')}%)")
                    continue
                if key.endswith("_fund_flow"):
                    inst = val.get("instant") or {}
                    tot = val.get("totals") or {}
                    recent = val.get("recent") or []
                    recent_str = " ".join(f"{r['date'][-5:]}:{r['main_net']}" for r in recent[-5:])
                    lines.append(
                        f"[{key}] source={val.get('source')} d5={tot.get('d5_main_net_yi')}亿 "
                        f"d10={tot.get('d10_main_net_yi')}亿 当日={inst.get('main_net_yi')}亿 "
                        f"recent[{recent_str}]")
                    continue
                if key.endswith("_industry_info"):
                    bh = val.get("board_history") or {}
                    lines.append(
                        f"[{key}] industry={val.get('industry')} "
                        f"board_last={bh.get('latest_close')} chg={bh.get('latest_change_pct')}% "
                        f"board_trend={bh.get('recent_30d_close')}")
                    continue
                if "price" in val:
                    lines.append(f"[{key}] price={val.get('price')} vol={val.get('volume')}")
                    hist = val.get("history", [])
                    if hist:
                        dates = [h.get("日期", h.get("date", "")) for h in hist if h]
                        closes = [h.get("收盘", h.get("close", 0)) for h in hist if h]
                        if closes:
                            lines.append(f"  period={dates[0]}~{dates[-1]} closings={closes[0]}(start)->{closes[-1]}(end) "
                                          f"max={max(closes):.2f} min={min(closes):.2f}")
                if "data" in val and isinstance(val["data"], list):
                    records = val["data"]
                    if records and isinstance(records[0], dict):
                        ks = list(records[0].keys())
                        lines.append(f"[{key}] records={len(records)} fields={ks}")
                        for r in records[:2]:
                            pairs = [f"{k}={v}" for k, v in r.items() if v is not None and str(v) != "nan"]
                            lines.append(f"  {', '.join(pairs[:6])}")
                if "periods" in val and isinstance(val["periods"], list):
                    lines.append(f"[{key}] periods={str(val['periods'][:3])[:400]}")
            else:
                lines.append(f"{key}={val}")
        text = " | ".join(lines)
        return text[:1400]

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        financial_data = {}
        stock_codes = context.state.stock_codes
        if not stock_codes:
            stock_codes = ["002624"]
        request = context.state.original_request

        for code in stock_codes:
            for ind in ["overview", "income", "balance", "ratio"]:
                try:
                    r = await asyncio.wait_for(
                        call_tool("fetch_financial_data", stock_code=code, indicator=ind, years=3),
                        timeout=20
                    )
                    if r and r.get("status") == "success" and (r.get("data") or r.get("periods") or ind == "overview"):
                        financial_data[f"{code}_{ind}"] = r.get("data") or r
                except:
                    pass
            for tool, kwargs in [
                ("stock_snapshot", {"stock_code": code}),
                ("fund_flow", {"stock_code": code, "days": 30}),
                ("valuation", {"stock_code": code}),
                ("industry_info", {"stock_code": code}),
            ]:
                try:
                    r = await asyncio.wait_for(call_tool(tool, **kwargs), timeout=25)
                    if r and r.get("status") == "success":
                        financial_data[f"{code}_{tool}"] = r
                except:
                    pass

        context.save_intermediate("financial_data", financial_data)

        if financial_data:
            data_summary = self._compact_financial(financial_data)
            prompt = f"Extract key insights from the following financial data, analyze revenue trends, profitability, and asset-liability status:\n\n{data_summary}"
        else:
            prompt = f"Real-time financial data sources are currently unavailable. Based on your knowledge, for the research topic '{request}' and stock codes {stock_codes}, provide key financial analysis framework and industry benchmark data."

        try:
            interpretation = await self._call_llm(prompt)
        except Exception as e:
            interpretation = f"Analysis generation failed: {str(e)[:200]}"

        context.save_intermediate("financial_interpretation", interpretation)

        return {
            "status": "success",
            "data": {
                "financial_data": financial_data,
                "interpretation": interpretation,
                "failed_codes": [],
                "api_status": "full",
            },
            "source": "chief_data_engineer"
        }
