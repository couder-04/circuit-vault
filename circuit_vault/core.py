"""High-level API shared by the CLI and GUI. No UI framework imports."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import keyring

from circuit_vault import gitops
from circuit_vault.dependencies import resolve_restore_set
from circuit_vault.formats import (
    CircFormat,
    config_dir,
    detect_format,
    format_label,
    normalize_format,
)
from circuit_vault.importer import IncomingCircuit, MergeResult, merge as importer_merge
from circuit_vault.importer import scan_incoming
from circuit_vault.parser import (
    ParseError,
    Project,
    extract_circuit_raw_bytes,
    list_circuits,
    load,
    rename_circuit_xml,
)
from circuit_vault.promptgen import generate_prompt, validate_generated
from circuit_vault.splicer import SpliceError, backup, insert_circuit, splice
from circuit_vault.validator import (
    HealthState,
    circuit_health,
    validate_circuit,
    validate_project,
)
from circuit_vault.vault import Vault

KEYRING_SERVICE = "circuit-vault"


def config_path() -> Path:
    base = config_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "config.json"


def load_session() -> dict:
    cfg = config_path()
    if not cfg.exists():
        return {}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def save_session(**updates: object) -> None:
    data = load_session()
    for key, value in updates.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = str(value) if isinstance(value, Path) else value
    config_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def last_circ_path() -> Path | None:
    raw = load_session().get("last_circ")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.exists() else None


def first_run_needed() -> bool:
    data = load_session()
    return not bool(data.get("setup_complete"))


def _token_key(repo_url: str) -> str:
    return repo_url.strip() or "default"


@dataclass
class CircuitStatus:
    name: str
    health: HealthState
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class OpenResult:
    ok: bool
    path: Path | None = None
    message: str = ""
    circuit_count: int = 0
    circ_format: CircFormat | None = None


@dataclass
class MarkFinalResult:
    ok: bool
    name: str
    message: str = ""
    overwritten: bool = False


@dataclass
class RestoreResult:
    ok: bool
    restored: list[str] = field(default_factory=list)
    backup_path: Path | None = None
    message: str = ""


@dataclass
class UndoResult:
    ok: bool
    backup_path: Path | None = None
    message: str = ""


@dataclass
class SyncResult:
    ok: bool
    status: str  # synced | failed | offline | skipped
    message: str = ""
    committed: bool = False


@dataclass
class SetupResult:
    ok: bool
    message: str = ""


@dataclass
class BuildMergeResult:
    ok: bool
    name: str | None = None
    backup_path: Path | None = None
    message: str = ""
    preview: dict = field(default_factory=dict)


class CircuitVaultApp:
    """Session object: one open .circ project + its vault + git repo."""

    def __init__(self) -> None:
        self.circ_path: Path | None = None
        self.project: Project | None = None
        self.vault: Vault | None = None
        self._last_backup: Path | None = None
        self._sync_status: str = "offline"
        self._last_sync_message: str = ""
        self._known_circs: list[Path] = []

    @property
    def project_dir(self) -> Path:
        if self.circ_path is None:
            raise RuntimeError("No project open")
        return self.circ_path.parent

    def _settings(self) -> dict:
        data = load_session()
        return {
            "auto_sync": data.get("auto_sync", True),
            "push_backups": data.get("push_backups", True),
            "repo_url": data.get("repo_url", ""),
            "git_name": data.get("git_name", ""),
            "git_email": data.get("git_email", ""),
        }

    def open_project(self, circ_path: str | Path) -> OpenResult:
        path = Path(circ_path).resolve()
        if not path.exists():
            return OpenResult(ok=False, message=f"File not found: {path}")
        try:
            project = load(path)
        except ParseError as exc:
            return OpenResult(ok=False, path=path, message=str(exc))

        vault = Vault(path.parent)
        vault.ensure()
        settings = self._settings()
        try:
            gitops.ensure_repo(path.parent, push_backups=bool(settings["push_backups"]))
        except gitops.GitError as exc:
            return OpenResult(ok=False, path=path, message=f"Git setup failed: {exc}")

        self.circ_path = path
        self.project = project
        self.vault = vault
        save_session(last_circ=path)
        self._remember_circ(path)
        bak_raw = load_session().get("last_backup")
        if bak_raw:
            bak = Path(str(bak_raw))
            self._last_backup = bak if bak.exists() else None
        names = list_circuits(project)
        fmt = detect_format(project)
        return OpenResult(
            ok=True,
            path=path,
            message=(
                f"Opened {path.name} ({len(names)} circuits, {format_label(fmt)})"
            ),
            circuit_count=len(names),
            circ_format=fmt,
        )

    def project_format(self) -> CircFormat:
        """Format of the open project, or preferred session default."""
        if self.project is not None:
            return detect_format(self.project)
        return normalize_format(load_session().get("preferred_format"))

    def _remember_circ(self, path: Path) -> None:
        path = path.resolve()
        known = [Path(p) for p in load_session().get("known_circs", []) if Path(p).exists()]
        if path not in known:
            known.append(path)
        # Keep unique, most recent last
        uniq: list[Path] = []
        for p in known:
            if p not in uniq:
                uniq.append(p)
        save_session(known_circs=[str(p) for p in uniq[-20:]])
        self._known_circs = uniq

    def list_target_circ_files(self) -> list[Path]:
        known = [Path(p) for p in load_session().get("known_circs", []) if Path(p).exists()]
        if self.circ_path and self.circ_path not in known:
            known.append(self.circ_path)
        if self.circ_path:
            # Active first
            known = [self.circ_path] + [p for p in known if p != self.circ_path]
        return known

    def _require_open(self) -> None:
        if self.project is None or self.vault is None or self.circ_path is None:
            raise RuntimeError("No project open — call open_project first")

    def reload(self) -> None:
        self._require_open()
        assert self.circ_path is not None
        self.project = load(self.circ_path)

    def status(self) -> list[CircuitStatus]:
        self._require_open()
        assert self.project is not None and self.vault is not None
        out: list[CircuitStatus] = []
        for name in list_circuits(self.project):
            final = self.vault.load_final(name)
            health = circuit_health(self.project, name, final)
            vr = validate_circuit(self.project, name)
            out.append(
                CircuitStatus(
                    name=name,
                    health=health,
                    errors=list(vr.errors),
                    warnings=list(vr.warnings),
                )
            )
        return out

    # ----- sync / setup -----

    def sync_status(self) -> str:
        return self._sync_status

    def sync_message(self) -> str:
        return self._last_sync_message

    def sync(self, message: str) -> SyncResult:
        """Unified auto add/commit/push. Nothing needed stays local when configured."""
        settings = self._settings()
        if not settings["auto_sync"]:
            self._sync_status = "skipped"
            self._last_sync_message = "Auto-sync is off"
            return SyncResult(ok=True, status="skipped", message=self._last_sync_message)

        if self.circ_path is None:
            self._sync_status = "failed"
            self._last_sync_message = "No project open"
            return SyncResult(ok=False, status="failed", message=self._last_sync_message)

        dig = self.project_dir
        gitops.configure_gitignore(dig, push_backups=bool(settings["push_backups"]))
        try:
            committed = gitops.commit(dig, message)
        except gitops.GitError as exc:
            self._sync_status = "failed"
            self._last_sync_message = str(exc)
            return SyncResult(ok=False, status="failed", message=str(exc))

        if not gitops.has_remote(dig):
            self._sync_status = "offline"
            self._last_sync_message = "Saved locally — no GitHub repo linked yet"
            return SyncResult(
                ok=True, status="offline", message=self._last_sync_message, committed=committed
            )

        ok, msg = gitops.push(dig)
        if ok:
            self._sync_status = "synced"
            self._last_sync_message = "Synced to GitHub"
            return SyncResult(ok=True, status="synced", message=self._last_sync_message, committed=committed)

        # Classify offline vs failed
        low = msg.lower()
        if any(x in low for x in ("could not resolve", "network", "timed out", "offline")):
            self._sync_status = "offline"
        else:
            self._sync_status = "failed"
        self._last_sync_message = msg
        return SyncResult(ok=False, status=self._sync_status, message=msg, committed=committed)

    def retry_sync(self) -> SyncResult:
        return self.sync("Retry sync")

    def setup_repo(
        self,
        remote_url: str,
        name: str = "",
        email: str = "",
        token: str = "",
        *,
        test_push: bool = True,
    ) -> SetupResult:
        """First-run / change-repo setup. Token goes to keyring only."""
        if self.circ_path is None:
            # Allow setup against last_circ or require open — use cwd fallback via last
            path = last_circ_path()
            if path is None:
                return SetupResult(ok=False, message="Open a .circ file before linking GitHub")
            opened = self.open_project(path)
            if not opened.ok:
                return SetupResult(ok=False, message=opened.message)

        assert self.circ_path is not None
        dig = self.project_dir
        settings = self._settings()
        try:
            gitops.ensure_repo(dig, push_backups=bool(settings["push_backups"]))
            if name or email:
                gitops.set_identity(dig, name or "Circuit Vault", email or "circuit-vault@localhost")
            if token:
                keyring.set_password(KEYRING_SERVICE, _token_key(remote_url), token)
            stored = keyring.get_password(KEYRING_SERVICE, _token_key(remote_url)) or token
            gitops.set_remote(dig, remote_url, token=stored or None)
        except Exception as exc:  # noqa: BLE001
            return SetupResult(ok=False, message=str(exc))

        save_session(
            repo_url=remote_url,
            git_name=name,
            git_email=email,
            setup_complete=True,
            auto_sync=True,
            push_backups=True,
        )

        if test_push:
            # Ensure at least one commit exists
            gitops.commit(dig, "Link Circuit Vault to GitHub")
            ok, msg = gitops.push(dig)
            if not ok:
                self._sync_status = "failed"
                return SetupResult(ok=False, message=f"Linked, but test push failed: {msg}")

        self._sync_status = "synced"
        return SetupResult(ok=True, message="GitHub linked — future changes sync automatically")

    def update_settings(self, **kwargs: object) -> None:
        allowed = {
            "auto_sync",
            "push_backups",
            "repo_url",
            "git_name",
            "git_email",
            "setup_complete",
            "preferred_format",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if updates:
            save_session(**updates)
        if self.circ_path and "push_backups" in updates:
            gitops.configure_gitignore(
                self.project_dir, push_backups=bool(updates["push_backups"])
            )

    def store_token(self, token: str, repo_url: str | None = None) -> None:
        url = repo_url or str(load_session().get("repo_url", ""))
        keyring.set_password(KEYRING_SERVICE, _token_key(url), token)

    # ----- existing actions (now end in sync) -----

    def mark_final(self, name: str) -> MarkFinalResult:
        self._require_open()
        assert self.project is not None and self.vault is not None

        names = list_circuits(self.project)
        if name not in names:
            return MarkFinalResult(ok=False, name=name, message=f"Unknown circuit: {name}")

        vr = validate_circuit(self.project, name)
        if not vr.ok:
            return MarkFinalResult(
                ok=False,
                name=name,
                message=(
                    f"Cannot mark {name!r} as final — it has problems: "
                    + "; ".join(vr.errors)
                ),
            )

        overwritten = self.vault.has_final(name)
        xml_bytes = extract_circuit_raw_bytes(self.project, name)
        self.vault.save_final(name, xml_bytes)

        self.sync(f"Saved final for {name}" if not overwritten else f"Updated final for {name}")

        action = "Updated" if overwritten else "Saved"
        return MarkFinalResult(
            ok=True,
            name=name,
            overwritten=overwritten,
            message=f"{action} final version of {name!r}",
        )

    def restore(self, name: str) -> RestoreResult:
        self._require_open()
        assert self.project is not None and self.vault is not None and self.circ_path is not None

        names = set(list_circuits(self.project))
        if name not in names:
            return RestoreResult(ok=False, message=f"Unknown circuit: {name}")

        if self.vault.load_final(name) is None:
            return RestoreResult(
                ok=False,
                message=f"No saved final for {name!r} — mark one first",
            )

        try:
            restore_order = resolve_restore_set(self.project, name)
        except KeyError as exc:
            return RestoreResult(ok=False, message=str(exc))

        missing_finals = [n for n in restore_order if self.vault.load_final(n) is None]
        if missing_finals:
            return RestoreResult(
                ok=False,
                message=(
                    "Cannot restore — missing saved finals for: "
                    + ", ".join(missing_finals)
                ),
            )

        bak = backup(self.circ_path)
        self._last_backup = bak
        save_session(last_backup=bak)

        project = self.project
        restored: list[str] = []
        try:
            for circ_name in restore_order:
                final = self.vault.load_final(circ_name)
                assert final is not None
                project = splice(project, circ_name, final)
                restored.append(circ_name)

            vr = validate_project(project)
            if not vr.ok:
                return RestoreResult(
                    ok=False,
                    backup_path=bak,
                    restored=[],
                    message=(
                        "Restore aborted — result would be invalid: "
                        + "; ".join(vr.errors)
                    ),
                )

            self.circ_path.write_bytes(project.raw_bytes)
            self.project = project
        except (SpliceError, ParseError, KeyError) as exc:
            return RestoreResult(
                ok=False,
                backup_path=bak,
                message=f"Restore failed: {exc}",
            )

        label = ", ".join(restored)
        self.sync(f"Restored {label}")

        return RestoreResult(
            ok=True,
            restored=restored,
            backup_path=bak,
            message=(
                f"Restored {label}. Your other circuits were left untouched. "
                f"Backup saved as {bak.name}."
            ),
        )

    def undo_last_restore(self) -> UndoResult:
        return self.undo()

    def undo(self) -> UndoResult:
        self._require_open()
        assert self.circ_path is not None

        bak = self._last_backup or _find_latest_backup(self.circ_path)
        if bak is None or not bak.exists():
            return UndoResult(ok=False, message="No backup found to undo")

        shutil.copy2(bak, self.circ_path)
        try:
            self.project = load(self.circ_path)
        except ParseError as exc:
            return UndoResult(
                ok=False,
                backup_path=bak,
                message=f"Undo copied backup but reload failed: {exc}",
            )

        self._last_backup = None
        save_session(last_backup=None)
        self.sync("Undid last restore")

        return UndoResult(
            ok=True,
            backup_path=bak,
            message=f"Restored file from backup {bak.name}",
        )

    def history(self, limit: int = 20) -> list[str]:
        self._require_open()
        return gitops.log(self.project_dir, limit=limit)

    def plain_status_summary(self) -> str:
        statuses = self.status()
        broken = [s for s in statuses if s.health == HealthState.BROKEN]
        changed = [s for s in statuses if s.health == HealthState.CHANGED]
        if broken:
            s = broken[0]
            return f"{s.name} is broken — click Restore to fix."
        if changed:
            s = changed[0]
            return (
                f"{s.name} doesn't match its saved final — "
                "click Mark Final to update, or Restore to revert."
            )
        no_final = [s for s in statuses if s.health == HealthState.NO_FINAL]
        if no_final:
            return f"{len(no_final)} circuit(s) have no saved final yet."
        return "All circuits look good."

    # ----- import / build -----

    def import_scan(self, path: str | Path) -> list[IncomingCircuit]:
        return scan_incoming(path)

    def import_merge(
        self,
        selected: list[str],
        target: str | Path,
        clash_policy: str = "replace",
        *,
        incoming_path: str | Path,
    ) -> MergeResult:
        result = importer_merge(
            selected,
            target,
            clash_policy,
            incoming_path=incoming_path,
        )
        if result.ok:
            # Reload if target is the active file
            if self.circ_path and Path(target).resolve() == self.circ_path.resolve():
                self.reload()
            if result.backup_path:
                self._last_backup = result.backup_path
                save_session(last_backup=result.backup_path)
            n = len(result.merged)
            self.sync(f"Imported {n} circuit{'s' if n != 1 else ''} from shared file")
        return result

    def build_prompt(
        self,
        description: str,
        components: list[str],
        inputs: str = "",
        outputs: str = "",
        target_format: str | CircFormat | None = None,
    ) -> str:
        fmt = (
            normalize_format(target_format)
            if target_format is not None
            else self.project_format()
        )
        save_session(preferred_format=fmt.value)
        return generate_prompt(description, components, inputs, outputs, fmt)

    def build_merge(self, xml_bytes: bytes, target: str | Path) -> BuildMergeResult:
        target_path = Path(target)
        try:
            target_project = load(target_path)
            fmt = detect_format(target_project)
        except ParseError:
            fmt = self.project_format()
            target_project = None

        ok, preview = validate_generated(xml_bytes, target_format=fmt)
        if not ok:
            return BuildMergeResult(
                ok=False,
                message=preview.get("error") or "Generated XML is not valid",
                preview=preview,
            )

        text = xml_bytes.strip()
        if text.startswith(b"```"):
            lines = text.split(b"\n")
            if lines and lines[0].startswith(b"```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == b"```":
                lines = lines[:-1]
            text = b"\n".join(lines).strip()

        name = preview.get("name") or "Built Circuit"
        try:
            project = target_project if target_project is not None else load(target_path)
        except ParseError as exc:
            return BuildMergeResult(ok=False, message=str(exc), preview=preview)

        bak = backup(target_path)
        self._last_backup = bak
        save_session(last_backup=bak)

        try:
            if name in list_circuits(project):
                # avoid clash — rename incoming
                alt = f"{name} (built)"
                n = 2
                while alt in list_circuits(project):
                    alt = f"{name} (built {n})"
                    n += 1
                text = rename_circuit_xml(text, alt)
                name = alt
                project = insert_circuit(project, text)
            else:
                project = insert_circuit(project, text)

            vr = validate_project(project)
            if not vr.ok:
                return BuildMergeResult(
                    ok=False,
                    backup_path=bak,
                    message="Build merge aborted — invalid result: " + "; ".join(vr.errors),
                    preview=preview,
                )
            target_path.write_bytes(project.raw_bytes)
        except (SpliceError, ParseError) as exc:
            return BuildMergeResult(ok=False, backup_path=bak, message=str(exc), preview=preview)

        if self.circ_path and target_path.resolve() == self.circ_path.resolve():
            self.reload()

        self.sync(f"Built {name} from XML")
        return BuildMergeResult(
            ok=True,
            name=name,
            backup_path=bak,
            message=f"Added {name} to {target_path.name}",
            preview=preview,
        )


_app = CircuitVaultApp()


def get_app() -> CircuitVaultApp:
    return _app


def ensure_session() -> OpenResult | None:
    if _app.circ_path is not None:
        _app.reload()
        return None
    path = last_circ_path()
    if path is None:
        return OpenResult(
            ok=False,
            message="No project open. Run: circuit-vault open <file.circ>",
        )
    return _app.open_project(path)


def open_project(circ_path: str | Path) -> OpenResult:
    return _app.open_project(circ_path)


def status() -> list[CircuitStatus]:
    result = ensure_session()
    if result is not None and not result.ok:
        raise RuntimeError(result.message)
    return _app.status()


def mark_final(name: str) -> MarkFinalResult:
    result = ensure_session()
    if result is not None and not result.ok:
        return MarkFinalResult(ok=False, name=name, message=result.message)
    return _app.mark_final(name)


def restore(name: str) -> RestoreResult:
    result = ensure_session()
    if result is not None and not result.ok:
        return RestoreResult(ok=False, message=result.message)
    return _app.restore(name)


def undo_last_restore() -> UndoResult:
    result = ensure_session()
    if result is not None and not result.ok:
        return UndoResult(ok=False, message=result.message)
    return _app.undo()


def _find_latest_backup(circ_path: Path) -> Path | None:
    pattern = f"{circ_path.name}.*.bak"
    candidates = sorted(circ_path.parent.glob(pattern), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None
