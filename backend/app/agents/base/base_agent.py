# coding: utf-8
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
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
    async def _call_llm(self, user_message: str, system_override: str = "", temperature: float = 0.7,
                        provider_override: str = "", max_tokens: int = 4096) -> str:
        """Calls LLM: auto 路由(简单→local Qwen / 复杂→DeepSeek), local 异常时回退 DeepSeek."""
        from app.core.config import settings
        provider = provider_override or settings.llm_provider
        if provider == "auto":
            from app.llm.router import route
            provider = await route(self.name, user_message)
        if provider == "local_qwen":
            try:
                from app.llm.local_qwen import generate
                system = system_override or self.system_prompt
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": user_message})
                result = await generate(messages, temperature=temperature, max_new_tokens=max_tokens)
                if result.strip():
                    return result
                logger.warning(f"[{self.name}] local model returned empty, falling back to DeepSeek")
            except Exception as e:
                logger.warning(f"[{self.name}] local model failed, falling back to DeepSeek: {e}")
            if settings.deepseek_api_key:
                return await self._call_llm_deepseek(user_message, system_override, temperature, max_tokens)
            raise
        return await self._call_llm_deepseek(user_message, system_override, temperature, max_tokens)

    async def _call_llm_deepseek(self, user_message: str, system_override: str = "", temperature: float = 0.7,
                                 max_tokens: int = 4096) -> str:
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
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            import json
            json_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{settings.deepseek_api_base}/chat/completions",
                    content=json_bytes,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"] or ""
        except asyncio.TimeoutError:
            logger.error(f"[{self.name}] LLM call timed out after 180s")
            raise
        except Exception as e:
            logger.error(f"[{self.name}] LLM call failed: {e}")
            raise

    async def _call_llm_with_tools(self, user_message: str, tools: Optional[List[Dict]] = None,
                                   max_rounds: int = 4, temperature: float = 0.7,
                                   provider_override: str = "") -> Dict[str, Any]:
        """LLM with function calling: model picks tools + args, tools execute, loop until final answer.
        auto 路由(简单→local / 复杂→DeepSeek), local 异常时回退 DeepSeek."""
        from app.core.config import settings
        provider = provider_override or settings.llm_provider
        if provider == "auto":
            from app.llm.router import route
            provider = await route(self.name, user_message)
        if provider == "local_qwen":
            try:
                from app.llm.local_qwen import generate_with_tools
                return await generate_with_tools(self.system_prompt, user_message, tools or [],
                                                 max_rounds=max_rounds, temperature=temperature)
            except Exception as e:
                logger.warning(f"[{self.name}] local tool loop failed, falling back to DeepSeek: {e}")
                if settings.deepseek_api_key:
                    return await self._call_llm_with_tools_deepseek(
                        user_message, tools, max_rounds, temperature)
                raise
        return await self._call_llm_with_tools_deepseek(user_message, tools, max_rounds, temperature)

    async def _call_llm_with_tools_deepseek(self, user_message: str, tools: Optional[List[Dict]] = None,
                                            max_rounds: int = 4, temperature: float = 0.7) -> Dict[str, Any]:
        import asyncio, httpx, json
        messages: List[Dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_message})

        trace: List[Dict[str, Any]] = []
        headers = {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            for _ in range(max_rounds):
                payload = {
                    "model": self.model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": 4096,
                }
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                json_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                resp = await client.post(
                    f"{settings.deepseek_api_base}/chat/completions",
                    content=json_bytes,
                    headers=headers,
                )
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
                calls = msg.get("tool_calls") or []

                if not calls:
                    return {"content": msg.get("content") or "", "tool_calls": trace, "rounds": len(trace)}

                messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": [{"id": c.get("id", ""), "type": "function",
                                    "function": {"name": c["function"]["name"],
                                                 "arguments": c["function"].get("arguments", "{}")}} for c in calls],
                })
                results = await asyncio.gather(
                    *[self._execute_tool_call(c.get("function", {})) for c in calls],
                    return_exceptions=True,
                )
                for c, result in zip(calls, results):
                    if isinstance(result, BaseException):
                        result = {"status": "error", "message": str(result)[:200]}
                    trace.append({
                        "name": c["function"]["name"],
                        "args": self._parse_tool_args(c["function"].get("arguments", "")),
                        "result": result,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": c.get("id", f"call_{len(trace)}"),
                        "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
                    })
        return {"content": "", "tool_calls": trace, "rounds": max_rounds, "max_rounds_reached": True}

    async def _execute_tool_call(self, fn: Dict[str, Any]) -> Any:
        name = fn.get("name", "")
        args = self._parse_tool_args(fn.get("arguments", ""))
        return await call_tool(name, **args)

    @staticmethod
    def _parse_tool_args(arguments: str) -> Dict[str, Any]:
        import json
        try:
            parsed = json.loads(arguments or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

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
