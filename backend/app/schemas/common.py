from pydantic import BaseModel, Field
from typing import Optional, Any, List, Dict
from datetime import datetime


class ResearchRequest(BaseModel):
    title: str = Field(..., description="Research topic")
    description: str = Field("", description="Detailed requirements")
    scenario: str = Field("financial_research", description="research / financial_report / competitor")
    output_format: str = Field("markdown", description="markdown / pdf / docx")


class FollowupRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="Follow-up question on existing project")


class ProjectResponse(BaseModel):
    id: int
    title: str
    scenario: str
    status: str
    report_path: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TaskResponse(BaseModel):
    id: int
    project_id: int
    agent_name: str
    status: str
    output_data: Optional[Any] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProjectDetail(BaseModel):
    project: ProjectResponse
    tasks: List[TaskResponse]
    agent_statuses: List[Dict[str, Any]]


class ReportResponse(BaseModel):
    project_id: int
    content: str = ""
    format: str = "markdown"
    sections: List[Dict[str, Any]] = []
    charts: List[Dict[str, Any]] = []
    metrics: Dict[str, Any] = {}

    class Config:
        arbitrary_types_allowed = True


class AgentStateResponse(BaseModel):
    agent_name: str
    status: str
    current_action: str = ""
    progress: float = 0.0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
