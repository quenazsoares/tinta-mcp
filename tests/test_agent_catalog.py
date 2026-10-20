import pytest

from tintamcp.agents.catalog import AgentContext, AgentProfile, McpTargetSpec, PathRule
from tintamcp.agents.detection import detect_agent_targets


def context(tmp_path, platform="linux", **env):
    return AgentContext(tmp_path / "Usuário com espaços", platform, env)


def test_explicit_selection_offers_missing_canonical_paths_without_creating_them(tmp_path):
    ctx = context(tmp_path)
    targets = detect_agent_targets(ctx, ("codex",))
    assert {t.path for t in targets} == {
        ctx.home / ".agents/skills",
        ctx.home / ".codex/config.toml",
    }
    assert all(t.evidence == "explicit selection" for t in targets)
    assert not ctx.home.exists()


def test_no_evidence_does_not_offer_profiles(tmp_path):
    assert detect_agent_targets(context(tmp_path)) == ()


def test_codex_home_overrides_config_but_not_skills(tmp_path):
    ctx = context(tmp_path, CODEX_HOME=str(tmp_path / "custom codex"))
    targets = detect_agent_targets(ctx, ("codex",))
    assert {t.path for t in targets} == {
        tmp_path / "custom codex/config.toml",
        ctx.home / ".agents/skills",
    }


def test_codex_legacy_skill_is_reused_without_second_copy(tmp_path):
    ctx = context(tmp_path)
    legacy = ctx.home / ".codex/skills"
    (legacy / "tinta-designer").mkdir(parents=True)
    skill = next(t for t in detect_agent_targets(ctx) if t.capability == "skill")
    assert skill.path == legacy


def test_shared_antigravity_paths_are_deduplicated(tmp_path):
    ctx = context(tmp_path)
    (ctx.home / ".gemini/config").mkdir(parents=True)
    targets = detect_agent_targets(ctx)
    assert len(targets) == 2
    assert all(t.profile_ids == ("antigravity", "antigravity-ide") for t in targets)


def test_unknown_profile_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown agent"):
        detect_agent_targets(context(tmp_path), ("nonexistent",))


def test_redirected_paths_require_explicit_selection(tmp_path):
    ctx = context(tmp_path)
    destination = tmp_path / "actual"
    destination.mkdir()
    ctx.home.mkdir()
    try:
        (ctx.home / ".codex").symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is not permitted")
    target = next(t for t in detect_agent_targets(ctx) if t.capability == "mcp")
    assert target.path == destination / "config.toml"
    assert target.requires_explicit_selection
    selected = next(t for t in detect_agent_targets(ctx, ("codex",)) if t.capability == "mcp")
    assert not selected.requires_explicit_selection


def test_another_profile_can_reuse_json_adapter(tmp_path):
    ctx = context(tmp_path)
    profile = AgentProfile(
        "example",
        "Example",
        (PathRule(".example/skills"),),
        McpTargetSpec(PathRule(".example/mcp.json"), "json-mcpServers"),
    )
    targets = detect_agent_targets(ctx, ("example",), profiles=(profile,))
    assert len(targets) == 2
    assert next(t for t in targets if t.capability == "mcp").adapter == "json-mcpServers"


def test_windows_case_variants_are_deduplicated(tmp_path):
    profiles = tuple(
        AgentProfile(n, n, (PathRule(p),), None)
        for n, p in [("one", ".shared/skills"), ("two", ".SHARED/skills")]
    )
    targets = detect_agent_targets(context(tmp_path, "win32"), ("one", "two"), profiles=profiles)
    assert len(targets) == 1
    assert targets[0].profile_ids == ("one", "two")


def test_new_shared_destination_reports_existing_legacy_copy(tmp_path):
    ctx = context(tmp_path)
    (ctx.home / ".gemini/config").mkdir(parents=True)
    legacy = ctx.home / ".gemini/antigravity/skills"
    (legacy / "tinta-designer").mkdir(parents=True)
    targets = detect_agent_targets(ctx)
    canonical = next(
        t for t in targets if t.capability == "skill" and t.profile_ids == ("antigravity",)
    )
    assert canonical.other_skill_paths == (legacy.resolve(),)
    existing = next(
        t for t in targets if t.capability == "skill" and t.profile_ids == ("antigravity-ide",)
    )
    assert existing.path == legacy.resolve()


def test_default_detection_keeps_skills_after_an_mcp_profile(tmp_path):
    ctx = context(tmp_path)
    (ctx.home / ".codex").mkdir(parents=True)
    (ctx.home / ".cursor").mkdir()
    targets = detect_agent_targets(ctx)
    assert {t.profile_ids for t in targets if t.capability == "skill"} == {("codex",), ("cursor",)}
