"""LangGraph multi-agent orchestrator with conditional rollback"""
import logging
import math
import time
from datetime import datetime, date as _date, timezone
from typing import Any, Dict, Optional, List, TypedDict, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from app.core.state import ResearchState, AgentTaskState
from app.models.database import ResearchProject, ResearchTask
from agent_core.sub_agents.chief_architect import ChiefAnalyst
from agent_core.sub_agents.data_engineer import DataEngineer
from agent_core.sub_agents.data_analyst import QuantAnalyst
from agent_core.sub_agents.chief_researcher import SeniorResearcher
from agent_core.sub_agents.critic_master import ComplianceOfficer
from agent_core.sub_agents.supervisor_agent import SupervisorAgent, WORKER_BY_TOOL
from agent_core.sub_agents.fundamental_analyst import FundamentalAnalyst
from agent_core.sub_agents.news_analyst import NewsAnalyst
from agent_core.sub_agents.technical_analyst import TechnicalAnalyst
from agent_core.scheduler_agent.intent_parser import IntentParser

logger = logging.getLogger(__name__)
SUPERVISOR_MAX_ROUNDS = 8
CANONICAL_ORDER = ["run_analyst", "run_data_engineer", "run_quant", "run_fundamental",
                   "run_news", "run_technical", "run_researcher", "run_compliance"]
REVIEW_ESCALATE_AFTER = 2
CONV_WINDOW = 4


