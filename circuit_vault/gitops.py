"""Git operations via the git CLI (subprocess). No libgit2."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(Exception):
    """Raised for unexpected git failures (not 'nothing to commit')."""


def _run(
    args: list[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=run_env,
    )


def is_repo(dir_path: str | Path) -> bool:
    """True only if *this* directory is a git work tree root (not a parent repo)."""
    d = Path(dir_path).resolve()
    result = _run(["git", "rev-parse", "--show-toplevel"], d)
    if result.returncode != 0:
        return False
    top = Path((result.stdout or "").strip()).resolve()
    return top == d


def ensure_repo(dir_path: str | Path, *, push_backups: bool = True) -> None:
    """git init if needed; configure .gitignore for backup policy."""
    d = Path(dir_path)
    d.mkdir(parents=True, exist_ok=True)
    if not is_repo(d):
        result = _run(
            ["git", "-c", "init.templateDir=", "init"],
            d,
            env={"GIT_TEMPLATE_DIR": ""},
        )
        if result.returncode != 0:
            raise GitError(result.stderr.strip() or "git init failed")
        _run(["git", "config", "user.email", "circuit-vault@localhost"], d)
        _run(["git", "config", "user.name", "Circuit Vault"], d)

    configure_gitignore(d, push_backups=push_backups)


def configure_gitignore(dir_path: str | Path, *, push_backups: bool = True) -> None:
    """When push_backups is True, do NOT ignore *.bak (v2 default)."""
    d = Path(dir_path)
    gitignore = d / ".gitignore"
    lines: list[str] = []
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()

    if push_backups:
        lines = [ln for ln in lines if ln.strip() != "*.bak"]
    else:
        if "*.bak" not in lines:
            lines.append("*.bak")

    # Always ignore splice temps
    if "*.splice-tmp" not in lines:
        lines.append("*.splice-tmp")
    if "*.scan-tmp" not in lines:
        lines.append("*.scan-tmp")

    text = "\n".join(lines).rstrip() + "\n"
    gitignore.write_text(text, encoding="utf-8")


def set_identity(dir_path: str | Path, name: str, email: str) -> None:
    d = Path(dir_path)
    ensure_repo(d)
    if name:
        _run(["git", "config", "user.name", name], d)
    if email:
        _run(["git", "config", "user.email", email], d)


def set_remote(dir_path: str | Path, url: str, *, token: str | None = None) -> None:
    """Set origin remote. If token given, embed for HTTPS push."""
    d = Path(dir_path)
    ensure_repo(d)
    push_url = _url_with_token(url, token) if token else url
    if has_remote(d):
        result = _run(["git", "remote", "set-url", "origin", push_url], d)
    else:
        result = _run(["git", "remote", "add", "origin", push_url], d)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or "failed to set remote")
    # Store clean URL in config comment via separate remote for display? Keep origin as push URL.


def remote_url(dir_path: str | Path) -> str | None:
    d = Path(dir_path)
    if not has_remote(d):
        return None
    result = _run(["git", "remote", "get-url", "origin"], d)
    if result.returncode != 0:
        return None
    return _strip_token_from_url((result.stdout or "").strip())


def _url_with_token(url: str, token: str) -> str:
    if not token:
        return url
    if url.startswith("git@"):
        return url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def _strip_token_from_url(url: str) -> str:
    parsed = urlparse(url)
    if "@" in (parsed.netloc or "") and ":" in parsed.netloc.split("@")[0]:
        host = parsed.netloc.split("@", 1)[1]
        return urlunparse((parsed.scheme, host, parsed.path, "", "", ""))
    return url


def commit(dir_path: str | Path, message: str) -> bool:
    """
    git add -A && git commit -m.

    Returns True if a commit was created, False if nothing to commit.
    Never raises on empty commit.
    """
    d = Path(dir_path)
    ensure_repo(d)
    _run(["git", "add", "-A"], d)
    status = _run(["git", "status", "--porcelain"], d)
    if not (status.stdout or "").strip():
        return False
    result = _run(["git", "commit", "-m", message], d)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if "nothing to commit" in err.lower():
            return False
        raise GitError(err or "git commit failed")
    return True


def has_remote(dir_path: str | Path) -> bool:
    d = Path(dir_path)
    if not is_repo(d):
        return False
    result = _run(["git", "remote"], d)
    return bool((result.stdout or "").strip())


def push(dir_path: str | Path) -> tuple[bool, str]:
    """
    git push. Returns (ok, message).

    No remote → offline-ish skip. Network/auth failure → failed.
    """
    d = Path(dir_path)
    if not has_remote(d):
        return False, "No remote configured — skipped push"
    # Ensure upstream on first push
    result = _run(["git", "push", "-u", "origin", "HEAD"], d)
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "git push failed").strip()
        return False, msg
    return True, "Pushed successfully"


def log(dir_path: str | Path, limit: int = 20) -> list[str]:
    d = Path(dir_path)
    if not is_repo(d):
        return []
    result = _run(["git", "log", f"-{limit}", "--pretty=format:%s"], d)
    if result.returncode != 0:
        return []
    return [ln for ln in (result.stdout or "").splitlines() if ln.strip()]


def is_dirty(dir_path: str | Path) -> bool:
    d = Path(dir_path)
    if not is_repo(d):
        return False
    result = _run(["git", "status", "--porcelain"], d)
    return bool((result.stdout or "").strip())
