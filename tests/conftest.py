"""Shared test helpers."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
MAIN = FIXTURES / "main.circ"
MAIN_CORRUPTED = FIXTURES / "main_corrupted.circ"
CLASSIC = FIXTURES / "classic.circ"
CLASSIC_CORRUPTED = FIXTURES / "classic_corrupted.circ"
