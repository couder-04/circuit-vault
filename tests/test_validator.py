"""Tests for validator.py."""

from __future__ import annotations

from circuit_vault.parser import load
from circuit_vault.validator import (
    HealthState,
    circuit_health,
    validate_circuit,
    validate_project,
)
from tests.conftest import CLASSIC, CLASSIC_CORRUPTED, MAIN, MAIN_CORRUPTED


def test_good_project_validates():
    project = load(MAIN)
    result = validate_project(project)
    assert result.ok
    assert result.errors == []


def test_corrupted_full_adder_32_bit_is_broken():
    project = load(MAIN_CORRUPTED)
    fa32 = validate_circuit(project, "Full Adder 32-bit")
    assert not fa32.ok
    assert any("unresolved" in e.lower() or "malformed" in e.lower() for e in fa32.errors)

    # Other circuits remain structurally fine.
    for name in ("Half Adder", "Full Adder", "Multiplier 4-bit"):
        assert validate_circuit(project, name).ok


def test_health_states_without_finals():
    project = load(MAIN_CORRUPTED)
    assert circuit_health(project, "Half Adder", None) == HealthState.NO_FINAL
    assert circuit_health(project, "Full Adder 32-bit", None) == HealthState.BROKEN


def test_classic_valid_and_corrupted():
    good = load(CLASSIC)
    assert validate_project(good).ok
    bad = load(CLASSIC_CORRUPTED)
    demo = validate_circuit(bad, "Demo")
    assert not demo.ok
    assert validate_circuit(bad, "AND2").ok
