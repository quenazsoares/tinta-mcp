"""Check bundled assets and installation without touching the user's Inkscape profile."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tintamcp import installer


def main() -> None:
    assets = Path(installer.__file__).parent / "assets"
    extension = assets / "extension"
    skill = assets / "skills" / "tinta-designer"
    if not (skill / "SKILL.md").is_file():
        raise RuntimeError("Installed package is missing the designer skill")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        profile = root / "extensions"
        profile.mkdir()
        skills = root / "skills"
        skills.mkdir()
        with (
            patch.object(installer, "get_inkscape_extensions_dir", return_value=profile),
            patch.object(installer, "find_agent_skills_dirs", return_value=[skills]),
        ):
            installer.run_installer(auto_yes=True)
        expected = [
            "tinta_mcp.py",
            "tinta_mcp.inx",
            "tinta_mcp_ops/__init__.py",
            "tinta_mcp_ops/common.py",
            "tinta_mcp_ops/document_operations.py",
            "tinta_mcp_ops/element_mapping.py",
        ]
        for name in expected:
            if (profile / name).read_bytes() != (extension / name).read_bytes():
                raise RuntimeError(f"Extension installation differs from package: {name}")
        if (skills / "tinta-designer" / "SKILL.md").read_bytes() != (
            skill / "SKILL.md"
        ).read_bytes():
            raise RuntimeError("Installed skill differs from package")
    print("Installed package assets and extension/skill installation passed")


if __name__ == "__main__":
    main()
