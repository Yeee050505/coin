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
            # 资金流/估值等结构化数据前置，确保进入上下文（避免截断丢失）
            head_parts = []
            for k, v in fin_data.items():
                if k.endswith("_fund_flow") and isinstance(v, dict):
                    recent = v.get("recent") or []
                    if recent:
                        head_parts.append(f"{k} 近{len(recent)}日主力净流入(亿): " +
                                          ", ".join(f"{r['date'][-5:]}={r['main_net']}" for r in recent[-12:]))
                    inst = v.get("instant") or {}
                    if inst:
                        head_parts.append(f"{k} 当日主力净流入={inst.get('main_net_yi')}亿")
                if k.endswith("_stock_snapshot") and isinstance(v, dict):
                    head_parts.append(f"{k} 价格={v.get('price')} 涨跌幅={v.get('change_pct')}%")
            data_text = ""
            if head_parts:
                data_text += "可用于图表的资金流/行情数据:\n" + "\n".join(head_parts) + "\n\n"
            data_text += str(fin_data)[:1600]
            prompt = ("Output 2-3 chart specifications in JSON array only (no markdown). "
                      "Prefer charts from the 资金流/行情数据 above (e.g. 主力净流入趋势 line chart):\n\n"
                      f"{data_text}\n\nResearch topic: {request}")
        else:
            prompt = f"Output 2-3 general industry analysis chart specifications for: {request}"

        chart_specs = []
        resp = ""
        charts_data = None
        for attempt in range(2):
            try:
                p = prompt if attempt == 0 else (
                    "只输出一个合法JSON数组（不要任何其他文字/markdown）。基于以下数据输出2个图表spec，"
                    f"优先资金流趋势:\n{head_parts if fin_data else request}")
                resp = await self._call_llm(p)
                m = re.search(r"\[.*\]", resp, re.DOTALL)
                if resp.strip().startswith("[") or m:
                    charts_data = json.loads(resp.strip() if resp.strip().startswith("[") else m.group())
                    if isinstance(charts_data, list) and charts_data:
                        break
                charts_data = None
            except Exception:
                charts_data = None
        try:
            if charts_data:
                for spec in charts_data[:3]:
                    if not isinstance(spec, dict):
                        continue
                    r = await call_tool("run_chart_code",
                        chart_type=spec.get("chart_type", "bar"),
                        title=spec.get("title", ""),
                        labels=spec.get("labels", []),
                        values=spec.get("values", []),
                    )
                    chart_specs.append({"index": len(chart_specs) + 1, "spec": spec, "result": r})
            if not chart_specs:
                raise ValueError("no valid chart specs")
        except Exception:
            # 兜底: 直接用资金流数据生成图表，避免假数据
            fflow = next((v for k, v in (fin_data or {}).items()
                          if k.endswith("_fund_flow") and isinstance(v, dict)), None)
            recent = (fflow or {}).get("recent") or []
            if recent:
                labels = [r["date"][-5:] for r in recent[-10:]]
                values = [r["main_net"] for r in recent[-10:]]
                fb_spec = {"chart_type": "line", "title": "主力资金净流入趋势（亿元）",
                           "labels": labels, "values": values}
            else:
                inst = (fflow or {}).get("instant") or {}
                fb_spec = {"chart_type": "bar", "title": "当日主力资金流入/流出（亿元）",
                           "labels": ["流入", "流出"],
                           "values": [inst.get("in_yi") or 0, inst.get("out_yi") or 0]}
            fallback = await call_tool("run_chart_code", **fb_spec)
            chart_specs.append({"index": 1, "spec": fb_spec, "fallback": True, "result": fallback})

        context.save_intermediate("analysis_charts", chart_specs)
        return {
            "status": "success",
            "data": {"charts": chart_specs},
            "source": "data_analyst"
        }
