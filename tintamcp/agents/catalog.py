"""Declarative global integration profiles, without filesystem side effects."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Adapter = Literal["json-mcpServers", "toml-codex"]


@dataclass(frozen=True)
class AgentContext:
    home: Path
    platform: str
    environ: Mapping[str, str]


@dataclass(frozen=True)
class PathRule:
    relative: str
    env_root: str | None = None
    env_relative: str = ""

    def resolve(self, context: AgentContext) -> Path:
        override = context.environ.get(self.env_root, "") if self.env_root else ""
        if override:
            # Resolve ~ against the supplied home, never the process user's home.
            root = context.home / override[2:] if override.startswith("~/") else Path(override)
            if not root.is_absolute():
                raise ValueError(f"{self.env_root} must be an absolute path")
            return root / self.env_relative
        return context.home / self.relative


@dataclass(frozen=True)
class McpTargetSpec:
    path: PathRule
    adapter: Adapter


@dataclass(frozen=True)
class AgentProfile:
    id: str
    label: str
    skill_paths: tuple[PathRule, ...]
    mcp: McpTargetSpec | None


def get_agent_profiles() -> tuple[AgentProfile, ...]:
    antigravity_mcp = McpTargetSpec(PathRule(".gemini/config/mcp_config.json"), "json-mcpServers")
    return (
        AgentProfile(
            "codex",
            "Codex",
            (PathRule(".agents/skills"), PathRule(".codex/skills")),
            McpTargetSpec(
                PathRule(".codex/config.toml", "CODEX_HOME", "config.toml"), "toml-codex"
            ),
        ),
        AgentProfile(
            "antigravity", "Antigravity App", (PathRule(".gemini/config/skills"),), antigravity_mcp
        ),
        AgentProfile(
            "antigravity-ide",
            "Antigravity IDE",
            (
                PathRule(".gemini/config/skills"),
                PathRule(".gemini/antigravity/skills"),
            ),
            antigravity_mcp,
        ),
        AgentProfile("claude-code", "Claude Code", (PathRule(".claude/skills"),), None),
        AgentProfile("cursor", "Cursor", (PathRule(".cursor/skills"),), None),
    )
