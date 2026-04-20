"""消息总线 — Agent 间异步通信

架构决策：Leader 旁听采用「事后审计」模式，不要求实时因果一致性。
- 消息直接投递给接收者是同步的，保证实时性
- Leader 旁听走异步队列，可能有延迟
- 通过全局单调递增序列号 (seq)，Leader 消费时可还原因果顺序
- Leader 真正关心的任务状态变更 (TASK_COMPLETE/FAILED) 不经过旁听队列，
  而是直接投递给 Leader，因此不存在乱序风险
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Coroutine

from team_agent.messaging.protocol import Message, MessageCategory, MessageType, _MESSAGE_CATEGORIES

logger = logging.getLogger(__name__)

# 消息处理器类型
MessageHandler = Callable[[Message], Coroutine[Any, Any, None]]


class MessageBus:
    """异步消息总线 — 支持点对点和广播通信，Leader 异步旁听

    因果一致性策略：
    - 主消息投递：同步、按 seq 顺序，接收者看到的顺序是因果正确的
    - Leader 旁听：异步队列 + 批量排序消费，保证 Leader 最终能还原因果顺序
    - 任务状态消息 (TASK_COMPLETE/FAILED)：直接投递给 Leader，不走旁听队列
    """

    def __init__(self):
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)
        self._type_handlers: dict[MessageType, list[MessageHandler]] = defaultdict(list)
        self._global_handlers: list[MessageHandler] = []
        self._leader: str | None = None
        self._sub_leaders: set[str] = set()  # 子团队长 — 可发送 command 类型消息
        self._history: list[Message] = []
        # 全局单调递增序列号，每条消息发送时分配
        self._seq_counter: int = 0
        # Leader 旁听队列 — 异步消费，不阻塞主消息投递
        self._leader_queue: asyncio.Queue[Message] = asyncio.Queue()
        self._leader_consumer_task: asyncio.Task | None = None
        # Leader 旁听排序窗口：消费时攒一批消息，按 seq 排序后再处理
        self._leader_sort_window_size: int = 8
        # 异步仲裁队列 — 冲突仲裁不阻塞主消息流
        self._arbitration_queue: asyncio.Queue[Message] = asyncio.Queue()
        self._arbitration_task: asyncio.Task | None = None

    def set_leader(self, agent_name: str) -> None:
        """设置 Leader Agent"""
        self._leader = agent_name

    def add_sub_leader(self, agent_name: str) -> None:
        """注册子团队长 — 可对其子团队发送 command 类型消息

        使用场景：Leader 将一组相关子任务委派给某个子 Agent 管理，
        该子 Agent 成为"子团队长"，拥有对组内成员分配任务的权限。
        """
        self._sub_leaders.add(agent_name)
        logger.info(f"Sub-leader registered: {agent_name}")

    def remove_sub_leader(self, agent_name: str) -> None:
        """移除子团队长"""
        self._sub_leaders.discard(agent_name)
        logger.info(f"Sub-leader removed: {agent_name}")

    def _can_send_command(self, sender: str) -> bool:
        """检查发送者是否有权发送 command 类型消息"""
        return sender == self._leader or sender in self._sub_leaders

    async def start_leader_consumer(self) -> None:
        """启动 Leader 旁听消费者 + 仲裁消费者"""
        if self._leader_consumer_task is None or self._leader_consumer_task.done():
            self._leader_consumer_task = asyncio.create_task(self._leader_consume_loop())
        if self._arbitration_task is None or self._arbitration_task.done():
            self._arbitration_task = asyncio.create_task(self._arbitration_consume_loop())

    async def stop_leader_consumer(self) -> None:
        """停止 Leader 旁听消费者 + 仲裁消费者"""
        for task in (self._leader_consumer_task, self._arbitration_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _leader_consume_loop(self) -> None:
        """Leader 旁听消费者循环 — 批量排序消费，保证因果顺序

        策略：攒一批消息（_leader_sort_window_size），按 seq 排序后再投递给 Leader。
        这样即使 A→B→Leader 的旁听有延迟，Leader 最终看到的消息顺序
        与全局发送顺序一致，因果关系得以还原。
        """
        while True:
            try:
                batch: list[Message] = []

                # 等待第一条消息
                first = await self._leader_queue.get()
                batch.append(first)

                # 非阻塞地收集更多消息，攒满窗口或队列空即止
                while len(batch) < self._leader_sort_window_size:
                    try:
                        msg = self._leader_queue.get_nowait()
                        batch.append(msg)
                    except asyncio.QueueEmpty:
                        break

                # 按 seq 排序，还原因果顺序
                batch.sort(key=lambda m: m.seq)

                # 投递给 Leader
                if self._leader and self._leader in self._handlers:
                    for message in batch:
                        for handler in self._handlers[self._leader]:
                            try:
                                await handler(message)
                            except Exception as e:
                                logger.error(f"Leader monitor handler error: {e}")

                for _ in batch:
                    self._leader_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Leader consumer error: {e}")

    async def _arbitration_consume_loop(self) -> None:
        """仲裁消费者循环 — 异步处理冲突仲裁请求

        设计决策：仲裁走异步队列，不阻塞子 Agent 间的正常通信。
        当子 Agent 间出现冲突时，发送 ARBITRATION_REQUEST 到总线，
        Leader 异步处理并返回 ARBITRATION_RESULT。
        """
        while True:
            try:
                message = await self._arbitration_queue.get()

                if self._leader and self._leader in self._handlers:
                    for handler in self._handlers[self._leader]:
                        try:
                            await handler(message)
                        except Exception as e:
                            logger.error(f"Arbitration handler error: {e}")

                self._arbitration_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Arbitration consumer error: {e}")

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
        """发送消息

        1. 权限校验：command 类型消息只有 Leader/子团队长能发
        2. 分配全局单调递增序列号，保证因果序
        3. 主消息直接同步投递给接收者（实时性）
        4. 子 Agent 间非广播消息 → Leader 异步旁听队列（事后审计）
        5. 仲裁请求 → 异步仲裁队列（不阻塞主消息流）
        """
        # 权限校验：command 类型消息只有 Leader/子团队长能发
        if message.category() == MessageCategory.COMMAND and not self._can_send_command(message.sender):
            logger.warning(
                f"Permission denied: {message.sender} cannot send {message.type.value} "
                f"(command type requires leader/sub-leader role)"
            )
            return

        # 分配全局序列号
        self._seq_counter += 1
        message.seq = self._seq_counter

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

        # 仲裁请求 → 异步仲裁队列（不阻塞主消息流）
        if message.type == MessageType.ARBITRATION_REQUEST and self._leader:
            await self._arbitration_queue.put(message)

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
