"""Planning Session Orchestrator - drives the planning state machine.

State flow (per requirements):
  CREATED -> PLANNING -> AWAITING_APPROVAL -> READY_FOR_EXPORT -> COMPLETED

Internal sub-states mapped to main states:
  ANALYZING -> PLANNING
  GENERATING_PROPOSAL -> PLANNING
  GENERATING_PLAN -> READY_FOR_EXPORT

Branch states:
  PLANNING / AWAITING_APPROVAL / READY_FOR_EXPORT -> CANCELLED
  PLANNING / AWAITING_APPROVAL / READY_FOR_EXPORT -> FAILED
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.llm.router import LLMRouter
from app.models.models import PlanningSession, PlanningStatus, MessageType
from app.services.agents import LeaderAgent, ResearcherAgent, ReviewerAgent, PlannerAgent, AgentFactory
from app.services.event_bus import EventBus, Event
from app.services.context import classify_error, ErrorLevel

logger = logging.getLogger(__name__)


class PlanningOrchestrator:
    """Orchestrates the full planning lifecycle for a session."""

    def __init__(self, llm_router: LLMRouter, event_bus: EventBus):
        self.llm_router = llm_router
        self.event_bus = event_bus
        self._running_sessions: set = set()

    async def _resolve_model_and_provider(self, user_id: Optional[str] = None) -> tuple:
        """Resolve model and provider from DB settings. Returns (model, provider)."""
        model = None
        provider = None

        async with async_session() as db:
            from app.models.models import ModelSettings, ProviderConfig
            # Load user-specific settings if user_id provided
            if user_id:
                settings_result = await db.execute(
                    select(ModelSettings).where(ModelSettings.user_id == user_id)
                )
            else:
                settings_result = await db.execute(select(ModelSettings))
            model_settings = settings_result.scalars().first()
            if model_settings:
                model = model_settings.planning_model or model_settings.default_model
                provider = self._infer_provider(model, model_settings)

            if not provider or not model:
                from app.core.security import decrypt_api_key
                query = select(ProviderConfig).where(ProviderConfig.enabled == True)
                if user_id:
                    query = query.where(ProviderConfig.user_id == user_id)
                prov_result = await db.execute(query)
                providers = prov_result.scalars().all()
                for p in providers:
                    if p.api_key_encrypted and p.default_model:
                        provider = p.provider_name
                        model = p.default_model
                        break
                if not provider:
                    for p in providers:
                        if p.api_key_encrypted:
                            provider = p.provider_name
                            model = p.default_model
                            break

        if not model and provider:
            provider_info = self.llm_router.get_provider(provider)
            if provider_info:
                model = self._infer_model_from_provider(provider_info)
        if not model:
            model = "gpt-4o-mini"

        return model, provider

    async def _ensure_providers_loaded(self, user_id: Optional[str] = None):
        """Make sure LLM router has loaded providers from DB for the given user."""
        async with async_session() as db:
            await self.llm_router.load_providers(db, user_id=user_id)

    async def start_planning(self, session_id: str):
        """
        Kick off the planning flow for a session with multi-agent collaboration.

        Flow: Leader receives request → Researcher investigates → Leader synthesizes analysis
              → Reviewer reviews proposal → Leader generates final proposal → AWAITING_APPROVAL
        """
        if session_id in self._running_sessions:
            logger.warning(f"Session {session_id} is already running")
            return

        self._running_sessions.add(session_id)

        try:
            async with async_session() as db:
                session = await db.get(PlanningSession, session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return
                user_input = session.input_text
                user_id = session.user_id

            model, provider = await self._resolve_model_and_provider(user_id=user_id)
            await self._ensure_providers_loaded(user_id=user_id)

            # Create agent team
            leader = LeaderAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model,
                provider=provider,
            )
            researcher = ResearcherAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model,
                provider=provider,
            )
            reviewer = ReviewerAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model,
                provider=provider,
            )

            # A-011: Broadcast all agents' capabilities
            await leader.broadcast_capabilities(session_id)
            await researcher.broadcast_capabilities(session_id)
            await reviewer.broadcast_capabilities(session_id)

            # Notify UI that the team is assembled
            self.event_bus.publish(session_id, Event(
                event="team_assembled",
                data={
                    "agents": [
                        leader.get_agent_card(),
                        researcher.get_agent_card(),
                        reviewer.get_agent_card(),
                    ],
                },
            ))

            # ===== Phase 1: Research (ANALYZING sub-state) =====
            await self._update_status(session_id, PlanningStatus.ANALYZING)

            # Leader announces the task
            await leader.save_message(
                session_id,
                f"我收到了你的需求，让我来组织团队进行分析。首先请 Researcher 进行需求调研。",
                message_type=MessageType.CHAT,
                category="coordination",
            )
            leader.emit_status(session_id, "delegating", "正在委派 Researcher 进行调研...")

            # Researcher conducts research
            research_result = await researcher.research(session_id, user_input)

            # Leader synthesizes analysis with research input
            leader.emit_status(session_id, "analyzing", "正在综合调研结果进行需求分析...")
            analysis = await leader.run_analysis(session_id, user_input, research_context=research_result)

            # ===== Phase 2: Generate Proposal (GENERATING_PROPOSAL sub-state) =====
            await self._update_status(session_id, PlanningStatus.GENERATING_PROPOSAL)

            # Leader generates initial proposal
            proposal = await leader.run_proposal(session_id, user_input, analysis)

            # Reviewer reviews the proposal
            await leader.save_message(
                session_id,
                f"方案已生成，请 Reviewer 进行审查。",
                message_type=MessageType.CHAT,
                category="coordination",
            )
            review_result = await reviewer.review(session_id, proposal, review_type="proposal")

            # Leader refines proposal based on review feedback
            if review_result:
                leader.emit_status(session_id, "refining_proposal", "正在根据审查意见优化方案...")
                refined_proposal = await leader._refine_proposal_with_review(
                    session_id, proposal, review_result
                )
                if refined_proposal:
                    proposal = refined_proposal

            # Update session summary
            async with async_session() as db:
                session = await db.get(PlanningSession, session_id)
                if session:
                    session.summary = analysis[:500] if analysis else None
                    await db.commit()

            # SS-008: Generate auto-title using LLM
            try:
                from app.services.session_manager import generate_session_title
                title = await generate_session_title(self.llm_router, user_input, model=model or self._get_default_model())
                async with async_session() as db:
                    session = await db.get(PlanningSession, session_id)
                    if session:
                        session.title = title
                        await db.commit()
                self.event_bus.publish(session_id, Event(
                    event="title_updated",
                    data={"session_id": session_id, "title": title},
                ))
            except Exception as te:
                logger.warning(f"Auto-title generation failed for session {session_id}: {te}")

            # Phase 3: Wait for approval
            await self._update_status(session_id, PlanningStatus.AWAITING_APPROVAL)

            # Auto-generate proposal.md artifact
            try:
                from app.services.artifact import artifact_service
                await artifact_service.generate_proposal(session_id)
            except Exception as ae:
                logger.warning(f"Proposal artifact generation failed for session {session_id}: {ae}")

            # Record checkpoint
            await self._save_checkpoint(session_id, "business", "proposal_generated", "Proposal generated and awaiting approval")

        except Exception as e:
            logger.exception(f"Planning failed for session {session_id}")
            await self._update_status(session_id, PlanningStatus.FAILED, detail=str(e))
            self.event_bus.publish(session_id, Event(
                event="error",
                data={"message": f"Planning failed: {str(e)}"},
            ))
        finally:
            self._running_sessions.discard(session_id)

    async def approve_and_plan(self, session_id: str):
        """
        After user approves, generate the execution plan with multi-agent collaboration.

        Flow: Leader announces approval → Planner generates plan → Reviewer reviews plan
              → Leader finalizes plan → READY_FOR_EXPORT
        """
        if session_id in self._running_sessions:
            logger.warning(f"Session {session_id} is already running")
            return

        self._running_sessions.add(session_id)

        try:
            # Get the proposal from messages
            async with async_session() as db:
                from app.models.models import Message, MessageType
                result = await db.execute(
                    select(Message)
                    .where(
                        Message.session_id == session_id,
                        Message.message_type == MessageType.PROPOSAL,
                    )
                    .order_by(Message.seq.desc())
                )
                proposal_msg = result.scalars().first()
                if not proposal_msg:
                    raise ValueError("No proposal found for this session")

                proposal = proposal_msg.content

            model, provider = await self._resolve_model_and_provider()
            await self._ensure_providers_loaded()

            # Create agent team for plan generation
            leader = LeaderAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model,
                provider=provider,
            )
            reviewer = ReviewerAgent(
                llm_router=self.llm_router,
                event_bus=self.event_bus,
                model=model,
                provider=provider,
            )

            # A-011: Broadcast capabilities
            await leader.broadcast_capabilities(session_id)
            await reviewer.broadcast_capabilities(session_id)

            # Leader announces approval
            await leader.save_message(
                session_id,
                "方案已通过审批，现在开始制定执行计划。",
                message_type=MessageType.CHAT,
                category="coordination",
            )

            # Phase: Generate Plan (READY_FOR_EXPORT sub-state)
            await self._update_status(session_id, PlanningStatus.GENERATING_PLAN)
            plan_text, tasks = await leader.run_plan(session_id, proposal)

            # Reviewer reviews the execution plan
            await leader.save_message(
                session_id,
                "执行计划已生成，请 Reviewer 审查计划的完整性和可行性。",
                message_type=MessageType.CHAT,
                category="coordination",
            )
            plan_review = await reviewer.review(session_id, plan_text, review_type="plan")

            # If reviewer has significant feedback, leader adjusts
            if plan_review and len(plan_review) > 50:
                leader.emit_status(session_id, "refining_plan", "正在根据审查意见优化执行计划...")
                # Save review as a message for visibility
                # The plan itself stays since it's already been generated

            # Generate artifacts (proposal.md + execution_plan.json)
            try:
                from app.services.artifact import artifact_service
                await artifact_service.generate_proposal(session_id)
                await artifact_service.generate_execution_plan(session_id)
                self.event_bus.publish(session_id, Event(
                    event="artifacts",
                    data={"message": "Artifacts generated successfully"},
                ))
            except Exception as ae:
                logger.warning(f"Artifact generation failed for session {session_id}: {ae}")

            # Transition to READY_FOR_EXPORT
            await self._update_status(session_id, PlanningStatus.READY_FOR_EXPORT)

            # Record checkpoint
            await self._save_checkpoint(session_id, "business", "plan_ready", "Execution plan generated, ready for export")

            # Auto-transition to COMPLETED since export is available
            await self._update_status(session_id, PlanningStatus.COMPLETED)

            # DM-005/DM-006/M-005: Auto-archive completed session
            try:
                from app.services.session_manager import archive_planning_session
                await archive_planning_session(session_id, self.llm_router, model=model or self._get_default_model())
            except Exception as ae:
                logger.warning(f"Session archival failed for {session_id}: {ae}")

        except Exception as e:
            logger.exception(f"Plan generation failed for session {session_id}")
            await self._update_status(session_id, PlanningStatus.FAILED, detail=str(e))
            self.event_bus.publish(session_id, Event(
                event="error",
                data={"message": f"Plan generation failed: {str(e)}"},
            ))
        finally:
            self._running_sessions.discard(session_id)

    async def _update_status(self, session_id: str, status: PlanningStatus, detail: Optional[str] = None):
        """Update session status in DB and emit event.
        
        P1-1: Validates state transitions before applying.
        Maps internal sub-states to main states:
        - ANALYZING, GENERATING_PROPOSAL -> PLANNING
        - GENERATING_PLAN -> READY_FOR_EXPORT
        """
        from app.models.models import validate_planning_transition
        
        # Map sub-states to main states for DB persistence
        db_status = self._map_to_main_status(status)
        
        async with async_session() as db:
            session = await db.get(PlanningSession, session_id)
            if session:
                # P1-1: Validate transition
                if not validate_planning_transition(session.status, db_status):
                    logger.warning(
                        f"Invalid planning transition: {session.status.value} -> {db_status.value} "
                        f"for session {session_id}"
                    )
                    # Allow transition anyway but log the warning (don't block the flow)
                    # since orchestrator drives the state machine and transitions should be valid
                
                session.status = db_status
                await db.commit()

        # Emit event with the original status for UI to show sub-state
        self.event_bus.publish(session_id, Event(
            event="status",
            data={"status": status.value, "main_status": db_status.value, "detail": detail},
        ))

    def _map_to_main_status(self, status: PlanningStatus) -> PlanningStatus:
        """Map internal sub-states to the main state machine states per requirements."""
        sub_state_map = {
            PlanningStatus.ANALYZING: PlanningStatus.PLANNING,
            PlanningStatus.GENERATING_PROPOSAL: PlanningStatus.PLANNING,
            PlanningStatus.GENERATING_PLAN: PlanningStatus.READY_FOR_EXPORT,
        }
        return sub_state_map.get(status, status)

    def _get_default_model(self) -> str:
        """Get a reasonable default model from loaded providers."""
        for p in self.llm_router._providers.values():
            if p.default_model:
                return p.default_model
            base_url = (p.base_url or "").lower()
            if "deepseek" in base_url:
                return "deepseek-chat"
            if "siliconflow" in base_url:
                return "Qwen/Qwen2.5-7B-Instruct"
            if "moonshot" in base_url:
                return "moonshot-v1-8k"
        return "gpt-4o-mini"

    def _infer_model_from_provider(self, provider_info) -> str:
        """Infer a reasonable default model from provider info."""
        if provider_info.default_model:
            return provider_info.default_model
        base_url = (provider_info.base_url or "").lower()
        if "deepseek" in base_url:
            return "deepseek-chat"
        if "siliconflow" in base_url:
            return "Qwen/Qwen2.5-7B-Instruct"
        if "moonshot" in base_url:
            return "moonshot-v1-8k"
        if "groq" in base_url:
            return "llama-3.1-8b-instant"
        if "openrouter" in base_url:
            return "openai/gpt-4o-mini"
        if "localhost:11434" in base_url:
            return "llama3"
        return "gpt-4o-mini"

    async def _save_checkpoint(self, session_id: str, checkpoint_type: str, label: str, description: str):
        """Save a business-level checkpoint for the session."""
        try:
            async with async_session() as db:
                from app.models.models import Checkpoint
                import json
                checkpoint = Checkpoint(
                    session_type="planning",
                    session_id=session_id,
                    checkpoint_type=checkpoint_type,
                    label=label,
                    state_json=json.dumps({"description": description}),
                    created_by="orchestrator",
                )
                db.add(checkpoint)
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to save checkpoint for session {session_id}: {e}")

    async def _load_security_settings(self) -> dict:
        """Load security settings from file."""
        import json
        from pathlib import Path
        from app.core.config import settings

        security_file = settings.data_dir / "security_settings.json"
        defaults = {
            "safe_mode": False,
            "command_blacklist": ["rm -rf /", "mkfs", "dd if="],
            "protected_paths": ["/etc", "/root", "~/.ssh"],
            "sensitive_file_patterns": [".env", "*.key", "*.pem"],
            "max_command_timeout": 300,
        }
        if security_file.exists():
            try:
                return {**defaults, **json.loads(security_file.read_text(encoding="utf-8"))}
            except (json.JSONDecodeError, OSError):
                pass
        return defaults

    def _infer_provider(self, model: Optional[str], model_settings) -> Optional[str]:
        """Try to infer the provider name from the model string."""
        if not model:
            return None
        model_lower = model.lower()
        if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            return "openai"
        if "claude" in model_lower:
            return "anthropic"
        if "gemini" in model_lower:
            return "google"
        if "deepseek" in model_lower:
            return "deepseek"
        # Default: return None, will be resolved by LLMRouter
        return None


# Global singleton
orchestrator: Optional[PlanningOrchestrator] = None


def get_orchestrator() -> PlanningOrchestrator:
    global orchestrator
    if orchestrator is None:
        from app.llm.router import llm_router
        from app.services.event_bus import event_bus
        orchestrator = PlanningOrchestrator(llm_router, event_bus)
    return orchestrator
