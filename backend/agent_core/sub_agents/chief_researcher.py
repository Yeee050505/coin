# coding: utf-8
"""Research Writer Agent - uses LLM to write full report from all context"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext


class ChiefResearcher(BaseAgent):
    def __init__(self):
        super().__init__(
            name="chief_researcher",
            model_name="deepseek-chat",
            system_prompt="""
You are a senior financial research analyst, expert at writing in-depth research reports. Your reports should:
1. Have clear structure and rigorous logic
2. Be data-rich with accurate citations
3. Have deep analysis and unique insights
4. Be professionally formatted and readable

CRITICAL: All numbers (prices, revenues, PE ratios, etc.) must come EXCLUSIVELY from the provided Financial Data Analysis section. NEVER fabricate, guess, or use your training knowledge for numerical data. If the provided data lacks a specific number, state "数据未提供" instead of making one up.

Write in Markdown format, with data-driven analysis and specific investment recommendations. Write in Chinese.
""",
        )

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        outline = context.intermediate.get("outline", "")
        search_synthesis = context.intermediate.get("search_synthesis", "No search results available")
        fin_interpretation = context.intermediate.get("financial_interpretation", "No financial data available")
        metrics_text = context.intermediate.get("metrics_text", "")
        title = context.state.title

        prompt = f"""Write a complete in-depth research report. Title: {title}

## Report Outline
{outline}

## Search Results Summary
{search_synthesis}

## Financial Data Analysis
{fin_interpretation}

## Key Metrics
{metrics_text}

Requirements:
1. Follow the outline structure strictly
2. Each section at least 300 characters, include specific data and analysis
3. Mark data sources in the report
4. Give investment recommendations and risk warnings at the end
5. Use Markdown format throughout

Write in Chinese."""
        instruction = context.intermediate.get("research_instruction", "")
        if instruction:
            prompt += f"\n\n## 修改要求（来自审查反馈，必须落实）\n{instruction}\n"
        provider = context.intermediate.get("research_provider", "")
        final_report = await self._call_llm(prompt, temperature=0.5, provider_override=provider)

        context.save_intermediate("draft_report", final_report)
        context.state.final_report = final_report

        return {
            "status": "success",
            "data": {"report": final_report, "word_count": len(final_report)},
            "source": "chief_researcher"
        }
