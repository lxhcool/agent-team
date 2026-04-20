"""消息通信层 — 消息总线 + 协议"""

from team_agent.messaging.protocol import (
    Message,
    MessageCategory,
    MessageType,
    MessagePriority,
)
from team_agent.messaging.bus import MessageBus

__all__ = ["Message", "MessageType", "MessageCategory", "MessagePriority", "MessageBus"]
