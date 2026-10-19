import io
import tarfile
import zipfile

import pytest

from scripts.verify_release_artifacts import main


@pytest.fixture
def release_artifacts(tmp_path):
    extension = [
        "tinta_mcp.py",
        "tinta_mcp.inx",
        "tinta_mcp_ops/__init__.py",
        "tinta_mcp_ops/common.py",
        "tinta_mcp_ops/document_operations.py",
        "tinta_mcp_ops/element_mapping.py",
    ]
    with zipfile.ZipFile(tmp_path / "tinta-designer-skill.zip", "w") as archive:
        archive.writestr("tinta-designer/SKILL.md", "Designer skill")
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "tinta_mcp-0.2.2rc2-py3-none-any.whl"
    wheel_files = [f"tintamcp/assets/extension/{name}" for name in extension]
    wheel_files += ["tintamcp/server.py", "tintamcp/assets/skills/tinta-designer/SKILL.md"]
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in wheel_files:
            archive.writestr(name, "fixture")
    source_files = [f"extension/{name}" for name in extension]
    source_files.append(".agents/skills/tinta-designer/SKILL.md")
    with tarfile.open(dist / "tinta_mcp-0.2.2rc2.tar.gz", "w:gz") as archive:
        for name in source_files:
            item = tarfile.TarInfo(f"tinta_mcp-0.2.2rc2/{name}")
            item.size = len(b"fixture")
            archive.addfile(item, io.BytesIO(b"fixture"))
    return tmp_path


def test_release_verification_accepts_only_skill_wheel_and_sdist(release_artifacts):
    main(release_artifacts)


@pytest.mark.parametrize(
    "missing",
    [
        "tintamcp/assets/extension/tinta_mcp_ops/common.py",
        "tintamcp/assets/skills/tinta-designer/SKILL.md",
    ],
)
def test_release_verification_still_requires_bundled_assets(release_artifacts, missing):
    wheel = next((release_artifacts / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        contents = {name: archive.read(name) for name in archive.namelist() if name != missing}
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    with pytest.raises(SystemExit) as error:
        main(release_artifacts)
    assert missing in str(error.value)
