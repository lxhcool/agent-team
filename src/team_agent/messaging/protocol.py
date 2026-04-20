"""消息协议 — 定义 Agent 间通信的消息格式"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """消息类型"""

    # 任务相关
    TASK_ASSIGN = "task_assign"  # Leader 分配任务
    TASK_ACCEPT = "task_accept"  # Agent 接受任务
    TASK_REJECT = "task_reject"  # Agent 拒绝任务
    TASK_COMPLETE = "task_complete"  # Agent 完成任务
    TASK_FAILED = "task_failed"  # Agent 任务失败
    TASK_PROGRESS = "task_progress"  # Agent 汇报进度

    # 协作相关
    COLLAB_REQUEST = "collab_request"  # 请求协作
    COLLAB_RESPONSE = "collab_response"  # 协作响应
    COLLAB_RESULT = "collab_result"  # 协作结果

    # 控制相关
    BROADCAST = "broadcast"  # 广播消息
    INTERRUPT = "interrupt"  # Leader 中断 Agent
    SHUTDOWN = "shutdown"  # 关闭 Agent

    # 信息相关
    QUESTION = "question"  # 提问
    ANSWER = "answer"  # 回答
    INFO = "info"  # 信息共享
    ERROR = "error"  # 错误报告

    # 人类介入
    HUMAN_APPROVAL = "human_approval"  # 请求人类审批
    HUMAN_APPROVED = "human_approved"  # 人类已批准
    HUMAN_REJECTED = "human_rejected"  # 人类已拒绝


class MessagePriority(int, Enum):
    """消息优先级"""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass
class Message:
    """Agent 间通信的消息"""

    type: MessageType
    sender: str  # 发送者 Agent 名称
    receiver: str | None = None  # 接收者，None 表示广播
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)  # 结构化数据
    priority: MessagePriority = MessagePriority.NORMAL
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    reply_to: str | None = None  # 回复的消息 ID
    session_id: str | None = None  # 所属会话

    def is_broadcast(self) -> bool:
        return self.receiver is None

    def is_command(self) -> bool:
        """是否为指令类消息（只有 Leader 能发）"""
        return self.type in {
            MessageType.TASK_ASSIGN,
            MessageType.INTERRUPT,
            MessageType.SHUTDOWN,
        }

    def is_collaboration(self) -> bool:
        """是否为协作类消息（子 Agent 间请求）"""
        return self.type in {
            MessageType.COLLAB_REQUEST,
            MessageType.COLLAB_RESPONSE,
            MessageType.COLLAB_RESULT,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "sender": self.sender,
            "receiver": self.receiver,
            "content": self.content,
            "data": self.data,
            "priority": self.priority.value,
            "timestamp": self.timestamp.isoformat(),
            "reply_to": self.reply_to,
            "session_id": self.session_id,
        }
