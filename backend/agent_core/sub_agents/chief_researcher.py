# coding: utf-8
"""高级研究员 - 负责整合看涨/看空观点，撰写完整研报"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext


class SeniorResearcher(BaseAgent):
    def __init__(self):
        super().__init__(
            name="senior_researcher",
            model_name="deepseek-chat",
            system_prompt="""
你是一名资深金融高级研究员，擅长撰写深度研究报告。你的报告应该：
1. 结构清晰，逻辑严谨
2. 数据丰富，引用准确
3. 分析深入，有独到见解
4. 格式专业，可读性强

核心要求：所有数字（价格、营收、PE等）必须完全来自提供的财务数据分析部分。
绝对不要编造、猜测或使用你的训练知识中的数据。如果提供的数据缺少某个数字，标注"数据未提供"。

基于看涨研究员和看空研究员的分析，给出平衡的投资建议。
用 Markdown 格式撰写，包含数据驱动的分析和具体的投资建议。用中文撰写。
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
        charts_text = ""

        bullish_report = context.intermediate.get("bullish_report", "")
        bearish_report = context.intermediate.get("bearish_report", "")

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
            fundamental_view = context.intermediate.get("fundamental_analysis", "暂无基本面分析")
            news_view = context.intermediate.get("news_analysis", "暂无新闻分析")
            technical_view = context.intermediate.get("technical_analysis", "暂无技术分析")
            charts = context.intermediate.get("analysis_charts", [])

            charts_text = ""
            if charts:
                for i, c in enumerate(charts, 1):
                    spec = c.get("spec") or c
                    result = c.get("result", {}) or {}
                    chart_data = result.get("chart_data") or (spec.get("chart_data") if isinstance(spec, dict) else None)
                    title = spec.get("title", "") if isinstance(spec, dict) else ""
                    if chart_data:
                        charts_text += f"\n**图表{i}**: {title}\n![图表]({chart_data})\n"
                    else:
                        charts_text += f"\n**图表{i}**: {title} (生成失败)\n"

            prompt = f"""请撰写一份完整的深度研究报告。标题：{title}

## 报告大纲
{outline}

## 搜索结果综合
{search_synthesis}

## 财务数据分析
{fin_interpretation}

## 关键指标
{metrics_text}

## 基本面分析师观点
{fundamental_view}

## 新闻分析师观点
{news_view}

## 技术分析师观点
{technical_view}

## 图表分析
{charts_text if charts_text else '暂无图表'}

要求：
1. 严格遵循大纲结构
2. 每个章节至少300字，包含具体数据和分析
3. 在报告中标注数据来源
4. 在结尾给出投资建议和风险提示
5. 整合四位分析师的观点，给出平衡的结论
6. 使用 Markdown 格式，用中文撰写"""
        instruction = context.intermediate.get("research_instruction", "")
        if instruction:
            prompt += f"\n\n## 修改要求（来自审查反馈，必须落实）\n{instruction}\n"
        provider = context.intermediate.get("research_provider", "")
        max_tokens = 2048 if followup_question else 4096
        final_report = await self._call_llm(prompt, temperature=0.5, provider_override=provider, max_tokens=max_tokens)

        # base64 图表 LLM 无法可靠复写 -> 程序化注入附录
        if not followup_question and charts_text and "data:image/svg" not in (final_report or ""):
            final_report = (final_report or "") + "\n\n---\n\n## 附录：关键数据图表\n" + charts_text

        context.save_intermediate("draft_report", final_report)
        context.state.final_report = final_report

        return {
            "status": "success",
            "data": {"report": final_report, "word_count": len(final_report)},
            "source": "chief_researcher"
        }
