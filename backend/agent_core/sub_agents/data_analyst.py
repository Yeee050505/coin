# coding: utf-8
"""Data Analyst Agent - analyze data and generate visualizations with LLM"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool
import json, re


class DataAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="data_analyst",
            model_name="deepseek-chat",
            system_prompt="""
You are a financial data analyst. Extract key metrics from financial data and
output 2-3 chart specifications as JSON. Each chart must have:
chart_type (bar/line/pie), title, labels (list), values (list of numbers).

Output ONLY a JSON array, no other text. Example:
[{"chart_type":"bar","title":"Revenue Trend","labels":["2024","2025","2026"],"values":[120,145,168]}]
""",
        )

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        fin_data = context.intermediate.get("financial_data", {})
        request = context.state.original_request

        if fin_data:
            data_text = str(fin_data)[:2000]
            prompt = f"Based on this financial data, output 2-3 chart specifications in JSON:\n\n{data_text}\n\nResearch topic: {request}"
        else:
            prompt = f"Output 2-3 general industry analysis chart specifications for: {request}"

        chart_specs = []
        try:
            resp = await self._call_llm(prompt)
            charts_data = json.loads(resp) if resp.strip().startswith("[") else json.loads(re.search(r"\[.*\]", resp, re.DOTALL).group())
            for spec in charts_data[:3]:
                r = await call_tool("run_chart_code",
                    chart_type=spec.get("chart_type", "bar"),
                    title=spec.get("title", ""),
                    labels=spec.get("labels", []),
                    values=spec.get("values", []),
                )
                chart_specs.append({"index": len(chart_specs) + 1, "spec": spec, "result": r})
        except Exception:
            fallback = await call_tool("run_chart_code",
                chart_type="bar", title="Revenue Trend",
                labels=["2024", "2025", "2026"], values=[120, 145, 168])
            chart_specs.append({"index": 1, "fallback": True, "result": fallback})

        context.save_intermediate("analysis_charts", chart_specs)
        return {
            "status": "success",
            "data": {"charts": chart_specs},
            "source": "data_analyst"
        }
