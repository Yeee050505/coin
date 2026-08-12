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
- run_architect: 生成研报大纲（第一阶段）
- run_scout: 多角度联网搜集信息
- run_data_engineer: 获取股票真实财务数据
- run_analyst: 基于财务数据生成图表
- run_researcher: 撰写研报草稿
- run_critic: 质量审查，返回分数与反馈

规则：
1. 输入顶部是当前阶段状态。每次只派遣一个最需要的 worker，跳过已完成阶段。
2. 当 critic 未通过时，可以带参数 {"instruction": "基于反馈的修改要求"} 重新派遣 run_researcher，再派 run_critic 复查，如此迭代直到通过。
3. 全部完成后，回答 {"answer": "报告已完成"}。
4. 决策要高效，不重复已完成阶段。
"""

LOCAL_LOOP_INSTRUCTION = (
    '需要派遣 worker 时，只输出 JSON: {"tool": "<worker名>", "args": {...}}。'
    '全部完成时，只输出 JSON: {"answer": "<完成说明>"}。不要输出其他任何内容。\n'
    '示例: {"tool": "run_architect", "args": {}}\n'
    '示例: {"tool": "run_researcher", "args": {"instruction": "补充行业竞争格局分析"}}\n'
    '示例: {"answer": "报告已完成"}'
)

WORKER_TOOL_DEFS: List[Dict] = [
    {"type": "function", "function": {"name": "run_architect",
                                      "description": "生成研报大纲（第一阶段）",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_scout",
                                      "description": "多角度联网搜集研究主题信息",
                                      "parameters": {"type": "object", "properties": {
                                          "focus": {"type": "string", "description": "可选的搜索角度/重点"}}}}},
    {"type": "function", "function": {"name": "run_data_engineer",
                                      "description": "获取股票真实财务数据（overview/income/balance）",
                                      "parameters": {"type": "object", "properties": {
                                          "stock_codes": {"type": "array", "items": {"type": "string"},
                                                          "description": "可选，股票代码列表"}}}}},
    {"type": "function", "function": {"name": "run_analyst",
                                      "description": "基于财务数据生成 2-3 个图表",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_researcher",
                                      "description": "撰写研报草稿；若在 critic 反馈后重跑，args.instruction 携带修改要求",
                                      "parameters": {"type": "object", "properties": {
                                          "instruction": {"type": "string",
                                                          "description": "可选，修改要求（如 critic 反馈）"}}}}},
    {"type": "function", "function": {"name": "run_critic",
                                      "description": "质量审查当前草稿，返回分数与反馈",
                                      "parameters": {"type": "object", "properties": {}}}},
]

WORKER_BY_TOOL = {
    "run_architect": "chief_architect",
    "run_scout": "deep_scout",
    "run_data_engineer": "chief_data_engineer",
    "run_analyst": "data_analyst",
    "run_researcher": "chief_researcher",
    "run_critic": "critic_master",
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
        """
        from app.core.config import settings
        history = history or []

        if settings.llm_provider == "local_qwen":
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
