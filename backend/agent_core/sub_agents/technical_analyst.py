# coding: utf-8
"""技术分析师 - 负责技术指标与K线分析"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext


class TechnicalAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="technical_analyst",
            model_name="deepseek-chat",
            system_prompt="""
你是一名专业的技术分析师。基于获取到的行情数据，你需要：
1. 分析K线形态和趋势
2. 计算技术指标（MACD、KDJ、RSI、均线等）
3. 识别支撑位和阻力位
4. 判断买卖信号
5. 给出技术评级（看涨/中性/看跌）

输出格式：
- 核心观点（1-2句话）
- 技术指标分析
- 趋势判断
- 支撑位/阻力位
- 技术评级

用中文输出，数据必须来自提供的行情数据。
""",
        )

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        fin_data = context.intermediate.get("financial_data", {})
        search_synthesis = context.intermediate.get("search_synthesis", "")
        request = context.state.original_request

        stock_info = ""
        if fin_data:
            for key, val in fin_data.items():
                if isinstance(val, dict) and "price" in val:
                    stock_info = f"当前价格: {val.get('price')}\n"
                    hist = val.get("history", [])
                    if hist:
                        closes = [h.get("收盘", h.get("close", 0)) for h in hist[-30:] if h]
                        if closes:
                            stock_info += f"近30日收盘价: {closes}\n"
                            stock_info += f"最高: {max(closes)}, 最低: {min(closes)}\n"

        prompt = f"""基于以下行情数据，进行技术分析：

## 研究主题
{request}

## 行情数据
{stock_info if stock_info else '暂无行情数据'}

## 搜索结果
{search_synthesis[:1500] if search_synthesis else '暂无'}

请给出：
1. 核心观点（1-2句话）
2. 技术指标分析（MACD、KDJ、RSI、均线系统）
3. 趋势判断（上升/下降/震荡）
4. 支撑位和阻力位
5. 技术评级（看涨/中性/看跌）
"""

        analysis = await self._call_llm(prompt, temperature=0.5)
        context.save_intermediate("technical_analysis", analysis)

        return {
            "status": "success",
            "data": {"technical_analysis": analysis},
            "source": "technical_analyst"
        }
