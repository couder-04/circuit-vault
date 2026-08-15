"""Parse Logisim / Logisim Evolution .circ XML files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree


class ParseError(Exception):
    """Raised when a .circ file cannot be parsed as XML."""


# Circuit name attribute patterns for locating spans in raw bytes.
_CIRCUIT_OPEN_RE = re.compile(
    rb'<circuit\b([^>]*)>',
    re.IGNORECASE,
)
_NAME_ATTR_RE = re.compile(
    rb"""\bname\s*=\s*(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class Project:
    """In-memory representation of a .circ project."""

    path: Path
    tree: etree._ElementTree
    raw_bytes: bytes
    root: etree._Element = field(init=False)

    def __post_init__(self) -> None:
        self.root = self.tree.getroot()


def load(path: str | Path) -> Project:
    """Parse a .circ file with lxml, keeping the tree and original bytes."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ParseError(f"Cannot read file: {path}") from exc

    try:
        parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
        tree = etree.parse(str(path), parser)
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"Malformed XML in {path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Failed to parse {path}: {exc}") from exc

    root = tree.getroot()
    if root is None or etree.QName(root).localname != "project":
        raise ParseError(f"Not a Logisim project file (missing <project>): {path}")

    return Project(path=path, tree=tree, raw_bytes=raw)


def list_circuits(project: Project) -> list[str]:
    """Return circuit names in document order."""
    names: list[str] = []
    for el in project.root.iterchildren():
        if _local(el) == "circuit":
            name = el.get("name")
            if name is not None:
                names.append(name)
    return names


def get_circuit_element(project: Project, name: str) -> etree._Element:
    """Return the exact <circuit> element with the given name."""
    for el in project.root.iterchildren():
        if _local(el) == "circuit" and el.get("name") == name:
            return el
    raise KeyError(f"Circuit not found: {name!r}")


def circuit_to_xml_bytes(element: etree._Element) -> bytes:
    """Serialize one circuit subtree without adding an XML declaration."""
    return etree.tostring(
        element,
        encoding="utf-8",
        xml_declaration=False,
        pretty_print=False,
    )


def serialize(project: Project) -> bytes:
    """
    Serialize the whole project.

    If the tree was not surgically rewritten via raw bytes, prefer the cached
    raw_bytes so an unmodified round-trip stays byte-stable.
    """
    return project.raw_bytes


def extract_circuit_raw_bytes(project: Project, name: str) -> bytes:
    """Extract the exact raw byte span of a named <circuit> from the file."""
    start, end = find_circuit_span(project.raw_bytes, name)
    return project.raw_bytes[start:end]


def find_circuit_span(raw: bytes, name: str) -> tuple[int, int]:
    """
    Locate the byte span [start, end) of `<circuit name="...">...</circuit>`.

    Circuits do not nest in Logisim files, so the first matching close tag after
    the open tag ends the element. Attribute order and whitespace inside the
    open tag are preserved by searching raw bytes.
    """
    name_bytes = name.encode("utf-8")
    for match in _CIRCUIT_OPEN_RE.finditer(raw):
        attrs = match.group(1)
        name_m = _NAME_ATTR_RE.search(attrs)
        if name_m is None:
            continue
        if name_m.group("val") != name_bytes:
            continue
        start = match.start()
        # Skip self-closing (rare / invalid for circuit) just in case.
        open_tag = match.group(0)
        if open_tag.rstrip().endswith(b"/>"):
            return start, match.end()
        close = _find_circuit_close(raw, match.end())
        if close < 0:
            raise ParseError(f"Unclosed <circuit> for {name!r}")
        return start, close
    raise KeyError(f"Circuit not found in raw XML: {name!r}")


def _find_circuit_close(raw: bytes, from_pos: int) -> int:
    """Find end index (exclusive) of the matching </circuit> tag."""
    # Track nested <circuit> just in case; Logisim does not nest them.
    depth = 1
    pos = from_pos
    open_pat = re.compile(rb"<circuit\b", re.IGNORECASE)
    close_pat = re.compile(rb"</circuit\s*>", re.IGNORECASE)
    while depth > 0 and pos < len(raw):
        next_open = open_pat.search(raw, pos)
        next_close = close_pat.search(raw, pos)
        if next_close is None:
            return -1
        if next_open is not None and next_open.start() < next_close.start():
            # Could be opening tag; ignore if self-closing handled elsewhere.
            tag_end = raw.find(b">", next_open.start())
            if tag_end < 0:
                return -1
            fragment = raw[next_open.start() : tag_end + 1]
            if not fragment.rstrip().endswith(b"/>"):
                depth += 1
            pos = tag_end + 1
        else:
            depth -= 1
            pos = next_close.end()
            if depth == 0:
                return pos
    return -1


def replace_circuit_raw(raw: bytes, name: str, new_circuit_xml: bytes) -> bytes:
    """Replace a circuit's raw byte span with new_circuit_xml."""
    start, end = find_circuit_span(raw, name)
    # Ensure new bytes look like a circuit element.
    stripped = new_circuit_xml.strip()
    if not stripped.lower().startswith(b"<circuit"):
        raise ParseError("Replacement is not a <circuit> element")
    return raw[:start] + stripped + raw[end:]


def insert_circuit_raw(raw: bytes, new_circuit_xml: bytes) -> bytes:
    """Insert a <circuit> element just before the closing </project> tag."""
    stripped = new_circuit_xml.strip()
    if not stripped.lower().startswith(b"<circuit"):
        raise ParseError("Insertion is not a <circuit> element")
    close = re.search(rb"</project\s*>", raw, re.IGNORECASE)
    if close is None:
        raise ParseError("Cannot insert circuit: missing </project>")
    # Prefer a preceding newline for readability without reformatting siblings.
    prefix = b"\n  " if not raw[close.start() - 1 : close.start()] in (b"\n", b" ") else b""
    return raw[: close.start()] + prefix + stripped + b"\n" + raw[close.start() :]


def rename_circuit_xml(xml_bytes: bytes, new_name: str) -> bytes:
    """Rename a standalone <circuit> snippet (name attr + circuit attribute)."""
    text = xml_bytes.decode("utf-8", errors="replace")
    text = re.sub(
        r'(<circuit\b[^>]*\bname\s*=\s*)(["\'])(.*?)(\2)',
        lambda m: f"{m.group(1)}{m.group(2)}{new_name}{m.group(2)}",
        text,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r'(<a\s+name\s*=\s*["\']circuit["\']\s+val\s*=\s*)(["\'])(.*?)(\2)',
        lambda m: f"{m.group(1)}{m.group(2)}{new_name}{m.group(2)}",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    return text.encode("utf-8")


def parse_circuit_bytes(xml_bytes: bytes) -> etree._Element:
    """Parse a standalone <circuit>...</circuit> snippet into an element."""
    try:
        return etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"Invalid circuit XML: {exc}") from exc


def _local(el: Any) -> str:
    return etree.QName(el).localname
