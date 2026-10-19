"""Regression tests for the correlated server-extension protocol."""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from tintamcp import server
from tintamcp.gdbus_util import ipc_process_lock


def _run_extension_simulation(
    ipc_dir: Path, *, response_override=None, omit_response=False, captured=None
):
    if captured is None:
        captured = []

    def run(command, **kwargs):
        request = json.loads((ipc_dir / "mcp_params.json").read_text(encoding="utf-8"))
        captured.append(request)
        if not omit_response:
            response = {
                "protocol_version": request["protocol_version"],
                "session_id": request["session_id"],
                "request_id": request["request_id"],
                "operation": request["request"]["operation"],
                "status": "success",
                "data": {"message": "done"},
            }
            if response_override:
                response.update(response_override)
            response_path = ipc_dir / f"mcp_response_{request['request_id']}.json"
            temporary_path = response_path.with_suffix(".tmp")
            temporary_path.write_text(json.dumps(response), encoding="utf-8")
            temporary_path.replace(response_path)
        return SimpleNamespace(returncode=0, stdout="()", stderr="")

    return run


def _execute(tmp_path, monkeypatch, run):
    monkeypatch.setattr(server, "get_tinta_ipc_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "find_gdbus", lambda: "gdbus")
    monkeypatch.setattr(
        server, "wait_for_response_file", lambda path, timeout=5.0: Path(path).exists()
    )
    monkeypatch.setattr(server.subprocess, "run", run)
    connection = server.InkscapeConnection()
    return connection.execute_operation({"operation": "get-info", "attributes": {}})


def test_request_and_response_are_versioned_and_correlated(tmp_path, monkeypatch):
    captured = []
    simulate = _run_extension_simulation(tmp_path, captured=captured)
    result = _execute(tmp_path, monkeypatch, simulate)

    assert result["status"] == "success"
    assert result["protocol_version"] == 1
    assert result["session_id"]
    assert result["request_id"]
    request = captured[0]
    assert "operation" not in request
    assert request["request"]["operation"] == "get-info"
    assert not (tmp_path / "mcp_params.json").exists()
    assert not list(tmp_path.glob("mcp_response_*.json"))
    assert not list(tmp_path.glob(".pending*"))


def test_mismatched_response_is_rejected(tmp_path, monkeypatch):
    result = _execute(
        tmp_path,
        monkeypatch,
        _run_extension_simulation(tmp_path, response_override={"request_id": uuid.uuid4().hex}),
    )

    assert result["status"] == "error"
    assert "correlation" in result["data"]["error"].lower()


def test_response_for_another_operation_is_rejected(tmp_path, monkeypatch):
    result = _execute(
        tmp_path,
        monkeypatch,
        _run_extension_simulation(tmp_path, response_override={"operation": "delete-elements"}),
    )

    assert result["status"] == "error"
    assert "operation" in result["data"]["error"].lower()


def test_missing_response_is_unknown_and_blocks_reuse(tmp_path, monkeypatch):
    connection = server.InkscapeConnection()
    monkeypatch.setattr(server, "get_tinta_ipc_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "find_gdbus", lambda: "gdbus")
    monkeypatch.setattr(
        server.subprocess,
        "run",
        _run_extension_simulation(tmp_path, omit_response=True),
    )
    monkeypatch.setattr(server, "wait_for_response_file", lambda path, timeout=5.0: False)

    first = connection.execute_operation({"operation": "modify-elements", "attributes": {}})
    second = connection.execute_operation({"operation": "get-info", "attributes": {}})

    assert first["status"] == "error"
    assert "unknown" in first["data"]["error"].lower()
    assert second["status"] == "error"
    assert "pending" in second["data"]["error"].lower()
    assert (tmp_path / "mcp_params.json").exists()
    assert (tmp_path / "pending_request.json").exists()


def test_late_correlated_response_reconciles_before_the_next_request(tmp_path, monkeypatch):
    connection = server.InkscapeConnection()
    monkeypatch.setattr(server, "get_tinta_ipc_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "find_gdbus", lambda: "gdbus")
    monkeypatch.setattr(
        server.subprocess, "run", _run_extension_simulation(tmp_path, omit_response=True)
    )
    monkeypatch.setattr(
        server, "wait_for_response_file", lambda path, timeout=5.0: Path(path).exists()
    )

    first = connection.execute_operation({"operation": "modify-elements", "attributes": {}})
    pending = json.loads((tmp_path / "pending_request.json").read_text(encoding="utf-8"))
    late_response = {
        **pending,
        "status": "success",
        "data": {"message": "finished after the caller timed out"},
    }
    late_path = tmp_path / f"mcp_response_{pending['request_id']}.json"
    late_path.write_text(json.dumps(late_response), encoding="utf-8")
    monkeypatch.setattr(server.subprocess, "run", _run_extension_simulation(tmp_path))

    second = connection.execute_operation({"operation": "get-info", "attributes": {}})

    assert first["status"] == "error"
    assert second["status"] == "success"
    assert second["request_id"] != pending["request_id"]
    assert not late_path.exists()
    assert not (tmp_path / "pending_request.json").exists()


def test_preexisting_request_is_never_overwritten(tmp_path, monkeypatch):
    stale_request = tmp_path / "mcp_params.json"
    stale_request.write_text('{"request_id":"old"}', encoding="utf-8")
    result = _execute(tmp_path, monkeypatch, _run_extension_simulation(tmp_path))

    assert result["status"] == "error"
    assert "stale request" in result["data"]["error"].lower()
    assert json.loads(stale_request.read_text(encoding="utf-8"))["request_id"] == "old"


def test_dbus_timeout_preserves_uncertain_request(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=30)

    result = _execute(tmp_path, monkeypatch, timeout)

    assert result["status"] == "error"
    assert "unknown" in result["data"]["error"].lower()
    assert (tmp_path / "pending_request.json").exists()


def test_second_process_cannot_claim_the_shared_request_slot(tmp_path):
    script = (
        "from pathlib import Path\n"
        "from tintamcp.gdbus_util import ipc_process_lock\n"
        "with ipc_process_lock(Path(__import__('sys').argv[1])): pass\n"
    )
    with ipc_process_lock(tmp_path):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True
        )
    assert result.returncode != 0
    assert "Another Tinta server" in result.stderr
