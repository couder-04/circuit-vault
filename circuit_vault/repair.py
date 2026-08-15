"""Best-effort repair of corrupted circuit / .circ XML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from circuit_vault.parser import ParseError, find_circuit_span, list_circuits, load, parse_circuit_bytes
from circuit_vault.validator import validate_file

_COORD_RE = re.compile(r"^\(\s*-?\d+\s*,\s*-?\d+\s*\)$")


@dataclass
class RepairResult:
    fixed_bytes: bytes | None
    ok: bool
    changes: list[str] = field(default_factory=list)
    unfixable_reason: str | None = None
    resolvable_deps: list[str] = field(default_factory=list)


def repair_circuit(xml_bytes: bytes) -> RepairResult:
    """Attempt to repair a single <circuit>...</circuit> snippet."""
    changes: list[str] = []
    data = _strip_encoding_junk(xml_bytes, changes)

    # Truncation: try to close open tags for a circuit fragment.
    if b"</circuit>" not in data.lower():
        data, closed = _close_truncated_circuit(data)
        if closed:
            changes.append("Closed truncated circuit tags")
        else:
            return RepairResult(
                fixed_bytes=None,
                ok=False,
                changes=changes,
                unfixable_reason="Circuit appears truncated and could not be closed safely",
            )

    data, wire_notes = _drop_junk_wires(data)
    changes.extend(wire_notes)

    try:
        el = parse_circuit_bytes(data.strip())
    except ParseError as exc:
        return RepairResult(
            fixed_bytes=None,
            ok=False,
            changes=changes,
            unfixable_reason=f"Still not valid circuit XML: {exc}",
        )

    name = el.get("name") or "?"
    # Structural check in isolation — wrap in a minimal project for validator.
    wrapped = _wrap_as_project(data.strip())
    vr = validate_file_bytes(wrapped)
    # Dangling refs are expected for a lone circuit; filter those for circuit-only repair.
    hard_errors = [
        e
        for e in vr.errors
        if "unresolved subcircuit" not in e.lower() and "malformed wire" not in e.lower()
    ]
    # Re-check wires after drop
    try:
        parse_circuit_bytes(data.strip())
    except ParseError as exc:
        return RepairResult(None, False, changes, str(exc))

    # Final: circuit must parse as XML; wire errors remaining = unfixable
    remaining_wire = [e for e in vr.errors if "malformed wire" in e.lower()]
    if remaining_wire and not wire_notes:
        # Try dropping remaining bad wires more aggressively
        data2, more = _drop_junk_wires(data, aggressive=True)
        changes.extend(more)
        data = data2
        wrapped = _wrap_as_project(data.strip())
        vr = validate_file_bytes(wrapped)
        remaining_wire = [e for e in vr.errors if "malformed wire" in e.lower()]

    if remaining_wire:
        return RepairResult(
            fixed_bytes=None,
            ok=False,
            changes=changes,
            unfixable_reason="; ".join(remaining_wire),
        )

    # Accept if the circuit element itself parses; dangling refs reported separately.
    return RepairResult(fixed_bytes=data.strip(), ok=True, changes=changes)


def repair_file(circ_bytes: bytes) -> RepairResult:
    """Repair a whole incoming .circ file."""
    changes: list[str] = []
    data = _strip_encoding_junk(circ_bytes, changes)

    # Drop incomplete trailing circuit if cut mid-element.
    data, dropped = _drop_incomplete_trailing_circuit(data)
    if dropped:
        changes.append(f"Dropped incomplete trailing circuit ({dropped})")

    # Close obvious unclosed project tag.
    if b"</project>" not in data.lower():
        if b"<project" in data.lower():
            data = data.rstrip() + b"\n</project>\n"
            changes.append("Closed unclosed </project>")
        else:
            return RepairResult(
                None, False, changes, "Missing <project> root — cannot repair"
            )

    # Write to temp validate via bytes
    try:
        from pathlib import Path
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".circ", delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        try:
            project = load(tmp)
        except ParseError as exc:
            tmp.unlink(missing_ok=True)
            return RepairResult(None, False, changes, f"Could not parse after repair: {exc}")
        finally:
            tmp.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        return RepairResult(None, False, changes, str(exc))

    # Per-circuit wire cleanup in raw file
    for name in list(list_circuits(project)):
        try:
            start, end = find_circuit_span(data, name)
        except (KeyError, ParseError):
            continue
        chunk = data[start:end]
        fixed_chunk, notes = _drop_junk_wires(chunk)
        if notes:
            changes.extend(f"{name}: {n}" for n in notes)
            data = data[:start] + fixed_chunk + data[end:]

    # Re-validate
    vr = validate_file_bytes(data)
    if not vr.ok:
        return RepairResult(
            fixed_bytes=data,
            ok=False,
            changes=changes,
            unfixable_reason="; ".join(vr.errors[:5]),
        )

    return RepairResult(fixed_bytes=data, ok=True, changes=changes)


def validate_file_bytes(data: bytes):
    from pathlib import Path
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".circ", delete=False) as fh:
        fh.write(data)
        tmp = Path(fh.name)
    try:
        return validate_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _strip_encoding_junk(data: bytes, changes: list[str]) -> bytes:
    original = data
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
        changes.append("Stripped UTF-8 BOM")
    # Remove null bytes
    if b"\x00" in data:
        data = data.replace(b"\x00", b"")
        changes.append("Removed null bytes")
    # CRLF → LF for consistency (Logisim accepts both; we only note if mixed junk)
    if b"\r\n" in data:
        data = data.replace(b"\r\n", b"\n")
        changes.append("Normalized CRLF to LF")
    elif b"\r" in data:
        data = data.replace(b"\r", b"\n")
        changes.append("Normalized bare CR to LF")

    # Strip leading junk before XML declaration / root
    start = data.find(b"<?xml")
    if start < 0:
        start = data.find(b"<project")
    if start > 0:
        data = data[start:]
        changes.append("Removed junk bytes before XML root")

    # Strip trailing junk after </project>
    close = data.lower().rfind(b"</project>")
    if close >= 0:
        end = close + len(b"</project>")
        # allow trailing whitespace
        trailing = data[end:].strip()
        if trailing:
            data = data[:end] + b"\n"
            changes.append("Removed junk bytes after </project>")

    # Fix malformed declaration
    if data.lstrip().startswith(b"<?xml"):
        decl_end = data.find(b"?>")
        if decl_end < 0:
            # broken declaration — replace
            rest_start = data.find(b"<project")
            if rest_start > 0:
                data = b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' + data[
                    rest_start:
                ]
                changes.append("Replaced malformed XML declaration")

    if data != original and not changes:
        changes.append("Normalized encoding")
    return data


def _close_truncated_circuit(data: bytes) -> tuple[bytes, bool]:
    text = data.decode("utf-8", errors="replace").rstrip()
    if "<circuit" not in text.lower():
        return data, False
    # Close open tags in reverse order of common Logisim elements.
    open_tags = re.findall(r"<([a-zA-Z_][\w-]*)\b[^>]*?(?<!/)>", text)
    stack: list[str] = []
    for tag in open_tags:
        stack.append(tag)
    # Pop properly closed
    for m in re.finditer(r"</([a-zA-Z_][\w-]*)\s*>", text):
        tag = m.group(1)
        if stack and stack[-1].lower() == tag.lower():
            stack.pop()
        elif tag.lower() in {t.lower() for t in stack}:
            while stack and stack[-1].lower() != tag.lower():
                stack.pop()
            if stack:
                stack.pop()
    if not stack:
        # maybe just missing </circuit>
        if not re.search(r"</circuit\s*>", text, re.I):
            text += "\n</circuit>"
            return text.encode("utf-8"), True
        return data, False
    for tag in reversed(stack):
        text += f"</{tag}>"
    if not re.search(r"</circuit\s*>", text, re.I):
        text += "\n</circuit>"
    return text.encode("utf-8"), True


def _drop_incomplete_trailing_circuit(data: bytes) -> tuple[bytes, str | None]:
    """If the last <circuit> has no closing tag, drop it."""
    opens = list(re.finditer(rb"<circuit\b", data, re.I))
    if not opens:
        return data, None
    last = opens[-1]
    after = data[last.start() :]
    if re.search(rb"</circuit\s*>", after, re.I):
        return data, None
    # Extract name if possible
    name_m = re.search(
        rb"""\bname\s*=\s*(["'])(.*?)\1""", data[last.start() : last.start() + 200], re.I
    )
    name = name_m.group(2).decode("utf-8", errors="replace") if name_m else "unknown"
    return data[: last.start()].rstrip() + b"\n", name


_WIRE_RE = re.compile(
    rb'<wire\b[^>]*?/?>',
    re.IGNORECASE,
)


def _drop_junk_wires(data: bytes, *, aggressive: bool = False) -> tuple[bytes, list[str]]:
    notes: list[str] = []
    out = data

    def repl(match: re.Match[bytes]) -> bytes:
        tag = match.group(0)
        frm = re.search(rb"""\bfrom\s*=\s*(["'])(.*?)\1""", tag, re.I)
        to = re.search(rb"""\bto\s*=\s*(["'])(.*?)\1""", tag, re.I)
        if frm is None or to is None:
            notes.append("Dropped wire missing from/to")
            return b""
        fval = frm.group(2).decode("utf-8", errors="replace")
        tval = to.group(2).decode("utf-8", errors="replace")
        if not _COORD_RE.match(fval.strip()) or not _COORD_RE.match(tval.strip()):
            notes.append(f"Dropped wire with bad coords from={fval!r} to={tval!r}")
            return b""
        return tag

    out = _WIRE_RE.sub(repl, out)
    return out, notes


def _wrap_as_project(circuit_xml: bytes) -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        b'<project source="3.8.0" version="1.0">\n'
        b'<lib desc="#Wiring" name="0"/>\n'
        b'<lib desc="#Gates" name="1"/>\n'
        b'<main name="x"/>\n'
        b"<options/>\n"
        + circuit_xml
        + b"\n</project>\n"
    )
