"""Resolve available integration destinations and explain their evidence."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .catalog import Adapter, AgentContext, AgentProfile, get_agent_profiles


@dataclass(frozen=True)
class DetectedTarget:
    path: Path
    capability: Literal["skill", "mcp"]
    profile_ids: tuple[str, ...]
    labels: tuple[str, ...]
    evidence: str
    adapter: Adapter | None = None
    requires_explicit_selection: bool = False
    other_skill_paths: tuple[Path, ...] = ()


def redirected(path: Path) -> bool:
    """Include ancestor links and Windows junctions, even for a missing leaf."""
    return any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))


def detect_agent_targets(
    context: AgentContext,
    selected: tuple[str, ...] = (),
    *,
    profiles: tuple[AgentProfile, ...] | None = None,
) -> tuple[DetectedTarget, ...]:
    profiles = get_agent_profiles() if profiles is None else profiles
    unknown = set(selected) - {p.id for p in profiles}
    if unknown:
        raise ValueError(f"Unknown agent: {', '.join(sorted(unknown))}")

    def key_for(path: Path) -> str:
        return str(path).casefold() if context.platform == "win32" else str(path)

    # Profiles with the same preferred destination may also load older paths.
    # Record installed copies so a new destination cannot silently duplicate one.
    installed_by_preferred: dict[str, set[Path]] = {}
    for profile in profiles:
        if profile.skill_paths:
            preferred = key_for(profile.skill_paths[0].resolve(context).resolve())
            installed = installed_by_preferred.setdefault(preferred, set())
            installed.update(
                r.resolve(context).resolve()
                for r in profile.skill_paths
                if (r.resolve(context) / "tinta-designer").is_dir()
            )
    found: dict[tuple[str, str], DetectedTarget] = {}
    for profile in profiles:
        if selected and profile.id not in selected:
            continue
        skill_paths = [r.resolve(context) for r in profile.skill_paths]
        mcp = profile.mcp
        config = mcp.path.resolve(context) if mcp else None
        available = any(p.parent.is_dir() for p in skill_paths) or (
            config is not None and config.parent.is_dir()
        )
        if not selected and not available:
            continue
        # Reuse an installed skill, including a supported legacy location.
        existing = [p for p in skill_paths if (p / "tinta-designer").is_dir()]
        paths: list[tuple[Path, Literal["skill", "mcp"], Adapter | None]] = [
            (p, "skill", None) for p in (existing or skill_paths[:1])
        ]
        if config is not None and mcp is not None:
            paths.append((config, "mcp", mcp.adapter))
        for path, capability, adapter in paths:
            actual = path.resolve()
            key = (key_for(actual), capability)
            alternatives = (
                installed_by_preferred.get(key_for(skill_paths[0].resolve()), set())
                if skill_paths
                else set()
            )
            target = DetectedTarget(
                actual,
                capability,
                (profile.id,),
                (profile.label,),
                "explicit selection" if selected else "configuration directory found",
                adapter,
                redirected(path) and not bool(selected),
                tuple(sorted((p for p in alternatives if key_for(p) != key_for(actual)), key=str))
                if capability == "skill"
                else (),
            )
            if key in found:
                prior = found[key]
                if prior.adapter != adapter:
                    raise ValueError(f"Conflicting adapters for shared destination: {actual}")
                target = replace(
                    prior,
                    profile_ids=prior.profile_ids + target.profile_ids,
                    labels=prior.labels + target.labels,
                )
            found[key] = target
    return tuple(found.values())
