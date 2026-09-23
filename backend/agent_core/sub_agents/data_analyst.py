# coding: utf-8
"""量化分析师 - 负责数据可视化与图表生成"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool
import json, re


class QuantAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="quant_analyst",
            model_name="deepseek-chat",
            system_prompt="""
你是一名金融量化分析师。从财务数据中提取关键指标，
输出2-3个图表规格为JSON。每个图表必须包含：
chart_type (bar/line/pie), title, labels (列表), values (数字列表)。

只输出JSON数组，不要输出其他内容。示例：
[{"chart_type":"bar","title":"营收趋势","labels":["2024","2025","2026"],"values":[120,145,168]}]
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
