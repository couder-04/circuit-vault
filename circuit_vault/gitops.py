"""Git operations via the git CLI (subprocess). No libgit2."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(Exception):
    """Raised for unexpected git failures (not 'nothing to commit')."""


_MACOS_PERM_RE = re.compile(
    r"warning:\s*could not open directory\s+'[^']+'\s*:\s*Operation not permitted",
    re.IGNORECASE,
)


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


def _resolve(dir_path: str | Path) -> Path:
    return Path(dir_path).expanduser().resolve()


def is_home_dir(dir_path: str | Path) -> bool:
    """True if *dir_path* is the user's home directory (unsafe as a git root)."""
    try:
        return _resolve(dir_path) == Path.home().resolve()
    except OSError:
        return False


def _home_repo_error() -> GitError:
    return GitError(
        "Your .circ is in your home folder. Circuit Vault needs a dedicated project "
        "folder — using home makes Git try to track the whole user directory "
        "(and on macOS that often hits Photos/Trash permission errors).\n\n"
        "How to fix:\n"
        "1. Create a project folder (example: Documents/my-lab or Desktop/my-lab).\n"
        "2. Move your .circ into that folder.\n"
        "3. Open that .circ in Circuit Vault again, then re-link GitHub if asked.\n\n"
        "How to restart:\n"
        "Quit Circuit Vault, then run: circuit-vault gui"
    )


def _assert_safe_repo_root(dir_path: str | Path) -> Path:
    d = _resolve(dir_path)
    if is_home_dir(d):
        raise _home_repo_error()
    return d


def _strip_macos_perm_warnings(text: str) -> str:
    """Remove macOS TCC noise so we can see the real git error (if any)."""
    cleaned = _MACOS_PERM_RE.sub("", text or "")
    return "\n".join(ln for ln in cleaned.splitlines() if ln.strip()).strip()


def _commit_error_message(raw: str, dir_path: Path) -> str:
    cleaned = _strip_macos_perm_warnings(raw)
    low = (cleaned or raw or "").lower()
    if (
        "operation not permitted" in low
        or "could not open directory" in low
        or (not cleaned and "operation not permitted" in (raw or "").lower())
    ):
        if is_home_dir(dir_path):
            return str(_home_repo_error())
        return (
            "Git hit a folder permission error while reading near your project "
            "(common on macOS when the project is too close to home).\n\n"
            "How to fix:\n"
            "1. Put your .circ in its own project folder (not your home folder).\n"
            "2. If you already have a .git folder in home by mistake, ignore it — "
            "use the project folder instead.\n"
            "3. Open the .circ from that project folder and try linking GitHub again.\n\n"
            "How to restart:\n"
            "Quit Circuit Vault, then run: circuit-vault gui"
        )
    if "author identity unknown" in low or "please tell me who you are" in low:
        return (
            "Git needs a name and email before it can commit.\n\n"
            "How to fix:\n"
            "Enter a commit name and email in the Setup / Settings screen, then try again.\n\n"
            "How to restart:\n"
            "Quit Circuit Vault, then run: circuit-vault gui"
        )
    return cleaned or raw or "git commit failed"


def is_repo(dir_path: str | Path) -> bool:
    """True only if *this* directory is a git work tree root (not a parent repo)."""
    d = _resolve(dir_path)
    result = _run(["git", "rev-parse", "--show-toplevel"], d)
    if result.returncode != 0:
        return False
    top = Path((result.stdout or "").strip()).resolve()
    return top == d


def ensure_repo(dir_path: str | Path, *, push_backups: bool = True) -> None:
    """git init if needed; configure .gitignore for backup policy."""
    d = _assert_safe_repo_root(dir_path)
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
    d = _assert_safe_repo_root(dir_path)
    ensure_repo(d)
    if name:
        _run(["git", "config", "user.name", name], d)
    if email:
        _run(["git", "config", "user.email", email], d)


def set_remote(dir_path: str | Path, url: str, *, token: str | None = None) -> None:
    """Set origin remote. If token given, embed for HTTPS push."""
    d = _assert_safe_repo_root(dir_path)
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
    d = _assert_safe_repo_root(dir_path)
    ensure_repo(d)
    add = _run(["git", "add", "-A"], d)
    add_err = _strip_macos_perm_warnings((add.stderr or "").strip())
    if add.returncode != 0 and add_err:
        raise GitError(_commit_error_message(add.stderr or add_err, d))
    status = _run(["git", "status", "--porcelain"], d)
    if not (status.stdout or "").strip():
        return False
    result = _run(["git", "commit", "-m", message], d)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if "nothing to commit" in err.lower():
            return False
        raise GitError(_commit_error_message(err, d))
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
