"""Build-from-description → Claude prompt generation + post-fix."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from circuit_vault.formats import CircFormat, normalize_format, wrap_circuit_as_project
from circuit_vault.parser import ParseError, circuit_to_xml_bytes, parse_circuit_bytes
from lxml import etree

# Packaged skeletons (installed with the wheel). Fixtures remain a fallback for
# editable checkouts that still have tests/fixtures.
_PACKAGE_SKELETONS = Path(__file__).resolve().parent / "skeletons"
_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

_FALLBACK_SKELETON = (
    '<circuit name="Example">\n'
    '  <a name="circuit" val="Example"/>\n'
    '  <comp lib="0" loc="(80,100)" name="Pin">\n'
    '    <a name="facing" val="east"/>\n'
    "  </comp>\n"
    '  <wire from="(80,100)" to="(120,100)"/>\n'
    "</circuit>"
)

_COORD_RE = re.compile(r"^\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)$")
_UNIQUE_SUFFIX_RE = re.compile(r"_CV[0-9A-F]{4}$")
_GRID = 10


def components_catalog(target_format: str | CircFormat | None = None) -> dict[str, list[str]]:
    """
    Component names suitable for prompts.

    Classic Logisim has a smaller palette; Evolution adds plexers / transistor
    names commonly used in course labs. Both formats share the core gate set.
    """
    fmt = normalize_format(target_format)
    basic = {
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
    }
    if fmt == CircFormat.EVOLUTION:
        basic["TRANSISTOR"] = [
            "Transistor",
            "Transmission Gate",
            "NMOS",
            "PMOS",
        ]
    return basic


def _skeleton(target_format: CircFormat) -> str:
    packaged = _PACKAGE_SKELETONS / (
        "classic_circuit.xml"
        if target_format == CircFormat.CLASSIC
        else "evolution_circuit.xml"
    )
    if packaged.exists():
        return packaged.read_text(encoding="utf-8")

    try:
        pkg = resources.files("circuit_vault.skeletons")
        name = (
            "classic_circuit.xml"
            if target_format == CircFormat.CLASSIC
            else "evolution_circuit.xml"
        )
        return (pkg / name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, AttributeError, TypeError, OSError):
        pass

    if target_format == CircFormat.CLASSIC:
        path = _FIXTURES / "classic.circ"
        circuit_name = "AND2"
    else:
        path = _FIXTURES / "main.circ"
        circuit_name = "Half Adder"
    if path.exists():
        from circuit_vault.parser import extract_circuit_raw_bytes, load

        project = load(path)
        return extract_circuit_raw_bytes(project, circuit_name).decode("utf-8")

    return _FALLBACK_SKELETON


# ----- Logisim circuit naming -------------------------------------------------


def sanitize_base_name(raw: str | None) -> str:
    """
    Make a Logisim-friendly base name (no unique suffix yet).

    Rules we enforce:
    - Must start with a letter (A–Z / a–z)
    - Only letters, digits, spaces, underscore, hyphen afterward
    - No leading digits or special characters
    """
    text = (raw or "").strip()
    text = re.sub(r"[^A-Za-z0-9 _\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _UNIQUE_SUFFIX_RE.sub("", text).rstrip(" _-")
    text = re.sub(r"^[^A-Za-z]+", "", text).strip()
    if not text:
        text = "BuiltCircuit"
    text = text[:40].rstrip(" _-")
    if not text or not text[0].isalpha():
        text = "BuiltCircuit"
    return text


def unique_circuit_name(
    raw: str | None,
    existing: set[str] | None = None,
) -> str:
    """
    Sanitize *raw* and ensure it does not collide with *existing*.

    First try the clean base name; if taken, append a decimal number
    (``Adder`` → ``Adder1`` → ``Adder2`` …).
    """
    base = sanitize_base_name(raw)
    existing = existing or set()
    if base not in existing:
        return base
    n = 1
    while True:
        candidate = f"{base}{n}"
        if candidate not in existing:
            return candidate
        n += 1


def is_valid_logisim_circuit_name(name: str) -> bool:
    """True if name follows our Logisim-safe pattern."""
    if not name or not name[0].isalpha():
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9 _\-]*", name))


# ----- Geometry / wiring cleanup ---------------------------------------------


def _parse_coord(val: str | None) -> tuple[int, int] | None:
    if not val:
        return None
    m = _COORD_RE.match(val.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _fmt_coord(x: int, y: int) -> str:
    return f"({x},{y})"


def _snap(n: int, grid: int = _GRID) -> int:
    return int(round(n / grid) * grid)


def _snap_pair(x: int, y: int, grid: int = _GRID) -> tuple[int, int]:
    return _snap(x, grid), _snap(y, grid)


def _local(el: object) -> str:
    """Return element localname, or \"\" for comments / non-elements."""
    tag = getattr(el, "tag", None)
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _strip_xml_comments(xml_bytes: bytes) -> bytes:
    """Remove <!-- ... --> so Logisim paste and our walkers stay safe."""
    return re.sub(br"<!--.*?-->", b"", xml_bytes, flags=re.DOTALL)


