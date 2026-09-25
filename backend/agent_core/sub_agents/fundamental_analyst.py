# coding: utf-8
"""基本面分析师 - 负责财务报表分析与估值"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool
import asyncio


class FundamentalAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="fundamental_analyst",
            model_name="deepseek-chat",
            system_prompt="""
你是一名专业的基本面分析师。基于获取到的财务数据，你需要：
1. 分析公司的盈利能力（ROE、毛利率、净利率等）
2. 评估公司的成长性（营收增长率、利润增长率等）
3. 分析公司的财务健康度（资产负债率、现金流等）
4. 进行估值分析（PE、PB、PS等指标）
5. 给出基本面评级（看涨/中性/看跌）

输出格式：
- 核心观点（1-2句话）
- 关键指标分析
- 估值评估
- 基本面评级

用中文输出，数据必须来自提供的财务数据。
""",
        )

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        fin_data = context.intermediate.get("financial_data", {})
        fin_interpretation = context.intermediate.get("financial_interpretation", "")
        request = context.state.original_request

        # 券商研报 + 评级 + 盈利预测
        reports_text = ""
        try:
            codes = context.state.stock_codes or []
            if codes:
                r = await asyncio.wait_for(call_tool("research_report", stock_code=codes[0], top=5), timeout=20)
                if r and r.get("status") == "success":
                    reports_text = "\n".join(
                        f"- [{x.get('东财评级', '?')}] {x.get('机构', '')}: {x.get('报告名称', '')[:60]} "
                        f"({x.get('日期', '')})" for x in r.get("reports", [])[:5])
                    if r.get("rating_counts"):
                        reports_text += f"\n近一年评级分布: {r['rating_counts']} (共{r.get('report_count', 0)}篇)"
        except Exception:
            pass

        # 显式提取估值/比率/资金流等结构化数据（避免 str 截断导致数据缺失与幻觉）
        key_lines = []
        for k, v in (fin_data or {}).items():
            if k.endswith("_valuation") and isinstance(v, dict):
                key_lines.append(
                    f"[估值 {k}] 截至{v.get('as_of')}: PE(TTM)={v.get('pe_ttm')}倍(历史分位{v.get('pe_ttm_percentile')}%), "
                    f"PB={v.get('pb')}倍(分位{v.get('pb_percentile')}%), PS={v.get('ps')}倍(分位{v.get('ps_percentile')}%), "
                    f"来源{v.get('source')}")
            elif k.endswith("_ratio") and isinstance(v, dict):
                key_lines.append(f"[财务比率 {k}] {str(v.get('periods'))[:600]}")
            elif k.endswith("_fund_flow") and isinstance(v, dict):
                inst = v.get("instant") or {}
                tot = v.get("totals") or {}
                recent = v.get("recent") or []
                line = f"[资金流 {k}] 来源{v.get('source')}"
                if tot.get("d5_main_net_yi") is not None:
                    line += f" 5日主力净流入={tot.get('d5_main_net_yi')}亿 10日={tot.get('d10_main_net_yi')}亿"
                if recent:
                    line += " 近期(亿): " + ", ".join(f"{r['date'][-5:]}={r['main_net']}" for r in recent[-5:])
                elif inst:
                    line += f" 当日主力净流入={inst.get('main_net_yi')}亿(流入{inst.get('in_yi')}/流出{inst.get('out_yi')})"
                key_lines.append(line)
            elif k.endswith("_industry_info") and isinstance(v, dict):
                bh = v.get("board_history") or {}
                key_lines.append(
                    f"[行业 {k}] 所属行业={v.get('industry')}, 板块最新={bh.get('latest_close')} "
                    f"涨跌={bh.get('latest_change_pct')}% 近30日={bh.get('recent_30d_close')}")

        prompt = f"""基于以下财务数据，进行基本面分析：

## 研究主题
{request}

## 关键结构化数据（估值/比率/资金流/行业）
{chr(10).join(key_lines) if key_lines else '暂无'}

## 财务数据
{str(fin_data)[:2000] if fin_data else '暂无数据'}

## 财务解读
{fin_interpretation[:1500] if fin_interpretation else '暂无解读'}

## 券商研报与评级
{reports_text if reports_text else '暂无研报数据'}

请给出：
1. 核心观点（1-2句话）
2. 关键指标分析（盈利能力、成长性、财务健康度）
3. 估值评估（严格引用上方 PE/PB 具体数值与分位数，不得编造）
4. 券商观点汇总（评级分布、盈利预测要点）
5. 基本面评级（看涨/中性/看跌）
"""

        analysis = await self._call_llm(prompt, temperature=0.5)
        context.save_intermediate("fundamental_analysis", analysis)

        return {
            "status": "success",
            "data": {"fundamental_analysis": analysis},
            "source": "fundamental_analyst"
        }
