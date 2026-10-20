from pathlib import Path
from types import SimpleNamespace

from tintamcp import installer


def test_inkscape_discovery_tries_next_candidate_after_query_failure(tmp_path, monkeypatch):
    exe, com = tmp_path / "inkscape.exe", tmp_path / "inkscape.com"
    exe.touch()
    com.touch()
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setenv("INKSCAPE_COMMAND", str(exe))
    calls = []

    def query(arguments, **kwargs):
        calls.append(arguments[0])
        if len(calls) == 1:
            raise OSError("cannot query")
        return SimpleNamespace(returncode=0, stdout=str(tmp_path / "actual-profile"))

    monkeypatch.setattr(installer.subprocess, "run", query)
    assert installer.get_inkscape_extensions_dir() == tmp_path / "actual-profile/extensions"
    assert len(calls) == 2
    assert not (tmp_path / "actual-profile").exists()


def test_fallback_warns_and_does_not_create_directories(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(Path, "is_file", lambda _: False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "fallback"))
    assert installer.get_inkscape_extensions_dir() == tmp_path / "fallback/inkscape/extensions"
    assert "fallback" in capsys.readouterr().out.lower()
    assert not (tmp_path / "fallback").exists()
