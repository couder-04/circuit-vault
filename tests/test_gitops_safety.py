"""Guards against unsafe git roots (e.g. home) and setup crash paths."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from circuit_vault import gitops
from circuit_vault.core import CircuitVaultApp, save_session
from tests.conftest import MAIN


def test_refuse_home_as_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    with pytest.raises(gitops.GitError) as exc:
        gitops.ensure_repo(tmp_path)
    msg = str(exc.value)
    assert "home folder" in msg.lower()
    assert "How to fix:" in msg
    assert "How to restart:" in msg


def test_open_project_in_home_is_friendly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    circ = tmp_path / "main.circ"
    shutil.copy2(MAIN, circ)
    app = CircuitVaultApp()
    result = app.open_project(circ)
    assert not result.ok
    assert "home folder" in result.message.lower()
    assert "How to restart:" in result.message


def test_setup_repo_commit_error_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    work = tmp_path / "work"
    work.mkdir()
    circ = work / "main.circ"
    shutil.copy2(MAIN, circ)
    app = CircuitVaultApp()
    assert app.open_project(circ).ok
    save_session(setup_complete=False)

    with (
        patch("circuit_vault.core.keyring.set_password"),
        patch("circuit_vault.core.keyring.get_password", return_value="tok"),
        patch(
            "circuit_vault.gitops.commit",
            side_effect=gitops.GitError(
                "warning: could not open directory 'Pictures/Photo Booth Library/': "
                "Operation not permitted"
            ),
        ),
    ):
        result = app.setup_repo(
            "https://example.com/lab.git", "T", "t@t", "tok", test_push=True
        )
    assert not result.ok
    assert "Operation not permitted" in result.message or "How to fix:" in result.message


def test_commit_error_message_rewrites_macos_noise(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "lab"
    work.mkdir()
    raw = (
        "warning: could not open directory 'Pictures/Photo Booth Library/': "
        "Operation not permitted\n"
        "warning: could not open directory '.Trash/': Operation not permitted"
    )
    msg = gitops._commit_error_message(raw, work)  # noqa: SLF001
    assert "How to fix:" in msg
    assert "How to restart:" in msg
    assert "Photo Booth" not in msg or "macOS permission" in msg
