"""消息总线 — Agent 间异步通信"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Coroutine

from team_agent.messaging.protocol import Message, MessageType

logger = logging.getLogger(__name__)

# 消息处理器类型
MessageHandler = Callable[[Message], Coroutine[Any, Any, None]]


class MessageBus:
    """异步消息总线 — 支持点对点和广播通信，Leader 异步旁听"""

    def __init__(self):
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)
        self._type_handlers: dict[MessageType, list[MessageHandler]] = defaultdict(list)
        self._global_handlers: list[MessageHandler] = []
        self._leader: str | None = None
        self._history: list[Message] = []
        # Leader 旁听队列 — 异步消费，不阻塞主消息投递
        self._leader_queue: asyncio.Queue[Message] = asyncio.Queue()
        self._leader_consumer_task: asyncio.Task | None = None

    def set_leader(self, agent_name: str) -> None:
        """设置 Leader Agent"""
        self._leader = agent_name

    async def start_leader_consumer(self) -> None:
        """启动 Leader 旁听消费者"""
        if self._leader_consumer_task is None or self._leader_consumer_task.done():
            self._leader_consumer_task = asyncio.create_task(self._leader_consume_loop())

    async def stop_leader_consumer(self) -> None:
        """停止 Leader 旁听消费者"""
        if self._leader_consumer_task and not self._leader_consumer_task.done():
            self._leader_consumer_task.cancel()
            try:
                await self._leader_consumer_task
            except asyncio.CancelledError:
                pass

    async def _leader_consume_loop(self) -> None:
        """Leader 旁听消费者循环 — 异步处理，不阻塞消息投递"""
        while True:
            try:
                message = await self._leader_queue.get()
                if self._leader and self._leader in self._handlers:
                    for handler in self._handlers[self._leader]:
                        try:
                            await handler(message)
                        except Exception as e:
                            logger.error(f"Leader monitor handler error: {e}")
                self._leader_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Leader consumer error: {e}")

    def register(self, agent_name: str, handler: MessageHandler) -> None:
        """注册 Agent 的消息处理器"""
        self._handlers[agent_name].append(handler)

    def register_by_type(self, msg_type: MessageType, handler: MessageHandler) -> None:
        """注册按消息类型的处理器"""
        self._type_handlers[msg_type].append(handler)

    def register_global(self, handler: MessageHandler) -> None:
        """注册全局消息处理器（所有消息都会经过）"""
        self._global_handlers.append(handler)

    async def send(self, message: Message) -> None:
        """发送消息"""
        # Leader 监听所有通信
        self._history.append(message)

        # 全局处理器
        for handler in self._global_handlers:
            try:
                await handler(message)
            except Exception as e:
                logger.error(f"Global handler error: {e}")

        # 按消息类型的处理器
        if message.type in self._type_handlers:
            for handler in self._type_handlers[message.type]:
                try:
                    await handler(message)
                except Exception as e:
                    logger.error(f"Type handler error for {message.type}: {e}")

        if message.is_broadcast():
            # 广播：发给所有注册的 Agent
            for agent_name, handlers in self._handlers.items():
                if agent_name != message.sender:
                    for handler in handlers:
                        try:
                            await handler(message)
                        except Exception as e:
                            logger.error(f"Broadcast handler error for {agent_name}: {e}")
        else:
            # 点对点：发给指定 Agent
            if message.receiver in self._handlers:
                for handler in self._handlers[message.receiver]:
                    try:
                        await handler(message)
                    except Exception as e:
                        logger.error(f"Handler error for {message.receiver}: {e}")
            else:
                logger.warning(f"No handler registered for agent: {message.receiver}")

        # Leader 异步旁听子 Agent 间的对话（不阻塞主消息投递）
        if (
            self._leader
            and message.receiver != self._leader
            and message.sender != self._leader
            and not message.is_broadcast()
        ):
            await self._leader_queue.put(message)

    async def send_and_wait(
        self,
        message: Message,
        timeout: float = 60.0,
    ) -> Message | None:
        """发送消息并等待回复"""
        reply_event = asyncio.Event()
        reply_message: Message | None = None

        async def reply_handler(msg: Message) -> None:
            nonlocal reply_message
            if msg.reply_to == message.id:
                reply_message = msg
                reply_event.set()

        # 临时注册回复处理器
        self.register_global(reply_handler)

        await self.send(message)

        try:
            await asyncio.wait_for(reply_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Reply timeout for message {message.id}")
            return None
        finally:
            self._global_handlers.remove(reply_handler)

        return reply_message

    def get_history(
        self,
        agent_name: str | None = None,
        session_id: str | None = None,
        msg_type: MessageType | None = None,
    ) -> list[Message]:
        """获取消息历史"""
        result = self._history
        if agent_name:
            result = [m for m in result if m.sender == agent_name or m.receiver == agent_name]
        if session_id:
            result = [m for m in result if m.session_id == session_id]
        if msg_type:
            result = [m for m in result if m.type == msg_type]
        return result

    def clear_history(self) -> None:
        """清空消息历史"""
        self._history.clear()
