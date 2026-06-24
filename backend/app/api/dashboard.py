"""Dashboard API"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.models import get_db
from app.models.database import ResearchProject, ResearchTask
from sqlalchemy import func

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def get_summary(db: Session = Depends(get_db)):
    total_projects = db.query(ResearchProject).count()
    total_tasks = db.query(ResearchTask).count()
    running = db.query(ResearchTask).filter(ResearchTask.status == "running").count()
    success = db.query(ResearchTask).filter(ResearchTask.status == "success").count()
    failed = db.query(ResearchTask).filter(ResearchTask.status == "failed").count()
    return {"total_projects": total_projects, "total_tasks": total_tasks,
            "tasks_by_status": {"running": running, "success": success, "failed": failed, "pending": total_tasks - running - success - failed},
            "avg_success_rate": round(success / total_tasks * 100, 1) if total_tasks else 0}
