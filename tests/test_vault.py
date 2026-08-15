"""Tests for vault.py."""

from __future__ import annotations

from circuit_vault.parser import extract_circuit_raw_bytes, load
from circuit_vault.vault import Vault
from tests.conftest import MAIN


def test_slug_stable():
    assert Vault.slug("Full Adder 32-bit") == "full-adder-32-bit"
    assert Vault.slug("Half Adder") == "half-adder"
    assert Vault.slug("  ALU  ") == "alu"


def test_save_load_final_round_trips_exact_bytes(tmp_path):
    project = load(MAIN)
    xml = extract_circuit_raw_bytes(project, "Full Adder")
    vault = Vault(tmp_path)
    path = vault.save_final("Full Adder", xml)
    assert path.name == "full-adder.xml"
    loaded = vault.load_final("Full Adder")
    assert loaded == xml
    assert "full-adder" in vault.list_finals()
