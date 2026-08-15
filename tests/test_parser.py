"""Tests for parser.py."""

from __future__ import annotations

import pytest

from circuit_vault.parser import (
    ParseError,
    extract_circuit_raw_bytes,
    find_circuit_span,
    list_circuits,
    load,
    serialize,
)
from tests.conftest import CLASSIC, MAIN


def test_load_lists_circuits_in_order():
    project = load(MAIN)
    assert list_circuits(project) == [
        "Half Adder",
        "Full Adder",
        "Full Adder 32-bit",
        "Multiplier 4-bit",
        "ALU",
    ]


def test_round_trip_whole_file_byte_stable():
    project = load(MAIN)
    assert serialize(project) == MAIN.read_bytes()


def test_extract_single_circuit_exactly():
    project = load(MAIN)
    raw = MAIN.read_bytes()
    start, end = find_circuit_span(raw, "Full Adder")
    expected = raw[start:end]
    got = extract_circuit_raw_bytes(project, "Full Adder")
    assert got == expected
    assert got.startswith(b"<circuit")
    assert b'name="Full Adder"' in got
    assert got.rstrip().endswith(b"</circuit>")


def test_classic_fixture_parses():
    project = load(CLASSIC)
    assert list_circuits(project) == ["AND2", "Demo"]
    assert serialize(project) == CLASSIC.read_bytes()


def test_malformed_xml_raises_parse_error(tmp_path):
    bad = tmp_path / "bad.circ"
    bad.write_text("<project><circuit></project>", encoding="utf-8")
    with pytest.raises(ParseError):
        load(bad)
