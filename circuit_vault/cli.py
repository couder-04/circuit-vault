"""Typer CLI for Circuit Vault."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from circuit_vault import core
from circuit_vault.formats import credential_store_name
from circuit_vault.validator import HealthState

app = typer.Typer(
    name="circuit-vault",
    help="Protect Logisim .circ files with per-circuit finals and surgical restore.",
    no_args_is_help=True,
)
console = Console()

_HEALTH_STYLE = {
    HealthState.HEALTHY: "green",
    HealthState.CHANGED: "yellow",
    HealthState.BROKEN: "red",
    HealthState.NO_FINAL: "bright_black",
}

_HEALTH_LABEL = {
    HealthState.HEALTHY: "healthy",
    HealthState.CHANGED: "changed",
    HealthState.BROKEN: "broken",
    HealthState.NO_FINAL: "no final",
}


def _ensure_open_or_exit() -> None:
    result = core.ensure_session()
    if result is not None and not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    if core.get_app().circ_path is None:
        console.print("[red]No project open.[/red] Run: circuit-vault open <file.circ>")
        raise typer.Exit(1)


@app.command()
def open(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Path to a .circ file"),
) -> None:
    """Open a Logisim project and remember it for later commands."""
    result = core.open_project(file)
    if not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{result.message}[/green]")
    console.print(f"Remembered for next commands: {result.path}")


@app.command()
def status() -> None:
    """Show each circuit and its health."""
    _ensure_open_or_exit()
    rows = core.get_app().status()
    table = Table(title="Circuits")
    table.add_column("Circuit")
    table.add_column("Health")
    table.add_column("Notes")
    for row in rows:
        style = _HEALTH_STYLE[row.health]
        label = _HEALTH_LABEL[row.health]
        notes = "; ".join(row.errors or row.warnings) if (row.errors or row.warnings) else ""
        table.add_row(row.name, f"[{style}]{label}[/{style}]", notes)
    console.print(table)
    console.print(core.get_app().plain_status_summary())


@app.command("mark")
def mark(name: str = typer.Argument(..., help="Circuit name to mark as final")) -> None:
    """Save the current circuit as the canonical final version."""
    _ensure_open_or_exit()
    result = core.get_app().mark_final(name)
    if not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{result.message}[/green]")


@app.command()
def restore(name: str = typer.Argument(..., help="Circuit name to restore")) -> None:
    """Restore a circuit (and broken dependencies) from its saved final."""
    _ensure_open_or_exit()
    result = core.get_app().restore(name)
    if not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{result.message}[/green]")


@app.command()
def undo() -> None:
    """Undo the last restore by putting the backup file back."""
    _ensure_open_or_exit()
    result = core.get_app().undo()
    if not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{result.message}[/green]")


@app.command("import")
def import_cmd(
    shared: Path = typer.Argument(..., exists=True, help="Shared .circ to import from"),
    into: Optional[Path] = typer.Option(None, "--into", help="Target .circ"),
    select: Optional[str] = typer.Option(None, "--select", help="Comma-separated circuit names"),
    on_clash: str = typer.Option(
        "replace", "--on-clash", help="replace | keep_both | skip"
    ),
) -> None:
    """Scan, repair, and merge circuits from a shared file, then sync."""
    _ensure_open_or_exit()
    app = core.get_app()
    target = into or app.circ_path
    if target is None:
        console.print("[red]No target .circ[/red]")
        raise typer.Exit(1)
    scanned = app.import_scan(shared)
    if select:
        names = [s.strip() for s in select.split(",") if s.strip()]
    else:
        names = [c.name for c in scanned if c.xml_bytes and not c.unfixable_reason]
    for c in scanned:
        tag = "ok"
        if c.repaired:
            tag = "auto-fixed"
        if c.unfixable_reason:
            tag = "couldn't fix"
        console.print(f"  {c.name}: {tag}")
    result = app.import_merge(names, target, on_clash.replace("-", "_"), incoming_path=shared)
    if not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{result.message}[/green]")


@app.command("build-prompt")
def build_prompt_cmd(
    description: str = typer.Argument(...),
    components: str = typer.Option("", "--components", help="Comma-separated component names"),
    inputs: str = typer.Option("", "--inputs"),
    outputs: str = typer.Option("", "--outputs"),
    format: str = typer.Option(
        "auto",
        "--format",
        help="classic | evolution | auto (detect from open .circ)",
    ),
) -> None:
    """Print a Claude prompt for building a circuit."""
    comps = [c.strip() for c in components.split(",") if c.strip()]
    target_format = None if format.strip().lower() == "auto" else format
    if target_format is None:
        # Ensure session is open when auto-detecting
        core.ensure_session()
    prompt = core.get_app().build_prompt(description, comps, inputs, outputs, target_format)
    console.print(prompt)


@app.command("build-merge")
def build_merge_cmd(
    generated: Path = typer.Argument(..., exists=True, help="Generated circuit XML"),
    into: Path = typer.Option(..., "--into", exists=True, help="Target .circ"),
    name: str = typer.Option(
        "",
        "--name",
        help="Circuit name (if taken, a decimal suffix is added)",
    ),
) -> None:
    """Validate generated XML, merge into target, sync."""
    data = generated.read_bytes()
    # Ensure target is known / open for sync dir
    core.open_project(into)
    result = core.get_app().build_merge(data, into, preferred_name=name)
    if not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{result.message}[/green]")


@app.command()
def setup(
    repo: str = typer.Option(..., "--repo", help="GitHub repo URL"),
    name: str = typer.Option("", "--name"),
    email: str = typer.Option("", "--email"),
    token: str = typer.Option(
        "",
        "--token",
        help=f"PAT (stored in {credential_store_name()})",
    ),
) -> None:
    """One-time GitHub link for auto-sync."""
    _ensure_open_or_exit()
    result = core.get_app().setup_repo(repo, name, email, token)
    if not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{result.message}[/green]")


@app.command()
def sync() -> None:
    """Manual add/commit/push (rarely needed)."""
    _ensure_open_or_exit()
    result = core.get_app().sync("Manual sync")
    color = "green" if result.ok else "red"
    console.print(f"[{color}]{result.status}: {result.message}[/{color}]")


@app.command()
def gui() -> None:
    """Launch the Circuit Vault desktop app."""
    from circuit_vault.gui.app import run_gui

    run_gui()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
