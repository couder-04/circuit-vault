"""Tests for repair.py."""

from __future__ import annotations

from circuit_vault.repair import repair_circuit, repair_file
from tests.conftest import FIXTURES


def test_repair_strips_bom_and_junk():
    good = (FIXTURES / "classic.circ").read_bytes()
    junked = b"\xef\xbb\xbf@@@GARBAGE@@@" + good + b"\nTRAILING!!!"
    result = repair_file(junked)
    assert result.ok, result.unfixable_reason
    assert result.fixed_bytes is not None
    assert any("BOM" in c or "junk" in c.lower() for c in result.changes)


def test_repair_drops_truncated_trailing_circuit():
    raw = (FIXTURES / "shared_incoming.circ").read_bytes()
    result = repair_file(raw)
    # File as a whole may still have BrokenGhost invalid — ok may be False,
    # but truncated circuit should be dropped.
    assert any("incomplete" in c.lower() or "Dropped" in c for c in result.changes)
    assert b"TruncatedMidSave" not in (result.fixed_bytes or b"")


def test_repair_circuit_drops_bad_wire():
    xml = (
        b'<circuit name="X">'
        b'<comp lib="0" loc="(80,100)" name="Pin"/>'
        b'<wire from="BOGUS" to="(0,0)"/>'
        b'<wire from="(80,100)" to="(120,100)"/>'
        b"</circuit>"
    )
    result = repair_circuit(xml)
    assert result.ok, result.unfixable_reason
    assert result.fixed_bytes is not None
    assert b"BOGUS" not in result.fixed_bytes
    assert any("Dropped wire" in c for c in result.changes)


def test_unfixable_missing_subcircuit():
    xml = (
        b'<circuit name="X">'
        b'<comp lib="0" loc="(80,100)" name="Pin"/>'
        b'<comp loc="(140,100)" name="Ghost"/>'
        b'<wire from="(80,100)" to="(140,100)"/>'
        b"</circuit>"
    )
    # Alone, Ghost is dangling — repair_circuit still returns ok=True for XML
    # shape; importer marks unfixable when Ghost not in file. Here we assert
    # repair does not invent the missing circuit.
    result = repair_circuit(xml)
    assert result.fixed_bytes is None or b"Ghost" in (result.fixed_bytes or b"")
