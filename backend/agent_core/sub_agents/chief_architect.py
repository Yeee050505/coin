# coding: utf-8
"""首席分析师 - 负责需求分析和研报框架规划"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext


class ChiefAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="chief_analyst",
            model_name="deepseek-chat",
            system_prompt="""
你是一名资深金融首席分析师。基于用户的研究需求：
1. 深入分析研究主题的核心维度
2. 设计结构化的研报大纲（包含章节和子章节）
3. 确定每个章节需要的数据类型
4. 输出标准 Markdown 格式的大纲，使用 ## 作为标题层级

只输出大纲，不要输出其他内容。
""",
        )

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        request = context.state.original_request
        outline = await self._call_llm(
            f"请为以下研究主题设计详细的研报大纲：\n\n{request}\n\n要求：6-8个章节，每个章节2-3个子章节，使用 Markdown ## 格式。\n\n用中文输出。"
        )
        context.save_intermediate("outline", outline)
        return {"status": "success", "data": {"outline": outline}, "source": "chief_analyst"}
