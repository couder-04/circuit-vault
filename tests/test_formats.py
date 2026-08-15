"""Tests for format detection and cross-platform helpers."""

from __future__ import annotations

import os
from pathlib import Path

from circuit_vault.formats import (
    CircFormat,
    config_dir,
    credential_store_name,
    detect_format,
    detect_format_bytes,
    detect_format_from_source,
    normalize_format,
    normalize_newlines,
    wrap_circuit_as_project,
)
from circuit_vault.parser import extract_circuit_raw_bytes, load
from circuit_vault.promptgen import generate_prompt, validate_generated
from circuit_vault.validator import HealthState, circuit_health
from tests.conftest import CLASSIC, MAIN


def test_detect_source_versions():
    assert detect_format_from_source("2.7.1") == CircFormat.CLASSIC
    assert detect_format_from_source("2.0") == CircFormat.CLASSIC
    assert detect_format_from_source("3.8.0") == CircFormat.EVOLUTION
    assert detect_format_from_source(None) == CircFormat.EVOLUTION


def test_detect_fixtures():
    assert detect_format(load(CLASSIC)) == CircFormat.CLASSIC
    assert detect_format(load(MAIN)) == CircFormat.EVOLUTION
    assert detect_format_bytes(CLASSIC.read_bytes()) == CircFormat.CLASSIC
    assert detect_format_bytes(MAIN.read_bytes()) == CircFormat.EVOLUTION


def test_normalize_format_aliases():
    assert normalize_format("classic") == CircFormat.CLASSIC
    assert normalize_format("Logisim") == CircFormat.CLASSIC
    assert normalize_format("evolution") == CircFormat.EVOLUTION
    assert normalize_format("auto") == CircFormat.EVOLUTION  # unknown → evolution


def test_wrap_uses_matching_source():
    circ = b'<circuit name="X"/>'
    classic = wrap_circuit_as_project(circ, CircFormat.CLASSIC)
    evo = wrap_circuit_as_project(circ, CircFormat.EVOLUTION)
    assert b'source="2.7.1"' in classic
    assert b'source="3.8.0"' in evo


def test_classic_and_evolution_prompts():
    classic_prompt = generate_prompt("AND", ["AND Gate"], "A,B", "Y", "classic")
    evo_prompt = generate_prompt("XOR", ["XOR Gate"], "A,B", "Y", "evolution")
    assert "classic" in classic_prompt.lower()
    assert "appear" in classic_prompt.lower() or "clabel" in classic_prompt.lower()
    assert "Evolution" in evo_prompt
    assert 'name="Pin"' in classic_prompt
    assert 'name="Pin"' in evo_prompt


def test_validate_classic_snippet():
    project = load(CLASSIC)
    good = extract_circuit_raw_bytes(project, "AND2")
    ok, preview = validate_generated(good, target_format="classic")
    assert ok
    assert preview["name"] == "AND2"
    assert preview["format"] == "classic"


def test_health_ignores_crlf_difference(tmp_path):
    project = load(MAIN)
    current = extract_circuit_raw_bytes(project, "Half Adder")
    final_crlf = current.replace(b"\n", b"\r\n")
    assert normalize_newlines(current) == normalize_newlines(final_crlf)
    assert circuit_health(project, "Half Adder", final_crlf) == HealthState.HEALTHY


def test_config_dir_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("APPDATA", raising=False)
    # Force non-Windows branch if somehow on win in CI
    if os.name != "nt":
        assert config_dir() == tmp_path / "xdg" / "circuit-vault"


def test_credential_store_name_nonempty():
    assert len(credential_store_name()) > 3
