# coding: utf-8
"""新闻分析师 - 负责新闻舆情分析"""
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool


class NewsAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="news_analyst",
            model_name="deepseek-chat",
            system_prompt="""
你是一名专业的新闻分析师。你需要分析新闻舆情：
1. 收集最近的公司新闻和行业动态
2. 分析政策变化对公司的影响
3. 识别重大事件和风险点
4. 评估新闻的正负面影响
5. 给出新闻评级（利好/中性/利空）

输出格式：
- 核心观点（1-2句话）
- 重要新闻汇总
- 政策影响分析
- 新闻评级

用中文输出。
""",
        )

    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        search_synthesis = context.intermediate.get("search_synthesis", "")
        request = context.state.original_request

        news_results = []
        # 主源: 结构化财经快讯 (东财, 关键词匹配)
        try:
            import asyncio as _aio
            kw = context.state.stock_codes[0] if context.state.stock_codes else ""
            r = await _aio.wait_for(call_tool("finance_news", keyword=kw, limit=12), timeout=20)
            if r and r.get("status") == "success":
                for item in r.get("results", []):
                    news_results.append({"title": item.get("title", ""),
                                         "content": f"{item.get('summary', '')} ({item.get('time', '')})",
                                         "url": item.get("url", "")})
        except Exception:
            pass
        # 备源: 联网搜索补充 (关键词匹配不足时更多依赖)
        try:
            r = await call_tool("web_search", query=f"{request} 最新新闻", max_results=5)
            if r and r.get("status") == "success":
                news_results.extend(r.get("results", []))
        except Exception:
            pass

        if len(news_results) < 6:
            try:
                r = await call_tool("web_search", query=f"{request} 政策 行业动态", max_results=3)
                if r and r.get("status") == "success":
                    news_results.extend(r.get("results", []))
            except Exception:
                pass

        news_text = "\n".join([f"- {s.get('title','')}: {s.get('content','')[:300]}" for s in news_results[:8]])

        prompt = f"""基于以下信息，进行新闻舆情分析：

## 研究主题
{request}

## 搜索结果
{search_synthesis[:2000] if search_synthesis else '暂无'}

## 新闻数据
{news_text if news_text else '暂无新闻数据'}

请给出：
1. 核心观点（1-2句话）
2. 重要新闻汇总（列出3-5条关键新闻）
3. 政策影响分析（行业政策、监管变化等）
4. 新闻评级（利好/中性/利空）
"""

        analysis = await self._call_llm(prompt, temperature=0.5)
        context.save_intermediate("news_analysis", analysis)

        return {
            "status": "success",
            "data": {"news_analysis": analysis},
            "source": "news_analyst"
        }
