"""Scan and selectively merge circuits from a shared .circ file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from circuit_vault.dependencies import build_graph
from circuit_vault.formats import detect_format, format_label
from circuit_vault.parser import (
    ParseError,
    extract_circuit_raw_bytes,
    list_circuits,
    load,
    rename_circuit_xml,
)
from circuit_vault.repair import repair_circuit, repair_file
from circuit_vault.splicer import SpliceError, backup, insert_circuit, splice
from circuit_vault.validator import (
    HealthState,
    circuit_health,
    is_subcircuit_instance,
    validate_circuit,
    validate_project,
)


@dataclass
class IncomingCircuit:
    name: str
    health: HealthState
    repaired: bool = False
    repair_changes: list[str] = field(default_factory=list)
    unfixable_reason: str | None = None
    xml_bytes: bytes | None = None
    resolvable_deps: list[str] = field(default_factory=list)


@dataclass
class MergeResult:
    ok: bool
    merged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)
    pulled_deps: list[str] = field(default_factory=list)
    unresolved_deps: list[str] = field(default_factory=list)
    backup_path: Path | None = None
    message: str = ""
    format_warning: str | None = None


def scan_incoming(path: str | Path) -> list[IncomingCircuit]:
    """Parse a shared file, health-check each circuit, attempt repair on broken ones."""
    path = Path(path)
    raw = path.read_bytes()

    # Try whole-file light repair first (encoding / trailing junk) without requiring ok.
    file_repair = repair_file(raw)
    working = file_repair.fixed_bytes if file_repair.fixed_bytes is not None else raw

    tmp = path.with_suffix(path.suffix + ".scan-tmp")
    try:
        tmp.write_bytes(working)
        try:
            project = load(tmp)
        except ParseError:
            # Fall back to per-circuit salvage is not possible without project — return one entry.
            return [
                IncomingCircuit(
                    name="(file)",
                    health=HealthState.BROKEN,
                    unfixable_reason="Could not parse shared file even after repair",
                )
            ]
    finally:
        if tmp.exists():
            tmp.unlink()

    names = list_circuits(project)
    name_set = set(names)
    results: list[IncomingCircuit] = []

    for name in names:
        try:
            xml = extract_circuit_raw_bytes(project, name)
        except (KeyError, ParseError) as exc:
            results.append(
                IncomingCircuit(
                    name=name,
                    health=HealthState.BROKEN,
                    unfixable_reason=str(exc),
                )
            )
            continue

        vr = validate_circuit(project, name)
        if vr.ok:
            results.append(
                IncomingCircuit(
                    name=name,
                    health=HealthState.NO_FINAL,
                    xml_bytes=xml,
                )
            )
            continue

        # Attempt repair on this circuit alone.
        from circuit_vault.formats import detect_format as _detect

        rr = repair_circuit(xml, target_format=_detect(project))
        if rr.ok and rr.fixed_bytes is not None:
            # Re-check dangling refs against the full incoming name set.
            from circuit_vault.parser import parse_circuit_bytes
            from lxml import etree

            el = parse_circuit_bytes(rr.fixed_bytes)
            dangling = []
            resolvable = []
            for comp in el.iter():
                if etree.QName(comp).localname != "comp":
                    continue
                ref = is_subcircuit_instance(comp, name_set)
                if ref is None or ref == name:
                    continue
                if ref in name_set:
                    resolvable.append(ref)
                else:
                    dangling.append(ref)
            if dangling:
                results.append(
                    IncomingCircuit(
                        name=name,
                        health=HealthState.BROKEN,
                        repaired=False,
                        repair_changes=rr.changes,
                        unfixable_reason=(
                            "Unresolved subcircuit(s) not in shared file: "
                            + ", ".join(dangling)
                        ),
                        resolvable_deps=resolvable,
                    )
                )
            else:
                results.append(
                    IncomingCircuit(
                        name=name,
                        health=HealthState.NO_FINAL,
                        repaired=True,
                        repair_changes=rr.changes + file_repair.changes,
                        xml_bytes=rr.fixed_bytes,
                        resolvable_deps=resolvable,
                    )
                )
        else:
            # Check if errors are only dangling refs that exist in the same file.
            only_dangling = all("unresolved subcircuit" in e.lower() for e in vr.errors)
            resolvable = []
            unresolvable = []
            if only_dangling:
                from circuit_vault.parser import get_circuit_element
                from lxml import etree

                el = get_circuit_element(project, name)
                for comp in el.iter():
                    if etree.QName(comp).localname != "comp":
                        continue
                    ref = is_subcircuit_instance(comp, name_set)
                    if ref is None or ref == name:
                        continue
                    if ref in name_set:
                        resolvable.append(ref)
                    else:
                        unresolvable.append(ref)
                if not unresolvable and resolvable:
                    # Refs resolve within the shared file — merge can pull deps.
                    results.append(
                        IncomingCircuit(
                            name=name,
                            health=HealthState.NO_FINAL,
                            repaired=True,
                            repair_changes=["Dangling refs resolve within shared file"],
                            xml_bytes=xml,
                            resolvable_deps=resolvable,
                        )
                    )
                    continue

            results.append(
                IncomingCircuit(
                    name=name,
                    health=HealthState.BROKEN,
                    repaired=False,
                    repair_changes=rr.changes,
                    unfixable_reason=rr.unfixable_reason
                    or "; ".join(vr.errors),
                )
            )

    return results


def merge(
    selected_names: list[str],
    target_circ_path: str | Path,
    clash_policy: str = "replace",
    *,
    incoming_path: str | Path | None = None,
    incoming_circuits: list[IncomingCircuit] | None = None,
) -> MergeResult:
    """
    Splice selected valid/repaired circuits into the target.

    clash_policy: "replace" | "keep_both" | "skip"
    """
    target_path = Path(target_circ_path)
    if not target_path.exists():
        return MergeResult(ok=False, message=f"Target not found: {target_path}")

    if incoming_circuits is None:
        if incoming_path is None:
            return MergeResult(ok=False, message="No incoming file provided")
        incoming_circuits = scan_incoming(incoming_path)

    by_name = {c.name: c for c in incoming_circuits}
    # Also need the full incoming project for dependency pull-in.
    if incoming_path is None:
        return MergeResult(ok=False, message="incoming_path required for merge")
    incoming_path = Path(incoming_path)

    try:
        target = load(target_path)
    except ParseError as exc:
        return MergeResult(ok=False, message=str(exc))

    bak = backup(target_path)
    target_fmt = detect_format(target)

    # Build merge plan: selected + needed deps from incoming that aren't valid in target.
    to_merge: list[str] = []
    for name in selected_names:
        circ = by_name.get(name)
        if circ is None or circ.xml_bytes is None or circ.unfixable_reason:
            continue
        to_merge.append(name)

    if not to_merge:
        return MergeResult(
            ok=False,
            backup_path=bak,
            message="No valid selected circuits to merge",
        )

    # Pull dependencies from incoming graph.
    try:
        # Reload incoming as project for graph
        raw = incoming_path.read_bytes()
        fr = repair_file(raw)
        working = fr.fixed_bytes or raw
        tmp = incoming_path.with_suffix(".merge-tmp.circ")
        tmp.write_bytes(working)
        try:
            incoming_proj = load(tmp)
        finally:
            tmp.unlink(missing_ok=True)
    except ParseError as exc:
        return MergeResult(ok=False, backup_path=bak, message=str(exc))

    incoming_fmt = detect_format(incoming_proj)
    format_warning: str | None = None
    if target_fmt != incoming_fmt:
        format_warning = (
            f"Format mismatch: shared file is {format_label(incoming_fmt)}, "
            f"target is {format_label(target_fmt)}. "
            "Circuits were merged as XML; open the result in your Logisim app and verify."
        )

    graph = build_graph(incoming_proj)
    target_names = set(list_circuits(target))
    pulled: list[str] = []
    unresolved: list[str] = []

    def need_dep(dep: str) -> bool:
        if dep not in target_names:
            return True
        return not validate_circuit(target, dep).ok

    expanded = list(to_merge)
    for name in list(to_merge):
        for dep in graph.get(name, set()):
            if need_dep(dep):
                if dep in by_name and by_name[dep].xml_bytes is not None:
                    if dep not in expanded:
                        expanded.append(dep)
                        pulled.append(dep)
                else:
                    # try extract from incoming project
                    try:
                        xml = extract_circuit_raw_bytes(incoming_proj, dep)
                        by_name[dep] = IncomingCircuit(
                            name=dep,
                            health=HealthState.NO_FINAL,
                            xml_bytes=xml,
                        )
                        if dep not in expanded:
                            expanded.append(dep)
                            pulled.append(dep)
                    except (KeyError, ParseError):
                        unresolved.append(dep)

    # Topo: deps before dependents using incoming graph
    ordered = _topo_available(expanded, graph)

    merged: list[str] = []
    skipped: list[str] = []
    renamed: dict[str, str] = {}
    project = target

    try:
        for name in ordered:
            circ = by_name.get(name)
            if circ is None or circ.xml_bytes is None:
                skipped.append(name)
                continue
            xml = circ.xml_bytes
            final_name = name
            exists = name in set(list_circuits(project))

            if exists:
                if clash_policy == "skip":
                    skipped.append(name)
                    continue
                if clash_policy == "keep_both":
                    final_name = f"{name} (imported)"
                    # ensure unique
                    n = 2
                    while final_name in set(list_circuits(project)):
                        final_name = f"{name} (imported {n})"
                        n += 1
                    xml = rename_circuit_xml(xml, final_name)
                    renamed[name] = final_name
                    project = insert_circuit(project, xml)
                else:  # replace
                    project = splice(project, name, xml)
            else:
                if final_name != name:
                    xml = rename_circuit_xml(xml, final_name)
                project = insert_circuit(project, xml)

            merged.append(final_name)
            target_names.add(final_name)

        vr = validate_project(project)
        if not vr.ok:
            return MergeResult(
                ok=False,
                backup_path=bak,
                message="Merge aborted — result would be invalid: "
                + "; ".join(vr.errors),
                unresolved_deps=unresolved,
            )

        target_path.write_bytes(project.raw_bytes)
    except (SpliceError, ParseError, KeyError) as exc:
        return MergeResult(ok=False, backup_path=bak, message=f"Merge failed: {exc}")

    msg = (
        f"Imported {len(merged)} circuit(s). "
        "Your other circuits were left untouched."
    )
    if format_warning:
        msg = f"{msg} {format_warning}"

    return MergeResult(
        ok=True,
        merged=merged,
        skipped=skipped,
        renamed=renamed,
        pulled_deps=pulled,
        unresolved_deps=unresolved,
        backup_path=bak,
        message=msg,
        format_warning=format_warning,
    )


def _topo_available(nodes: list[str], graph: dict[str, set[str]]) -> list[str]:
    node_set = set(nodes)
    subgraph = {n: (graph.get(n, set()) & node_set) for n in node_set}
    indeg = {n: len(subgraph[n]) for n in node_set}
    ready = sorted(n for n, d in indeg.items() if d == 0)
    out: list[str] = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for m in sorted(node_set):
            if n in subgraph[m]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
                    ready.sort()
    if len(out) < len(node_set):
        out.extend(sorted(node_set - set(out)))
    return out
