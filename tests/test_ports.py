"""Tests for port geometry and wire snap (including Evolution named boxes)."""

from __future__ import annotations

from circuit_vault.parser import parse_circuit_bytes
from circuit_vault.ports import (
    evolution_named_box_ports,
    pin_specs_from_circuit,
    ports_for_component,
    repair_circuit_connections,
)
from lxml import etree


def test_evolution_named_box_ports_match_test_mult_layout():
    """bit_4_mult @ (580,300): A/B west at x=360, PROD at anchor."""
    pins = [
        {"output": False, "label": "B", "y": 190, "x": 380, "width": 4},
        {"output": False, "label": "A", "y": 200, "x": 80, "width": 4},
        {"output": True, "label": "PROD", "y": 740, "x": 160, "width": 8},
    ]
    ports = set(
        evolution_named_box_ports((580, 300), pins, circuit_name="bit_4_mult", fixed_size=True)
    )
    assert (580, 300) in ports  # PROD / anchor
    assert (360, 300) in ports  # first west (B, lower y)
    assert (360, 320) in ports  # second west (A)


def test_snap_near_miss_onto_subcircuit_ports():
    sub = parse_circuit_bytes(
        b'<circuit name="Box">'
        b'<a name="circuitnamedboxfixedsize" val="true"/>'
        b'<comp lib="0" loc="(80,100)" name="Pin">'
        b'<a name="label" val="IN"/>'
        b"</comp>"
        b'<comp lib="0" loc="(200,100)" name="Pin">'
        b'<a name="label" val="OUT"/>'
        b'<a name="type" val="output"/>'
        b"</comp>"
        b"</circuit>"
    )
    pins = {"Box": pin_specs_from_circuit(sub)}
    meta = {"Box": {"fixed_size": True}}

    # Parent wires miss west port by 10 units
    parent = parse_circuit_bytes(
        b'<circuit name="Top">'
        b'<comp lib="0" loc="(100,200)" name="Pin"/>'
        b'<comp loc="(400,200)" name="Box"/>'
        b'<comp lib="0" loc="(520,200)" name="Pin">'
        b'<a name="type" val="output"/>'
        b"</comp>"
        b'<wire from="(100,200)" to="(170,200)"/>'
        b'<wire from="(400,200)" to="(520,200)"/>'
        b"</circuit>"
    )
    expected_west = evolution_named_box_ports(
        (400, 200), pins["Box"], circuit_name="Box", fixed_size=True
    )
    west = [p for p in expected_west if p[0] < 400][0]

    # Deliberately short wire toward west port
    for w in parent.iter("wire"):
        if w.get("from") == "(100,200)":
            w.set("to", f"({west[0] - 10},{west[1]})")

    notes = repair_circuit_connections(
        parent, subcircuit_pins=pins, subcircuit_meta=meta, extend_dist=60
    )
    assert notes
    ends = set()
    for w in parent.iter("wire"):
        ends.add(w.get("from"))
        ends.add(w.get("to"))
    assert f"({west[0]},{west[1]})" in ends


def test_splitter_ports_include_fanout():
    comp = etree.fromstring(
        b'<comp lib="0" loc="(130,200)" name="Splitter">'
        b'<a name="fanout" val="4"/>'
        b'<a name="incoming" val="4"/>'
        b"</comp>"
    )
    ports = ports_for_component(comp)
    assert (130, 200) in ports
    assert len(ports) == 5  # combined + 4 fanouts
