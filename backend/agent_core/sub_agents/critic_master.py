# coding: utf-8
"""Critic Agent - uses LLM to review report quality"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
import json, re


class CriticMaster(BaseAgent):
    def __init__(self):
        super().__init__(
            name="critic_master",
            model_name="deepseek-chat",
            system_prompt="""
You are a strict report quality control expert. When reviewing a report, focus on:
1. Logical rigor: whether arguments are sufficient, conclusions are data-supported
2. Data accuracy: whether data citations are reasonable, calculations are correct
3. Completeness: whether all necessary dimensions are covered
4. Practicality: whether investment recommendations are specific and actionable
5. Expression quality: whether language is professional and clear

Output JSON format:
{ "passed": true/false, "score": 0-100, "issues": ["issue1", "issue2"], "strengths": ["strength1"], "suggestions": ["suggestion1"] }

Output in Chinese.
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
        review_response = await self._call_llm(
            f"Strictly review the quality of the following research report:\n\n{report_snippet}\n\nScore and comment from 5 dimensions: logic, data, completeness, practicality, expression."
        )

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

        context.state.review_passed = passed
        context.state.review_feedback = feedback
        context.state.review_score = float(score)
        context.state.review_attempts += 1

        return {
            "status": "success",
            "data": {"passed": passed, "score": score, "feedback": feedback},
            "source": "critic_master"
        }
