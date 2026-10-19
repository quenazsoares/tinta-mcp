"""Prepare release versions and validate publication to TestPyPI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:rc([1-9]\d*))?")


def version_key(version: str) -> tuple[int, int, int, bool, int]:
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError("Use X.Y.Z or X.Y.ZrcN, with N starting at 1")
    major, minor, patch, candidate = match.groups()
    return int(major), int(minor), int(patch), candidate is None, int(candidate or 0)


def release_metadata(version: str, ref: str, event: str, target: str) -> dict:
    prerelease = not version_key(version)[3]
    tagged = ref.startswith("refs/tags/")
    if tagged and ref != f"refs/tags/v{version}":
        raise ValueError("Tag must match the version in pyproject.toml")
    if target not in {"none", "testpypi", "pypi"}:
        raise ValueError("Unknown publication target")
    if target != "none" and not tagged:
        raise ValueError("Publication requires selecting a version tag")
    if prerelease and target == "pypi":
        raise ValueError("Release candidates are only published to TestPyPI")
    publish = tagged and (event == "push" or target != "none")
    return {
        "version": version,
        "prerelease": prerelease,
        "testpypi": publish,
        "pypi": publish and not prerelease and (event == "push" or target == "pypi"),
    }


def prepare_version(root: Path, version: str) -> None:
    project = root / "pyproject.toml"
    current = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    if version_key(version) <= version_key(current):
        raise ValueError(f"Version must be newer than {current}")
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
    if status.strip():
        raise ValueError("Commit or stash pending changes before preparing a release")
    tag = f"v{version}"
    if subprocess.check_output(["git", "tag", "--list", tag], cwd=root).strip():
        raise ValueError(f"Tag {tag} already exists")
    lock = root / "uv.lock"
    originals = {project: project.read_bytes(), lock: lock.read_bytes() if lock.exists() else None}
    try:
        subprocess.run(["uv", "version", version], cwd=root, check=True)
        subprocess.run(["uv", "lock"], cwd=root, check=True)
    except subprocess.CalledProcessError:
        for path, contents in originals.items():
            if contents is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(contents)
        raise
    print("Review the version changes, then run:")
    print("git add pyproject.toml uv.lock")
    print(f'git commit -S -m "chore(release): prepare {version}"')
    print(f'git tag -s {tag} -m "Release {version}"')
    print(f"git push --atomic origin HEAD {tag}")


def download_verified_files(package_dir: Path, version: str, destination: Path) -> None:
    version_key(version)
    packages = sorted([*package_dir.glob("*.whl"), *package_dir.glob("*.tar.gz")])
    if not packages:
        raise ValueError("No package distributions found")
    index_url = f"https://test.pypi.org/pypi/tinta-mcp/{version}/json"
    published = {}
    for attempt in range(12):
        try:
            with urllib.request.urlopen(index_url, timeout=15) as response:
                metadata = json.load(response)
            published = {item["filename"]: item for item in metadata["urls"]}
            if all(package.name in published for package in packages):
                break
        except (urllib.error.URLError, TimeoutError):
            if attempt == 11:
                raise
        if attempt == 11:
            raise ValueError("TestPyPI did not expose all expected distributions")
        time.sleep(5)
    destination.mkdir(parents=True, exist_ok=True)
    for package in packages:
        expected = hashlib.sha256(package.read_bytes()).hexdigest()
        item = published[package.name]
        if item["digests"]["sha256"] != expected:
            raise ValueError(f"Published hash differs from tested build: {package.name}")
        with urllib.request.urlopen(item["url"], timeout=30) as response:
            contents = response.read()
        if hashlib.sha256(contents).hexdigest() != expected:
            raise ValueError(f"Downloaded hash differs from tested build: {package.name}")
        (destination / package.name).write_bytes(contents)
        print(f"Verified TestPyPI download: {package.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare", help="Update version and lock; print signed Git commands"
    )
    prepare.add_argument("version")
    commands.add_parser("info", help="Validate release context and write GitHub Actions outputs")
    download = commands.add_parser("download", help="Download and verify TestPyPI distributions")
    download.add_argument("version")
    download.add_argument("--directory", type=Path, default=Path("dist"))
    download.add_argument("--destination", type=Path, default=Path("testpypi-dist"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare_version(ROOT, args.version)
    elif args.command == "download":
        download_verified_files(args.directory, args.version, args.destination)
    else:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
            "version"
        ]
        values = release_metadata(
            version,
            os.environ["GITHUB_REF"],
            os.environ["GITHUB_EVENT_NAME"],
            os.environ.get("PUBLISH_TARGET", "none"),
        )
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            for key, value in values.items():
                output.write(f"{key}={str(value).lower()}\n")


if __name__ == "__main__":
    main()
