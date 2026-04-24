"""Tool System API - endpoints for tool listing and execution."""

import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.tools import (
    tool_registry, ToolRiskLevel, ToolResult, get_security_enforcer,
)
from app.models.models import ToolExecution

router = APIRouter()


# ===== Schemas =====

class ToolInfo(BaseModel):
    name: str
    description: str
    risk_level: str
    parameters: dict


class ExecuteToolRequest(BaseModel):
    tool_name: str = Field(..., min_length=1)
    parameters: dict = Field(default_factory=dict)
    session_id: Optional[str] = None
    session_type: str = Field(default="planning")
    agent_name: Optional[str] = None
    task_id: Optional[str] = None
    auto_approve: bool = Field(default=False)


class ExecuteToolResponse(BaseModel):
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}
    execution_id: Optional[str] = None
    requires_approval: bool = False


class ToolExecutionLog(BaseModel):
    id: str
    tool_name: str
    agent_name: Optional[str] = None
    status: str
    duration_ms: int
    created_at: Optional[str] = None


# ===== Endpoints =====

@router.get("/tools", response_model=List[ToolInfo])
async def list_tools():
    """List all available tools with their risk levels and parameter schemas."""
    return [
        ToolInfo(
            name=info["name"],
            description=info["description"],
            risk_level=info["risk_level"],
            parameters=info["parameters"],
        )
        for info in tool_registry.list_tools()
    ]


@router.post("/tools/execute", response_model=ExecuteToolResponse)
async def execute_tool(
    req: ExecuteToolRequest,
    db: AsyncSession = Depends(get_db),
):
    """Execute a tool with the given parameters.

    Implements:
    - T-010: Default risk mapping
    - T-011: Three-level security with approval check
    - T-012: Execution timeout
    - T-013: Standardized ToolResult output
    """
    tool = tool_registry.get_tool(req.tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{req.tool_name}' not found")

    # T-011: Check if approval is required
    enforcer = get_security_enforcer()
    await enforcer.load_settings()

    requires_approval = enforcer.requires_approval(req.tool_name, tool.risk_level)
    if requires_approval and not req.auto_approve:
        return ExecuteToolResponse(
            success=False,
            error=f"Tool '{req.tool_name}' (risk: {tool.risk_level.value}) requires approval",
            requires_approval=True,
            metadata={"tool": req.tool_name, "risk_level": tool.risk_level.value},
        )

    # Execute with logging
    start_time = time.time()
    try:
        # Add context parameters
        context = {
            "session_id": req.session_id,
            "agent_name": req.agent_name,
            "task_id": req.task_id,
        }
        params = {**req.parameters, **context}

        result: ToolResult = await tool.execute(**params)
        duration_ms = int((time.time() - start_time) * 1000)

        # Log execution
        if req.session_id:
            execution_id = str(__import__('uuid').uuid4())
            try:
                execution = ToolExecution(
                    session_type=req.session_type,
                    session_id=req.session_id,
                    task_id=req.task_id,
                    agent_name=req.agent_name,
                    tool_name=req.tool_name,
                    status="completed" if result.success else "failed",
                    duration_ms=duration_ms,
                    input_json=json.dumps(req.parameters, ensure_ascii=False),
                    output_json=json.dumps(result.to_dict(), ensure_ascii=False),
                )
                db.add(execution)
                await db.commit()
            except Exception:
                pass

        return ExecuteToolResponse(
            success=result.success,
            data=result.data,
            error=result.error,
            metadata=result.metadata,
        )

    except PermissionError as e:
        return ExecuteToolResponse(
            success=False,
            error=str(e),
            metadata={"tool": req.tool_name, "blocked": True},
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)

        # Log failed execution
        if req.session_id:
            try:
                execution = ToolExecution(
                    session_type=req.session_type,
                    session_id=req.session_id,
                    task_id=req.task_id,
                    agent_name=req.agent_name,
                    tool_name=req.tool_name,
                    status="error",
                    duration_ms=duration_ms,
                    input_json=json.dumps(req.parameters, ensure_ascii=False),
                    output_json=json.dumps({"error": str(e)}, ensure_ascii=False),
                )
                db.add(execution)
                await db.commit()
            except Exception:
                pass

        return ExecuteToolResponse(
            success=False,
            error=f"Tool execution failed: {str(e)}",
            metadata={"tool": req.tool_name, "duration_ms": duration_ms},
        )


@router.get("/tools/{tool_name}", response_model=ToolInfo)
async def get_tool_info(tool_name: str):
    """Get detailed information about a specific tool."""
    tool = tool_registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    return ToolInfo(
        name=tool.name,
        description=tool.description,
        risk_level=tool.risk_level.value,
        parameters=tool.parameters_schema,
    )


@router.get("/tools/executions/{session_id}", response_model=List[ToolExecutionLog])
async def get_tool_executions(
    session_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Get tool execution logs for a session."""
    from sqlalchemy import select
    from app.models.models import ToolExecution

    result = await db.execute(
        select(ToolExecution)
        .where(ToolExecution.session_id == session_id)
        .order_by(ToolExecution.created_at.desc())
        .limit(limit)
    )
    executions = result.scalars().all()

    return [
        ToolExecutionLog(
            id=e.id,
            tool_name=e.tool_name,
            agent_name=e.agent_name,
            status=e.status,
            duration_ms=e.duration_ms,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in executions
    ]
