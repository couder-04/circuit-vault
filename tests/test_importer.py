"""Tests for importer.py."""

from __future__ import annotations

import shutil

from circuit_vault.importer import merge, scan_incoming
from circuit_vault.parser import extract_circuit_raw_bytes, list_circuits, load
from circuit_vault.validator import HealthState, validate_project
from tests.conftest import CLASSIC, FIXTURES, MAIN


SHARED = FIXTURES / "shared_incoming.circ"


def test_scan_incoming_outcomes():
    scanned = scan_incoming(SHARED)
    by = {c.name: c for c in scanned}
    assert "HealthyOR" in by
    assert by["HealthyOR"].xml_bytes is not None
    assert by["HealthyOR"].unfixable_reason is None
    assert "Parent" in by and by["Parent"].xml_bytes is not None
    assert "ChildGate" in by
    assert "BrokenGhost" in by
    assert by["BrokenGhost"].unfixable_reason is not None
    assert by["BrokenGhost"].xml_bytes is None
    # Truncated should be dropped by repair and absent from scan
    assert "TruncatedMidSave" not in by


def test_merge_preserves_sibling_bytes(tmp_path):
    target = tmp_path / "target.circ"
    shutil.copy2(MAIN, target)
    before = {
        n: extract_circuit_raw_bytes(load(target), n)
        for n in list_circuits(load(target))
    }
    result = merge(["HealthyOR"], target, "replace", incoming_path=SHARED)
    assert result.ok, result.message
    after_proj = load(target)
    assert "HealthyOR" in list_circuits(after_proj)
    for n, raw in before.items():
        assert extract_circuit_raw_bytes(after_proj, n) == raw
    assert validate_project(after_proj).ok


def test_merge_preserves_source_wires_and_components(tmp_path):
    """Import must copy circuits exactly — no wire-snap rewrite of good XML."""
    from collections import Counter

    from lxml import etree

    from circuit_vault.importer import scan_incoming

    target = tmp_path / "target.circ"
    shutil.copy2(MAIN, target)

    scanned = {c.name: c for c in scan_incoming(SHARED)}
    src_xml = scanned["HealthyOR"].xml_bytes
    assert src_xml
    src_el = etree.fromstring(src_xml)
    src_wires = [(w.get("from"), w.get("to")) for w in src_el.iter("wire")]
    src_types = Counter(c.get("name") for c in src_el.iter("comp"))

    result = merge(["HealthyOR"], target, "replace", incoming_path=SHARED)
    assert result.ok, result.message

    dst_xml = extract_circuit_raw_bytes(load(target), "HealthyOR")
    assert dst_xml == src_xml  # byte-identical circuit span
    dst_el = etree.fromstring(dst_xml)
    assert [(w.get("from"), w.get("to")) for w in dst_el.iter("wire")] == src_wires
    assert Counter(c.get("name") for c in dst_el.iter("comp")) == src_types


def test_merge_strips_orphan_subcircuit_lib_and_pulls_deps(tmp_path):
    """Evolution lib=\"13\" same-project refs must become no-lib and deps merge."""
    from lxml import etree

    # Minimal Evolution-like project: parent uses lib=13 for ChildGate
    src = tmp_path / "1.circ"
    src.write_text(
        """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<project source="3.8.0" version="1.0">
  <lib desc="#Wiring" name="0"/>
  <lib desc="#Gates" name="1"/>
  <main name="Parent"/>
  <circuit name="ChildGate">
    <comp lib="0" loc="(80,100)" name="Pin"/>
    <comp lib="0" loc="(200,100)" name="Pin">
      <a name="facing" val="west"/>
      <a name="output" val="true"/>
    </comp>
    <wire from="(80,100)" to="(200,100)"/>
  </circuit>
  <circuit name="Parent">
    <comp lib="0" loc="(80,100)" name="Pin"/>
    <comp lib="13" loc="(200,100)" name="ChildGate"/>
    <comp lib="0" loc="(320,100)" name="Pin">
      <a name="facing" val="west"/>
      <a name="output" val="true"/>
    </comp>
    <wire from="(80,100)" to="(200,100)"/>
    <wire from="(200,100)" to="(320,100)"/>
  </circuit>
</project>
""",
        encoding="utf-8",
    )
    target = tmp_path / "2.circ"
    shutil.copy2(MAIN, target)

    result = merge(["Parent"], target, "replace", incoming_path=src)
    assert result.ok, result.message
    assert "ChildGate" in result.pulled_deps or "ChildGate" in result.merged

    parent = etree.fromstring(extract_circuit_raw_bytes(load(target), "Parent"))
    child_comps = [c for c in parent.iter("comp") if c.get("name") == "ChildGate"]
    assert child_comps
    assert child_comps[0].get("lib") in (None, "")


def test_merge_keep_both_and_deps(tmp_path):
    target = tmp_path / "target.circ"
    shutil.copy2(MAIN, target)
    # First import Parent (pulls ChildGate)
    result = merge(["Parent"], target, "replace", incoming_path=SHARED)
    assert result.ok, result.message
    assert "Parent" in result.merged or any("Parent" in m for m in result.merged)
    assert "ChildGate" in result.pulled_deps or "ChildGate" in result.merged
    # Clash keep_both
    result2 = merge(["HealthyOR"], target, "keep_both", incoming_path=SHARED)
    assert result2.ok, result2.message
    # Import again with keep_both after replace already added HealthyOR
    result3 = merge(["HealthyOR"], target, "keep_both", incoming_path=SHARED)
    assert result3.ok, result3.message
    assert result3.renamed.get("HealthyOR", "").endswith("(imported)") or any(
        "imported" in m for m in result3.merged
    )



def test_merge_skip_clash(tmp_path):
    target = tmp_path / "target.circ"
    shutil.copy2(CLASSIC, target)
    # Force a name clash by importing AND2-shaped rename — use HealthyOR then skip
    merge(["HealthyOR"], target, "replace", incoming_path=SHARED)
    before = extract_circuit_raw_bytes(load(target), "HealthyOR")
    result = merge(["HealthyOR"], target, "skip", incoming_path=SHARED)
    assert result.ok
    assert "HealthyOR" in result.skipped
    assert extract_circuit_raw_bytes(load(target), "HealthyOR") == before


def test_classic_target_merge(tmp_path):
    target = tmp_path / "classic.circ"
    shutil.copy2(CLASSIC, target)
    result = merge(["HealthyOR"], target, "replace", incoming_path=SHARED)
    assert result.ok, result.message
    assert validate_project(load(target)).ok
    # SHARED is Evolution; classic target should surface a format warning
    assert result.format_warning
    assert "Format mismatch" in result.format_warning