def normalize_circuit_geometry(el: etree._Element) -> list[str]:
    """
    Snap component/wire coordinates to the Logisim 10-unit grid, replace
    diagonal wires with Manhattan segments, then snap floating ends onto
    real component ports (fixes red disconnected wires in Logisim).

    Mutates *el* in place. Returns human-readable fix notes.
    """
    from circuit_vault.ports import snap_wires_to_ports

    notes: list[str] = []
    snapped_comps = 0
    for comp in el.iter("comp"):
        loc = _parse_coord(comp.get("loc"))
        if loc is None:
            continue
        sx, sy = _snap_pair(*loc)
        if (sx, sy) != loc:
            comp.set("loc", _fmt_coord(sx, sy))
            snapped_comps += 1
    if snapped_comps:
        notes.append(f"snapped {snapped_comps} component(s) to {_GRID}-unit grid")

    wires = list(el.iter("wire"))
    split = 0
    snapped_wires = 0
    for wire in wires:
        a = _parse_coord(wire.get("from"))
        b = _parse_coord(wire.get("to"))
        if a is None or b is None:
            continue
        x1, y1 = _snap_pair(*a)
        x2, y2 = _snap_pair(*b)
        if (x1, y1) != a or (x2, y2) != b:
            snapped_wires += 1
        if (x1, y1) == (x2, y2):
            parent = wire.getparent()
            if parent is not None:
                parent.remove(wire)
            notes.append("removed zero-length wire")
            continue
        if x1 == x2 or y1 == y2:
            wire.set("from", _fmt_coord(x1, y1))
            wire.set("to", _fmt_coord(x2, y2))
            continue
        mid = (x2, y1)
        wire.set("from", _fmt_coord(x1, y1))
        wire.set("to", _fmt_coord(*mid))
        second = etree.Element("wire")
        second.set("from", _fmt_coord(*mid))
        second.set("to", _fmt_coord(x2, y2))
        wire.addnext(second)
        split += 1
    if snapped_wires:
        notes.append(f"snapped {snapped_wires} wire end(s) to grid")
    if split:
        notes.append(f"split {split} diagonal wire(s) into axis-aligned segments")

    notes.extend(snap_wires_to_ports(el))
    return notes


