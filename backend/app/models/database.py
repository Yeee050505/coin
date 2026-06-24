
"""Database models with China-timezone-aware ORM layer (Asia/Shanghai on read)."""
from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, JSON, TypeDecorator
from app.models import Base


class TZDateTime(TypeDecorator):
    """Stores UTC in DB, returns Asia/Shanghai (UTC+8) on read."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                # Treat naive datetimes as UTC (prevents errors with old data)
                value = value.replace(tzinfo=timezone.utc)
            else:
                # Normalize to UTC for storage
                value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            # Convert to China timezone (UTC+8) on read
            value = value.astimezone(timezone(timedelta(hours=8)))
        return value


def _now_utc():
    """Current UTC time, timezone-aware."""
    return datetime.now(timezone.utc)


class ResearchProject(Base):
    __tablename__ = "research_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    scenario = Column(String(100), nullable=False)
    status = Column(String(20), default="pending", index=True)
    params = Column(JSON, nullable=True)
    report_path = Column(String(500), nullable=True)
    report_content = Column(Text, nullable=True)
    chart_data = Column(JSON, nullable=True)
    metrics = Column(JSON, nullable=True)
    agent_progress = Column(JSON, nullable=True)
    total_tokens = Column(Integer, default=0)
    created_at = Column(TZDateTime, default=_now_utc)
    updated_at = Column(TZDateTime, default=_now_utc, onupdate=_now_utc)


class ResearchTask(Base):
    __tablename__ = "research_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, nullable=False, index=True)
    agent_name = Column(String(100), nullable=False)
    status = Column(String(20), default="pending", index=True)
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    tool_calls = Column(JSON, nullable=True)
    tokens_used = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(TZDateTime, nullable=True)
    completed_at = Column(TZDateTime, nullable=True)
    created_at = Column(TZDateTime, default=_now_utc)
