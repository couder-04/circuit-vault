"""Per-circuit canonical finals stored under circuit-vault/."""

from __future__ import annotations

import re
from pathlib import Path


class Vault:
    """Filesystem vault for exact circuit XML finals."""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.vault_dir = self.project_dir / "circuit-vault"

    def ensure(self) -> Path:
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        return self.vault_dir

    @staticmethod
    def slug(name: str) -> str:
        """Filesystem-safe slug: 'Full Adder 32-bit' -> 'full-adder-32-bit'."""
        s = name.strip().lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        s = re.sub(r"-{2,}", "-", s).strip("-")
        return s or "circuit"

    def _path_for(self, name: str) -> Path:
        return self.vault_dir / f"{self.slug(name)}.xml"

    def save_final(self, name: str, xml_bytes: bytes) -> Path:
        """Write exact circuit bytes to circuit-vault/<slug>.xml."""
        self.ensure()
        path = self._path_for(name)
        path.write_bytes(xml_bytes)
        return path

    def load_final(self, name: str) -> bytes | None:
        path = self._path_for(name)
        if not path.exists():
            return None
        return path.read_bytes()

    def list_finals(self) -> list[str]:
        """Return slugs (stem names) of stored finals, sorted."""
        if not self.vault_dir.exists():
            return []
        return sorted(p.stem for p in self.vault_dir.glob("*.xml"))

    def has_final(self, name: str) -> bool:
        return self._path_for(name).exists()

    def final_path(self, name: str) -> Path:
        return self._path_for(name)