def _json_safe(obj):
    if isinstance(obj, (_date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return obj
    _np = type(obj).__module__
    if _np == "numpy":
        return _json_safe(obj.item()) if hasattr(obj, "item") else str(obj)
    if _np == "pandas" or _np.startswith("pandas."):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if hasattr(obj, "to_pydatetime"):
            return obj.to_pydatetime().isoformat()
        if obj is not obj:
            return None
        return str(obj)
    try:
        return int(obj)
    except (TypeError, ValueError):
        pass
    try:
        return float(obj)
    except (TypeError, ValueError):
        pass
    return str(obj)


def _merge_dict(a: dict, b: dict) -> dict:
    """Reducer: merge two dicts, right takes precedence."""
    if a is None:
        return b
    if b is None:
        return a
    return {**a, **b}


class GraphState(TypedDict):
    project_id: int
    title: str
    original_request: str
    stock_codes: list[str]
    status: str
    intermediate: Annotated[dict, _merge_dict]
    agent_outputs: Annotated[dict, _merge_dict]
    final_report: Optional[str]
    review_passed: bool
    review_feedback: Optional[str]
    review_score: Optional[float]
    review_attempts: int


class WorkflowGraph:
    def __init__(self):
        self.agents = {
            "supervisor": SupervisorAgent(),
            "chief_analyst": ChiefAnalyst(),
            "data_engineer": DataEngineer(),
            "quant_analyst": QuantAnalyst(),
            "senior_researcher": SeniorResearcher(),
            "compliance_officer": ComplianceOfficer(),
            "fundamental_analyst": FundamentalAnalyst(),
            "news_analyst": NewsAnalyst(),
            "technical_analyst": TechnicalAnalyst(),
        }
        self.parser = IntentParser()
        self._db = None
        self._pid = 0
        self._agent_durations: Dict[str, float] = {}

    async def _save_task(self, agent_name: str, output_data: Any, status: str, error: Optional[str] = None):
        if not self._db:
            return
        task = self._db.query(ResearchTask).filter(
            ResearchTask.project_id == self._pid, ResearchTask.agent_name == agent_name
        ).first()
        safe_data = _json_safe(output_data)
        now = datetime.now(timezone.utc)
        if task:
            task.status = status
            if safe_data:
                task.output_data = safe_data
            if error:
                task.error_message = error
            task.completed_at = now
        else:
            task = ResearchTask(
                project_id=self._pid, agent_name=agent_name, status=status,
                output_data=safe_data, error_message=error,
                started_at=now,
                completed_at=now,
            )
            self._db.add(task)
        try:
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _to_research_state(self, gs: GraphState) -> ResearchState:
        rs = ResearchState(
            project_id=gs["project_id"],
            title=gs.get("title", ""),
            original_request=gs.get("original_request", ""),
            status=gs.get("status", "running"),
            stock_codes=list(gs.get("stock_codes", [])),
            intermediate=dict(gs.get("intermediate", {})),
            final_report=gs.get("final_report"),
            review_passed=gs.get("review_passed", False),
            review_feedback=gs.get("review_feedback"),
            review_score=gs.get("review_score"),
            review_attempts=gs.get("review_attempts", 0),
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        for name in self.agents:
            output = gs.get("agent_outputs", {}).get(name)
            if output:
                rs.agent_tasks[name] = AgentTaskState(
                    agent_name=name,
                    status=output.get("status", "success"),
                    output_data=output.get("data"),
                    error=output.get("error"),
                )
            else:
                rs.agent_tasks[name] = AgentTaskState(agent_name=name)
        return rs

    def _from_research_state(self, rs: ResearchState, agent_name: str) -> dict:
        task = rs.agent_tasks.get(agent_name)
        update: dict = {"intermediate": dict(rs.intermediate)}
        if task:
            update["agent_outputs"] = {
                agent_name: {"status": task.status, "data": task.output_data, "error": task.error}
            }
        if agent_name == "senior_researcher":
            update["final_report"] = rs.final_report
        elif agent_name == "compliance_officer":
            update["review_passed"] = rs.review_passed
            update["review_feedback"] = rs.review_feedback
            update["review_score"] = rs.review_score
            update["review_attempts"] = rs.review_attempts
        return update

    async def _node_agent(self, state: GraphState, name: str) -> dict:
        t0 = time.time()
        await self._save_task(name, None, "running")
        rs = self._to_research_state(state)
        rs = await self.agents[name].run(rs)
        update = self._from_research_state(rs, name)
        elapsed = round(time.time() - t0, 1)
        task = rs.agent_tasks.get(name)
        if task:
            await self._save_task(name, task.output_data, task.status, task.error)
        logger.info(f"[agent] {name} done in {elapsed}s")
        self._agent_durations[name] = elapsed
        return update

    async def _node_parse(self, state: GraphState) -> dict:
        parsed = self.parser.parse(state.get("original_request", ""))
        return {"stock_codes": list(parsed.get("stock_codes", []))}

    def _supervisor_snapshot(self, gs: GraphState) -> str:
        it = gs.get("intermediate") or {}
        report = gs.get("final_report") or it.get("draft_report") or ""
        fd = it.get("financial_data") or {}
        has_val = any(k.endswith("_valuation") for k in fd)
        has_fflow = any(k.endswith("_fund_flow") for k in fd)
        hint = ""
        if report and not gs.get("review_passed"):
            hint = "\n建议: 审查未通过，可带 instruction 重派 run_researcher 修改，再派 run_compliance 复查。"
        followup = it.get("followup_question", "")
        task_line = gs.get("original_request", "")
        if followup:
            task_line += f"（追问）{str(followup)[:80]}"
        conv_text = ""
        conv = it.get("conversation_window") or []
        if conv:
            conv_lines = [f"- 第{i}轮 Q: {str(t['q'])[:50]} / A: {str(t['a'])[:80]}" for i, t in enumerate(conv, 1)]
            conv_text = "最近多轮对话（仅作上下文参考，勿重复已回答内容）:\n" + "\n".join(conv_lines)
        return "\n".join([
            f"任务: {task_line}",
            "当前阶段状态:",
            f"- outline: {'已完成' if it.get('outline') else '未完成'}",
            f"- financial data: {'已完成' if it.get('financial_interpretation') else '未完成'}"
            f"{'（含估值分位）' if has_val else ''}"
            f"{'（含资金流）' if has_fflow else ''}",
            f"- charts: {'已完成' if it.get('analysis_charts') else '未完成'}",
            f"- fundamental: {'已完成' if it.get('fundamental_analysis') else '未完成'}",
            f"- news: {'已完成' if it.get('news_analysis') else '未完成'}",
            f"- technical: {'已完成' if it.get('technical_analysis') else '未完成'}",
            f"- draft report: {'已完成(%d字)' % len(report) if report else '未完成'}",
            f"- compliance: score={gs.get('review_score')} passed={gs.get('review_passed')} feedback={str(gs.get('review_feedback') or '')[:200]}",
            "下一步: 派遣一个需要的 worker（run_analyst / run_data_engineer / run_quant / run_fundamental / run_news / run_technical / run_researcher / run_compliance），或全部完成后回答完成。" + hint,
        ]) + (("\n" + conv_text) if conv_text else "")

    def _next_missing_stage(self, gs: GraphState) -> str:
        it = gs.get("intermediate") or {}
        report = gs.get("final_report") or it.get("draft_report") or ""
        if not it.get("outline"):
            return "run_analyst"
        if not it.get("financial_interpretation"):
            return "run_data_engineer"
        if not it.get("analysis_charts"):
            return "run_quant"
        if not it.get("fundamental_analysis"):
            return "run_fundamental"
        if not it.get("news_analysis"):
            return "run_news"
        if not it.get("technical_analysis"):
            return "run_technical"
        if not report:
            return "run_researcher"
        return "run_compliance"

    async def _node_supervisor(self, state: GraphState) -> dict:
        gs: GraphState = dict(state)
        gs["intermediate"] = dict(gs.get("intermediate") or {})
        gs["agent_outputs"] = dict(gs.get("agent_outputs") or {})
        supervisor = self.agents["supervisor"]
        history: list = []
        last_tool, repeats = "", 0
        fallback = False
        rounds = 0

        for rounds in range(SUPERVISOR_MAX_ROUNDS):
            if gs.get("review_passed") and gs.get("final_report"):
                break
            try:
                decision = await supervisor.decide(self._supervisor_snapshot(gs), history)
            except Exception as e:
                logger.warning(f"[supervisor] decide error at round {rounds}: {e}")
                decision = {"type": "invalid", "raw": ""}

            if decision["type"] == "finish":
                if gs.get("final_report") or gs["intermediate"].get("draft_report"):
                    logger.info(f"[supervisor] finished at round {rounds + 1}")
                    break
                decision = {"type": "invalid", "raw": "finished before report"}

            tool = decision.get("tool", "")
            if tool == "run_compliance" and not (gs.get("final_report") or gs["intermediate"].get("draft_report")):
                tool = "run_researcher"
            if tool not in WORKER_BY_TOOL:
                if decision["type"] == "invalid" and not (gs.get("final_report") or gs["intermediate"].get("draft_report")):
                    tool = self._next_missing_stage(gs)
                    logger.info(f"[supervisor] round {rounds + 1} auto-gated to {tool}")
                else:
                    history.append({"role": "user", "content": f"无效决策: {str(decision)[:200]}。请只派遣一个 worker。"})
                    history = history[-6:]
                    continue

            logger.info(f"[supervisor] round {rounds + 1} decision: {str(decision)[:300]}")
            if tool == "run_compliance" and not (gs.get("final_report") or gs["intermediate"].get("draft_report")):
                tool = "run_researcher"
            if tool not in WORKER_BY_TOOL:
                history.append({"role": "user", "content": f"无效决策: {str(decision)[:200]}。请只派遣一个 worker。"})
                history = history[-6:]
                continue

            if tool == last_tool:
                repeats += 1
            else:
                last_tool, repeats = tool, 1
            if repeats >= 2:
                logger.warning(f"[supervisor] tool {tool} repeated {repeats} times, fallback pipeline")
                fallback = True
                break

            if tool == "run_researcher":
                instruction = str((decision.get("args") or {}).get("instruction") or "").strip()
                if instruction:
                    gs["intermediate"]["research_instruction"] = instruction
                else:
                    gs["intermediate"].pop("research_instruction", None)
                if gs.get("review_attempts", 0) >= REVIEW_ESCALATE_AFTER:
                    gs["intermediate"]["research_provider"] = "deepseek"
                    gs["intermediate"]["critic_provider"] = "deepseek"
                    logger.info(f"[supervisor] review failed {gs.get('review_attempts')} times, "
                                f"escalating researcher+critic to DeepSeek")
                else:
                    gs["intermediate"].pop("research_provider", None)
                    gs["intermediate"].pop("critic_provider", None)

            worker = WORKER_BY_TOOL[tool]
            try:
                update = await self._node_agent(gs, worker)
            except Exception as e:
                logger.error(f"[supervisor] worker {worker} failed: {e}")
                history.append({"role": "user", "content": f"{tool} 执行失败: {str(e)[:200]}"})
                history = history[-6:]
                continue
            gs.update(update)
            gs["intermediate"] = dict(gs["intermediate"] or {})
            gs["agent_outputs"] = dict(gs["agent_outputs"] or {})
            logger.info(f"[supervisor] round {rounds + 1}: dispatched {tool}")
            history.append({"role": "user", "content": f"{tool} 已完成"})
            history = history[-6:]

            if tool == "run_researcher":
                upd = await self._node_agent(gs, "compliance_officer")
                gs.update(upd)
                gs["intermediate"] = dict(gs["intermediate"] or {})
                gs["agent_outputs"] = dict(gs["agent_outputs"] or {})
                if gs.get("review_feedback"):
                    gs["intermediate"]["critic_previous_feedback"] = gs["review_feedback"]
                last_tool, repeats = "", 0
                logger.info(f"[supervisor] auto compliance after researcher, passed={gs.get('review_passed')}")

        if not (gs.get("final_report") or gs["intermediate"].get("draft_report")):
            logger.warning("[supervisor] no report produced, fallback pipeline")
            fallback = True
            gs = await self._run_fallback_pipeline(gs)

        await self._save_task("supervisor", {
            "rounds": rounds + 1,
            "fallback": fallback,
            "review_passed": bool(gs.get("review_passed")),
            "review_score": gs.get("review_score"),
            "review_attempts": gs.get("review_attempts", 0),
            "report_chars": len(gs.get("final_report") or ""),
        }, "success")

        if self._agent_durations:
            total = round(sum(self._agent_durations.values()), 1)
            lines = [f"[latency] agent durations (total {total}s):"]
            for n, d in sorted(self._agent_durations.items(), key=lambda x: -x[1]):
                lines.append(f"  {n:<25s} {d}s")
            logger.info("\n".join(lines))

        return {k: gs[k] for k in ("intermediate", "agent_outputs", "final_report",
                                   "review_passed", "review_feedback", "review_score", "review_attempts")
                if k in gs}

    async def _run_fallback_pipeline(self, gs: GraphState) -> GraphState:
        it = gs.get("intermediate") or {}
        if not it.get("outline"):
            gs.update(await self._node_agent(gs, "chief_analyst"))
        if not it.get("financial_interpretation"):
            gs.update(await self._node_agent(gs, "data_engineer"))
        if not it.get("analysis_charts"):
            gs.update(await self._node_agent(gs, "quant_analyst"))
        if not it.get("fundamental_analysis"):
            gs.update(await self._node_agent(gs, "fundamental_analyst"))
        if not it.get("news_analysis"):
            gs.update(await self._node_agent(gs, "news_analyst"))
        if not it.get("technical_analysis"):
            gs.update(await self._node_agent(gs, "technical_analyst"))
        if not gs.get("final_report"):
            gs.update(await self._node_agent(gs, "senior_researcher"))
        if not gs.get("review_passed"):
            gs.update(await self._node_agent(gs, "compliance_officer"))
            if not gs.get("review_passed") and gs.get("final_report"):
                gs["intermediate"] = dict(gs.get("intermediate") or {})
                gs["intermediate"]["research_instruction"] = str(gs.get("review_feedback") or "")[:1000]
                gs["intermediate"]["critic_previous_feedback"] = str(gs.get("review_feedback") or "")[:1000]
                gs["intermediate"]["research_provider"] = "deepseek"
                gs["intermediate"]["critic_provider"] = "deepseek"
                logger.info("[supervisor] fallback revision round uses DeepSeek")
                gs.update(await self._node_agent(gs, "senior_researcher"))
                gs.update(await self._node_agent(gs, "compliance_officer"))
        return gs

    def _make_agent_node(self, name: str):
        async def node(state: GraphState) -> dict:
            return await self._node_agent(state, name)
        node.__name__ = f"_node_{name}"
        return node

    def _build_graph(self):
        builder = StateGraph(GraphState)
        builder.add_node("parse", self._node_parse)
        builder.add_node("supervisor", self._node_supervisor)
        builder.add_edge(START, "parse")
        builder.add_edge("parse", "supervisor")
        builder.add_edge("supervisor", END)
        return builder.compile()

    async def run_followup(self, project_id: int, question: str, db_session=None) -> Dict[str, Any]:
        """Q&A 式追问：不重生成报告，直接基于已有报告与最近对话回答用户问题。"""
        self._db = db_session
        self._pid = project_id

        existing_report = ""
        conv_turns = []
        followup_index = 1
        started = datetime.now(timezone.utc).isoformat()
        if db_session:
            p = db_session.query(ResearchProject).filter(ResearchProject.id == project_id).first()
            if p and p.report_content:
                existing_report = p.report_content
            tasks = db_session.query(ResearchTask).filter(
                ResearchTask.project_id == project_id
            ).order_by(ResearchTask.created_at).all()
            for t in tasks:
                name = t.agent_name or ""
                if name.startswith("followup_"):
                    try:
                        followup_index = max(followup_index, int(name.split("_")[1]))
                    except (IndexError, ValueError):
                        pass
                    od = t.output_data or {}
                    if isinstance(od, dict) and od.get("question"):
                        conv_turns.append({"q": str(od["question"]), "a": str(od.get("answer") or "")[:400]})
            followup_index += 1

        conv_text = ""
        if conv_turns:
            conv_lines = [f"{i}. Q: {t['q']}\n   A: {t['a']}" for i, t in enumerate(conv_turns[-CONV_WINDOW:], 1)]
            conv_text = "\n".join(conv_lines)

        qa_system = (
            "你是一名资深金融分析师助手。基于已有研报内容与上下文，直接回答用户的追问。"
            "要求：以问答形式直接作答，简洁、结论明确；数据从已有报告中引用（不得编造），"
            "需要时给出简短理由与风险提示。用中文、Markdown 列表/要点。不要重新生成整篇研报。"
        )
        qa_prompt = f"""## 用户追问
{question}

## 已有报告（节选）
{existing_report[:4000] if existing_report else '（暂无）'}

## 最近对话（参考，避免重复）
{conv_text if conv_text else '（本轮为首个追问）'}

请直接回答该追问，500~1500 字。"""
        answer = await self.agents["supervisor"]._call_llm(
            qa_prompt, system_override=qa_system, temperature=0.4, max_tokens=1536
        )
        if not answer.strip():
            answer = "（未能生成有效回答）"

        followup_key = f"followup_{followup_index}"
        await self._save_task(followup_key, {
            "mode": "qa",
            "question": question,
            "answer": answer,
            "answer_chars": len(answer),
            "started_at": started,
        }, "success")
        logger.info(f"[followup] Q&A {followup_key} done for project {project_id}, chars={len(answer)}")

        return {"state": {}, "final_report": answer, "followup_key": followup_key}

    async def run(self, project_id: int, request_text: str, db_session=None) -> Dict[str, Any]:
        self._db = db_session
        self._pid = project_id

        initial: GraphState = {
            "project_id": project_id,
            "title": request_text[:100],
            "original_request": request_text,
            "stock_codes": [],
            "status": "running",
            "intermediate": {},
            "agent_outputs": {},
            "final_report": None,
            "review_passed": False,
            "review_feedback": None,
            "review_score": None,
            "review_attempts": 0,
        }

        if db_session:
            p = db_session.query(ResearchProject).filter(ResearchProject.id == project_id).first()
            if p:
                p.status = "running"
                try:
                    db_session.commit()
                except Exception:
                    db_session.rollback()
                    raise

        app = self._build_graph()
        final = await app.ainvoke(initial)

        final_status = "success" if final.get("review_passed", False) else "completed_with_issues"
        final["status"] = final_status

        return {"state": dict(final), "final_report": final.get("final_report", "")}