def _strip_fences(xml_bytes: bytes) -> bytes:
    text = xml_bytes.strip()
    if text.startswith(b"```"):
        lines = text.split(b"\n")
        if lines and lines[0].startswith(b"```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == b"```":
            lines = lines[:-1]
        text = b"\n".join(lines).strip()
    return _strip_xml_comments(text)


def prepare_generated_circuit(
    xml_bytes: bytes,
    *,
    existing_names: set[str] | None = None,
    preferred_name: str | None = None,
    force_unique_suffix: bool = True,
) -> tuple[bytes, str, list[str]]:
    """
    Strip fences, fix geometry, assign a Logisim-safe unique circuit name.

    If *preferred_name* is set (user-chosen in Build), that wins over the XML
    ``name``. Collisions get a decimal suffix (``Foo``, ``Foo1``, ``Foo2``…).

    Returns (xml_bytes, final_name, fix_notes).
    """
    from circuit_vault.parser import rename_circuit_xml

    text = _strip_fences(xml_bytes)
    el = parse_circuit_bytes(text)
    if _local(el) != "circuit":
        raise ParseError("Root element must be <circuit>")

    notes = normalize_circuit_geometry(el)
    raw_name = (preferred_name or "").strip() or (el.get("name") or "BuiltCircuit")
    if force_unique_suffix or not is_valid_logisim_circuit_name(raw_name) or (
        existing_names and raw_name in existing_names
    ):
        final_name = unique_circuit_name(raw_name, existing_names)
    else:
        final_name = sanitize_base_name(raw_name)

    el.set("name", final_name)
    for child in el:
        if _local(child) == "a" and child.get("name") == "circuit":
            child.set("val", final_name)

    out = circuit_to_xml_bytes(el)
    out = rename_circuit_xml(out, final_name)
    notes.append(f"circuit named {final_name!r}")
    return out, final_name, notes


def generate_prompt(
    description: str,
    components: list[str],
    inputs: str,
    outputs: str,
    target_format: str | CircFormat = CircFormat.EVOLUTION,
) -> str:
    fmt = normalize_format(target_format)
    skeleton = _skeleton(fmt)
    comps = ", ".join(components) if components else "(none specified — choose appropriate gates)"
    product = "Evolution" if fmt == CircFormat.EVOLUTION else "classic"
    extra = ""
    if fmt == CircFormat.CLASSIC:
        extra = (
            "\n- Do NOT emit <appear>, <clabel>, or nested <tool> under <lib> — "
            "classic Logisim does not use those.\n"
        )
    else:
        extra = (
            "\n- Evolution may include <appear>…</appear> and <a name=\"clabel\">; "
            "match the skeleton if present.\n"
        )
    suggested = sanitize_base_name(description) if description.strip() else "BuiltCircuit"
    from circuit_vault.ports import port_guide_for_prompt

    return f"""You are generating a Logisim {product} circuit as XML.

## Exact <circuit> skeleton (match this shape and attribute style — do not invent new element types)
```
{skeleton}
```

## Circuit naming (Logisim rules — strict)
- `name="..."` and `<a name="circuit" val="..."/>` must be the SAME string.
- Name MUST start with a letter (A–Z or a–z). NEVER start with a digit or special character.
- Allowed characters after the first letter: letters, digits, spaces, underscore `_`, hyphen `-` only.
- Do NOT use parentheses, slashes, commas, or punctuation in the name.
- Prefer a short readable name derived from the description, e.g. `{suggested}` (not `4bit-adder`, not `!!xor`).
- Circuit Vault will rename on merge if needed (user-chosen name, or a decimal suffix like `Adder1` if the name already exists) — you do not need to invent uniqueness codes.

## Placement rules (layout)
- Use integer coordinates on a **10-unit grid** only (…70, 80, 90, 100…). Never use odd values like 85 or 113.
- Put **input Pins on the left**, facing east; **output Pins on the right**, facing west.
- Space pins vertically by **40** units (e.g. y=100, 140, 180).
- Place gates in a clear left→right signal flow; keep related gates aligned on the same y when possible.
- Leave gaps so wires do not cross through component bodies when avoidable.
- Typical canvas: inputs around x=80, gates around x=200–220, outputs around x=300–320.

## Wiring rules (alignment + connection — critical)
- Every connection is a `<wire from="(x,y)" to="(x,y)"/>`.
- Wires MUST be **axis-aligned** (horizontal OR vertical). NEVER draw a diagonal wire.
- If a connection needs both an x and y change, use **two** wires via a corner.
- Red wires in Logisim mean an endpoint is floating — avoid that completely.
{port_guide_for_prompt()}
- Built-in library components include numeric `lib="N"`. Subcircuit instances omit `lib`.
- Preserve attribute order similar to the skeleton.{extra}
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


def validate_generated(
    xml_bytes: bytes,
    target_format: str | CircFormat = CircFormat.EVOLUTION,
    *,
    existing_names: set[str] | None = None,
    preferred_name: str | None = None,
    prepare: bool = True,
) -> tuple[bool, dict]:
    """
    Validate generated circuit XML.

    When *prepare* is True (default), snaps geometry to the grid, splits diagonal
    wires, and assigns a Logisim-safe unique name (decimal suffix on clash)
    before checks. *preferred_name* overrides the XML circuit name when set.

    Returns (ok, preview) where preview has name, input_count, output_count,
    component_count, tip (optional), prepared_xml (bytes when prepare ran).
    """
    fmt = normalize_format(target_format)
    preview: dict = {
        "name": None,
        "input_count": 0,
        "output_count": 0,
        "component_count": 0,
        "tip": None,
        "error": None,
        "format": fmt.value,
        "fixes": [],
        "prepared_xml": None,
    }

    text = _strip_fences(xml_bytes)
    try:
        if prepare:
            text, final_name, notes = prepare_generated_circuit(
                text,
                existing_names=existing_names,
                preferred_name=preferred_name,
                force_unique_suffix=True,
            )
            preview["fixes"] = notes
            preview["prepared_xml"] = text
            preview["name"] = final_name
        el = parse_circuit_bytes(text)
    except ParseError as exc:
        preview["error"] = str(exc)
        return False, preview

    if _local(el) != "circuit":
        preview["error"] = "Root element must be <circuit>"
        return False, preview

    if fmt == CircFormat.CLASSIC:
        lowered = text.lower()
        if b"<appear" in lowered or b'name="clabel"' in lowered:
            preview["tip"] = (
                "XML looks Evolution-style; classic Logisim may ignore or reject "
                "<appear>/<clabel> — open and check in classic Logisim."
            )

    if not preview.get("name"):
        preview["name"] = el.get("name")

    if not is_valid_logisim_circuit_name(preview["name"] or ""):
        preview["error"] = (
            f"Circuit name {preview['name']!r} is not Logisim-safe "
            "(must start with a letter; only letters, digits, spaces, _ , -)."
        )
        return False, preview

    comps = list(el.iter("comp"))
    wires = list(el.iter("wire"))
    preview["component_count"] = len(comps)
    inputs = 0
    outputs = 0
    for c in comps:
        if c.get("name") != "Pin":
            continue
        is_out = False
        for a in c:
            if _local(a) == "a" and a.get("name") == "output":
                if (a.get("val") or "").lower() == "true":
                    is_out = True
        if is_out:
            outputs += 1
        else:
            inputs += 1
    preview["input_count"] = inputs
    preview["output_count"] = outputs

    for w in wires:
        a = _parse_coord(w.get("from"))
        b = _parse_coord(w.get("to"))
        if a and b and a[0] != b[0] and a[1] != b[1]:
            preview["error"] = (
                "Diagonal wire found — wires must be horizontal or vertical only."
            )
            return False, preview

    wrapped = wrap_circuit_as_project(text, fmt)
    from circuit_vault.repair import validate_file_bytes

    vr = validate_file_bytes(wrapped)
    hard = [e for e in vr.errors if "unresolved subcircuit" not in e.lower()]
    if hard:
        preview["error"] = "; ".join(hard)
        return False, preview

    tip_bits = []
    if preview.get("fixes"):
        tip_bits.append("Auto-fixed: " + "; ".join(preview["fixes"]))
    if comps and not wires:
        tip_bits.append("Circuit parses, but has no wires — check wiring in Logisim.")
    elif comps and wires:
        tip_bits.append("Confirm pin alignment in Logisim after merge.")
    if tip_bits and not preview.get("tip"):
        preview["tip"] = " ".join(tip_bits)
    elif tip_bits and preview.get("tip"):
        preview["tip"] = preview["tip"] + " " + " ".join(tip_bits)

    return True, preview
