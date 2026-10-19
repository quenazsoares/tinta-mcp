"""Check that release archives contain only intended files and required assets."""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_FILES = {
    "tinta_mcp.inx",
    "tinta_mcp.py",
    "tinta_mcp_ops/__init__.py",
    "tinta_mcp_ops/common.py",
    "tinta_mcp_ops/document_operations.py",
    "tinta_mcp_ops/element_mapping.py",
}
SKILL_FILES = {"SKILL.md"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"Release artifact verification failed: {message}")


def validate_zip_paths(names: list[str], artifact: str) -> None:
    for name in names:
        path = PurePosixPath(name.rstrip("/"))
        require(
            not path.is_absolute() and ".." not in path.parts, f"unsafe path in {artifact}: {name}"
        )


def check_zip(
    path: Path,
    required: set[str],
    artifact: str,
    expected: set[str] | None = None,
) -> None:
    require(path.is_file(), f"missing {path.name}")
    with zipfile.ZipFile(path) as archive:
        entries = archive.namelist()
    validate_zip_paths(entries, artifact)
    files = {name for name in entries if not name.endswith("/")}
    missing = required - files
    require(not missing, f"{artifact} is missing: {', '.join(sorted(missing))}")
    if expected is not None:
        unexpected = files - expected
        require(
            not unexpected,
            f"{artifact} contains unexpected files: {', '.join(sorted(unexpected))}",
        )


def main(artifact_dir: Path) -> None:
    skill = artifact_dir / "tinta-designer-skill.zip"
    skill_paths = {f"tinta-designer/{name}" for name in SKILL_FILES}
    check_zip(skill, skill_paths, skill.name, skill_paths)

    wheel_paths = list((artifact_dir / "dist").glob("*.whl"))
    require(bool(wheel_paths), "no wheel found in dist/")
    wheel_path = wheel_paths[0]
    wheel_files = {f"tintamcp/assets/extension/{name}" for name in EXTENSION_FILES}
    wheel_files |= {f"tintamcp/assets/skills/tinta-designer/{name}" for name in SKILL_FILES}
    check_zip(wheel_path, wheel_files | {"tintamcp/server.py"}, wheel_path.name)

    source_paths = list((artifact_dir / "dist").glob("*.tar.gz"))
    require(bool(source_paths), "no source distribution found in dist/")
    source_path = source_paths[0]
    with tarfile.open(source_path, "r:gz") as archive:
        source_names = {PurePosixPath(name).as_posix() for name in archive.getnames()}
    validate_zip_paths(list(source_names), source_path.name)
    suffixes = {"/" + name for name in EXTENSION_FILES}
    missing = {
        suffix for suffix in suffixes if not any(name.endswith(suffix) for name in source_names)
    }
    require(
        not missing, f"{source_path.name} is missing extension files: {', '.join(sorted(missing))}"
    )
    print("Designer skill ZIP, wheel, and source distribution contain the required assets.")


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--directory", type=Path, default=ROOT)
        main(parser.parse_args().directory.resolve())
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"Release artifact verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
