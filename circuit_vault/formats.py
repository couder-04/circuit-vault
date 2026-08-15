"""Logisim format detection and cross-platform OS helpers."""

from __future__ import annotations

import os
import sys
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from circuit_vault.parser import Project


class CircFormat(str, Enum):
    """Logisim classic (2.x) vs Logisim Evolution (3.x+)."""

    CLASSIC = "classic"
    EVOLUTION = "evolution"


def normalize_format(value: str | CircFormat | None) -> CircFormat:
    """Map user/CLI strings to CircFormat. Default: evolution."""
    if value is None:
        return CircFormat.EVOLUTION
    if isinstance(value, CircFormat):
        return value
    key = str(value).strip().lower().replace("-", "").replace("_", "")
    if key in ("classic", "logisim", "2", "2.7", "2.7.1"):
        return CircFormat.CLASSIC
    return CircFormat.EVOLUTION


def detect_format_from_source(source: str | None) -> CircFormat:
    """
    Infer format from <project source="...">.

    Classic Logisim uses 2.x (e.g. 2.7.1). Evolution uses 3.x+ (e.g. 3.8.0).
    Unknown / missing → evolution (most common today).
    """
    if not source:
        return CircFormat.EVOLUTION
    text = source.strip()
    if text.startswith("2.") or text == "2":
        return CircFormat.CLASSIC
    return CircFormat.EVOLUTION


def detect_format(project: Project) -> CircFormat:
    """Detect format from an opened Project."""
    return detect_format_from_source(project.root.get("source"))


def detect_format_bytes(raw: bytes) -> CircFormat:
    """Best-effort detect from raw .circ bytes without full parse."""
    import re

    m = re.search(
        rb"""<project\b[^>]*\bsource\s*=\s*(["'])(?P<val>.*?)\1""",
        raw[:4000],
        re.IGNORECASE | re.DOTALL,
    )
    if m is None:
        return CircFormat.EVOLUTION
    return detect_format_from_source(m.group("val").decode("utf-8", errors="replace"))


def format_label(fmt: CircFormat) -> str:
    if fmt == CircFormat.CLASSIC:
        return "Logisim (classic)"
    return "Logisim Evolution"


def project_source_attr(fmt: CircFormat) -> bytes:
    if fmt == CircFormat.CLASSIC:
        return b"2.7.1"
    return b"3.8.0"


def wrap_circuit_as_project(circuit_xml: bytes, fmt: CircFormat = CircFormat.EVOLUTION) -> bytes:
    """Minimal valid project wrapper for validating a lone <circuit> snippet."""
    source = project_source_attr(fmt)
    return (
        b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        b'<project source="'
        + source
        + b'" version="1.0">\n'
        b'<lib desc="#Wiring" name="0"/>\n'
        b'<lib desc="#Gates" name="1"/>\n'
        b'<main name="x"/>\n'
        b"<options/>\n"
        + circuit_xml
        + b"\n</project>\n"
    )


def normalize_newlines(data: bytes) -> bytes:
    """Normalize CRLF/CR to LF for cross-OS comparisons."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def config_dir() -> Path:
    """
    OS-appropriate config directory for Circuit Vault.

    - Windows: %APPDATA%\\circuit-vault
    - macOS / Linux: $XDG_CONFIG_HOME/circuit-vault or ~/.config/circuit-vault
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "circuit-vault"
        return Path.home() / "AppData" / "Roaming" / "circuit-vault"

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "circuit-vault"
    return Path.home() / ".config" / "circuit-vault"


def credential_store_name() -> str:
    """Human label for where keyring stores the GitHub token on this OS."""
    if sys.platform == "darwin":
        return "macOS Keychain"
    if sys.platform == "win32":
        return "Windows Credential Manager"
    return "system keyring (Secret Service)"


def quit_shortcut_hint() -> str:
    if sys.platform == "darwin":
        return "Cmd+Q"
    return "Alt+F4 or close the window"
