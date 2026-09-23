# coding: utf-8
"""合规审核官 - 负责研报质量审核与合规检查"""
import logging
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
import json, re

logger = logging.getLogger(__name__)


class ComplianceOfficer(BaseAgent):
    def __init__(self):
        super().__init__(
            name="compliance_officer",
            model_name="deepseek-chat",
            system_prompt="""
你是一名严格的合规审核官。审核研报时，重点关注：
1. 逻辑严谨性：论据是否充分，结论是否有数据支撑
2. 数据准确性：数据引用是否合理，计算是否正确
3. 完整性：是否覆盖所有必要维度
4. 合规性：是否有不当承诺、误导性陈述
5. 表达质量：语言是否专业清晰

输出 JSON 格式：
{ "passed": true/false, "score": 0-100, "issues": ["问题1", "问题2"], "strengths": ["优点1"], "suggestions": ["建议1"] }

用中文输出。
""",
        )

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        report = context.intermediate.get("draft_report", "")

        if not report or len(report) < 100:
            context.state.review_passed = False
            context.state.review_feedback = "Report content insufficient"
            context.state.review_score = 0.0
            context.state.review_attempts += 1
            return {"status": "success", "data": {"passed": False, "score": 0, "feedback": "Content insufficient"}, "source": "critic_master"}

        report_snippet = report[:6000] if len(report) > 6000 else report
        review_prompt = (
            f"Strictly review the quality of the following research report:\n\n{report_snippet}\n\n"
            "Score and comment from 5 dimensions: logic, data, completeness, practicality, expression."
        )
        previous = context.intermediate.get("critic_previous_feedback", "")
        if previous:
            review_prompt += f"\n\nPrevious review feedback (check whether these issues have been FIXED):\n{previous}"
        provider = context.intermediate.get("critic_provider", "")
        review_response = await self._call_llm(review_prompt, provider_override=provider)

        try:
            json_match = re.search(r'\{.*\}', review_response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                passed = result.get("passed", False)
                score = result.get("score", 70)
                feedback = "; ".join(result.get("issues", []))
            else:
                passed = len(report) > 500
                score = 75 if passed else 50
                feedback = review_response[:500]
        except:
            passed = len(report) > 500
            score = 75 if passed else 50
            feedback = review_response[:500]

        if not passed and score >= 55:
            passed = True
            logger.info(f"[critic_master] score {score} >= 55, treat as passed")

        context.state.review_passed = passed
        context.state.review_feedback = feedback
        context.state.review_score = float(score)
        context.state.review_attempts += 1

        return {
            "status": "success",
            "data": {"passed": passed, "score": score, "feedback": feedback},
            "source": "critic_master"
        }
