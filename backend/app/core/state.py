from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class AgentTaskState(BaseModel):
    agent_name: str
    status: str = "pending"
    input_data: Dict[str, Any] = {}
    output_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_count: int = 0


class ResearchState(BaseModel):
    project_id: int = 0
    title: str = ""
    original_request: str = ""
    scenario: str = "financial_research"
    status: str = "init"

    outline: Optional[str] = None
    search_results: List[Dict[str, Any]] = []
    financial_data: Optional[Dict[str, Any]] = None
    analysis_charts: List[Dict[str, Any]] = []
    draft_report: Optional[str] = None
    final_report: Optional[str] = None

    review_feedback: Optional[str] = None
    review_score: Optional[float] = None
    review_passed: Optional[bool] = None
    review_attempts: int = 0
    intermediate: Dict[str, Any] = {}
    stock_codes: List[str] = []

    agent_tasks: Dict[str, AgentTaskState] = {}
    current_agent: Optional[str] = None

    created_at: str = ""
    updated_at: str = ""

    class Config:
        arbitrary_types_allowed = True
