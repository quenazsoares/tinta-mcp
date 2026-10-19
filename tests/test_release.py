import hashlib
import io
import json
import subprocess

import pytest

from scripts.release import download_verified_files, prepare_version, release_metadata


@pytest.mark.parametrize(
    "version,target,expected",
    [
        ("0.2.2rc1", "none", (True, True, False)),
        ("0.2.2", "none", (False, True, True)),
        ("0.2.2", "testpypi", (False, True, False)),
        ("0.2.2", "pypi", (False, True, True)),
        ("0.2.2rc2", "testpypi", (True, True, False)),
    ],
)
def test_tag_routes_candidates_and_stable_versions(version, target, expected):
    event = "push" if target == "none" else "workflow_dispatch"
    result = release_metadata(version, f"refs/tags/v{version}", event, target)
    assert (result["prerelease"], result["testpypi"], result["pypi"]) == expected


def test_manual_branch_run_builds_without_publishing():
    result = release_metadata("0.2.1", "refs/heads/main", "workflow_dispatch", "none")
    assert result["testpypi"] is False
    assert result["pypi"] is False


def test_manual_tag_run_without_target_does_not_publish():
    result = release_metadata("0.2.2", "refs/tags/v0.2.2", "workflow_dispatch", "none")
    assert result["testpypi"] is False
    assert result["pypi"] is False


@pytest.mark.parametrize(
    "version,ref,target",
    [
        ("0.2.2rc1", "refs/tags/v0.2.2rc1", "pypi"),
        ("0.2.2", "refs/tags/v0.2.1", "none"),
        ("0.2.2", "refs/heads/main", "testpypi"),
        ("0.2.2", "refs/heads/main", "pypi"),
        ("0.2.2+local", "refs/tags/v0.2.2+local", "none"),
        ("0.2.2rc0", "refs/tags/v0.2.2rc0", "none"),
    ],
)
def test_invalid_release_requests_are_rejected(version, ref, target):
    with pytest.raises(ValueError):
        release_metadata(version, ref, "workflow_dispatch", target)


def test_prepare_updates_version_and_lock_without_committing(tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nname = "release-test"\nversion = "0.2.1"\n')
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "add", "pyproject.toml"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    original = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path)
    prepare_version(tmp_path, "0.2.2rc1")
    assert 'version = "0.2.2rc1"' in project.read_text()
    assert 'version = "0.2.2rc1"' in (tmp_path / "uv.lock").read_text()
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path) == original
    assert subprocess.check_output(["git", "tag", "--list"], cwd=tmp_path) == b""


@pytest.mark.parametrize("version", ["0.2.1", "0.2.0", "0.2.1rc1"])
def test_prepare_rejects_non_increasing_versions(tmp_path, version):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "release-test"\nversion = "0.2.1"\n'
    )
    with pytest.raises(ValueError, match="newer"):
        prepare_version(tmp_path, version)


def test_download_requires_published_bytes_to_match_build(tmp_path, monkeypatch):
    package = tmp_path / "dist"
    package.mkdir()
    payload = b"tested wheel bytes"
    (package / "tinta_mcp-0.2.2-py3-none-any.whl").write_bytes(payload)
    metadata = {
        "urls": [
            {
                "filename": "tinta_mcp-0.2.2-py3-none-any.whl",
                "url": "https://test-files.pythonhosted.org/example.whl",
                "digests": {"sha256": hashlib.sha256(payload).hexdigest()},
            }
        ]
    }

    def response(url, **kwargs):
        return io.BytesIO(json.dumps(metadata).encode() if url.endswith("/json") else payload)

    monkeypatch.setattr("scripts.release.urllib.request.urlopen", response)
    destination = tmp_path / "downloaded"
    download_verified_files(package, "0.2.2", destination)
    assert (destination / "tinta_mcp-0.2.2-py3-none-any.whl").read_bytes() == payload

    (package / "tinta_mcp-0.2.2-py3-none-any.whl").write_bytes(b"different build")
    with pytest.raises(ValueError, match="hash"):
        download_verified_files(package, "0.2.2", tmp_path / "mismatch")


def test_download_rejects_bytes_that_disagree_with_index(tmp_path, monkeypatch):
    package = tmp_path / "dist"
    package.mkdir()
    payload = b"tested wheel bytes"
    (package / "tinta_mcp-0.2.2-py3-none-any.whl").write_bytes(payload)
    metadata = {
        "urls": [
            {
                "filename": "tinta_mcp-0.2.2-py3-none-any.whl",
                "url": "https://test-files.pythonhosted.org/example.whl",
                "digests": {"sha256": hashlib.sha256(payload).hexdigest()},
            }
        ]
    }

    def response(url, **kwargs):
        return io.BytesIO(json.dumps(metadata).encode() if url.endswith("/json") else b"corrupt")

    monkeypatch.setattr("scripts.release.urllib.request.urlopen", response)
    destination = tmp_path / "downloaded"
    with pytest.raises(ValueError, match="Downloaded hash"):
        download_verified_files(package, "0.2.2", destination)
    assert list(destination.iterdir()) == []
