"""LangGraph multi-agent orchestrator with conditional rollback"""
import json
import logging
import asyncio
import math
from datetime import datetime, date as _date, timezone
from typing import Any, Dict
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

    async def _save_task(self, db_session, project_id: int, agent_name: str, ts: AgentTaskState):
        task = db_session.query(ResearchTask).filter(
            ResearchTask.project_id == project_id, ResearchTask.agent_name == agent_name
        ).first()
        safe_data = _json_safe(ts.output_data)
        if task:
            task.status = ts.status
            task.output_data = safe_data
            task.error_message = ts.error
            task.completed_at = datetime.now(timezone.utc)
        else:
            task = ResearchTask(project_id=project_id, agent_name=agent_name, status=ts.status,
                output_data=safe_data, error_message=ts.error,
                started_at=datetime.fromisoformat(ts.started_at) if ts.started_at else datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc))
            db_session.add(task)
        try:
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    async def run(self, project_id: int, request_text: str, db_session=None) -> Dict[str, Any]:
        parsed = self.parser.parse(request_text)
        state = ResearchState(
            project_id=project_id, title=request_text[:100], original_request=request_text,
            status="running", created_at=datetime.now(timezone.utc).isoformat(), updated_at=datetime.now(timezone.utc).isoformat(),
            stock_codes=parsed.get("stock_codes", []),
        )
        for name in self.agents:
            state.agent_tasks[name] = AgentTaskState(agent_name=name)

        if db_session:
            p = db_session.query(ResearchProject).filter(ResearchProject.id == project_id).first()
            if p:
                p.status = "running"
                db_session.commit()

        agent_order = ["chief_architect", "deep_scout", "chief_data_engineer", "data_analyst", "chief_researcher", "critic_master"]

        parallel_group1 = ["chief_architect", "deep_scout", "chief_data_engineer"]
        await asyncio.gather(*[self.agents[n].run(state) for n in parallel_group1])
        if db_session:
            for name in parallel_group1:
                try:
                    await self._save_task(db_session, project_id, name, state.agent_tasks[name])
                except Exception as e:
                    with open("D:/py/fastapi_demo/coin/backend/logs/trace_err.txt", "a", 1) as f:
                        f.write(f"[{datetime.now().isoformat()}] _save_task FAIL {name}: {type(e).__name__}: {e}\n")
                    raise

        for name in agent_order[3:]:
            retry_count = 0
            while retry_count <= MAX_RETRIES:
                state = await self.agents[name].run(state)
                if db_session:
                    await self._save_task(db_session, project_id, name, state.agent_tasks[name])
                if name == "critic_master" and not state.review_passed and retry_count < MAX_RETRIES:
                    logger.info(f"Review failed, rollback to chief_researcher (attempt {retry_count + 1})")
                    state.agent_tasks["chief_researcher"] = AgentTaskState(agent_name="chief_researcher")
                    retry_count += 1
                    continue
                break

        state.status = "success" if state.review_passed else "completed_with_issues"
        state.updated_at = datetime.now(timezone.utc).isoformat()

        return {"state": state.model_dump(), "final_report": state.final_report}
