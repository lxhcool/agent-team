"""消息协议 — 定义 Agent 间通信的消息格式"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """消息类型

    权限分类：
    - command: 只有 Leader（或被委派的子团队长）能发送
    - request: 子 Agent 间协作请求，不能命令
    - inform: 信息传递，无权限限制
    """

    # === 任务相关 ===
    TASK_ASSIGN = "task_assign"  # [command] Leader 分配任务
    TASK_ACCEPT = "task_accept"  # [inform] Agent 接受任务
    TASK_REJECT = "task_reject"  # [inform] Agent 拒绝任务
    TASK_COMPLETE = "task_complete"  # [inform] Agent 完成任务
    TASK_FAILED = "task_failed"  # [inform] Agent 任务失败
    TASK_PROGRESS = "task_progress"  # [inform] Agent 汇报进度
    TASK_DELEGATE = "task_delegate"  # [command] Leader 委派子团队长管理子任务

    # === 协作相关 ===
    COLLAB_REQUEST = "collab_request"  # [request] 请求协作
    COLLAB_RESPONSE = "collab_response"  # [request] 协作响应
    COLLAB_RESULT = "collab_result"  # [request] 协作结果

    # === 控制相关 ===
    BROADCAST = "broadcast"  # [inform] 广播消息
    INTERRUPT = "interrupt"  # [command] Leader 中断 Agent
    SHUTDOWN = "shutdown"  # [command] 关闭 Agent

    # === 信息相关 ===
    QUESTION = "question"  # [request] 提问
    ANSWER = "answer"  # [inform] 回答
    INFO = "info"  # [inform] 信息共享
    ERROR = "error"  # [inform] 错误报告

    # === 人类介入 ===
    HUMAN_APPROVAL = "human_approval"  # [inform] 请求人类审批
    HUMAN_APPROVED = "human_approved"  # [inform] 人类已批准
    HUMAN_REJECTED = "human_rejected"  # [inform] 人类已拒绝

    # === 冲突仲裁 ===
    ARBITRATION_REQUEST = "arbitration_request"  # [request] 请求 Leader 仲裁
    ARBITRATION_RESULT = "arbitration_result"  # [command] Leader 仲裁结果


class MessageCategory(str, Enum):
    """消息权限分类 — 总线层强制校验"""

    COMMAND = "command"  # 指令类：只有 Leader/子团队长能发
    REQUEST = "request"  # 请求类：子 Agent 间协作，不能命令
    INFORM = "inform"  # 信息类：无权限限制


# 消息类型 → 权限分类 映射
_MESSAGE_CATEGORIES: dict[MessageType, MessageCategory] = {
    # command
    MessageType.TASK_ASSIGN: MessageCategory.COMMAND,
    MessageType.TASK_DELEGATE: MessageCategory.COMMAND,
    MessageType.INTERRUPT: MessageCategory.COMMAND,
    MessageType.SHUTDOWN: MessageCategory.COMMAND,
    MessageType.ARBITRATION_RESULT: MessageCategory.COMMAND,
    # request
    MessageType.COLLAB_REQUEST: MessageCategory.REQUEST,
    MessageType.COLLAB_RESPONSE: MessageCategory.REQUEST,
    MessageType.COLLAB_RESULT: MessageCategory.REQUEST,
    MessageType.QUESTION: MessageCategory.REQUEST,
    MessageType.ARBITRATION_REQUEST: MessageCategory.REQUEST,
    # inform
    MessageType.TASK_ACCEPT: MessageCategory.INFORM,
    MessageType.TASK_REJECT: MessageCategory.INFORM,
    MessageType.TASK_COMPLETE: MessageCategory.INFORM,
    MessageType.TASK_FAILED: MessageCategory.INFORM,
    MessageType.TASK_PROGRESS: MessageCategory.INFORM,
    MessageType.BROADCAST: MessageCategory.INFORM,
    MessageType.ANSWER: MessageCategory.INFORM,
    MessageType.INFO: MessageCategory.INFORM,
    MessageType.ERROR: MessageCategory.INFORM,
    MessageType.HUMAN_APPROVAL: MessageCategory.INFORM,
    MessageType.HUMAN_APPROVED: MessageCategory.INFORM,
    MessageType.HUMAN_REJECTED: MessageCategory.INFORM,
}


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
    seq: int = 0  # 全局单调递增序列号，由 MessageBus 分配，保证因果顺序

    def is_broadcast(self) -> bool:
        return self.receiver is None

    def is_command(self) -> bool:
        """是否为指令类消息（只有 Leader/子团队长能发）"""
        return _MESSAGE_CATEGORIES.get(self.type) == MessageCategory.COMMAND

    def is_collaboration(self) -> bool:
        """是否为协作类消息（子 Agent 间请求）"""
        return _MESSAGE_CATEGORIES.get(self.type) == MessageCategory.REQUEST

    def category(self) -> MessageCategory:
        """获取消息的权限分类"""
        return _MESSAGE_CATEGORIES.get(self.type, MessageCategory.INFORM)

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
            "seq": self.seq,
        }
