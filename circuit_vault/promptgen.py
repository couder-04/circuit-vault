"""Build-from-description → Claude prompt generation."""

from __future__ import annotations

from pathlib import Path

from circuit_vault.parser import ParseError, parse_circuit_bytes
from circuit_vault.validator import validate_file
from lxml import etree

_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def components_catalog() -> dict[str, list[str]]:
    return {
        "BASIC": [
            "AND Gate",
            "OR Gate",
            "NOT Gate",
            "XOR Gate",
            "NAND Gate",
            "NOR Gate",
            "XNOR Gate",
            "Buffer",
        ],
        "PLEXERS": [
            "Multiplexer",
            "Demultiplexer",
            "Decoder",
            "Encoder",
            "Priority Encoder",
            "Bit Selector",
        ],
        "ARITHMETIC": [
            "Adder",
            "Subtractor",
            "Comparator",
            "Multiplier",
            "Divider",
            "Negator",
            "Shifter",
        ],
        "MEMORY": [
            "Register",
            "D Flip-Flop",
            "JK Flip-Flop",
            "T Flip-Flop",
            "SR Latch",
            "RAM",
            "ROM",
            "Counter",
        ],
        "WIRING": [
            "Splitter",
            "Pin",
            "Constant",
            "Tunnel",
            "Clock",
            "Probe",
            "Bit Extender",
        ],
        "INPUT/OUTPUT": [
            "Button",
            "LED",
            "Hex Digit Display",
            "7-Segment Display",
        ],
        "TRANSISTOR": [
            "Transistor",
            "Transmission Gate",
            "NMOS",
            "PMOS",
        ],
    }


def _skeleton(target_format: str) -> str:
    if target_format == "classic":
        path = _FIXTURES / "classic.circ"
        name = "AND2"
    else:
        path = _FIXTURES / "main.circ"
        name = "Half Adder"
    if not path.exists():
        # Fallback minimal authentic shape
        return (
            '<circuit name="Example">\n'
            '  <a name="circuit" val="Example"/>\n'
            '  <comp lib="0" loc="(80,100)" name="Pin">\n'
            '    <a name="facing" val="east"/>\n'
            "  </comp>\n"
            '  <wire from="(80,100)" to="(120,100)"/>\n'
            "</circuit>"
        )
    from circuit_vault.parser import extract_circuit_raw_bytes, load

    project = load(path)
    return extract_circuit_raw_bytes(project, name).decode("utf-8")


def generate_prompt(
    description: str,
    components: list[str],
    inputs: str,
    outputs: str,
    target_format: str = "evolution",
) -> str:
    fmt = "classic" if target_format == "classic" else "evolution"
    skeleton = _skeleton(fmt)
    comps = ", ".join(components) if components else "(none specified — choose appropriate gates)"
    return f"""You are generating a Logisim {"Evolution" if fmt == "evolution" else "classic"} circuit as XML.

## Exact <circuit> skeleton (match this shape and attribute style — do not invent new element types)
```
{skeleton}
```

## Coordinate and wiring rules
- Place components with loc="(x,y)" using integer coordinates on a 10-unit grid (e.g. 80, 90, 100).
- Connect with <wire from="(x,y)" to="(x,y)"/> — both ends must be well-formed "(x,y)" pairs.
- Wires must meet component connection points exactly; do not leave floating endpoints.
- Input/output pins use <comp lib="0" ... name="Pin">; set <a name="output" val="true"/> for outputs.
- Built-in library components include a numeric lib="N" attribute. Subcircuit instances omit lib.
- Preserve attribute order similar to the skeleton. Do not pretty-print beyond normal indentation.

## Components to use (standard ticks AND custom names — use these verbatim)
{comps}

## Specification
Description: {description}
Inputs: {inputs or "(unspecified)"}
Outputs: {outputs or "(unspecified)"}

## Output rules (strict)
Return ONLY a single <circuit name="...">...</circuit> element.
No prose, no markdown fences, no XML declaration, no <project> wrapper.
"""


def validate_generated(xml_bytes: bytes) -> tuple[bool, dict]:
    """
    Validate generated circuit XML.

    Returns (ok, preview) where preview has name, input_count, output_count,
    component_count, tip (optional).
    """
    text = xml_bytes.strip()
    # Strip markdown fences if user pasted them anyway
    if text.startswith(b"```"):
        lines = text.split(b"\n")
        if lines and lines[0].startswith(b"```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == b"```":
            lines = lines[:-1]
        text = b"\n".join(lines).strip()

    preview: dict = {
        "name": None,
        "input_count": 0,
        "output_count": 0,
        "component_count": 0,
        "tip": None,
        "error": None,
    }
    try:
        el = parse_circuit_bytes(text)
    except ParseError as exc:
        preview["error"] = str(exc)
        return False, preview

    if etree.QName(el).localname != "circuit":
        preview["error"] = "Root element must be <circuit>"
        return False, preview

    preview["name"] = el.get("name")
    comps = [c for c in el.iter() if etree.QName(c).localname == "comp"]
    wires = [w for w in el.iter() if etree.QName(w).localname == "wire"]
    preview["component_count"] = len(comps)
    inputs = 0
    outputs = 0
    for c in comps:
        if c.get("name") != "Pin":
            continue
        is_out = False
        for a in c:
            if etree.QName(a).localname == "a" and a.get("name") == "output":
                if (a.get("val") or "").lower() == "true":
                    is_out = True
        if is_out:
            outputs += 1
        else:
            inputs += 1
    preview["input_count"] = inputs
    preview["output_count"] = outputs

    # Wrap and structurally validate (dangling ok for standalone)
    wrapped = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        b'<project source="3.8.0" version="1.0">\n'
        b'<lib desc="#Wiring" name="0"/>\n'
        b'<lib desc="#Gates" name="1"/>\n'
        b'<main name="x"/>\n'
        + text
        + b"\n</project>\n"
    )
    from circuit_vault.repair import validate_file_bytes

    vr = validate_file_bytes(wrapped)
    hard = [e for e in vr.errors if "unresolved subcircuit" not in e.lower()]
    if hard:
        preview["error"] = "; ".join(hard)
        return False, preview

    if comps and not wires:
        preview["tip"] = "Circuit parses, but has no wires — check wiring in Logisim."
    elif comps and wires:
        preview["tip"] = "Confirm wiring in Logisim after merge."

    return True, preview
