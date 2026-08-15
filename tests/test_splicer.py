"""Tests for splicer.py — sibling byte stability and backups."""

from __future__ import annotations

import shutil

from circuit_vault.parser import extract_circuit_raw_bytes, list_circuits, load
from circuit_vault.splicer import backup, splice, splice_and_save
from circuit_vault.validator import validate_project
from tests.conftest import CLASSIC, MAIN


def test_splice_leaves_sibling_circuits_byte_identical(tmp_path):
    circ = tmp_path / "main.circ"
    shutil.copy2(MAIN, circ)
    project = load(circ)

    before = {
        name: extract_circuit_raw_bytes(project, name)
        for name in list_circuits(project)
    }

    # Replace Full Adder with its own bytes (identity splice) — siblings must
    # remain byte-identical even when we replace a different circuit with a
    # slightly different valid copy from the same file.
    new_fa = before["Half Adder"]  # wrong name — use Full Adder's own
    new_fa = before["Full Adder"]
    # Tweak by re-using exact same bytes (still a real splice path).
    updated = splice(project, "Full Adder", new_fa)

    for name in list_circuits(updated):
        if name == "Full Adder":
            continue
        assert extract_circuit_raw_bytes(updated, name) == before[name], name

    # Top-level non-circuit content before first circuit stays identical.
    raw_before = project.raw_bytes
    raw_after = updated.raw_bytes
    first_circuit = raw_before.find(b"<circuit")
    assert raw_after[:first_circuit] == raw_before[:first_circuit]


def test_splice_different_circuit_changes_only_target(tmp_path):
    circ = tmp_path / "main.circ"
    shutil.copy2(MAIN, circ)
    project = load(circ)
    before = {
        name: extract_circuit_raw_bytes(project, name)
        for name in list_circuits(project)
    }

    # Build a modified Full Adder: change a label value in the raw span.
    fa = before["Full Adder"]
    modified = fa.replace(b'val="Cin"', b'val="CarryIn"', 1)
    assert modified != fa

    updated = splice(project, "Full Adder", modified)
    assert extract_circuit_raw_bytes(updated, "Full Adder") == modified.strip()
    for name in before:
        if name == "Full Adder":
            continue
        assert extract_circuit_raw_bytes(updated, name) == before[name]


def test_backup_creates_timestamped_file(tmp_path):
    circ = tmp_path / "main.circ"
    shutil.copy2(MAIN, circ)
    bak = backup(circ)
    assert bak.exists()
    assert ".bak" in bak.name
    assert bak.read_bytes() == circ.read_bytes()


def test_classic_splice_byte_stable_siblings(tmp_path):
    circ = tmp_path / "classic.circ"
    shutil.copy2(CLASSIC, circ)
    project = load(circ)
    before_and2 = extract_circuit_raw_bytes(project, "AND2")
    demo = extract_circuit_raw_bytes(project, "Demo")
    updated = splice(project, "Demo", demo)
    assert extract_circuit_raw_bytes(updated, "AND2") == before_and2
    assert validate_project(updated).ok


def test_splice_and_save_rejects_invalid(tmp_path):
    circ = tmp_path / "main.circ"
    shutil.copy2(MAIN, circ)
    project = load(circ)
    original = circ.read_bytes()
    bad = (
        b'<circuit name="Full Adder">'
        b'<comp loc="(1,1)" name="NoSuchThing"/>'
        b'<wire from="BAD" to="(0,0)"/>'
        b"</circuit>"
    )
    try:
        splice_and_save(project, "Full Adder", bad)
        assert False, "expected SpliceError"
    except Exception as exc:
        from circuit_vault.splicer import SpliceError

        assert isinstance(exc, SpliceError)
    assert circ.read_bytes() == original
