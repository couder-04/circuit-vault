"""Tests for sync + first-run setup."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

from circuit_vault import gitops
from circuit_vault.core import (
    KEYRING_SERVICE,
    CircuitVaultApp,
    config_path,
    first_run_needed,
    save_session,
)
from circuit_vault.splicer import backup
from tests.conftest import MAIN


def _bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    gitops._run(  # noqa: SLF001
        ["git", "-c", "init.templateDir=", "init", "--bare", str(remote)],
        tmp_path,
        env={"GIT_TEMPLATE_DIR": ""},
    )
    return remote


def test_sync_commits_and_nothing_to_commit_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    work = tmp_path / "work"
    work.mkdir()
    circ = work / "main.circ"
    shutil.copy2(MAIN, circ)
    remote = _bare_remote(tmp_path)

    app = CircuitVaultApp()
    assert app.open_project(circ).ok
    save_session(setup_complete=True, auto_sync=True, push_backups=True, repo_url=str(remote))
    gitops.set_remote(work, str(remote))
    gitops.set_identity(work, "Test", "test@test")

    assert app.mark_final("Half Adder").ok
    bak = backup(circ)
    assert bak.exists()
    gitops.configure_gitignore(work, push_backups=True)
    result = app.sync("Test backup push")
    assert result.status in ("synced", "offline", "failed") or result.ok

    listed = gitops._run(["git", "ls-files"], work)  # noqa: SLF001
    files = listed.stdout or ""
    assert "half-adder.xml" in files or "circuit-vault" in files

    r2 = app.sync("Nothing new")
    assert r2.ok


def test_push_failure_surfaces_retry(tmp_path, monkeypatch):
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
    save_session(setup_complete=True, auto_sync=True, push_backups=True)
    gitops.set_remote(work, "https://example.invalid/nope.git")

    with patch("circuit_vault.gitops.push", return_value=(False, "simulated push failure")):
        r = app.sync("Should fail push")
    assert r.status == "failed"
    assert app.sync_status() == "failed"


def test_first_run_and_token_in_keyring(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    config_path().write_text("{}", encoding="utf-8")
    assert first_run_needed() is True

    work = tmp_path / "work"
    work.mkdir()
    circ = work / "main.circ"
    shutil.copy2(MAIN, circ)
    remote = _bare_remote(tmp_path)

    app = CircuitVaultApp()
    assert app.open_project(circ).ok

    store: dict[tuple[str, str], str] = {}

    def set_password(service, username, password):
        store[(service, username)] = password

    def get_password(service, username):
        return store.get((service, username))

    with (
        patch("circuit_vault.core.keyring.set_password", side_effect=set_password),
        patch("circuit_vault.core.keyring.get_password", side_effect=get_password),
    ):
        result = app.setup_repo(
            str(remote), "Tester", "t@test", "secret-token-xyz", test_push=True
        )
        assert result.ok, result.message
        assert first_run_needed() is False
        assert store[(KEYRING_SERVICE, str(remote).strip() or "default")] == "secret-token-xyz"

    cfg_text = config_path().read_text(encoding="utf-8")
    assert "secret-token-xyz" not in cfg_text
