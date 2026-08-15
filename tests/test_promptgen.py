"""Tests for promptgen.py."""

from __future__ import annotations

from circuit_vault.promptgen import components_catalog, generate_prompt, validate_generated
from tests.conftest import MAIN
from circuit_vault.parser import extract_circuit_raw_bytes, load


def test_catalog_has_groups():
    cat = components_catalog()
    assert "BASIC" in cat
    assert "AND Gate" in cat["BASIC"]
    assert "Pin" in cat["WIRING"]


def test_prompt_contains_skeleton_and_components():
    prompt = generate_prompt(
        "build a half adder",
        ["AND Gate", "XOR Gate", "my_custom_block"],
        "A, B",
        "Sum, Carry",
        "evolution",
    )
    assert "<circuit" in prompt
    assert "AND Gate" in prompt
    assert "XOR Gate" in prompt
    assert "my_custom_block" in prompt
    assert "ONLY" in prompt or "only" in prompt.lower()
    # Real skeleton fragment from packaged skeleton / fixture
    assert 'name="Pin"' in prompt or "Half Adder" in prompt


def test_classic_prompt_path():
    prompt = generate_prompt(
        "two-input AND",
        ["AND Gate"],
        "A, B",
        "Y",
        "classic",
    )
    assert "classic" in prompt.lower()
    assert "AND2" in prompt or 'name="Pin"' in prompt
    assert "Do NOT emit <appear>" in prompt or "appear" in prompt.lower()


def test_validate_generated_accepts_good_and_rejects_junk():
    project = load(MAIN)
    good = extract_circuit_raw_bytes(project, "Half Adder")
    ok, preview = validate_generated(good, target_format="evolution")
    assert ok
    assert preview["name"] == "Half Adder"
    assert preview["component_count"] > 0

    bad_ok, bad_preview = validate_generated(b"<not-a-circuit/>")
    assert not bad_ok
    assert bad_preview.get("error")
