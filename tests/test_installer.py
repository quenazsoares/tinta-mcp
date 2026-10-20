from pathlib import Path

import pytest

from tintamcp import installer
from tintamcp.agents.catalog import AgentContext
from tintamcp.agents.detection import DetectedTarget
from tintamcp.main import main as cli_main


def test_installer_copies_complete_extension_to_user_directory(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "extension"
    monkeypatch.setattr(installer, "get_inkscape_extensions_dir", lambda: tmp_path)
    monkeypatch.setattr(
        installer, "get_agent_context", lambda: AgentContext(tmp_path / "home", "linux", {})
    )

    installer.run_installer(auto_yes=True)

    expected = ["tinta_mcp.py", "tinta_mcp.inx"]
    expected.extend(
        str(path.relative_to(source)) for path in (source / "tinta_mcp_ops").rglob("*.py")
    )
    for relative_path in expected:
        assert (tmp_path / relative_path).read_bytes() == (source / relative_path).read_bytes()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    extension = tmp_path / "extensions"
    monkeypatch.setattr(installer, "get_agent_context", lambda: AgentContext(home, "linux", {}))
    monkeypatch.setattr(installer, "get_inkscape_extensions_dir", lambda: extension)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/usr/bin/uvx")
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    return home, extension


def test_shared_destinations_prompt_once_per_component(installation, monkeypatch, capsys):
    home, _ = installation
    (home / ".gemini/config").mkdir(parents=True)
    calls = []
    monkeypatch.setattr("builtins.input", lambda prompt: calls.append(prompt) or "y")
    installer.run_installer()
    assert len(calls) == 2
    assert sum("skill" in prompt for prompt in calls) == 1
    assert sum("MCP" in prompt for prompt in calls) == 1
    assert (home / ".gemini/config/skills/tinta-designer/SKILL.md").is_file()
    assert capsys.readouterr().out.count("Antigravity App, Antigravity IDE (") == 1


@pytest.mark.parametrize("answer", ["n", EOFError, KeyboardInterrupt])
def test_skill_decline_and_interrupt_do_not_copy(installation, monkeypatch, answer):
    home, _ = installation

    def reply(prompt):
        if isinstance(answer, type):
            raise answer()
        return answer

    monkeypatch.setattr("builtins.input", reply)
    installer.run_installer(agents=("codex",))
    assert not (home / ".agents").exists()


def test_dry_run_never_writes_even_extension_directory(installation, capsys):
    home, extension = installation
    installer.run_installer(auto_yes=True, agents=("codex",), dry_run=True)
    assert not extension.exists()
    assert list(home.iterdir()) == []
    assert "command" in capsys.readouterr().out


def test_unknown_agent_fails_before_any_install(installation):
    home, extension = installation
    with pytest.raises(ValueError, match="Unknown agent"):
        installer.run_installer(agents=("unknown",))
    assert not extension.exists()
    assert list(home.iterdir()) == []


def test_mcp_has_separate_approval_and_backup(installation, monkeypatch):
    home, _ = installation
    config = home / ".codex/config.toml"
    config.parent.mkdir()
    before = b'# original\n[mcp_servers.tinta]\ncommand="uvx"\nargs=["--from","old","tinta-mcp"]\n'
    config.write_bytes(before)
    answers = iter(["n", "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    installer.run_installer(agents=("codex",))
    assert not (home / ".agents").exists()
    assert b"--from" not in config.read_bytes()
    assert next(config.parent.glob("config.toml.bak-*")).read_bytes() == before


@pytest.mark.parametrize("answer", ["n", EOFError, KeyboardInterrupt])
def test_yes_never_approves_mcp(installation, monkeypatch, answer):
    home, _ = installation

    def reply(prompt):
        if isinstance(answer, type):
            raise answer()
        return answer

    monkeypatch.setattr("builtins.input", reply)
    installer.run_installer(auto_yes=True, agents=("codex",))
    assert (home / ".agents/skills/tinta-designer/SKILL.md").is_file()
    assert not (home / ".codex/config.toml").exists()


def test_noninteractive_yes_cannot_configure_mcp(installation, monkeypatch):
    home, _ = installation
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    installer.run_installer(auto_yes=True, agents=("codex",))
    assert not (home / ".codex/config.toml").exists()


def test_unchanged_skill_and_mcp_do_not_prompt_or_create_backups(installation, monkeypatch):
    home, _ = installation
    config = home / ".codex/config.toml"
    config.parent.mkdir()
    before = b'[mcp_servers.tinta]\ncommand="uvx"\nargs=["tinta-mcp"]\n'
    config.write_bytes(before)
    installer.run_installer(auto_yes=True, agents=("codex",))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    installer.run_installer(agents=("codex",))
    assert config.read_bytes() == before
    assert not list(config.parent.glob("*.bak-*"))


def test_missing_uvx_is_reported_without_writing_config(installation, monkeypatch, capsys):
    home, _ = installation
    monkeypatch.setattr(installer.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit) as exc:
        installer.run_installer(auto_yes=True, agents=("codex",))
    assert exc.value.code == 1
    assert not (home / ".codex/config.toml").exists()
    assert "uvx" in capsys.readouterr().out


def test_partial_failure_reports_successful_configuration_and_exits_nonzero(
    installation, monkeypatch, capsys
):
    home, _ = installation
    bad = home / ".gemini/config/mcp_config.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("invalid json")
    monkeypatch.setattr("builtins.input", lambda _: "y")
    with pytest.raises(SystemExit) as exc:
        installer.run_installer(auto_yes=True, agents=("codex", "antigravity"))
    assert exc.value.code == 1
    assert (home / ".codex/config.toml").is_file()
    assert bad.read_text() == "invalid json"
    output = capsys.readouterr().out
    assert "Configured" in output and "Error" in output
    assert output.count("Invalid JSON") == 1


def test_mcp_preview_is_printed_before_asking_for_approval(installation, monkeypatch, capsys):
    def answer(prompt):
        assert "command" in capsys.readouterr().out
        return "n"

    monkeypatch.setattr("builtins.input", answer)
    installer.run_installer(auto_yes=True, agents=("codex",))


def test_install_cli_parsing_and_rejection(monkeypatch):
    calls = []
    monkeypatch.setattr(installer, "run_installer", lambda **kw: calls.append(kw))
    cli_main(["install", "--agent", "codex", "--agent", "cursor", "--yes", "--dry-run"])
    assert calls == [dict(auto_yes=True, agents=("codex", "cursor"), dry_run=True, only=None)]
    with pytest.raises(SystemExit) as exc:
        cli_main(["install", "--unknown"])
    assert exc.value.code == 2


def test_empty_reply_does_not_approve_mcp(installation, monkeypatch):
    home, _ = installation
    monkeypatch.setattr("builtins.input", lambda _: "")
    installer.run_installer(auto_yes=True, agents=("codex",))
    assert not (home / ".codex/config.toml").exists()


def test_relative_path_lookup_is_resolved_before_mcp_configuration(installation, monkeypatch):
    import tomllib

    home, _ = installation
    monkeypatch.setattr(installer.shutil, "which", lambda _: "tools/uvx.exe")
    monkeypatch.setattr("builtins.input", lambda _: "y")
    installer.run_installer(auto_yes=True, agents=("codex",))
    data = tomllib.loads((home / ".codex/config.toml").read_text())
    assert Path(data["mcp_servers"]["tinta"]["command"]).is_absolute()


@pytest.fixture
def skill_update(tmp_path):
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "SKILL.md").write_text("new skill")
    skills = tmp_path / "skills"
    destination = skills / "tinta-designer"
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("old skill")
    (destination / "personal.md").write_text("personal notes")
    target = DetectedTarget(skills, "skill", ("test",), ("Test",), "test")
    return source, destination, target


def test_skill_update_preserves_additional_files(skill_update):
    source, destination, target = skill_update
    installer._install_skill(target, source, True, False)
    assert (destination / "SKILL.md").read_text() == "new skill"
    assert (destination / "personal.md").read_text() == "personal notes"
    assert list(destination.parent.iterdir()) == [destination]


def test_failed_skill_copy_keeps_the_installed_version(skill_update, monkeypatch):
    source, destination, target = skill_update
    copy = installer.shutil.copytree

    def fail_bundle(src, dst, *args, **kwargs):
        if Path(src) == source:
            raise OSError("bundle copy failed")
        return copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(installer.shutil, "copytree", fail_bundle)
    with pytest.raises(OSError, match="bundle copy failed"):
        installer._install_skill(target, source, True, False)
    assert (destination / "SKILL.md").read_text() == "old skill"
    assert (destination / "personal.md").read_text() == "personal notes"
    assert list(destination.parent.iterdir()) == [destination]


def test_failed_skill_replacement_restores_previous_directory(skill_update, monkeypatch):
    source, destination, target = skill_update
    rename = Path.rename

    def fail_new(path, new_path):
        if path.name == "new":
            raise OSError("replacement failed")
        return rename(path, new_path)

    monkeypatch.setattr(Path, "rename", fail_new)
    with pytest.raises(OSError, match="replacement failed"):
        installer._install_skill(target, source, True, False)
    assert (destination / "SKILL.md").read_text() == "old skill"
    assert (destination / "personal.md").read_text() == "personal notes"
    assert list(destination.parent.iterdir()) == [destination]


def test_failed_skill_restore_retains_recoverable_directory(skill_update, monkeypatch):
    source, destination, target = skill_update
    rename = Path.rename

    def fail_new_and_restore(path, new_path):
        if path.name in ("new", "previous"):
            raise OSError("rename failed")
        return rename(path, new_path)

    monkeypatch.setattr(Path, "rename", fail_new_and_restore)
    with pytest.raises(OSError) as exc:
        installer._install_skill(target, source, True, False)
    retained = next(destination.parent.glob(".tinta-skill-*/previous"))
    assert (retained / "SKILL.md").read_text() == "old skill"
    assert (retained / "personal.md").read_text() == "personal notes"
    assert any(str(retained) in note for note in getattr(exc.value, "__notes__", ()))


def test_legacy_copy_requires_confirmation_even_with_yes(installation, monkeypatch, capsys):
    home, _ = installation
    (home / ".gemini/config").mkdir(parents=True)
    legacy = home / ".gemini/antigravity/skills/tinta-designer"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("old version")
    calls = []
    monkeypatch.setattr("builtins.input", lambda prompt: calls.append(prompt) or "n")
    installer.run_installer(auto_yes=True)
    assert not (home / ".gemini/config/skills/tinta-designer").exists()
    assert len(calls) == 2
    assert sum("another skill copy" in prompt for prompt in calls) == 1
    assert sum("MCP" in prompt for prompt in calls) == 1
    assert "another copy" in capsys.readouterr().out


@pytest.mark.parametrize("interactive", [False, True])
def test_legacy_copy_is_skipped_without_affirmative_approval(
    installation, monkeypatch, interactive
):
    home, _ = installation
    (home / ".gemini/config").mkdir(parents=True)
    legacy = home / ".gemini/antigravity/skills/tinta-designer"
    legacy.mkdir(parents=True)
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(
        "builtins.input", lambda _: "" if interactive else pytest.fail("unexpected prompt")
    )
    installer.run_installer(auto_yes=True)
    assert not (home / ".gemini/config/skills/tinta-designer").exists()


def test_legacy_copy_preview_shows_both_paths_without_prompting(installation, monkeypatch, capsys):
    home, _ = installation
    (home / ".gemini/config").mkdir(parents=True)
    legacy = home / ".gemini/antigravity/skills/tinta-designer"
    legacy.mkdir(parents=True)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    installer.run_installer(auto_yes=True, dry_run=True)
    output = capsys.readouterr().out
    assert str(legacy) in output
    assert str(home / ".gemini/config/skills/tinta-designer") in output
    assert "another copy" in output
    assert not (home / ".gemini/config/skills/tinta-designer").exists()


def test_json_adapter_reload_message_does_not_name_a_client(installation, capsys):
    # The same JSON adapter can belong to another client.
    home, _ = installation
    target = DetectedTarget(
        home / "example.json", "mcp", ("example",), ("Example",), "test", "json-mcpServers"
    )
    from unittest.mock import patch

    with patch("builtins.input", return_value="y"):
        installer._configure_mcp(target, False)
    output = capsys.readouterr().out
    assert "Antigravity" not in output
    assert "restart" in output.lower()


def test_only_mcp_does_not_probe_or_install_extension_or_skills(installation, monkeypatch):
    home, extension = installation
    monkeypatch.setattr(
        installer, "get_inkscape_extensions_dir", lambda: pytest.fail("unexpected Inkscape lookup")
    )
    monkeypatch.setattr("builtins.input", lambda _: "y")
    installer.run_installer(agents=("codex",), only="mcp")
    assert (home / ".codex/config.toml").is_file()
    assert not extension.exists()
    assert not (home / ".agents").exists()


def test_only_skills_ignores_invalid_codex_mcp_location(installation, monkeypatch):
    home, extension = installation
    monkeypatch.setattr(
        installer,
        "get_agent_context",
        lambda: AgentContext(home, "linux", {"CODEX_HOME": "relative"}),
    )
    installer.run_installer(auto_yes=True, agents=("codex",), only="skills")
    assert (home / ".agents/skills/tinta-designer/SKILL.md").is_file()
    assert not extension.exists()


def test_only_extension_does_not_detect_agents(installation, monkeypatch):
    _, extension = installation
    monkeypatch.setattr(
        installer, "get_agent_context", lambda: pytest.fail("unexpected agent detection")
    )
    installer.run_installer(only="extension")
    assert (extension / "tinta_mcp.inx").is_file()


def test_bad_codex_home_reports_error_but_continues_other_clients(
    installation, monkeypatch, capsys
):
    home, extension = installation
    (home / ".gemini/config").mkdir(parents=True)
    monkeypatch.setattr(
        installer,
        "get_agent_context",
        lambda: AgentContext(home, "linux", {"CODEX_HOME": "relative"}),
    )
    with pytest.raises(SystemExit) as exc:
        installer.run_installer(auto_yes=True)
    assert exc.value.code == 1
    assert (extension / "tinta_mcp.py").is_file()
    assert (home / ".gemini/config/skills/tinta-designer/SKILL.md").is_file()
    output = capsys.readouterr().out
    assert "CODEX_HOME" in output
    assert "Summary" in output
    assert "Codex: error" in output


def test_header_shows_executed_package_version_and_summary(installation, monkeypatch, capsys):
    monkeypatch.setattr(installer, "version", lambda _: "9.8.7")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    installer.run_installer(agents=("codex",))
    output = capsys.readouterr().out
    assert "Tinta MCP 9.8.7" in output
    assert "Summary" in output
    assert "Inkscape extension: installed" in output
    assert "Codex skill: skipped" in output
    assert "Codex MCP: skipped" in output


def test_only_mcp_reports_manual_setup_for_unsupported_client(installation, capsys):
    installer.run_installer(agents=("cursor",), only="mcp")
    assert "Cursor MCP: skipped (manual setup)" in capsys.readouterr().out


def test_only_mcp_dry_run_has_planned_summary_without_any_write(installation, monkeypatch, capsys):
    home, extension = installation
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    installer.run_installer(agents=("codex",), only="mcp", dry_run=True)
    assert list(home.iterdir()) == [] and not extension.exists()
    output = capsys.readouterr().out
    assert "Codex MCP: planned" in output
    assert "no changes written" in output


def test_default_install_offers_skills_and_mcp_with_separate_approvals(installation, monkeypatch):
    home, extension = installation
    (home / ".codex").mkdir()
    prompts = []
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or next(answers))
    installer.run_installer()
    assert len(prompts) == 2
    assert "skill" in prompts[0] and "MCP" in prompts[1]
    assert (extension / "tinta_mcp.py").is_file()
    assert (home / ".agents/skills/tinta-designer/SKILL.md").is_file()
    assert not (home / ".codex/config.toml").exists()


def test_default_install_can_configure_mcp_when_approved(installation, monkeypatch):
    home, _ = installation
    (home / ".codex").mkdir()
    monkeypatch.setattr("builtins.input", lambda _: "y")
    installer.run_installer()
    assert (home / ".codex/config.toml").is_file()


def test_default_dry_run_includes_mcp_without_writing(installation, monkeypatch, capsys):
    home, extension = installation
    (home / ".codex").mkdir()
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    installer.run_installer(dry_run=True)
    output = capsys.readouterr().out
    assert "Codex MCP: planned" in output
    assert not (home / ".codex/config.toml").exists()
    assert not extension.exists()


def test_default_yes_does_not_approve_mcp_without_terminal(installation, monkeypatch, capsys):
    home, _ = installation
    (home / ".codex").mkdir()
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    installer.run_installer(auto_yes=True)
    assert not (home / ".codex/config.toml").exists()
    assert "Codex MCP: skipped" in capsys.readouterr().out


def test_cli_without_mcp_flag_includes_mcp_preview(installation, capsys):
    home, _ = installation
    (home / ".codex").mkdir()
    cli_main(["install", "--dry-run"])
    assert "Codex MCP: planned" in capsys.readouterr().out
