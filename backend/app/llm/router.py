# coding: utf-8
"""LLM 任务路由器: 简单任务→本地3B推理, 复杂推理→大模型API(DeepSeek等)

三层分类(网关侧轻量分层, 逐层递进):
  ① Agent白名单  — 确定性规则, 命中即返回, 零成本零延迟
  ② 启发式特征   — prompt长度(长文档推理) + 关键词(因果/交叉比对)
  ③ 3B分类器     — Agent不在白名单且启发式无结论时, 用3B本身输出单标签
"""
import logging

logger = logging.getLogger(__name__)

# 简单任务: 结构化输出/短文本摘要/数据透传, 3B可胜任 → 本地推理
SIMPLE_AGENTS = {
    "chief_analyst",       # 研报大纲, 结构化生成
    "data_engineer",       # 财务数据压缩摘要
    "quant_analyst",       # 图表spec JSON
    "technical_analyst",   # 技术指标数据透传解读
    "supervisor",          # 主控路由决策 JSON
}

# 复杂任务: 长文档推理/因果分析/多段交叉比对 → API
COMPLEX_AGENTS = {
    "fundamental_analyst",  # 估值与因果分析
    "news_analyst",         # 多源新闻交叉归纳
    "senior_researcher",    # 长文档研报写作
    "critic_master",        # 合规审校
    "compliance_officer",
}

COMPLEX_KEYWORDS = ("因果", "归因", "交叉比对", "对比多个", "综合判断", "深度推理", "矛盾之处", "互相印证")
LONG_PROMPT_THRESHOLD = 3000  # 字符, 超过视为长文档推理

_LOCAL_CLASSIFIER_PROMPT = (
    "判断下面任务属于简单还是复杂。只输出一个词: SIMPLE 或 COMPLEX。\n"
    "简单=关键词提取/摘要/格式化/短文本; 复杂=长文档推理/因果分析/多段交叉比对。\n\n任务:\n"
)


def _heuristic(prompt: str) -> str:
    """启发式判定, 返回 'simple'/'complex'/'' (空=无结论)"""
    if len(prompt or "") > LONG_PROMPT_THRESHOLD:
        return "complex"
    for kw in COMPLEX_KEYWORDS:
        if kw in prompt:
            return "complex"
    return ""


async def _classify_with_local(prompt: str) -> str:
    """3B分类器兜底: 输出单标签, 失败返回 '' (不影响主链路)"""
    try:
        from app.llm.local_qwen import generate
        msgs = [{"role": "user", "content": _LOCAL_CLASSIFIER_PROMPT + (prompt or "")[:1500]}]
        resp = await generate(msgs, temperature=0.0, max_new_tokens=4)
        tag = (resp or "").upper()
        if "COMPLEX" in tag:
            return "complex"
        if "SIMPLE" in tag:
            return "simple"
    except Exception as e:
        logger.warning(f"[router] local classifier failed: {e}")
    return ""


async def classify(agent_name: str, prompt: str) -> tuple:
    """返回 (complexity, reason)"""
    if agent_name in COMPLEX_AGENTS:
        return "complex", "agent:complex"
    if agent_name in SIMPLE_AGENTS:
        # 白名单simple, 但长prompt/因果关键词覆盖 → 仍升级
        h = _heuristic(prompt)
        if h == "complex":
            return "complex", "heuristic:override"
        return "simple", "agent:simple"
    h = _heuristic(prompt)
    if h:
        return h, f"heuristic:{'long' if h == 'complex' else 'kw'}"
    c = await _classify_with_local(prompt)
    if c:
        return c, "local_classifier"
    return "complex", "default:conservative"


async def route(agent_name: str, prompt: str) -> str:
    """返回 provider: 'local_qwen' | 'deepseek'"""
    complexity, reason = await classify(agent_name, prompt)
    provider = "local_qwen" if complexity == "simple" else "deepseek"
    logger.info(f"[router] {agent_name} -> {provider} ({complexity}/{reason}, "
                f"len={len(prompt or '')})")
    return provider
