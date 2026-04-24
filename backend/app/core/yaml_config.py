"""YAML + Pydantic configuration system.

Per requirements (CF-001, A-010): "配置系统统一采用 YAML + Pydantic"

Supports two types of config:
1. Governance config: security, approval, budget, path restrictions (can only be tightened)
2. Behavior config: model, prompt, stream, fallback (can be overridden per layer)
3. Prompt templates (L-012): customizable per-phase prompts

Usage:
  from app.core.yaml_config import yaml_settings
  governance = yaml_settings.governance
  behavior = yaml_settings.behavior
"""

import os
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

try:
    import yaml
except ImportError:
    yaml = None


# ===== Governance Config (can only be tightened, not relaxed) =====

class GovernanceConfig(BaseModel):
    """Security-related configuration that can only be tightened at lower levels."""
    safe_mode: bool = False
    command_blacklist: List[str] = Field(default_factory=lambda: [
        "rm -rf /", "rm -rf ~", "mkfs", "dd if=/dev/zero",
        ":(){ :|:& };:", "chmod -R 777 /", "chown -R",
        "> /dev/sda", "mv / /dev/null",
    ])
    protected_paths: List[str] = Field(default_factory=lambda: [
        "/etc", "/root", "~/.ssh", "/var", "/sys", "/proc",
    ])
    sensitive_file_patterns: List[str] = Field(default_factory=lambda: [
        ".env", "*.key", "*.pem", "*.p12", "*.pfx",
        "id_rsa", "id_ed25519", "credentials.json",
        "service-account*.json",
    ])
    max_command_timeout: int = Field(default=300, ge=1, le=3600)
    auto_approve: bool = False
    high_risk_requires_approval: bool = True


# ===== Behavior Config (can be overridden per layer) =====

class BehaviorConfig(BaseModel):
    """Behavior-related configuration that can be overridden per layer."""
    default_model: Optional[str] = None
    planning_model: Optional[str] = None
    execution_model: Optional[str] = None
    fallback_chain: List[str] = Field(default_factory=list)
    session_budget_usd: float = Field(default=10.0, ge=0)
    stream: bool = True
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
    max_continuation_rounds: int = Field(default=3, ge=0)
    max_roundtable_rounds: int = Field(default=5, ge=1, le=20)


class YamlSettings(BaseModel):
    """Root settings loaded from YAML + env, validated by Pydantic."""
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)


def _find_config_file() -> Optional[Path]:
    """Search for config file in standard locations."""
    search_paths = [
        Path("config.yaml"),
        Path("config.yml"),
        Path("config/team-agent.yaml"),
        Path("config/team-agent.yml"),
        Path(os.environ.get("TEAM_AGENT_CONFIG", "")),
    ]
    for p in search_paths:
        if p and p.exists():
            return p
    return None


def load_yaml_settings() -> YamlSettings:
    """Load settings from YAML file, falling back to defaults."""
    config_path = _find_config_file()
    if not config_path or yaml is None:
        return YamlSettings()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return YamlSettings(**data)
    except Exception:
        return YamlSettings()


# Global singleton
yaml_settings = load_yaml_settings()


# ===== L-012: Prompt Template Management =====

class PromptTemplates:
    """Customizable prompt templates for each planning phase (L-012).

    These can be overridden in config.yaml under the 'prompts' key.
    """

    DEFAULTS = {
        "analysis": LeaderAgent.ANALYSIS_SYSTEM_PROMPT if 'LeaderAgent' in dir() else "",
        "proposal": LeaderAgent.PROPOSAL_SYSTEM_PROMPT if 'LeaderAgent' in dir() else "",
        "plan": LeaderAgent.PLAN_SYSTEM_PROMPT if 'LeaderAgent' in dir() else "",
        "roundtable": "你是一个专业的讨论参与者。请从你的专业角度出发，对讨论主题发表看法。",
        "review": "你是一个代码审查专家。请审查以下代码或方案，给出具体的改进建议。",
    }

    _instance: Optional["PromptTemplates"] = None

    def __init__(self):
        self._templates: dict = dict(self.DEFAULTS)
        self._load_from_yaml()

    def _load_from_yaml(self):
        """Load custom prompts from yaml_settings."""
        try:
            custom = yaml_settings.prompts if hasattr(yaml_settings, 'prompts') else {}
            if custom and isinstance(custom, dict):
                self._templates.update(custom)
        except Exception:
            pass

    @classmethod
    def get(cls) -> "PromptTemplates":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_template(self, phase: str) -> str:
        """Get the prompt template for a given phase."""
        return self._templates.get(phase, "")

    def set_template(self, phase: str, template: str):
        """Override a prompt template at runtime."""
        self._templates[phase] = template

    def list_templates(self) -> dict:
        """List all available templates."""
        return dict(self._templates)

    def reset(self, phase: Optional[str] = None):
        """Reset template(s) to defaults."""
        if phase:
            self._templates[phase] = self.DEFAULTS.get(phase, "")
        else:
            self._templates = dict(self.DEFAULTS)


# Eagerly import to resolve defaults
try:
    from app.services.agents import LeaderAgent
    PromptTemplates.DEFAULTS["analysis"] = LeaderAgent.ANALYSIS_SYSTEM_PROMPT
    PromptTemplates.DEFAULTS["proposal"] = LeaderAgent.PROPOSAL_SYSTEM_PROMPT
    PromptTemplates.DEFAULTS["plan"] = LeaderAgent.PLAN_SYSTEM_PROMPT
except ImportError:
    pass

prompt_templates = PromptTemplates.get()
