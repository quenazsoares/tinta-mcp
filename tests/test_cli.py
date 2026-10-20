import pytest

from tintamcp import installer, server
from tintamcp.main import main


def test_root_help_lists_install_and_explains_default_server(monkeypatch, capsys):
    monkeypatch.setattr(server, "main", lambda *args: pytest.fail("unexpected server startup"))
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "install" in output and "commands:" in output
    assert "Run without a command to start the server." in output


def test_install_help_uses_plain_language_and_has_no_removed_option(monkeypatch, capsys):
    monkeypatch.setattr(
        installer, "run_installer", lambda **kw: pytest.fail("unexpected installation")
    )
    with pytest.raises(SystemExit) as exc:
        main(["install", "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "--configure-mcp" not in output
    assert "Show planned changes without modifying files" in output
    assert "Choose a client" in output


def test_no_command_starts_server(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "main", lambda args=None: calls.append(args))
    main([])
    assert calls == [[]]


def test_install_dispatches_without_starting_server(monkeypatch):
    calls = []
    monkeypatch.setattr(installer, "run_installer", lambda **kw: calls.append(kw))
    monkeypatch.setattr(server, "main", lambda *args: pytest.fail("unexpected server startup"))
    main(["install", "--only", "mcp", "--agent", "codex", "--dry-run"])
    assert calls == [dict(auto_yes=False, agents=("codex",), dry_run=True, only="mcp")]


def test_removed_mcp_flag_fails_before_any_install(monkeypatch):
    monkeypatch.setattr(
        installer, "run_installer", lambda **kw: pytest.fail("unexpected installation")
    )
    with pytest.raises(SystemExit) as exc:
        main(["install", "--configure-mcp"])
    assert exc.value.code == 2
