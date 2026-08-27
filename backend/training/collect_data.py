# coding: utf-8
"""从 DB 采集训练数据，输出 JSONL 文件。

支持 4 个训练方向：
1. react_protocol   — ReAct JSON 协议 (tool calling 格式训练)
2. report_writing   — 研报撰写 (prompt → report)
3. critic_review    — Critic 评审 (report → JSON 评分)
4. supervisor_dispatch — Supervisor 决策 (snapshot → tool dispatch)

用法:
    cd backend
    python -m training.collect_data --direction all
    python -m training.collect_data --direction react_protocol
    python -m training.collect_data --min-score 70
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.database import ResearchProject, ResearchTask


def get_db_session():
    from app.core.config import settings
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine)()


def collect_report_writing_data(session, min_score: float = 0) -> List[Dict]:
    """从 ResearchProject.report_content 采集报告撰写训练数据。"""
    samples = []
    projects = session.query(ResearchProject).filter(
        ResearchProject.status.in_(["success", "completed_with_issues"])
    ).all()

    for p in projects:
        if not p.report_content or len(p.report_content) < 200:
            continue

        score = _get_project_score(session, p.id)
        if score and score < min_score:
            continue

        outline = _get_agent_output(session, p.id, "chief_architect", "outline")
        search_synth = _get_agent_output(session, p.id, "deep_scout", "search_synthesis")
        fin_data = _get_agent_output(session, p.id, "chief_data_engineer", "financial_interpretation")

        system_prompt = (
            "You are a senior financial research analyst, expert at writing in-depth research reports. "
            "Your reports should have clear structure, be data-rich with accurate citations, "
            "have deep analysis and unique insights. CRITICAL: All numbers must come EXCLUSIVELY "
            "from the provided data. NEVER fabricate numerical data. Write in Chinese."
        )

        user_prompt = f"""Write a complete in-depth research report. Title: {p.title}

## Report Outline
{outline or '(not available)'}

## Search Results Summary
{search_synth or '(not available)'}

## Financial Data Analysis
{fin_data or '(not available)'}

Requirements:
1. Follow the outline structure strictly
2. Each section at least 300 characters, include specific data and analysis
3. Mark data sources in the report
4. Give investment recommendations and risk warnings at the end
5. Use Markdown format throughout

