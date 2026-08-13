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
        followup_question = context.intermediate.get("followup_question", "")
        existing_report = context.intermediate.get("existing_report", "")

        if followup_question:
            conv_text = ""
            conv = context.intermediate.get("conversation_window") or []
            if conv:
                conv_lines = [f"{i}. Q: {str(t['q'])}\n   A: {str(t['a'])}" for i, t in enumerate(conv, 1)]
                conv_text = "\n".join(conv_lines)
            prompt = f"""用户对已有研报提出追问（多轮），请撰写针对该追问的补充分析报告（Markdown，中文）。

## 最近对话（参考，勿重复已答复内容）
{conv_text if conv_text else '（本轮为首个追问，无历史）'}

## 用户追问
{followup_question}

## 已有报告（供引用结论、数据与结构）
{existing_report[:3000]}

## 报告大纲
{outline}

## 搜索综合
{search_synthesis}

## 财务数据
{fin_interpretation}

## 关键指标
{metrics_text}

要求：
1. 直接回答追问，给出明确结论与数据支撑
2. 与前几轮答复保持口径一致，避免重复
3. 引用已有报告结论时可标注"（原报告）"
4. 如需新数据支撑但未提供，标注"数据未提供"并基于现有数据分析
5. 给出投资建议与风险提示
6. 使用 Markdown 格式，中文撰写"""
        else:
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
        max_tokens = 2048 if followup_question else 4096
        final_report = await self._call_llm(prompt, temperature=0.5, provider_override=provider, max_tokens=max_tokens)

        context.save_intermediate("draft_report", final_report)
        context.state.final_report = final_report

        return {
            "status": "success",
            "data": {"report": final_report, "word_count": len(final_report)},
            "source": "chief_researcher"
        }
