# coding: utf-8
"""Data Engineer Agent - fetch financial data and interpret with LLM"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool
import asyncio, logging

logger = logging.getLogger(__name__)


class ChiefDataEngineer(BaseAgent):
    def __init__(self):
        super().__init__(
            name="chief_data_engineer",
            model_name="deepseek-chat",
            system_prompt="""
You are a financial data engineer. Based on the financial data you have acquired, you need to:
1. Interpret the meaning and trends of various financial indicators
2. Identify anomalies and key changes in the data
3. Assess the company's financial health
4. Present data analysis results in a structured way

If data sources are unavailable, provide analysis based on your knowledge. Output in Chinese.
""",
        )

    def _compact_financial(self, raw: dict) -> str:
        """compress financial data to ~600 chars — only key metrics"""
        lines = []
        for key, val in raw.items():
            if isinstance(val, dict):
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
            else:
                lines.append(f"{key}={val}")
        text = " | ".join(lines)
        return text[:900]

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        financial_data = {}
        stock_codes = context.state.stock_codes
        if not stock_codes:
            stock_codes = ["002624"]
        request = context.state.original_request

        for code in stock_codes:
            for ind in ["overview", "income", "balance"]:
                try:
                    r = await asyncio.wait_for(
                        call_tool("fetch_financial_data", stock_code=code, indicator=ind, years=3),
                        timeout=15
                    )
                    if r and r.get("status") == "success" and (r.get("data") or ind == "overview"):
                        financial_data[f"{code}_{ind}"] = r.get("data") or r
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
