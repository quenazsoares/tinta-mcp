from pathlib import Path

from tintamcp import installer


def test_installer_copies_complete_extension_to_user_directory(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "extension"
    monkeypatch.setattr(installer, "get_inkscape_extensions_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "find_agent_skills_dirs", lambda: [])

    installer.run_installer(auto_yes=True)

    expected = ["tinta_mcp.py", "tinta_mcp.inx"]
    expected.extend(
        str(path.relative_to(source)) for path in (source / "tinta_mcp_ops").rglob("*.py")
    )
    for relative_path in expected:
        assert (tmp_path / relative_path).read_bytes() == (source / relative_path).read_bytes()
