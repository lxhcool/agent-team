"""SQLAlchemy models for Team Agent."""

import enum
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_id():
    return str(uuid.uuid4())


# ===== Enums =====

class PlanningStatus(str, enum.Enum):
    CREATED = "created"
    PLANNING = "planning"              # Leader + Agent 团队正在分析需求和形成方案
    AWAITING_APPROVAL = "awaiting_approval"  # 等待用户确认方案
    READY_FOR_EXPORT = "ready_for_export"    # 方案已确认，可导出 proposal.md 和 execution_plan.json
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

    # 内部子状态（映射到 PLANNING）
    ANALYZING = "analyzing"
    GENERATING_PROPOSAL = "generating_proposal"
    # 内部子状态（映射到 READY_FOR_EXPORT）
    GENERATING_PLAN = "generating_plan"


class ExecutionStatus(str, enum.Enum):
    CREATED = "created"      # Execution Session 已建立
    READY = "ready"          # 准备就绪，可开始执行
    EXECUTING = "executing"  # 正在执行
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"
    PAUSED = "paused"
    CANCELLED = "cancelled"

    # 兼容旧状态
    PENDING = "pending"
    RUNNING = "running"


class RoundtableStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CONVERTED = "converted"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    PAUSED = "paused"
    READY = "ready"
    BLOCKED = "blocked"
    WAITING_APPROVAL = "waiting_approval"
    CANCELLED = "cancelled"


class WorkspaceStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class WorkspaceMemberRole(str, enum.Enum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class WorkspaceStageKey(str, enum.Enum):
    REQUIREMENTS = "requirements"
    PRODUCT = "product"
    UI_DIRECTION = "ui_direction"
    PROTOTYPE = "prototype"
    TECHNICAL = "technical"
    DEVELOPMENT = "development"
    ACCEPTANCE = "acceptance"
    DEPLOYMENT = "deployment"


class WorkspaceStageStatus(str, enum.Enum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"
    SKIPPED = "skipped"


# ===== P1-1: Planning Session State Transition Validation =====

VALID_PLANNING_TRANSITIONS: dict[PlanningStatus, set[PlanningStatus]] = {
    PlanningStatus.CREATED: {PlanningStatus.PLANNING, PlanningStatus.CANCELLED, PlanningStatus.FAILED},
    PlanningStatus.PLANNING: {PlanningStatus.AWAITING_APPROVAL, PlanningStatus.CANCELLED, PlanningStatus.FAILED},
    PlanningStatus.AWAITING_APPROVAL: {PlanningStatus.READY_FOR_EXPORT, PlanningStatus.CANCELLED, PlanningStatus.FAILED},
    PlanningStatus.READY_FOR_EXPORT: {PlanningStatus.COMPLETED, PlanningStatus.CANCELLED, PlanningStatus.FAILED},
    PlanningStatus.COMPLETED: set(),  # Terminal state
    PlanningStatus.CANCELLED: set(),  # Terminal state
    PlanningStatus.FAILED: {PlanningStatus.CREATED},  # Allow retry
    # Internal sub-states
    PlanningStatus.ANALYZING: {PlanningStatus.GENERATING_PROPOSAL, PlanningStatus.PLANNING, PlanningStatus.FAILED},
    PlanningStatus.GENERATING_PROPOSAL: {PlanningStatus.AWAITING_APPROVAL, PlanningStatus.PLANNING, PlanningStatus.FAILED},
    PlanningStatus.GENERATING_PLAN: {PlanningStatus.READY_FOR_EXPORT, PlanningStatus.FAILED},
}

# ===== P1-4: Task State Transition Validation =====

VALID_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.READY: {TaskStatus.ASSIGNED, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.WAITING_APPROVAL, TaskStatus.PAUSED},
    TaskStatus.COMPLETED: set(),  # Terminal
    TaskStatus.FAILED: {TaskStatus.PENDING, TaskStatus.READY},  # Retry allowed
    TaskStatus.SKIPPED: set(),
    TaskStatus.PAUSED: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: {TaskStatus.READY, TaskStatus.CANCELLED},
    TaskStatus.WAITING_APPROVAL: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.CANCELLED: set(),
}


def validate_planning_transition(current: PlanningStatus, target: PlanningStatus) -> bool:
    """Check if a planning status transition is valid (P1-1)."""
    allowed = VALID_PLANNING_TRANSITIONS.get(current, set())
    return target in allowed


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Check if a task status transition is valid (P1-4)."""
    if current == target:
        return True  # Idempotent
    allowed = VALID_TASK_TRANSITIONS.get(current, set())
    return target in allowed


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class MessageType(str, enum.Enum):
    CHAT = "chat"
    SYSTEM = "system"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PROPOSAL = "proposal"
    PLAN = "plan"
    INTERRUPT = "interrupt"     # C-005: Leader intervention - pause current task
    COMMAND = "command"         # C-005: Leader intervention - directive to agent


# ===== Models =====

class User(Base):
    """User account for authentication and data isolation."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Workspace(Base):
    """Product workspace that owns the staged product/design/code flow."""
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_platform: Mapped[str] = mapped_column(String(50), default="website")
    status: Mapped[WorkspaceStatus] = mapped_column(Enum(WorkspaceStatus), default=WorkspaceStatus.ACTIVE)
    current_stage: Mapped[WorkspaceStageKey] = mapped_column(
        Enum(WorkspaceStageKey), default=WorkspaceStageKey.REQUIREMENTS
    )
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    members: Mapped[List["WorkspaceMember"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    stages: Mapped[List["WorkspaceStage"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", order_by="WorkspaceStage.order"
    )
    planning_sessions: Mapped[List["PlanningSession"]] = relationship(back_populates="workspace")


class WorkspaceMember(Base):
    """Workspace access list for multi-user isolation."""
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[WorkspaceMemberRole] = mapped_column(
        Enum(WorkspaceMemberRole), default=WorkspaceMemberRole.EDITOR
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    workspace: Mapped["Workspace"] = relationship(back_populates="members")


class WorkspaceStage(Base):
    """A confirmable stage in a workspace's AI development-team flow."""
    __tablename__ = "workspace_stages"
    __table_args__ = (UniqueConstraint("workspace_id", "stage_key", name="uq_workspace_stage"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id"), nullable=False, index=True
    )
    stage_key: Mapped[WorkspaceStageKey] = mapped_column(Enum(WorkspaceStageKey), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[WorkspaceStageStatus] = mapped_column(
        Enum(WorkspaceStageStatus), default=WorkspaceStageStatus.DRAFT
    )
    order: Mapped[int] = mapped_column(Integer, default=0)
    recommendation_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    workspace: Mapped["Workspace"] = relationship(back_populates="stages")


class PlanningSession(Base):
    __tablename__ = "planning_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workspace_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("workspaces.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[PlanningStatus] = mapped_column(
        Enum(PlanningStatus), default=PlanningStatus.CREATED
    )
    mode: Mapped[str] = mapped_column(String(50), default="planning")
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    workspace: Mapped[Optional["Workspace"]] = relationship(back_populates="planning_sessions")
    tasks: Mapped[List["Task"]] = relationship(back_populates="planning_session", foreign_keys="[Task.session_id]", primaryjoin="PlanningSession.id == Task.session_id")
    messages: Mapped[List["Message"]] = relationship(back_populates="planning_session", foreign_keys="[Message.session_id]", primaryjoin="PlanningSession.id == Message.session_id")
    artifacts: Mapped[List["Artifact"]] = relationship(back_populates="planning_session", foreign_keys="[Artifact.session_id]", primaryjoin="PlanningSession.id == Artifact.session_id")
    llm_calls: Mapped[List["LLMCall"]] = relationship(back_populates="planning_session", foreign_keys="[LLMCall.session_id]", primaryjoin="PlanningSession.id == LLMCall.session_id")


class ExecutionSession(Base):
    __tablename__ = "execution_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    proposal_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus), default=ExecutionStatus.CREATED
    )
    project_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    tasks: Mapped[List["Task"]] = relationship(back_populates="execution_session", foreign_keys="[Task.execution_session_id]")
    # artifacts relationship via session_id is polymorphic - query manually when needed


class RoundtableSession(Base):
    __tablename__ = "roundtable_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[RoundtableStatus] = mapped_column(
        Enum(RoundtableStatus), default=RoundtableStatus.ACTIVE
    )
    max_rounds: Mapped[int] = mapped_column(Integer, default=5)
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    execution_session_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("execution_sessions.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    assigned_agent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    owner_role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    dependencies_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_paths_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation_commands_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    planning_session: Mapped[Optional["PlanningSession"]] = relationship(
        back_populates="tasks", primaryjoin="PlanningSession.id == foreign(Task.session_id)"
    )
    execution_session: Mapped[Optional["ExecutionSession"]] = relationship(
        back_populates="tasks", foreign_keys=[execution_session_id]
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    sender: Mapped[str] = mapped_column(String(100), nullable=False)
    receiver: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    message_type: Mapped[MessageType] = mapped_column(Enum(MessageType), default=MessageType.CHAT)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attachments_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)
    ack_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    planning_session: Mapped[Optional["PlanningSession"]] = relationship(
        back_populates="messages", primaryjoin="PlanningSession.id == foreign(Message.session_id)"
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/json")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="generated")
    retention_policy: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    planning_session: Mapped[Optional["PlanningSession"]] = relationship(
        back_populates="artifacts", primaryjoin="PlanningSession.id == foreign(Artifact.session_id)"
    )
    # execution_session relationship is polymorphic - query manually


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    agent_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    finish_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    was_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    was_continued: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    planning_session: Mapped[Optional["PlanningSession"]] = relationship(
        back_populates="llm_calls", primaryjoin="PlanningSession.id == foreign(LLMCall.session_id)"
    )


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    agent_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    source_type: Mapped[str] = mapped_column(String(20), default="builtin")
    source_ref: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    author: Mapped[str] = mapped_column(String(100), default="team-agent")
    tools_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_for_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_format: Mapped[str] = mapped_column(String(20), default="markdown")
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AgentTemplate(Base):
    __tablename__ = "agent_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    skills_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capabilities_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    allowed_tools_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    constraints_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    participation_modes_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ProviderConfig(Base):
    """Stores LLM provider configurations including API keys (per-user)."""
    __tablename__ = "provider_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True, default="system")
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_type: Mapped[str] = mapped_column(String(50), default="openai_compatible")
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    models_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ModelSettings(Base):
    """Global model settings (default model, fallback chain, budget) - per user."""
    __tablename__ = "model_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True, default="system")
    default_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    planning_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    execution_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    fallback_chain_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    session_budget_usd: Mapped[float] = mapped_column(Float, default=10.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Checkpoint(Base):
    """Execution snapshots for recovery, display, and auditing."""
    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(50), nullable=False)  # business, tool, manual
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    state_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class MemoryEntry(Base):
    """Layered memory entries for structured recall across sessions."""
    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    entry_type: Mapped[str] = mapped_column(String(50), nullable=False)  # conclusion, summary, experience, failure
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # planning, execution, roundtable
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retention_policy: Mapped[str] = mapped_column(String(20), default="session")  # session, permanent, ttl
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AgentHeartbeat(Base):
    """Agent and CLI executor liveness tracking."""
    __tablename__ = "agent_heartbeats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    agent_type: Mapped[str] = mapped_column(String(20), default="server")  # server, cli
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle, busy, unresponsive
    current_task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    current_session_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_progress_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
