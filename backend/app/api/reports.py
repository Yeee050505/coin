from fastapi.responses import FileResponse
import os
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.models import get_db
from app.models.database import ResearchProject
from app.schemas.common import ReportResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{project_id}")
async def get_report(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if not project:
        raise HTTPException(404, "报告不存在")
    content = project.report_content or ""
    sections = []
    if content:
        for part in content.split("## ")[1:]:
            lines = part.strip().split("\n", 1)
            sections.append({"title": lines[0].strip(), "content": lines[1] if len(lines) > 1 else ""})
    return ReportResponse(
        project_id=project_id,
        content=content,
        format="markdown",
        sections=sections,
        charts=project.chart_data or [],
        metrics=project.metrics or {},
    )


@router.get("/{project_id}/download")
async def download_report(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if not project or not project.report_content:
        raise HTTPException(404, "Report not found")
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    file_path = os.path.join(reports_dir, f"report_{project_id}.md")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(project.report_content)
    project.report_path = file_path
    db.commit()
    return FileResponse(file_path, filename=f"report_{project_id}.md", media_type="text/markdown")
