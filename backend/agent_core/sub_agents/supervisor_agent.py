# coding: utf-8
"""Supervisor Agent - LLM-driven dynamic orchestration (multi-agent supervisor pattern).
Decides round-by-round which worker agent to dispatch, supports multi-round follow-up
(critic feedback -> researcher revision loop)."""
import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.base.base_agent import BaseAgent

logger = logging.getLogger(__name__)

SUPERVISOR_PROMPT = """你是多智能体研究团队的 Supervisor（主控）。你负责在运行时决定派遣哪个 worker、顺序和轮次。

可派遣的 worker（以工具形式调用）：
- run_analyst: 生成研报大纲（第一阶段）
- run_data_engineer: 获取股票真实财务数据
- run_quant: 基于财务数据生成图表
- run_fundamental: 基本面分析（财务报表、估值）
- run_news: 新闻分析（舆情、政策）
- run_technical: 技术分析（K线、技术指标）
- run_researcher: 整合所有分析，撰写研报草稿
- run_compliance: 质量审核，返回分数与反馈

规则：
1. 输入顶部是当前阶段状态。每次只派遣一个最需要的 worker，跳过已完成阶段。
2. 当 compliance 未通过时，可以带参数 {"instruction": "基于反馈的修改要求"} 重新派遣 run_researcher，再派 run_compliance 复查，如此迭代直到通过。
3. 全部完成后，回答 {"answer": "报告已完成"}。
4. 决策要高效，不重复已完成阶段。
"""

LOCAL_LOOP_INSTRUCTION = (
    '需要派遣 worker 时，只输出 JSON: {"tool": "<worker名>", "args": {...}}。'
    '全部完成时，只输出 JSON: {"answer": "<完成说明>"}。不要输出其他任何内容。\n'
    '示例: {"tool": "run_analyst", "args": {}}\n'
    '示例: {"tool": "run_researcher", "args": {"instruction": "补充行业竞争格局分析"}}\n'
    '示例: {"answer": "报告已完成"}'
)

WORKER_TOOL_DEFS: List[Dict] = [
    {"type": "function", "function": {"name": "run_analyst",
                                      "description": "生成研报大纲（第一阶段）",
                                       "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_data_engineer",
                                      "description": "获取股票真实财务数据（overview/income/balance）",
                                      "parameters": {"type": "object", "properties": {
                                          "stock_codes": {"type": "array", "items": {"type": "string"},
                                                          "description": "可选，股票代码列表"}}}}},
    {"type": "function", "function": {"name": "run_quant",
                                      "description": "基于财务数据生成 2-3 个图表",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_fundamental",
"description": "基本面分析（财务报表、估值）",
                                       "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_news",
                                      "description": "新闻分析（舆情、政策）",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_technical",
                                      "description": "技术分析（K线、技术指标）",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_researcher",
                                      "description": "整合所有分析，撰写研报草稿；若在 compliance 反馈后重跑，args.instruction 携带修改要求",
                                      "parameters": {"type": "object", "properties": {
                                          "instruction": {"type": "string",
                                                          "description": "可选，修改要求（如 compliance 反馈）"}}}}},
    {"type": "function", "function": {"name": "run_compliance",
                                      "description": "质量审核当前草稿，返回分数与反馈",
                                      "parameters": {"type": "object", "properties": {}}}},
]

WORKER_BY_TOOL = {
    "run_analyst": "chief_analyst",
    "run_data_engineer": "data_engineer",
    "run_quant": "quant_analyst",
    "run_fundamental": "fundamental_analyst",
    "run_news": "news_analyst",
    "run_technical": "technical_analyst",
    "run_researcher": "senior_researcher",
    "run_compliance": "compliance_officer",
}


class SupervisorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="supervisor",
            model_name="deepseek-chat",
            system_prompt=SUPERVISOR_PROMPT,
        )

    async def execute(self, context) -> Dict[str, Any]:
        raise RuntimeError("Supervisor runs via graph node (_node_supervisor), not BaseAgent.run")

    @property
    def tool_defs(self) -> List[Dict]:
        return [json.loads(json.dumps(t)) for t in WORKER_TOOL_DEFS]

    async def decide(self, snapshot: str, history: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """One LLM decision step. Returns:
        {"type": "tool", "tool": ..., "args": {...}}
        {"type": "finish", "content": ...}
        {"type": "invalid", "raw": ...}
        Local Qwen primary, DeepSeek API as fallback on local failure.
        """
        from app.core.config import settings
        history = history or []

        provider = settings.llm_provider
        if provider == "auto":
            from app.llm.router import route
            provider = await route("supervisor", snapshot)
        if provider == "local_qwen":
            try:
                from app.llm.local_qwen import generate, _parse_json
                msgs = [
                    {"role": "system", "content": self.system_prompt + "\n\n" + LOCAL_LOOP_INSTRUCTION},
                    *history,
                    {"role": "user", "content": snapshot},
                ]
                resp = await generate(msgs, temperature=0.3, max_new_tokens=800)
                parsed = _parse_json(resp)
                if parsed and parsed.get("tool") and isinstance(parsed.get("args"), dict):
                    return {"type": "tool", "tool": str(parsed["tool"]), "args": parsed["args"]}
                if parsed and "answer" in parsed:
                    return {"type": "finish", "content": str(parsed["answer"])}
                return {"type": "invalid", "raw": resp[:300]}
            except Exception as e:
                logger.warning(f"[supervisor] local decide failed, falling back to DeepSeek: {e}")
                if settings.deepseek_api_key:
                    return await self._decide_deepseek(snapshot, history)
                raise
        return await self._decide_deepseek(snapshot, history)

    async def _decide_deepseek(self, snapshot: str, history: List[Dict]) -> Dict[str, Any]:
        from app.core.config import settings
        import asyncio, httpx
        headers = {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                *history,
                {"role": "user", "content": snapshot},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
            "tools": self.tool_defs,
            "tool_choice": "auto",
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{settings.deepseek_api_base}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if calls:
            fn = calls[0].get("function", {})
            return {"type": "tool", "tool": fn.get("name", ""),
                    "args": self._parse_tool_args(fn.get("arguments", ""))}
        return {"type": "finish", "content": msg.get("content") or ""}
