"""Tests for dependencies.py."""

from __future__ import annotations

from circuit_vault.dependencies import build_graph, detect_cycles, resolve_restore_set
from circuit_vault.parser import load
from tests.conftest import MAIN, MAIN_CORRUPTED


def test_full_adder_32_depends_on_full_adder():
    project = load(MAIN)
    graph = build_graph(project)
    assert graph["Full Adder 32-bit"] == {"Full Adder"}
    assert "Half Adder" in graph["Full Adder"]
    assert "Full Adder 32-bit" in graph["ALU"]


def test_restore_set_ordered_deps_first():
    project = load(MAIN)
    ordered = resolve_restore_set(project, "ALU")
    # ALU itself always included; healthy deps not pulled in.
    assert ordered == ["ALU"]


def test_restore_set_pulls_broken_dependency():
    """If a dep of the target is broken, include it before the target."""
    project = load(MAIN_CORRUPTED)
    # Corrupt Full Adder as well by validating — only FA32 is broken in fixture.
    # ALU depends on FA32 (broken) and Multiplier (ok) → restore FA32 then ALU.
    ordered = resolve_restore_set(project, "ALU")
    assert "Full Adder 32-bit" in ordered
    assert "ALU" in ordered
    assert ordered.index("Full Adder 32-bit") < ordered.index("ALU")


def test_cycles_detected_gracefully():
    # Synthetic graph with a cycle
    graph = {"A": {"B"}, "B": {"A"}, "C": set()}
    cycles = detect_cycles(graph)
    assert cycles  # at least one cycle reported
