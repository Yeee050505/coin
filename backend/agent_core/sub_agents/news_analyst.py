# coding: utf-8
"""新闻分析师 - 负责新闻舆情分析"""
import logging
from typing import Any, Dict
from app.agents.base.base_agent import BaseAgent, AgentContext
from app.tools.registry import call_tool

logger = logging.getLogger(__name__)


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
        codes = context.state.stock_codes or []
        import asyncio as _aio

        # 解析股票名称 (快讯关键词用名称, 代码在新闻标题中几乎不出现)
        company = ""
        fin_data = context.intermediate.get("financial_data") or {}
        for c in codes:
            snap = fin_data.get(f"{c}_stock_snapshot") or {}
            if snap.get("name"):
                company = str(snap["name"])
                break
        if not company and codes:
            try:
                r = await _aio.wait_for(call_tool("stock_snapshot", stock_code=codes[0]), timeout=15)
                if r and r.get("status") == "success" and r.get("name"):
                    company = str(r["name"])
            except Exception:
                pass

        announcements = []
        news_results = []
        # 主源1: 公司公告 (巨潮, 公司级真实事件)
        for c in codes:
            try:
                r = await _aio.wait_for(call_tool("company_announcement", stock_code=c, days=30), timeout=20)
                if r and r.get("status") == "success":
                    announcements.extend(r.get("results", []))
                else:
                    logger.warning(f"[news] announcement status={r.get('status') if r else None} msg={str(r)[:120]}")
            except Exception as e:
                logger.warning(f"[news] announcement failed for {c}: {type(e).__name__}: {e}")
        # 主源2: 结构化财经快讯 (东财, 用公司名称匹配)
        for kw in filter(None, [company, codes[0] if codes else ""]):
            try:
                r = await _aio.wait_for(call_tool("finance_news", keyword=kw, limit=12), timeout=20)
                if r and r.get("status") == "success" and (r.get("matched", 0) > 0 or not company):
                    for item in r.get("results", []):
                        news_results.append({"title": item.get("title", ""),
                                             "content": f"{item.get('summary', '')} ({item.get('time', '')})",
                                             "url": item.get("url", "")})
                    if r.get("matched", 0) >= 3:
                        break
            except Exception as e:
                logger.warning(f"[news] finance_news failed for {kw}: {type(e).__name__}: {e}")

        # 备源: 联网搜索 (短查询+站点限定, 过去长查询会搜到百科/官网等无关页)
        q_name = company or request
        for q in [f"{q_name} 最新消息", f"{q_name} site:finance.sina.com.cn"]:
            if len(news_results) >= 8:
                break
            try:
                r = await call_tool("web_search", query=q, max_results=5)
                if r and r.get("status") == "success":
                    news_results.extend(r.get("results", []))
            except Exception:
                pass

        # 去重 (标题前缀比对)
        seen, uniq = set(), []
        for s in news_results:
            t = (s.get("title") or "").strip()[:30]
            if t and t not in seen:
                seen.add(t)
                uniq.append(s)
        news_results = uniq

        ann_text = "\n".join(f"- [{a.get('time','')}] {a.get('title','')}" for a in announcements[:8])
        news_text = "\n".join([f"- {s.get('title','')}: {s.get('content','')[:300]}" for s in news_results[:8]])

        prompt = f"""基于以下信息，进行新闻舆情分析：

## 研究主题
{request}
{'公司名称: ' + company if company else ''}

## 公司公告（近30天, 来源巨潮资讯）
{ann_text if ann_text else '该渠道未获取'}

## 联网新闻
{news_text if news_text else '该渠道未获取'}

## 搜索综合
{search_synthesis[:2000] if search_synthesis else '暂无'}

请给出：
1. 核心观点（1-2句话）
2. 重要事件汇总（列出3-5条，优先引用上方公告与新闻的具体条目）
3. 政策/行业影响分析（若无相关政策新闻，基于公告与行业背景分析）
4. 新闻评级（利好/中性/利空）

要求：数字与事件必须来自上方数据；仅当三个渠道均无相关信息时才可写"信息不足"。
"""
        logger.info(f"[news] codes={codes} company={company!r} ann={len(announcements)} "
                    f"news={len(news_results)} prompt_len={len(prompt)}")

        analysis = await self._call_llm(prompt, temperature=0.5)
        context.save_intermediate("news_analysis", analysis)

        return {
            "status": "success",
            "data": {"news_analysis": analysis},
            "source": "news_analyst"
        }
