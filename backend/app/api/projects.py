"""项目 API 路由"""
import os
import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, Depends
import asyncio
from sqlalchemy.orm import Session
from app.models import get_db
from app.models.database import ResearchProject, ResearchTask
from app.schemas.common import ResearchRequest, ProjectResponse, ProjectDetail, TaskResponse

logger = logging.getLogger(__name__)
_background_tasks: set = set()
router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=dict)
async def create_project(req: ResearchRequest, db: Session = Depends(get_db)):
    project = ResearchProject(title=req.title, scenario=req.scenario, params=req.model_dump(), status="pending")
    db.add(project)
    db.commit()
    db.refresh(project)

    full_request = req.title + " " + req.description
    project_id = project.id

    def run_workflow_sync():
        import asyncio, os, traceback
        from app.models import get_session
        try:
            local_db = get_session()
            try:
                from agent_core.scheduler_agent.graph_builder import WorkflowGraph
                builder = WorkflowGraph()
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                coro = builder.run(project_id, full_request, db_session=local_db)
                result = loop.run_until_complete(asyncio.wait_for(coro, timeout=300))
                loop.close()
                status_db = get_session()
                try:
                    p = status_db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
                    if p:
                        p.status = "success"
                        report = result.get("final_report", "")
                        if report:
                            p.report_content = report
                            reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
                            os.makedirs(reports_dir, exist_ok=True)
                            file_path = os.path.join(reports_dir, f"report_{project_id}.md")
                            with open(file_path, "w", encoding="utf-8") as f:
                                f.write(report)
                            p.report_path = file_path
                        status_db.commit()
                    logger.info(f"workflow complete for project {project_id}")
                finally:
                    status_db.close()
            except asyncio.TimeoutError:
                logger.error(f"workflow timeout {project_id}")
                err_db = get_session()
                try:
                    p = err_db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
                    if p:
                        p.status = "failed"
                        err_db.commit()
                finally:
                    err_db.close()
            except Exception as e:
                logger.error(f"workflow error for project {project_id}: {e}\n{traceback.format_exc()}")
                err_db = get_session()
                try:
                    p = err_db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
                    if p:
                        p.status = "failed"
                        err_db.commit()
                finally:
                    err_db.close()
            finally:
                local_db.close()
        except Exception as e:
            logger.error(f"session init error for project {project_id}: {e}\n{traceback.format_exc()}")

    import threading
    _t = threading.Thread(target=run_workflow_sync, daemon=True)
    _t.start()
    return {"project_id": project_id, "message": "任务已创建，正在调度Agent执行"}


@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: Session = Depends(get_db)):
    projects = db.query(ResearchProject).order_by(ResearchProject.created_at.desc()).all()
    return [ProjectResponse(id=p.id, title=p.title, scenario=p.scenario, status=p.status, report_path=p.report_path, created_at=p.created_at) for p in projects]


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project_detail(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if not project:
        return {"error": "not found"}
    tasks = db.query(ResearchTask).filter(ResearchTask.project_id == project_id).order_by(ResearchTask.created_at).all()
    agent_statuses = [{"agent_name": t.agent_name, "status": t.status, "current_action": t.agent_name,
                       "progress": 1.0 if t.status == "success" else 0.0,
                       "started_at": t.started_at.isoformat() if t.started_at else None,
                       "completed_at": t.completed_at.isoformat() if t.completed_at else None} for t in tasks]
    return ProjectDetail(
        project=ProjectResponse(id=project.id, title=project.title, scenario=project.scenario, status=project.status, report_path=project.report_path, created_at=project.created_at),
        tasks=[TaskResponse(id=t.id, project_id=t.project_id, agent_name=t.agent_name, status=t.status, output_data=t.output_data, error_message=t.error_message,
                            started_at=t.started_at, completed_at=t.completed_at) for t in tasks],
        agent_statuses=agent_statuses,
    )


@router.delete("/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    db.query(ResearchTask).filter(ResearchTask.project_id == project_id).delete(synchronize_session=False)
    db.query(ResearchProject).filter(ResearchProject.id == project_id).delete(synchronize_session=False)
    db.commit()
    return {"message": "deleted"}

@router.post("/batch-delete")
async def batch_delete_projects(ids: list[int], db: Session = Depends(get_db)):
    db.query(ResearchTask).filter(ResearchTask.project_id.in_(ids)).delete(synchronize_session=False)
    db.query(ResearchProject).filter(ResearchProject.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"message": f"deleted {len(ids)} projects"}
