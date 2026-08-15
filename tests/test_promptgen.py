"""Tests for promptgen.py."""

from __future__ import annotations

from circuit_vault.parser import extract_circuit_raw_bytes, load, parse_circuit_bytes
from circuit_vault.promptgen import (
    components_catalog,
    generate_prompt,
    is_valid_logisim_circuit_name,
    normalize_circuit_geometry,
    prepare_generated_circuit,
    sanitize_base_name,
    unique_circuit_name,
    validate_generated,
)
from lxml import etree
from tests.conftest import MAIN


def test_catalog_has_groups():
    cat = components_catalog()
    assert "BASIC" in cat
    assert "AND Gate" in cat["BASIC"]
    assert "Pin" in cat["WIRING"]


def test_prompt_contains_skeleton_and_components():
    prompt = generate_prompt(
        "build a half adder",
        ["AND Gate", "XOR Gate", "my_custom_block"],
        "A, B",
        "Sum, Carry",
        "evolution",
    )
    assert "<circuit" in prompt
    assert "AND Gate" in prompt
    assert "XOR Gate" in prompt
    assert "my_custom_block" in prompt
    assert "ONLY" in prompt or "only" in prompt.lower()
    assert 'name="Pin"' in prompt or "Half Adder" in prompt
    assert "axis-aligned" in prompt.lower() or "NEVER draw a diagonal" in prompt
    assert "MUST start with a letter" in prompt


def test_classic_prompt_path():
    prompt = generate_prompt(
        "two-input AND",
        ["AND Gate"],
        "A, B",
        "Y",
        "classic",
    )
    assert "classic" in prompt.lower()
    assert "AND2" in prompt or 'name="Pin"' in prompt
    assert "Do NOT emit <appear>" in prompt or "appear" in prompt.lower()


def test_sanitize_base_name_logisim_rules():
    assert sanitize_base_name("4-bit adder").startswith("bit") or sanitize_base_name(
        "4-bit adder"
    )[0].isalpha()
    assert sanitize_base_name("4-bit adder")[0].isalpha()
    assert sanitize_base_name("!!NAND") == "NAND"
    assert sanitize_base_name("").startswith("Built")
    assert sanitize_base_name("Half Adder") == "Half Adder"
    assert "(" not in sanitize_base_name("Foo (built)")


def test_unique_circuit_name_decimal_suffix():
    name = unique_circuit_name("4bit XOR")
    assert name[0].isalpha()
    assert is_valid_logisim_circuit_name(name)
    assert "_CV" not in name
    assert unique_circuit_name("Adder", set()) == "Adder"
    assert unique_circuit_name("Adder", {"Adder"}) == "Adder1"
    assert unique_circuit_name("Adder", {"Adder", "Adder1"}) == "Adder2"


def test_normalize_splits_diagonal_and_snaps():
    xml = (
        b'<circuit name="T">'
        b'<comp lib="0" loc="(81,103)" name="Pin"/>'
        b'<wire from="(81,103)" to="(151,143)"/>'
        b"</circuit>"
    )
    el = parse_circuit_bytes(xml)
    notes = normalize_circuit_geometry(el)
    assert any("snap" in n or "split" in n for n in notes)
    assert el.find("comp").get("loc") == "(80,100)"
    wires = [w for w in el.iter() if etree.QName(w).localname == "wire"]
    assert len(wires) == 2
    for w in wires:
        a = w.get("from")
        b = w.get("to")
        # axis-aligned: share x or y
        ax, ay = map(int, a.strip("()").split(","))
        bx, by = map(int, b.strip("()").split(","))
        assert ax == bx or ay == by


def test_prepare_renames_with_decimal_when_needed():
    xml = (
        b'<circuit name="9bad!!name">'
        b'<a name="circuit" val="9bad!!name"/>'
        b'<comp lib="0" loc="(80,100)" name="Pin">'
        b'<a name="facing" val="east"/>'
        b"</comp>"
        b'<wire from="(80,100)" to="(120,100)"/>'
        b"</circuit>"
    )
    out, name, notes = prepare_generated_circuit(xml, existing_names=set())
    assert name[0].isalpha()
    assert "_CV" not in name
    assert is_valid_logisim_circuit_name(name)
    assert name.encode() in out
    assert any("named" in n for n in notes)

    _, preferred, _ = prepare_generated_circuit(
        xml, existing_names={"MyGate"}, preferred_name="MyGate"
    )
    assert preferred == "MyGate1"


def test_snap_floating_wire_onto_gate_ports():
    # AND at (200,120): inputs should be (150,100) and (150,140)
    # Wire deliberately ends short at (160,100) — must snap/extend to port
    xml = (
        b'<circuit name="T">'
        b'<comp lib="0" loc="(80,100)" name="Pin">'
        b'<a name="facing" val="east"/>'
        b"</comp>"
        b'<comp lib="1" loc="(200,120)" name="AND Gate">'
        b'<a name="facing" val="east"/>'
        b"</comp>"
        b'<comp lib="0" loc="(280,120)" name="Pin">'
        b'<a name="facing" val="west"/>'
        b'<a name="output" val="true"/>'
        b"</comp>"
        b'<wire from="(80,100)" to="(160,100)"/>'
        b'<wire from="(80,140)" to="(160,140)"/>'
        b'<wire from="(200,120)" to="(280,120)"/>'
        b"</circuit>"
    )
    el = parse_circuit_bytes(xml)
    notes = normalize_circuit_geometry(el)
    assert any("port" in n.lower() or "floating" in n.lower() for n in notes)
    ends = set()
    for w in el.iter():
        if etree.QName(w).localname != "wire":
            continue
        ends.add(w.get("from"))
        ends.add(w.get("to"))
    assert "(150,100)" in ends
    assert "(150,140)" in ends
    assert "(200,120)" in ends


def test_and_gate_port_offsets():
    from circuit_vault.ports import ports_for_component

    xml = (
        b'<comp lib="1" loc="(200,120)" name="AND Gate">'
        b'<a name="facing" val="east"/>'
        b"</comp>"
    )
    comp = etree.fromstring(xml)
    ports = set(ports_for_component(comp))
    assert (200, 120) in ports  # output
    assert (150, 100) in ports  # input 0
    assert (150, 140) in ports  # input 1


def test_comments_in_xml_do_not_crash_prepare():
    xml = (
        b'<circuit name="Demo">'
        b"<!-- section header -->"
        b'<comp lib="0" loc="(80,100)" name="Pin">'
        b'<a name="facing" val="east"/>'
        b"</comp>"
        b'<wire from="(80,100)" to="(120,100)"/>'
        b"</circuit>"
    )
    out, name, notes = prepare_generated_circuit(xml)
    assert b"<!--" not in out
    assert name[0].isalpha()
    assert is_valid_logisim_circuit_name(name)


def test_validate_generated_accepts_good_and_rejects_junk():
    project = load(MAIN)
    good = extract_circuit_raw_bytes(project, "Half Adder")
    ok, preview = validate_generated(
        good,
        target_format="evolution",
        existing_names={"Half Adder"},
        preferred_name="Half Adder",
        prepare=True,
    )
    assert ok
    assert preview["name"] == "Half Adder1"
    assert preview["component_count"] > 0
    assert preview.get("prepared_xml")

    bad_ok, bad_preview = validate_generated(b"<not-a-circuit/>")
    assert not bad_ok
    assert bad_preview.get("error")
