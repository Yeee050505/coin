"""LangGraph multi-agent orchestrator with conditional rollback"""
import logging
import math
from datetime import datetime, date as _date, timezone
from typing import Any, Dict, Optional, List, TypedDict, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from app.core.state import ResearchState, AgentTaskState
from app.models.database import ResearchProject, ResearchTask
from agent_core.sub_agents.chief_architect import ChiefArchitect
from agent_core.sub_agents.deep_scout import DeepScout
from agent_core.sub_agents.data_engineer import ChiefDataEngineer
from agent_core.sub_agents.data_analyst import DataAnalyst
from agent_core.sub_agents.chief_researcher import ChiefResearcher
from agent_core.sub_agents.critic_master import CriticMaster
from agent_core.sub_agents.supervisor_agent import SupervisorAgent, WORKER_BY_TOOL
from agent_core.scheduler_agent.intent_parser import IntentParser

logger = logging.getLogger(__name__)
SUPERVISOR_MAX_ROUNDS = 8
CANONICAL_ORDER = ["run_architect", "run_scout", "run_data_engineer", "run_analyst", "run_researcher"]


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
            "chief_architect": ChiefArchitect(),
            "deep_scout": DeepScout(),
            "chief_data_engineer": ChiefDataEngineer(),
            "data_analyst": DataAnalyst(),
            "chief_researcher": ChiefResearcher(),
            "critic_master": CriticMaster(),
        }
        self.parser = IntentParser()
        self._db = None
        self._pid = 0

    async def _save_task(self, agent_name: str, output_data: Any, status: str, error: Optional[str] = None):
        if not self._db:
            return
        task = self._db.query(ResearchTask).filter(
            ResearchTask.project_id == self._pid, ResearchTask.agent_name == agent_name
        ).first()
        safe_data = _json_safe(output_data)
        if task:
            task.status = status
            task.output_data = safe_data
            task.error_message = error
            task.completed_at = datetime.now(timezone.utc)
        else:
            task = ResearchTask(
                project_id=self._pid, agent_name=agent_name, status=status,
                output_data=safe_data, error_message=error,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
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
        if agent_name == "chief_researcher":
            update["final_report"] = rs.final_report
        elif agent_name == "critic_master":
            update["review_passed"] = rs.review_passed
            update["review_feedback"] = rs.review_feedback
            update["review_score"] = rs.review_score
            update["review_attempts"] = rs.review_attempts
        return update

    async def _node_agent(self, state: GraphState, name: str) -> dict:
        rs = self._to_research_state(state)
        rs = await self.agents[name].run(rs)
        update = self._from_research_state(rs, name)
        task = rs.agent_tasks.get(name)
        if task:
            await self._save_task(name, task.output_data, task.status, task.error)
        return update

    async def _node_parse(self, state: GraphState) -> dict:
        parsed = self.parser.parse(state.get("original_request", ""))
        return {"stock_codes": list(parsed.get("stock_codes", []))}

    def _supervisor_snapshot(self, gs: GraphState) -> str:
        it = gs.get("intermediate") or {}
        report = gs.get("final_report") or it.get("draft_report") or ""
        search_n = len(it.get("raw_search_results") or [])
        hint = ""
        if report and not gs.get("review_passed"):
            hint = "\n建议: 审查未通过，可带 instruction 重派 run_researcher 修改，再派 run_critic 复查。"
        return "\n".join([
            f"任务: {gs.get('original_request', '')}",
            "当前阶段状态:",
            f"- outline: {'已完成' if it.get('outline') else '未完成'}",
            f"- search: {'已完成(%d条)' % search_n if it.get('search_synthesis') else '未完成'}",
            f"- financial data: {'已完成' if it.get('financial_interpretation') else '未完成'}",
            f"- charts: {'已完成' if it.get('analysis_charts') else '未完成'}",
            f"- draft report: {'已完成(%d字)' % len(report) if report else '未完成'}",
            f"- critic: score={gs.get('review_score')} passed={gs.get('review_passed')} feedback={str(gs.get('review_feedback') or '')[:200]}",
            "下一步: 派遣一个需要的 worker（run_architect / run_scout / run_data_engineer / run_analyst / run_researcher / run_critic），或全部完成后回答完成。" + hint,
        ])

    def _next_missing_stage(self, gs: GraphState) -> str:
        it = gs.get("intermediate") or {}
        report = gs.get("final_report") or it.get("draft_report") or ""
        if not it.get("outline"):
            return "run_architect"
        if not it.get("search_synthesis"):
            return "run_scout"
        if not it.get("financial_interpretation"):
            return "run_data_engineer"
        if not it.get("analysis_charts"):
            return "run_analyst"
        if not report:
            return "run_researcher"
        return "run_critic"

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
            if tool == "run_critic" and not (gs.get("final_report") or gs["intermediate"].get("draft_report")):
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
            if tool == "run_critic" and not (gs.get("final_report") or gs["intermediate"].get("draft_report")):
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
                upd = await self._node_agent(gs, "critic_master")
                gs.update(upd)
                gs["intermediate"] = dict(gs["intermediate"] or {})
                gs["agent_outputs"] = dict(gs["agent_outputs"] or {})
                if gs.get("review_feedback"):
                    gs["intermediate"]["critic_previous_feedback"] = gs["review_feedback"]
                last_tool, repeats = "", 0
                logger.info(f"[supervisor] auto critic after researcher, passed={gs.get('review_passed')}")

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

        return {k: gs[k] for k in ("intermediate", "agent_outputs", "final_report",
                                   "review_passed", "review_feedback", "review_score", "review_attempts")
                if k in gs}

    async def _run_fallback_pipeline(self, gs: GraphState) -> GraphState:
        it = gs.get("intermediate") or {}
        if not it.get("outline"):
            gs.update(await self._node_agent(gs, "chief_architect"))
        if not it.get("search_synthesis"):
            gs.update(await self._node_agent(gs, "deep_scout"))
        if not it.get("financial_interpretation"):
            gs.update(await self._node_agent(gs, "chief_data_engineer"))
        if not it.get("analysis_charts"):
            gs.update(await self._node_agent(gs, "data_analyst"))
        if not gs.get("final_report"):
            gs.update(await self._node_agent(gs, "chief_researcher"))
        if not gs.get("review_passed"):
            gs.update(await self._node_agent(gs, "critic_master"))
            if not gs.get("review_passed") and gs.get("final_report"):
                gs["intermediate"] = dict(gs.get("intermediate") or {})
                gs["intermediate"]["research_instruction"] = str(gs.get("review_feedback") or "")[:1000]
                gs["intermediate"]["critic_previous_feedback"] = str(gs.get("review_feedback") or "")[:1000]
                gs.update(await self._node_agent(gs, "chief_researcher"))
                gs.update(await self._node_agent(gs, "critic_master"))
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
