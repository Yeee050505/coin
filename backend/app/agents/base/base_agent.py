# coding: utf-8
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from app.core.state import ResearchState
from app.tools.registry import call_tool
from app.core.config import settings
logger = logging.getLogger(__name__)


class AgentContext:
    def __init__(self, state: ResearchState):
        self.state = state
        self.intermediate: Dict[str, Any] = {}

    def save_intermediate(self, key: str, value: Any):
        self.intermediate[key] = value


class BaseAgent(ABC):
    def __init__(self, name: str, model_name: str = "deepseek-chat", system_prompt: str = ""):
        self.name = name
        self.model_name = model_name
        self.system_prompt = system_prompt
    async def _call_llm(self, user_message: str, system_override: str = "", temperature: float = 0.7) -> str:
        """Calls DeepSeek LLM via direct HTTP (avoids openai package encoding issues)."""
        import asyncio, httpx
        system = system_override or self.system_prompt
        if system:
            final_prompt = f"{system}\n\n---\n\n{user_message}"
        else:
            final_prompt = user_message
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": final_prompt}],
            "temperature": temperature,
            "max_tokens": 4096,
        }
        headers = {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            import json
            json_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"{settings.deepseek_api_base}/chat/completions",
                    content=json_bytes,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"] or ""
        except asyncio.TimeoutError:
            logger.error(f"[{self.name}] LLM call timed out after 90s")
            raise
        except Exception as e:
            logger.error(f"[{self.name}] LLM call failed: {e}")
            raise

    @abstractmethod
    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        """Sub-agents override this with ReAct + Reflection pattern."""

    async def reflect(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate the agent output."""
        issues = []
        data = result.get("data", {})
        if not data:
            issues.append("No data produced")
        if result.get("status") == "failed":
            issues.append(result.get("error", "unknown error"))
        return {"passed": len(issues) == 0, "issues": issues, "result": result}

    async def run(self, state: ResearchState) -> ResearchState:
        ctx = AgentContext(state)
        ctx.intermediate = dict(state.intermediate)
        orig_save = ctx.save_intermediate
        def save_both(key: str, value: Any):
            orig_save(key, value)
            state.intermediate[key] = value
        ctx.save_intermediate = save_both
        state.current_agent = self.name
        task = state.agent_tasks.get(self.name)
        if task:
            task.status = "running"
            task.started_at = datetime.now(timezone.utc).isoformat()

        try:
            result = await self.execute(ctx)
            reflection = await self.reflect(result)
            if not reflection["passed"]:
                for issue in reflection["issues"]:
                    logger.warning(f"[{self.name}] reflection: {issue}")
            state.agent_tasks[self.name].output_data = result
            state.agent_tasks[self.name].status = "success" if reflection["passed"] else "success"
        except Exception as e:
            logger.error(f"[{self.name}] failed: {e}")
            state.agent_tasks[self.name].status = "failed"
            state.agent_tasks[self.name].error = str(e)

        state.agent_tasks[self.name].completed_at = datetime.now(timezone.utc).isoformat()
        state.current_agent = None
        state.updated_at = datetime.now(timezone.utc).isoformat()
        return state
