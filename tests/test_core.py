"""Integration tests for core.py."""

from __future__ import annotations

import shutil

from circuit_vault.core import CircuitVaultApp
from circuit_vault.parser import extract_circuit_raw_bytes, list_circuits, load
from circuit_vault.validator import HealthState, validate_project
from tests.conftest import CLASSIC, CLASSIC_CORRUPTED, MAIN, MAIN_CORRUPTED


def _setup_project(tmp_path, fixture):
    circ = tmp_path / "main.circ"
    shutil.copy2(fixture, circ)
    app = CircuitVaultApp()
    result = app.open_project(circ)
    assert result.ok, result.message
    return app, circ


def test_core_restore_corrupted_evolution(tmp_path):
    good_dir = tmp_path / "good"
    good_dir.mkdir()
    good_circ = good_dir / "main.circ"
    shutil.copy2(MAIN, good_circ)

    good_app = CircuitVaultApp()
    assert good_app.open_project(good_circ).ok
    for name in list_circuits(good_app.project):
        assert good_app.mark_final(name).ok

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad_circ = bad_dir / "main.circ"
    shutil.copy2(MAIN_CORRUPTED, bad_circ)
    shutil.copytree(good_dir / "circuit-vault", bad_dir / "circuit-vault")

    app = CircuitVaultApp()
    assert app.open_project(bad_circ).ok

    statuses = {s.name: s.health for s in app.status()}
    assert statuses["Full Adder 32-bit"] == HealthState.BROKEN
    assert statuses["Half Adder"] in (HealthState.HEALTHY, HealthState.CHANGED)

    before = {
        n: extract_circuit_raw_bytes(app.project, n)
        for n in list_circuits(app.project)
        if n != "Full Adder 32-bit"
    }

    result = app.restore("Full Adder 32-bit")
    assert result.ok, result.message
    assert "Full Adder 32-bit" in result.restored
    assert result.backup_path is not None
    assert result.backup_path.exists()

    assert validate_project(app.project).ok
    after = {
        n: extract_circuit_raw_bytes(app.project, n)
        for n in list_circuits(app.project)
        if n != "Full Adder 32-bit"
    }
    assert after == before

    undo = app.undo_last_restore()
    assert undo.ok, undo.message
    assert not validate_project(app.project).ok


def test_core_dependency_aware_restore(tmp_path):
    good_dir = tmp_path / "good"
    good_dir.mkdir()
    good_circ = good_dir / "main.circ"
    shutil.copy2(MAIN, good_circ)
    good_app = CircuitVaultApp()
    assert good_app.open_project(good_circ).ok
    for name in (
        "Full Adder 32-bit",
        "ALU",
        "Multiplier 4-bit",
        "Full Adder",
        "Half Adder",
    ):
        assert good_app.mark_final(name).ok

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad_circ = bad_dir / "main.circ"
    shutil.copy2(MAIN_CORRUPTED, bad_circ)
    shutil.copytree(good_dir / "circuit-vault", bad_dir / "circuit-vault")

    app = CircuitVaultApp()
    assert app.open_project(bad_circ).ok
    result = app.restore("ALU")
    assert result.ok, result.message
    assert "Full Adder 32-bit" in result.restored
    assert "ALU" in result.restored
    assert result.restored.index("Full Adder 32-bit") < result.restored.index("ALU")
    assert validate_project(app.project).ok


def test_mark_final_refuses_broken(tmp_path):
    app, _ = _setup_project(tmp_path, MAIN_CORRUPTED)
    result = app.mark_final("Full Adder 32-bit")
    assert not result.ok
    assert "problem" in result.message.lower() or "cannot" in result.message.lower()


def test_classic_parse_validate_splice_restore(tmp_path):
    good_dir = tmp_path / "good"
    good_dir.mkdir()
    good_circ = good_dir / "classic.circ"
    shutil.copy2(CLASSIC, good_circ)
    good_app = CircuitVaultApp()
    assert good_app.open_project(good_circ).ok
    assert good_app.mark_final("Demo").ok
    assert good_app.mark_final("AND2").ok

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad_circ = bad_dir / "classic.circ"
    shutil.copy2(CLASSIC_CORRUPTED, bad_circ)
    shutil.copytree(good_dir / "circuit-vault", bad_dir / "circuit-vault")

    app = CircuitVaultApp()
    assert app.open_project(bad_circ).ok
    assert any(s.health == HealthState.BROKEN for s in app.status())
    result = app.restore("Demo")
    assert result.ok, result.message
    assert validate_project(app.project).ok
    and2_before = extract_circuit_raw_bytes(load(CLASSIC_CORRUPTED), "AND2")
    assert extract_circuit_raw_bytes(app.project, "AND2") == and2_before
