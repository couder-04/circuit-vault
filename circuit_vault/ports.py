"""Logisim component port geometry for wire snap-to-connect.

Port offsets follow Logisim Evolution's AbstractGate / NotGate conventions
(default wide gates, east-facing). Used to stop red floating wires after Build.
"""

from __future__ import annotations

import re
from typing import Iterable

from lxml import etree

_COORD_RE = re.compile(r"^\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)$")

# Default wide gate size (Logisim Evolution GateAttributes.SIZE_WIDE)
_DEFAULT_GATE_SIZE = 50
_XOR_BONUS = 10  # XorGate / XnorGate setAdditionalWidth(10)

_MULTI_INPUT_GATES = {
    "AND Gate",
    "OR Gate",
    "NAND Gate",
    "NOR Gate",
    "XOR Gate",
    "XNOR Gate",
}
_XOR_FAMILY = {"XOR Gate", "XNOR Gate"}
_NOT_FAMILY = {"NOT Gate", "Buffer"}


def _local(el: object) -> str:
    """Return element localname, or \"\" for comments / non-elements."""
    tag = getattr(el, "tag", None)
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _parse_coord(val: str | None) -> tuple[int, int] | None:
    if not val:
        return None
    m = _COORD_RE.match(val.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _fmt(x: int, y: int) -> str:
    return f"({x},{y})"


def _attr_map(comp: etree._Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for child in comp:
        if _local(child) != "a":
            continue
        name = child.get("name")
        if name:
            out[name] = child.get("val") or ""
    return out


def _facing(attrs: dict[str, str]) -> str:
    return (attrs.get("facing") or "east").strip().lower()


def _gate_size(attrs: dict[str, str]) -> int:
    raw = attrs.get("size") or ""
    if raw.isdigit():
        return int(raw)
    if raw.lower() in {"narrow", "30"}:
        return 30
    if raw.lower() in {"wide", "50"}:
        return 50
    return _DEFAULT_GATE_SIZE


def _num_inputs(attrs: dict[str, str], default: int = 2) -> int:
    raw = attrs.get("inputs") or attrs.get("number of inputs") or ""
    if raw.isdigit():
        return max(1, int(raw))
    return default


def _rotate_offset(dx: int, dy: int, facing: str) -> tuple[int, int]:
    """Map east-facing relative offset to absolute facing (Logisim convention)."""
    f = facing.lower()
    if f == "east":
        return dx, dy
    if f == "west":
        return -dx, -dy
    if f == "north":
        return dy, -dx
    if f == "south":
        return -dy, dx
    return dx, dy


def _gate_input_offset(
    index: int,
    *,
    inputs: int,
    size: int,
    bonus: int = 0,
    facing: str = "east",
) -> tuple[int, int]:
    """Mirror AbstractGate.getInputOffset for non-negated inputs."""
    axis_length = size + bonus
    if inputs <= 3:
        if size < 40:
            skip_start, skip_dist, skip_lower_even = -5, 10, 10
        elif size < 60 or inputs <= 2:
            skip_start, skip_dist, skip_lower_even = -10, 20, 20
        else:
            skip_start, skip_dist, skip_lower_even = -15, 30, 30
    elif inputs == 4 and size >= 60:
        skip_start, skip_dist, skip_lower_even = -5, 20, 0
    else:
        skip_start, skip_dist, skip_lower_even = -5, 10, 10

    if (inputs & 1) == 1:
        dy = skip_start * (inputs - 1) + skip_dist * index
    else:
        dy = skip_start * inputs + skip_dist * index
        if index >= inputs / 2:
            dy += skip_lower_even
        if inputs == 4 and size >= 60:
            dy -= 10

    return _rotate_offset(-axis_length, dy, facing)


# Logisim Evolution DrawAttr (DefaultEvolutionAppearance)
_FIXED_FONT_HEIGHT = 12
_FIXED_FONT_CHAR_WIDTH = 8


def _pin_is_output(attrs: dict[str, str]) -> bool:
    if (attrs.get("type") or "").strip().lower() == "output":
        return True
    return (attrs.get("output") or "").strip().lower() in {"true", "1", "yes"}


def _circuit_attr(circuit_el: etree._Element, name: str) -> str | None:
    for child in circuit_el:
        if _local(child) != "a":
            continue
        if child.get("name") == name:
            return child.get("val")
    return None


def pin_specs_from_circuit(circuit_el: etree._Element) -> list[dict]:
    """Pin metadata used to place Evolution named-box appearance ports."""
    specs: list[dict] = []
    for comp in circuit_el.iter("comp"):
        if (comp.get("name") or "") != "Pin":
            continue
        loc = _parse_coord(comp.get("loc"))
        if loc is None:
            continue
        attrs = _attr_map(comp)
        width_raw = attrs.get("width") or "1"
        try:
            width = max(1, int(width_raw))
        except ValueError:
            width = 1
        specs.append(
            {
                "loc": loc,
                "output": _pin_is_output(attrs),
                "label": attrs.get("label") or "",
                "width": width,
                "y": loc[1],
                "x": loc[0],
            }
        )
    return specs


def evolution_named_box_ports(
    anchor: tuple[int, int],
    pin_specs: list[dict],
    *,
    circuit_name: str = "",
    fixed_size: bool = True,
) -> list[tuple[int, int]]:
    """
    Absolute port locations for a Logisim Evolution named-box subcircuit.

    Mirrors DefaultEvolutionAppearance.build (east-facing instance).
    ``anchor`` is the instance ``loc`` (AppearanceAnchor on the east side when
    the subcircuit has any output pins).
    """
    west = [p for p in pin_specs if not p.get("output")]
    east = [p for p in pin_specs if p.get("output")]
    west.sort(key=lambda p: (p.get("y", 0), p.get("x", 0)))
    east.sort(key=lambda p: (p.get("y", 0), p.get("x", 0)))

    num_east = len(east)
    num_west = len(west)
    max_vert = max(num_east, num_west)

    dy = (
        (_FIXED_FONT_HEIGHT + (_FIXED_FONT_HEIGHT >> 2) + 5) // 10
    ) * 10
    max_left = max((len(p.get("label") or "") for p in west), default=0) * _FIXED_FONT_CHAR_WIDTH
    max_right = max((len(p.get("label") or "") for p in east), default=0) * _FIXED_FONT_CHAR_WIDTH
    title_w = max(14, len(circuit_name or "")) * _FIXED_FONT_CHAR_WIDTH
    if fixed_size:
        text_width = 25 * _FIXED_FONT_CHAR_WIDTH
    else:
        text_width = max(max_left + max_right + 35, title_w + 15)
    width = (text_width // 10) * 10 + 20
    # thight unused for port coords; height only affects box drawing

    ax = width if num_east > 0 else 0
    # Instance loc == (rx+ax, ry+ay) with ay=10 when any side has pins.
    # First port row is at ry+10 == anchor.y when anchor is on east/west.
    ports: list[tuple[int, int]] = []
    if num_east > 0 or num_west > 0:
        west_x = anchor[0] - ax  # rx when ax=width; else anchor.x
        east_x = west_x + width
        y0 = anchor[1]  # ry+10
        for i, _ in enumerate(west):
            ports.append((west_x, y0 + i * dy))
        for i, _ in enumerate(east):
            ports.append((east_x, y0 + i * dy))
    else:
        ports.append(anchor)
    return ports


def _splitter_fanout_ports(
    loc: tuple[int, int],
    *,
    fanout: int,
    facing: str,
) -> list[tuple[int, int]]:
    """
    Approximate Splitter bit ends (Logisim Wiring.Splitter).

    Combined end is at ``loc``. Fanout ends sit 20 units along ``facing`` and
    are spaced 10 apart, centered on the axis through ``loc``.
    """
    lx, ly = loc
    n = max(1, fanout)
    span = (n - 1) * 10
    start = -span // 2
    ports: list[tuple[int, int]] = [loc]
    f = facing.lower()
    for i in range(n):
        off = start + i * 10
        if f == "east":
            ports.append((lx + 20, ly + off))
        elif f == "west":
            ports.append((lx - 20, ly + off))
        elif f == "north":
            ports.append((lx + off, ly - 20))
        elif f == "south":
            ports.append((lx + off, ly + 20))
        else:
            ports.append((lx + 20, ly + off))
    return ports


def ports_for_component(
    comp: etree._Element,
    *,
    subcircuit_pins: dict[str, list[dict]] | None = None,
    subcircuit_meta: dict[str, dict] | None = None,
) -> list[tuple[int, int]]:
    """
    Absolute (x, y) connection points for a <comp>.

    Always includes ``loc`` (output tip for gates / pin contact). Additional
    input ports are added for standard gates. Subcircuit instances use Evolution
    named-box port geometry when pin specs are provided.
    """
    loc = _parse_coord(comp.get("loc"))
    if loc is None:
        return []
    lx, ly = loc
    name = comp.get("name") or ""
    attrs = _attr_map(comp)
    facing = _facing(attrs)
    ports: list[tuple[int, int]] = [(lx, ly)]

    if name in _MULTI_INPUT_GATES:
        size = _gate_size(attrs)
        n_in = _num_inputs(attrs, 2)
        bonus = _XOR_BONUS if name in _XOR_FAMILY else 0
        for i in range(n_in):
            dx, dy = _gate_input_offset(
                i, inputs=n_in, size=size, bonus=bonus, facing=facing
            )
            ports.append((lx + dx, ly + dy))
        return ports

    if name in _NOT_FAMILY:
        narrow = (attrs.get("size") or "").lower() in {"narrow", "20"}
        dist = -20 if narrow else -30
        dx, dy = _rotate_offset(dist, 0, facing)
        ports.append((lx + dx, ly + dy))
        return ports

    if name == "Splitter":
        try:
            fanout = int(attrs.get("fanout") or attrs.get("incoming") or "2")
        except ValueError:
            fanout = 2
        return _splitter_fanout_ports(loc, fanout=fanout, facing=facing)

    # Custom / subcircuit instance (no lib, or empty lib)
    lib = comp.get("lib")
    if (lib is None or lib == "") and subcircuit_pins and name in subcircuit_pins:
        meta = (subcircuit_meta or {}).get(name) or {}
        box_ports = evolution_named_box_ports(
            loc,
            subcircuit_pins[name],
            circuit_name=name,
            fixed_size=bool(meta.get("fixed_size", True)),
        )
        return box_ports or ports

    return ports


def collect_ports(
    circuit_el: etree._Element,
    *,
    subcircuit_pins: dict[str, list[dict]] | None = None,
    subcircuit_meta: dict[str, dict] | None = None,
) -> set[tuple[int, int]]:
    ports: set[tuple[int, int]] = set()
    for comp in circuit_el.iter("comp"):
        ports.update(
            ports_for_component(
                comp,
                subcircuit_pins=subcircuit_pins,
                subcircuit_meta=subcircuit_meta,
            )
        )
    return ports


def build_subcircuit_port_index(
    circuits: dict[str, etree._Element],
) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Map circuit name → pin specs / appearance meta for instance port calc."""
    pins: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}
    for name, el in circuits.items():
        pins[name] = pin_specs_from_circuit(el)
        fixed = (_circuit_attr(el, "circuitnamedboxfixedsize") or "true").lower()
        meta[name] = {
            "fixed_size": fixed in {"true", "1", "yes", ""},
            "appearance": _circuit_attr(el, "appearance") or "",
        }
    return pins, meta


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _nearest(
    point: tuple[int, int],
    candidates: Iterable[tuple[int, int]],
    *,
    max_dist: int,
) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    best_d = max_dist + 1
    for c in candidates:
        d = _manhattan(point, c)
        if d < best_d:
            best_d = d
            best = c
    return best if best is not None and best_d <= max_dist else None


def connectivity_stats(
    circuit_el: etree._Element,
    *,
    subcircuit_pins: dict[str, list[dict]] | None = None,
    subcircuit_meta: dict[str, dict] | None = None,
) -> dict:
    """
    Rough wiring completeness for Build validation.

    Returns component_count, wire_count, port_count, connected_ports,
    floating_ports, underwired (bool), summary (str).
    """
    comps = list(circuit_el.iter("comp"))
    wires = list(circuit_el.iter("wire"))
    ports = collect_ports(
        circuit_el,
        subcircuit_pins=subcircuit_pins,
        subcircuit_meta=subcircuit_meta,
    )
    ends: set[tuple[int, int]] = set()
    for w in wires:
        for attr in ("from", "to"):
            pt = _parse_coord(w.get(attr))
            if pt is not None:
                ends.add(pt)
    connected = ports & ends
    floating = ports - ends
    n_comp = len(comps)
    n_wire = len(wires)
    n_port = len(ports)
    n_conn = len(connected)

    underwired = False
    reasons: list[str] = []
    if n_comp >= 2 and n_wire == 0:
        underwired = True
        reasons.append("no <wire> elements at all")
    elif n_comp >= 4 and n_wire < max(3, n_comp - 1):
        underwired = True
        reasons.append(
            f"only {n_wire} wire(s) for {n_comp} components (need many more connections)"
        )
    elif n_port >= 6 and n_conn < max(3, (n_port + 1) // 2):
        underwired = True
        reasons.append(
            f"only {n_conn}/{n_port} known ports have a wire attached"
        )

    summary = (
        f"{n_comp} comps, {n_wire} wires, {n_conn}/{n_port} ports wired"
        + ((" — " + "; ".join(reasons)) if reasons else "")
    )
    return {
        "component_count": n_comp,
        "wire_count": n_wire,
        "port_count": n_port,
        "connected_ports": n_conn,
        "floating_ports": len(floating),
        "underwired": underwired,
        "summary": summary,
        "reasons": reasons,
    }


def snap_wires_to_ports(
    circuit_el: etree._Element,
    *,
    snap_dist: int = 20,
    extend_dist: int = 40,
    subcircuit_pins: dict[str, list[dict]] | None = None,
    subcircuit_meta: dict[str, dict] | None = None,
) -> list[str]:
    """
    Snap floating wire ends onto real component ports (fixes red wires).

    1. Snap each endpoint to a port within *snap_dist* (Manhattan).
    2. Endpoints that still float (not on a port and not shared with another
       wire end) are pulled to the nearest port within *extend_dist*.
    3. Any newly diagonal segment is split into Manhattan stubs.
    """
    notes: list[str] = []
    ports = collect_ports(
        circuit_el,
        subcircuit_pins=subcircuit_pins,
        subcircuit_meta=subcircuit_meta,
    )
    if not ports:
        return notes

    wires = list(circuit_el.iter("wire"))
    if not wires:
        return notes

    snapped = 0
    ends: list[tuple[etree._Element, str, tuple[int, int]]] = []
    for wire in wires:
        for attr in ("from", "to"):
            pt = _parse_coord(wire.get(attr))
            if pt is None:
                continue
            near = _nearest(pt, ports, max_dist=snap_dist)
            if near is not None and near != pt:
                wire.set(attr, _fmt(*near))
                pt = near
                snapped += 1
            ends.append((wire, attr, pt))

    # Refresh ends after soft snap for pass 2
    ends = []
    for wire in wires:
        if wire.getparent() is None:
            continue
        for attr in ("from", "to"):
            pt = _parse_coord(wire.get(attr))
            if pt is not None:
                ends.append((wire, attr, pt))
    counts: dict[tuple[int, int], int] = {}
    for _, _, pt in ends:
        counts[pt] = counts.get(pt, 0) + 1

    extended = 0
    for wire, attr, pt in ends:
        on_port = pt in ports
        junction = counts.get(pt, 0) >= 2
        if on_port or junction:
            continue
        near = _nearest(pt, ports, max_dist=extend_dist)
        if near is not None and near != pt:
            wire.set(attr, _fmt(*near))
            extended += 1

    if snapped:
        notes.append(f"snapped {snapped} wire end(s) onto component ports")
    if extended:
        notes.append(f"extended {extended} floating wire end(s) to nearest ports")

    split = 0
    for wire in list(circuit_el.iter("wire")):
        a = _parse_coord(wire.get("from"))
        b = _parse_coord(wire.get("to"))
        if a is None or b is None:
            continue
        if a == b:
            parent = wire.getparent()
            if parent is not None:
                parent.remove(wire)
            continue
        if a[0] == b[0] or a[1] == b[1]:
            continue
        mid = (b[0], a[1])
        wire.set("from", _fmt(*a))
        wire.set("to", _fmt(*mid))
        second = etree.Element("wire")
        second.set("from", _fmt(*mid))
        second.set("to", _fmt(*b))
        wire.addnext(second)
        split += 1
    if split:
        notes.append(f"re-routed {split} wire(s) after port snap (Manhattan)")

    return notes


def repair_circuit_connections(
    circuit_el: etree._Element,
    *,
    subcircuit_pins: dict[str, list[dict]] | None = None,
    subcircuit_meta: dict[str, dict] | None = None,
    snap_dist: int = 20,
    extend_dist: int = 60,
) -> list[str]:
    """Snap near-miss wire ends onto pins, gates, splitters, and subcircuit ports."""
    return snap_wires_to_ports(
        circuit_el,
        snap_dist=snap_dist,
        extend_dist=extend_dist,
        subcircuit_pins=subcircuit_pins,
        subcircuit_meta=subcircuit_meta,
    )


def port_guide_for_prompt() -> str:
    """Short wiring cheat-sheet embedded in the Claude prompt."""
    return (
        "Port geometry (default wide gates, facing east — loc is the OUTPUT tip):\n"
        "- Pin / Tunnel / Constant: connect exactly at loc=\"(x,y)\".\n"
        "- AND/OR/NAND/NOR at loc=(X,Y): output (X,Y); "
        "inputs (X-50,Y-20) and (X-50,Y+20).\n"
        "- XOR/XNOR at loc=(X,Y): output (X,Y); "
        "inputs (X-60,Y-20) and (X-60,Y+20).\n"
        "- NOT Gate at loc=(X,Y): output (X,Y); input (X-30,Y).\n"
        "- NEVER end a wire at empty space; every end must hit a port or another wire end.\n"
        "- Example: AND at (200,120) ← wire to inputs (150,100) and (150,140), "
        "wire from output (200,120) to an output Pin."
    )
