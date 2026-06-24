# coding: utf-8
"""Architect Agent - uses LLM for research outline generation"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext


class ChiefArchitect(BaseAgent):
    def __init__(self):
        super().__init__(
            name="chief_architect",
            model_name="deepseek-chat",
            system_prompt="""
You are a senior financial research architect. Based on the user's research requirements:
1. Analyze the core dimensions of the research topic
2. Create a structured report outline (with chapters and sub-chapters)
3. Determine the data types needed for each chapter
4. Output the outline in standard Markdown format with ## heading levels

Only output the outline, no other content.
""",
        )

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        request = context.state.original_request
        outline = await self._call_llm(
            f"Please design a detailed research report outline for the following topic:\n\n{request}\n\nRequirements: 6-8 chapters, 2-3 sub-sections per chapter, use Markdown ## format.\n\nOutput in Chinese."
        )
        context.save_intermediate("outline", outline)
        return {"status": "success", "data": {"outline": outline}, "source": "chief_architect"}
