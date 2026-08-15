"""Surgical single-circuit splice and timestamped backups."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from circuit_vault.parser import (
    ParseError,
    Project,
    insert_circuit_raw,
    load,
    parse_circuit_bytes,
    replace_circuit_raw,
)
from circuit_vault.validator import validate_project


class SpliceError(Exception):
    """Raised when a splice would produce an invalid project."""


def backup(path: str | Path) -> Path:
    """
    Copy current .circ to a timestamped backup next to it.

    Example: main.circ.2026-08-16T10-30-00.bak
    """
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    backup_path = path.with_name(f"{path.name}.{stamp}.bak")
    # Avoid collision within the same second.
    n = 1
    while backup_path.exists():
        backup_path = path.with_name(f"{path.name}.{stamp}-{n}.bak")
        n += 1
    shutil.copy2(path, backup_path)
    return backup_path


def _project_from_raw(project: Project, new_raw: bytes) -> Project:
    tmp = project.path.with_suffix(project.path.suffix + ".splice-tmp")
    try:
        tmp.write_bytes(new_raw)
        updated = load(tmp)
        updated.path = project.path
        updated.raw_bytes = new_raw
    finally:
        if tmp.exists():
            tmp.unlink()
    return updated


def splice(project: Project, name: str, new_circuit_xml_bytes: bytes) -> Project:
    """
    Replace the named <circuit> in place via raw byte splicing.

    Sibling circuits and top-level elements stay byte-stable. Does not write
    to disk — caller validates and saves.
    """
    try:
        parse_circuit_bytes(new_circuit_xml_bytes.strip())
    except ParseError as exc:
        raise SpliceError(str(exc)) from exc

    try:
        new_raw = replace_circuit_raw(project.raw_bytes, name, new_circuit_xml_bytes)
    except KeyError as exc:
        raise SpliceError(str(exc)) from exc
    except ParseError as exc:
        raise SpliceError(str(exc)) from exc

    return _project_from_raw(project, new_raw)


def insert_circuit(project: Project, new_circuit_xml_bytes: bytes) -> Project:
    """Append a new <circuit> before </project> without rewriting siblings."""
    try:
        parse_circuit_bytes(new_circuit_xml_bytes.strip())
    except ParseError as exc:
        raise SpliceError(str(exc)) from exc
    try:
        new_raw = insert_circuit_raw(project.raw_bytes, new_circuit_xml_bytes)
    except ParseError as exc:
        raise SpliceError(str(exc)) from exc
    return _project_from_raw(project, new_raw)


def splice_and_save(
    project: Project,
    name: str,
    new_circuit_xml_bytes: bytes,
    *,
    do_backup: bool = True,
) -> tuple[Project, Path | None]:
    """
    Backup (optional), splice, validate whole file, then write.

    If validation fails, do not save — original file and backup stay intact.
    """
    backup_path: Path | None = None
    if do_backup:
        backup_path = backup(project.path)

    updated = splice(project, name, new_circuit_xml_bytes)
    result = validate_project(updated)
    if not result.ok:
        raise SpliceError(
            "Spliced project failed validation; file not saved. "
            + "; ".join(result.errors)
        )

    project.path.write_bytes(updated.raw_bytes)
    return updated, backup_path
