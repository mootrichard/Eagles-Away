"""Guard against setup overwriting teammate credentials or breaking worker routing."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def setup_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "configure_local", Path(__file__).resolve().parents[1] / "scripts/configure-local.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def local_root(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    (tmp_path / ".env.example").write_text((root / ".env.example").read_text())
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/.env.example").write_text(
        "NEXT_PUBLIC_API_ORIGIN=http://localhost:8000\n"
    )
    return tmp_path


def test_new_checkout_is_ready_and_idempotent(setup_module, local_root: Path) -> None:  # type: ignore[no-untyped-def]
    setup_module.configure_local(local_root)
    before = (local_root / ".env").read_text()
    values = dict(
        line.split("=", 1)
        for line in before.splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert "CONTACT_ENCRYPTION_KEY" not in values
    assert "SESSION_SECRET" not in values
    assert values["TEMPORAL_TASK_QUEUE"] == "cpfc-trip-v3"
    assert values["PLANNER_MODE"] == "recorded"
    assert "EMAIL_MODE" not in values
    assert (local_root / ".env").stat().st_mode & 0o777 == 0o600
    setup_module.configure_local(local_root)
    assert (local_root / ".env").read_text() == before


def test_preserves_custom_secrets_cloud_and_frontend(setup_module, local_root: Path) -> None:  # type: ignore[no-untyped-def]
    original = "SEARCHAPI_API_KEY=private-key\nTEMPORAL_TASK_QUEUE=team-queue\nGOOGLE_CLOUD_LOCATION=custom-region\nPLANNER_MODE=live\n"
    (local_root / ".env").write_text(original)
    (local_root / "frontend/.env.local").write_text("NEXT_PUBLIC_API_ORIGIN=https://team.example\n")
    setup_module.configure_local(local_root)
    updated = (local_root / ".env").read_text()
    for line in original.splitlines():
        assert line in updated.splitlines()
    assert (
        local_root / "frontend/.env.local"
    ).read_text() == "NEXT_PUBLIC_API_ORIGIN=https://team.example\n"


def test_migrates_only_obsolete_defaults(setup_module, local_root: Path) -> None:  # type: ignore[no-untyped-def]
    (local_root / ".env").write_text(
        "TEMPORAL_TASK_QUEUE=cpfc-trip-v2\nPLANNER_MODE=mock\nEMAIL_MODE=console\n"
    )
    setup_module.configure_local(local_root)
    updated = (local_root / ".env").read_text()
    assert "TEMPORAL_TASK_QUEUE=cpfc-trip-v3\n" in updated
    assert "PLANNER_MODE=recorded\n" in updated
    assert "EMAIL_MODE=" not in updated
