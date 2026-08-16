"""Structural validation for Logisim .circ projects (no hashing)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from lxml import etree

from circuit_vault.formats import normalize_newlines
from circuit_vault.parser import (
    ParseError,
    Project,
    extract_circuit_raw_bytes,
    get_circuit_element,
    list_circuits,
    load,
)

_COORD_RE = re.compile(r"^\(\s*-?\d+\s*,\s*-?\d+\s*\)$")


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    CHANGED = "CHANGED"
    BROKEN = "BROKEN"
    NO_FINAL = "NO_FINAL"


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_file(path: str | Path) -> ValidationResult:
    """Validate a .circ path: parse + structural checks."""
    try:
        project = load(path)
    except ParseError as exc:
        return ValidationResult(ok=False, errors=[str(exc)])
    return validate_project(project)


def validate_project(project: Project) -> ValidationResult:
    """Run structural checks on an already-parsed project."""
    errors: list[str] = []
    warnings: list[str] = []
    name_set = set(list_circuits(project))

    for el in project.root.iterchildren():
        if _local(el) != "circuit":
            continue
        name = el.get("name")
        if not name:
            errors.append("Found a <circuit> element without a name attribute")
            continue
        errors.extend(_wire_errors(el, name))
        errors.extend(_dangling_subcircuit_errors(el, name, name_set))
        warnings.extend(_pin_warnings(el, name))

    return ValidationResult(ok=len(errors) == 0, errors=errors, warnings=warnings)


def validate_circuit(project: Project, name: str) -> ValidationResult:
    """Validate a single named circuit within a project."""
    try:
        el = get_circuit_element(project, name)
    except KeyError:
        return ValidationResult(ok=False, errors=[f"Circuit not found: {name!r}"])

    name_set = set(list_circuits(project))
    errors = _wire_errors(el, name) + _dangling_subcircuit_errors(el, name, name_set)
    warnings = _pin_warnings(el, name)
    return ValidationResult(ok=len(errors) == 0, errors=errors, warnings=warnings)


def circuit_health(
    project: Project,
    name: str,
    final_bytes: bytes | None,
) -> HealthState:
    """
    Determine health state for GUI coloring.

    BROKEN comes only from validation failure.
    Matching stored final decides HEALTHY vs CHANGED when a final exists.
    """
    result = validate_circuit(project, name)
    if not result.ok:
        return HealthState.BROKEN
    if final_bytes is None:
        return HealthState.NO_FINAL
    try:
        current = extract_circuit_raw_bytes(project, name)
    except Exception:  # noqa: BLE001
        from circuit_vault.parser import circuit_to_xml_bytes

        current = circuit_to_xml_bytes(get_circuit_element(project, name))
    if normalize_newlines(current.strip()) == normalize_newlines(final_bytes.strip()):
        return HealthState.HEALTHY
    return HealthState.CHANGED


def is_subcircuit_instance(comp: etree._Element, circuit_names: set[str]) -> str | None:
    """
    Return referenced circuit name if this <comp> is a subcircuit instance.

    Built-in components carry lib="N" for Wiring/Gates/…. Same-project
    subcircuits are usually written with no lib; Logisim Evolution sometimes
    writes lib="10"+ without a matching <lib> entry — if ``name`` matches a
    circuit in this project, treat it as a subcircuit either way.
    """
    if _local(comp) != "comp":
        return None
    comp_name = comp.get("name")
    if not comp_name:
        return None
    if comp_name in circuit_names:
        return comp_name
    lib = comp.get("lib")
    if lib is not None and str(lib).strip() != "":
        return None
    return comp_name


def _dangling_subcircuit_errors(
    el: etree._Element, circuit_name: str, name_set: set[str]
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for comp in el.iter():
        ref = is_subcircuit_instance(comp, name_set)
        if ref is None:
            continue
        if ref == circuit_name:
            continue
        if ref not in name_set and ref not in seen:
            seen.add(ref)
            errors.append(
                f"Circuit {circuit_name!r}: unresolved subcircuit reference {ref!r}"
            )
    return errors


def missing_subcircuit_names(
    el: etree._Element,
    known_circuits: set[str],
    *,
    self_name: str | None = None,
) -> list[str]:
    """Unique subcircuit names referenced (no lib=) that are not in *known_circuits*."""
    missing: list[str] = []
    seen: set[str] = set()
    own = self_name or el.get("name")
    for comp in el.iter("comp"):
        ref = is_subcircuit_instance(comp, known_circuits)
        if ref is None or ref == own:
            continue
        if ref not in known_circuits and ref not in seen:
            seen.add(ref)
            missing.append(ref)
    return missing


def format_missing_subcircuits_help(
    circuit_name: str,
    missing: list[str],
) -> str:
    """User-facing explanation when Build XML needs other circuits first."""
    listed = "\n".join(f"  • {m}" for m in missing)
    return (
        f'"{circuit_name}" uses other circuits that are not in this .circ file yet:\n'
        f"{listed}\n\n"
        "This is not a crash — the XML is incomplete for this file.\n\n"
        'Click "Copy fix prompt" (also filled into Step 2) and paste it into Claude '
        "so it returns corrected XML that either expands those blocks into gates "
        "or only uses subcircuit names that already exist in your .circ.\n\n"
        "Then paste the new XML into Step 3 and Build & Merge again."
    )

def _wire_errors(el: etree._Element, circuit_name: str) -> list[str]:
    errors: list[str] = []
    for wire in el.iter("wire"):
        for attr in ("from", "to"):
            val = wire.get(attr)
            if val is None:
                errors.append(
                    f"Circuit {circuit_name!r}: wire missing {attr!r} attribute"
                )
            elif not _COORD_RE.match(val.strip()):
                errors.append(
                    f"Circuit {circuit_name!r}: malformed wire {attr}={val!r}"
                )
    return errors


def _pin_warnings(el: etree._Element, circuit_name: str) -> list[str]:
    """Warn if a circuit has comps but no Pin instances (never an error)."""
    comps = list(el.iter("comp"))
    if not comps:
        return []
    has_pin = any(c.get("name") == "Pin" for c in comps)
    if not has_pin:
        return [f"Circuit {circuit_name!r}: has components but no Pin instances"]
    return []


def _local(el: object) -> str:
    tag = getattr(el, "tag", None)
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag
