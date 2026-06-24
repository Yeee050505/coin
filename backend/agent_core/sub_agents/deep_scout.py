# coding: utf-8
"""Deep Scout Agent - multi-source search with LLM synthesis"""
import asyncio
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool


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

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        request = context.state.original_request
        queries = [f"{request}", f"{request} industry data", f"{request} latest developments"]
        results = await asyncio.gather(*[call_tool("web_search", query=q, max_results=3) for q in queries], return_exceptions=True)
        search_results = []
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

        rag_results = await call_tool("retrieve_knowledge", query=request, top_k=5)
        context.save_intermediate("rag_results", rag_results.get("results", []))

        return {
            "status": "success",
            "data": {"web_results_count": len(search_results), "synthesis": synthesis},
            "source": "deep_scout"
        }
