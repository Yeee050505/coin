# coding: utf-8
"""基本面分析师 - 负责财务报表分析与估值"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext


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

        prompt = f"""基于以下财务数据，进行基本面分析：

## 研究主题
{request}

## 财务数据
{str(fin_data)[:3000] if fin_data else '暂无数据'}

## 财务解读
{fin_interpretation[:2000] if fin_interpretation else '暂无解读'}

请给出：
1. 核心观点（1-2句话）
2. 关键指标分析（盈利能力、成长性、财务健康度）
3. 估值评估（PE、PB等）
4. 基本面评级（看涨/中性/看跌）
"""

        analysis = await self._call_llm(prompt, temperature=0.5)
        context.save_intermediate("fundamental_analysis", analysis)

        return {
            "status": "success",
            "data": {"fundamental_analysis": analysis},
            "source": "fundamental_analyst"
        }
