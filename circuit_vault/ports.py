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


def ports_for_component(comp: etree._Element) -> list[tuple[int, int]]:
    """
    Absolute (x, y) connection points for a <comp>.

    Always includes ``loc`` (output tip for gates / pin contact). Additional
    input ports are added for standard gates.
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

    return ports


def collect_ports(circuit_el: etree._Element) -> set[tuple[int, int]]:
    ports: set[tuple[int, int]] = set()
    for comp in circuit_el.iter("comp"):
        ports.update(ports_for_component(comp))
    return ports


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


def snap_wires_to_ports(
    circuit_el: etree._Element,
    *,
    snap_dist: int = 20,
    extend_dist: int = 40,
) -> list[str]:
    """
    Snap floating wire ends onto real component ports (fixes red wires).

    1. Snap each endpoint to a port within *snap_dist* (Manhattan).
    2. Endpoints that still float (not on a port and not shared with another
       wire end) are pulled to the nearest port within *extend_dist*.
    3. Any newly diagonal segment is split into Manhattan stubs.
    """
    notes: list[str] = []
    ports = collect_ports(circuit_el)
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
