# coding: utf-8
"""Deep Scout Agent - multi-source search with LLM synthesis"""
import asyncio
import logging
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool, get_tool_definitions

logger = logging.getLogger(__name__)


class DeepScout(BaseAgent):
    def __init__(self):
        super().__init__(
            name="deep_scout",
            model_name="deepseek-chat",
            system_prompt="""
You are a professional financial information gathering analyst. Based on search results, you need to:
1. Extract key facts and data points
2. Identify consensus and divergence between multiple sources
3. Note timeliness and reliability of information
4. Output findings in structured JSON format

Output in Chinese.
""",
        )

    def _search_tools(self) -> list:
        tools = {t["function"]["name"]: t for t in get_tool_definitions()}
        return [tools[n] for n in ("web_search",) if n in tools]

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        request = context.state.original_request
        search_results = []

        try:
            fc = await self._call_llm_with_tools(
                f"Research topic: {request}. Call web_search multiple times with different angles (company overview, industry data, latest developments). After gathering enough information, synthesize the key facts in Chinese.",
                tools=self._search_tools(),
                max_rounds=4,
            )
            for tc in fc.get("tool_calls", []):
                r = tc.get("result") or {}
                if tc["name"] == "web_search" and isinstance(r, dict):
                    search_results.extend(r.get("results", []))
            if search_results:
                context.save_intermediate("raw_search_results", search_results)
            if fc.get("content") and search_results:
                context.save_intermediate("search_synthesis", fc["content"])
                return {
                    "status": "success",
                    "data": {"web_results_count": len(search_results), "synthesis": fc["content"], "function_calling": True},
                    "source": "deep_scout"
                }
        except Exception as e:
            logger.warning(f"[{self.name}] function calling failed, falling back: {e}")

        if not search_results:
            queries = [f"{request}", f"{request} industry data", f"{request} latest developments"]
            results = await asyncio.gather(*[call_tool("web_search", query=q, max_results=3) for q in queries], return_exceptions=True)
            for r in results:
                if isinstance(r, dict) and r.get("results"):
                    search_results.extend(r["results"])
            context.save_intermediate("raw_search_results", search_results)

        if search_results:
            search_text = "\n\n---\n\n".join(
                f"Source: {s['title']}\n{s.get('content','')}"
                for s in search_results[:10]
            )
            synthesis = await self._call_llm(
                f"Please synthesize the following search results and extract key information related to the research topic '{request}':\n\n{search_text}"
            )
        else:
            synthesis = await self._call_llm(
                f"Based on your knowledge, provide key industry information, market size, main participants, and latest developments related to: {request}"
            )
        context.save_intermediate("search_synthesis", synthesis)

        return {
            "status": "success",
            "data": {"web_results_count": len(search_results), "synthesis": synthesis},
            "source": "deep_scout"
        }