Write in Chinese."""

        samples.append({
            "direction": "report_writing",
            "system": system_prompt,
            "input": user_prompt,
            "output": p.report_content[:8000],
            "project_id": p.id,
            "score": score,
        })

    logger.info(f"report_writing: collected {len(samples)} samples")
    return samples


def collect_critic_review_data(session, min_score: float = 0) -> List[Dict]:
    """从 critic_master 的 output_data 采集评审训练数据。"""
    samples = []
    tasks = session.query(ResearchTask).filter(
        ResearchTask.agent_name == "critic_master",
        ResearchTask.status == "success"
    ).all()

    for t in tasks:
        od = t.output_data or {}
        if not isinstance(od, dict):
            continue
        if "score" not in od:
            continue

        project = session.query(ResearchProject).filter(
            ResearchProject.id == t.project_id
        ).first()
        if not project or not project.report_content:
            continue

        score = od.get("score", 0)
        if score < min_score:
            continue

        report_snippet = project.report_content[:5000]
        review_result = json.dumps({
            "passed": od.get("passed", False),
            "score": od.get("score", 0),
            "issues": od.get("issues", []),
            "strengths": od.get("strengths", []),
            "suggestions": od.get("suggestions", []),
        }, ensure_ascii=False)

        samples.append({
            "direction": "critic_review",
            "system": (
                "You are a strict report quality control expert. Review the report and output "
                "JSON: {passed, score, issues, strengths, suggestions}. Output in Chinese."
            ),
            "input": f"Review this research report:\n\n{report_snippet}",
            "output": review_result,
            "project_id": t.project_id,
            "score": score,
        })

    logger.info(f"critic_review: collected {len(samples)} samples")
    return samples


def collect_react_protocol_data(session) -> List[Dict]:
    """构造 ReAct JSON 协议训练数据 (合成 + 规则生成)。"""
    samples = []
    tools = [
        {"name": "web_search", "description": "Search web for information", "args": "query: str, max_results: int"},
        {"name": "fetch_financial_data", "description": "Fetch stock financial data", "args": "stock_code: str, indicator: str, years: int"},
        {"name": "run_chart_code", "description": "Generate chart", "args": "chart_type: str, title: str, labels: list, values: list"},
    ]

    tool_descriptions = "\n".join(
        f"- {t['name']}: {t['description']} args: {t['args']}" for t in tools
    )

    system_prompt = (
        "You are a financial research assistant. Use tools to gather data.\n\n"
        f"Tools available:\n{tool_descriptions}\n\n"
        'When a tool is needed, reply with ONLY: {"tool": "<name>", "args": {...}}\n'
        'When the final answer is ready, reply with ONLY: {"answer": "<text>"}\n'
        "Never output anything other than that JSON."
    )

    stocks = [
        ("完美世界", "002624"), ("贵州茅台", "600519"), ("五粮液", "000858"),
        ("宁德时代", "300750"), ("比亚迪", "002594"), ("中国平安", "601318"),
        ("招商银行", "600036"), ("格力电器", "000651"), ("美的集团", "000333"),
        ("恒瑞医药", "600276"), ("药明康德", "603259"), ("隆基绿能", "601012"),
        ("海康威视", "002415"), ("京东方A", "000725"), ("中兴通讯", "000063"),
    ]

    search_angles = [
        "公司概况", "最新动态", "财务数据", "行业地位", "竞争优势",
        "风险因素", "管理层变动", "股东结构", "研发投入", "市场份额",
        "营收趋势", "利润分析", "资产负债", "现金流", "分红派息",
    ]

    indicators = ["overview", "income", "balance"]
    chart_types = ["bar", "line", "pie"]

    for name, code in stocks:
        angle = search_angles[hash(name) % len(search_angles)]
        samples.append({
            "direction": "react_protocol",
            "system": system_prompt,
            "input": f"Research topic: {name}({code}). Search for company overview.",
            "output": json.dumps({"tool": "web_search", "args": {"query": f"{name} {code} {angle}", "max_results": 5}}, ensure_ascii=False),
            "project_id": 0, "score": 100,
        })
        samples.append({
            "direction": "react_protocol",
            "system": system_prompt,
            "input": f"Got {name} overview. Now get income statement data.",
            "output": json.dumps({"tool": "fetch_financial_data", "args": {"stock_code": code, "indicator": "income", "years": 3}}, ensure_ascii=False),
            "project_id": 0, "score": 100,
        })
        samples.append({
            "direction": "react_protocol",
            "system": system_prompt,
            "input": f"Got {name} income data. Get balance sheet too.",
            "output": json.dumps({"tool": "fetch_financial_data", "args": {"stock_code": code, "indicator": "balance", "years": 3}}, ensure_ascii=False),
            "project_id": 0, "score": 100,
        })

    multi_step_scenarios = [
        {
            "context": "Research topic: 宁德时代(300750). Start by searching for company overview.",
            "output": json.dumps({"tool": "web_search", "args": {"query": "宁德时代 300750 公司概况 2024", "max_results": 5}}, ensure_ascii=False),
        },
        {
            "context": "Search results show 宁德时代 is global battery leader with 4000亿 revenue. Get financial statements.",
            "output": json.dumps({"tool": "fetch_financial_data", "args": {"stock_code": "300750", "indicator": "income", "years": 3}}, ensure_ascii=False),
        },
        {
            "context": "Income data: revenue growing 25% YoY. Need balance sheet for leverage analysis.",
            "output": json.dumps({"tool": "fetch_financial_data", "args": {"stock_code": "300750", "indicator": "balance", "years": 3}}, ensure_ascii=False),
        },
        {
            "context": "All financial data collected. Generate a revenue trend chart.",
            "output": json.dumps({"tool": "run_chart_code", "args": {"chart_type": "bar", "title": "宁德时代营收趋势", "labels": ["2022", "2023", "2024"], "values": [3286, 4009, 4500]}}, ensure_ascii=False),
        },
        {
            "context": "Chart generated. Also need a profit margin comparison chart.",
            "output": json.dumps({"tool": "run_chart_code", "args": {"chart_type": "line", "title": "宁德时代净利润率趋势", "labels": ["2022", "2023", "2024"], "values": [10.2, 11.5, 12.1]}}, ensure_ascii=False),
        },
        {
            "context": "All data and charts ready. Synthesize the final report.",
            "output": json.dumps({"answer": "宁德时代(300750)是全球动力电池龙头企业。2024年营收约4500亿元，同比增长12%；净利润约545亿元，净利润率12.1%。公司在动力电池和储能领域均保持全球市占率第一，竞争优势明显。资产负债率合理，现金流健康。建议关注电池技术迭代和海外市场拓展风险。"}, ensure_ascii=False),
        },
    ]
    for step in multi_step_scenarios:
        samples.append({
            "direction": "react_protocol",
            "system": system_prompt,
            "input": step["context"],
            "output": step["output"],
            "project_id": 0, "score": 100,
        })

    error_recovery_samples = [
        {
            "context": "web_search returned no results. Try different search query for 贵州茅台.",
            "output": json.dumps({"tool": "web_search", "args": {"query": "贵州茅台 600519 公司基本面分析", "max_results": 5}}, ensure_ascii=False),
        },
        {
            "context": "fetch_financial_data timed out for stock 002594. Retry with different indicator.",
            "output": json.dumps({"tool": "fetch_financial_data", "args": {"stock_code": "002594", "indicator": "overview", "years": 3}}, ensure_ascii=False),
        },
        {
            "context": "Data source unavailable for 600036. Search web for financial data instead.",
            "output": json.dumps({"tool": "web_search", "args": {"query": "招商银行 600036 2024年财务数据 营收 净利润", "max_results": 5}}, ensure_ascii=False),
        },
        {
            "context": "Got partial data for 比亚迪. Need more industry comparison data.",
            "output": json.dumps({"tool": "web_search", "args": {"query": "比亚迪 002594 行业竞争格局 市场份额 2024", "max_results": 5}}, ensure_ascii=False),
        },
    ]
    for sample in error_recovery_samples:
        samples.append({
            "direction": "react_protocol",
            "system": system_prompt,
            "input": sample["context"],
            "output": sample["output"],
            "project_id": 0, "score": 100,
        })

    search_angle_variations = [
        ("海康威视", "002415", "安防行业市场格局"),
        ("隆基绿能", "601012", "光伏行业产能过剩风险"),
        ("恒瑞医药", "600276", "创新药研发管线进展"),
        ("药明康德", "603259", "CRO行业全球竞争格局"),
        ("美的集团", "000333", "家电行业出海战略"),
        ("格力电器", "000651", "空调市场份额变化"),
        ("中国平安", "601318", "保险行业转型金融科技"),
        ("中兴通讯", "000063", "5G设备市场份额"),
        ("京东方A", "000725", "面板行业周期性分析"),
    ]
    for name, code, angle in search_angle_variations:
        samples.append({
            "direction": "react_protocol",
            "system": system_prompt,
            "input": f"Research {name}({code}). Search for: {angle}.",
            "output": json.dumps({"tool": "web_search", "args": {"query": f"{name} {code} {angle}", "max_results": 5}}, ensure_ascii=False),
            "project_id": 0, "score": 100,
        })

    chart_scenarios = [
        {"input": "Generate a pie chart for 贵州茅台 revenue breakdown by product line.",
         "output": json.dumps({"tool": "run_chart_code", "args": {"chart_type": "pie", "title": "贵州茅台营收构成", "labels": ["茅台酒", "系列酒", "其他"], "values": [1200, 200, 100]}}, ensure_ascii=False)},
        {"input": "Create a line chart showing 比亚迪 quarterly sales trend.",
         "output": json.dumps({"tool": "run_chart_code", "args": {"chart_type": "line", "title": "比亚迪季度销量趋势", "labels": ["Q1", "Q2", "Q3", "Q4"], "values": [552000, 623000, 701000, 758000]}}, ensure_ascii=False)},
        {"input": "Make a bar chart comparing top 3 battery makers market share.",
         "output": json.dumps({"tool": "run_chart_code", "args": {"chart_type": "bar", "title": "动力电池市场格局", "labels": ["宁德时代", "比亚迪", "LG新能源"], "values": [36.8, 15.8, 13.6]}}, ensure_ascii=False)},
        {"input": "Generate a bar chart for 招商银行 assets vs liabilities trend.",
         "output": json.dumps({"tool": "run_chart_code", "args": {"chart_type": "bar", "title": "招商银行资产负债趋势", "labels": ["2022", "2023", "2024"], "values": [94000, 101000, 108000]}}, ensure_ascii=False)},
        {"input": "Create a line chart showing 恒瑞医药 R&D spending over years.",
         "output": json.dumps({"tool": "run_chart_code", "args": {"chart_type": "line", "title": "恒瑞医药研发投入趋势", "labels": ["2021", "2022", "2023", "2024"], "values": [48, 52, 59, 65]}}, ensure_ascii=False)},
    ]
    for scenario in chart_scenarios:
        samples.append({
            "direction": "react_protocol",
            "system": system_prompt,
            "input": scenario["input"],
            "output": scenario["output"],
            "project_id": 0, "score": 100,
        })

    answer_synthesis_samples = [
        {"context": "All data collected for 完美世界: overview, income, balance, charts.",
         "output": json.dumps({"answer": "完美世界(002624)是国内领先的游戏公司。2024年营收约85亿元，净利润约12亿元。公司在端游和手游领域均有布局，核心产品流水稳定。建议关注新游戏上线节奏和海外市场拓展。"}, ensure_ascii=False)},
        {"context": "All data collected for 五粮液: overview, income, balance.",
         "output": json.dumps({"answer": "五粮液(000858)是中国白酒行业第二大企业。2024年营收约850亿元，净利润约300亿元。高端白酒品牌护城河深厚，分红比例稳定在50%以上。风险在于白酒消费降级趋势。"}, ensure_ascii=False)},
        {"context": "Data synthesis complete for 中国平安: overview, financial statements.",
         "output": json.dumps({"answer": "中国平安(601318)是国内最大的综合金融集团。2024年营收约9500亿元，净利润约1200亿元。寿险改革成效显现，科技板块贡献提升。需关注房地产敞口风险和利率下行影响。"}, ensure_ascii=False)},
        {"context": "Research complete for 美的集团: all data and charts ready.",
         "output": json.dumps({"answer": "美的集团(000333)是全球家电龙头。2024年营收约3700亿元，净利润约350亿元。海外营收占比超40%，ToB业务（机器人与自动化）增长迅速。估值合理，分红率约70%。"}, ensure_ascii=False)},
    ]
    for sample in answer_synthesis_samples:
        samples.append({
            "direction": "react_protocol",
            "system": system_prompt,
            "input": sample["context"],
            "output": sample["output"],
            "project_id": 0, "score": 100,
        })

    logger.info(f"react_protocol: collected {len(samples)} samples")
    return samples


def collect_supervisor_dispatch_data(session) -> List[Dict]:
    """从历史任务构建 Supervisor 决策训练数据。"""
    samples = []
    projects = session.query(ResearchProject).filter(
        ResearchProject.status.in_(["success", "completed_with_issues"])
    ).all()

    for p in projects:
        tasks = session.query(ResearchTask).filter(
            ResearchTask.project_id == p.id,
            ResearchTask.status == "success"
        ).all()
        task_map = {t.agent_name: t for t in tasks}

        stages_done = set()
        for agent_name in ["chief_architect", "deep_scout", "chief_data_engineer", "data_analyst", "chief_researcher", "critic_master"]:
            if agent_name in task_map:
                tool_name = {
                    "chief_architect": "run_architect",
                    "deep_scout": "run_scout",
                    "chief_data_engineer": "run_data_engineer",
                    "data_analyst": "run_analyst",
                    "chief_researcher": "run_researcher",
                    "critic_master": "run_critic",
                }.get(agent_name)
                if tool_name:
                    stages_done.add(tool_name)

        if not stages_done:
            continue

        snapshot = _build_snapshot(p, task_map)
        canonical = ["run_architect", "run_scout", "run_data_engineer", "run_analyst", "run_researcher"]
        for stage in canonical:
            if stage not in stages_done:
                decision = json.dumps({"tool": stage, "args": {}}, ensure_ascii=False)
                samples.append({
                    "direction": "supervisor_dispatch",
                    "system": (
                        "You are the Supervisor of a multi-agent research team. "
                        "Decide which worker to dispatch next based on the stage snapshot. "
                        "Output ONLY JSON: {\"tool\": \"<worker_name>\", \"args\": {...}}"
                    ),
                    "input": snapshot,
                    "output": decision,
                    "project_id": p.id,
                    "score": 100,
                })
                break

    logger.info(f"supervisor_dispatch: collected {len(samples)} samples")
    return samples


def _get_project_score(session, project_id: int) -> Optional[float]:
    task = session.query(ResearchTask).filter(
        ResearchTask.project_id == project_id,
        ResearchTask.agent_name == "critic_master"
    ).first()
    if task and task.output_data:
        return task.output_data.get("score")
    return None


def _get_agent_output(session, project_id: int, agent_name: str, key: str) -> Optional[str]:
    task = session.query(ResearchTask).filter(
        ResearchTask.project_id == project_id,
        ResearchTask.agent_name == agent_name
    ).first()
    if task and task.output_data:
        val = task.output_data.get(key)
        if isinstance(val, str):
            return val
        if isinstance(val, dict):
            return val.get(key, "")
    return None


def _build_snapshot(project, task_map: Dict) -> str:
    it = {}
    for name, task in task_map.items():
        if task.output_data and isinstance(task.output_data, dict):
            it.update(task.output_data)

    return "\n".join([
        f"Task: {project.title}",
        "Stage status:",
        f"- outline: {'done' if it.get('outline') else 'pending'}",
        f"- search: {'done' if it.get('search_synthesis') else 'pending'}",
        f"- financial data: {'done' if it.get('financial_interpretation') else 'pending'}",
        f"- charts: {'done' if it.get('analysis_charts') else 'pending'}",
        f"- draft report: {'done' if it.get('draft_report') else 'pending'}",
        "Decide the next worker to dispatch.",
    ])


def save_jsonl(samples: List[Dict], output_dir: str, direction: str):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{direction}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    logger.info(f"Saved {len(samples)} samples to {path}")


def main():
    parser = argparse.ArgumentParser(description="Collect LoRA training data from DB")
    parser.add_argument("--direction", default="all",
                        choices=["all", "react_protocol", "report_writing", "critic_review", "supervisor_dispatch"])
    parser.add_argument("--min-score", type=float, default=0, help="Minimum critic score filter")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "data"))
    args = parser.parse_args()

    session = get_db_session()
    all_samples = []

    directions = ["react_protocol", "report_writing", "critic_review", "supervisor_dispatch"] if args.direction == "all" else [args.direction]

    for d in directions:
        if d == "react_protocol":
            samples = collect_react_protocol_data(session)
        elif d == "report_writing":
            samples = collect_report_writing_data(session, args.min_score)
        elif d == "critic_review":
            samples = collect_critic_review_data(session, args.min_score)
        elif d == "supervisor_dispatch":
            samples = collect_supervisor_dispatch_data(session)
        else:
            continue
        all_samples.extend(samples)
        save_jsonl(samples, args.output_dir, d)

    session.close()
    logger.info(f"Total: {len(all_samples)} samples across {len(directions)} directions")


if __name__ == "__main__":
    main()
