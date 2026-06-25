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
from agent_core.scheduler_agent.intent_parser import IntentParser

logger = logging.getLogger(__name__)
MAX_RETRIES = 2


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

    def _route_phase1(self, state: GraphState) -> List[Send]:
        return [
            Send("chief_architect", state),
            Send("deep_scout", state),
            Send("chief_data_engineer", state),
        ]

    def _route_review(self, state: GraphState) -> str:
        if state.get("review_passed", False):
            return "end"
        if state.get("review_attempts", 0) <= MAX_RETRIES:
            return "retry"
        return "end"

    def _make_agent_node(self, name: str):
        async def node(state: GraphState) -> dict:
            return await self._node_agent(state, name)
        node.__name__ = f"_node_{name}"
        return node

    def _build_graph(self):
        builder = StateGraph(GraphState)

        builder.add_node("parse", self._node_parse)
        for name in ("chief_architect", "deep_scout", "chief_data_engineer",
                      "data_analyst", "chief_researcher", "critic_master"):
            builder.add_node(name, self._make_agent_node(name))

        builder.add_edge(START, "parse")
        builder.add_conditional_edges("parse", self._route_phase1)
        builder.add_edge(["chief_architect", "deep_scout", "chief_data_engineer"], "data_analyst")
        builder.add_edge("data_analyst", "chief_researcher")
        builder.add_edge("chief_researcher", "critic_master")
        builder.add_conditional_edges("critic_master", self._route_review, {
            "end": END,
            "retry": "chief_researcher",
        })

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
