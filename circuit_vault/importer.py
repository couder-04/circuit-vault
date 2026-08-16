"""Scan and selectively merge circuits from a shared .circ file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from circuit_vault.dependencies import build_graph
from circuit_vault.formats import detect_format, format_label
from circuit_vault.parser import (
    ParseError,
    circuit_to_xml_bytes,
    extract_circuit_raw_bytes,
    list_circuits,
    load,
    parse_circuit_bytes,
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
    incoming_names = set(list_circuits(incoming_proj))
    incoming_lib_ids = _declared_lib_ids(incoming_proj)
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

    # Always pull transitive deps from the source file so the merged circuit
    # keeps the same subcircuits (AND_GATE, FULL_ADDER, …) as in file 1.
    # Missing deps in the target are the usual cause of “gates vanished” after
    # open-in-Logisim / cleanup; overwriting with source deps matches clash_policy.
    expanded = list(to_merge)
    seen = set(expanded)
    stack = list(to_merge)
    while stack:
        name = stack.pop()
        for dep in sorted(graph.get(name, set())):
            if dep in seen:
                continue
            if dep in by_name and by_name[dep].xml_bytes is not None:
                seen.add(dep)
                expanded.append(dep)
                if dep not in to_merge:
                    pulled.append(dep)
                stack.append(dep)
                continue
            try:
                xml = extract_circuit_raw_bytes(incoming_proj, dep)
                by_name[dep] = IncomingCircuit(
                    name=dep,
                    health=HealthState.NO_FINAL,
                    xml_bytes=xml,
                )
                seen.add(dep)
                expanded.append(dep)
                pulled.append(dep)
                stack.append(dep)
            except (KeyError, ParseError):
                unresolved.append(dep)

    # Topo: deps before dependents using incoming graph
    ordered = _topo_available(expanded, graph)

    merged: list[str] = []
    skipped: list[str] = []
    renamed: dict[str, str] = {}
    project = target
    lib_notes: list[str] = []

    try:
        for name in ordered:
            circ = by_name.get(name)
            if circ is None or circ.xml_bytes is None:
                skipped.append(name)
                continue
            # Exact source bytes — do not re-serialize or “snap” wires.
            # Auto connection repair was rewriting good circuits (every wire
            # endpoint moved) and made merges look like lost connections.
            xml = circ.xml_bytes
            # Evolution often writes <comp lib="13" name="AND_GATE"/> without a
            # matching <lib name="13">. In file 1 Logisim still resolves it; after
            # paste into file 2 those blocks vanish. Strip orphan lib= when the
            # name is a circuit in the source project and lib id is undeclared.
            xml, _norm = _normalize_project_subcircuit_refs(
                xml, incoming_names, declared_libs=incoming_lib_ids
            )
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

        # Whole-project pass: Evolution often writes lib="13" (etc.) for same-file
        # subcircuits without a matching <lib> entry. Strip those so Logisim does
        # not report "library '13' not found" and drop the blocks.
        project, lib_notes = repair_orphan_project_libs(project)

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
    if pulled:
        msg += f" Also brought {len(pulled)} dependenc{'y' if len(pulled)==1 else 'ies'} from the source file."
    if lib_notes:
        msg += " Fixed orphan library refs (lib=13 etc.) so Logisim can resolve subcircuits."
    if unresolved:
        msg += (
            " Warning: missing in source: "
            + ", ".join(sorted(set(unresolved)))
            + " — open Logisim may drop those blocks."
        )
    msg += " Quit Logisim before merge and reopen the file after — do not Save from an already-open Logisim window or it can overwrite this fix."
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


def _declared_lib_ids(project) -> set[str]:
    """Return ``name`` attrs of top-level ``<lib>`` entries (e.g. {\"0\",\"1\",…})."""
    from lxml import etree

    ids: set[str] = set()
    for el in project.root.iterchildren():
        if etree.QName(el).localname != "lib":
            continue
        nid = el.get("name")
        if nid is not None:
            ids.add(str(nid).strip())
    return ids


def repair_orphan_project_libs(project):
    """
    Strip undeclared ``lib=`` on same-project subcircuit instances in every circuit.

    Returns (project, notes).
    """
    names = set(list_circuits(project))
    declared = _declared_lib_ids(project)
    notes: list[str] = []
    for name in list_circuits(project):
        xml = extract_circuit_raw_bytes(project, name)
        fixed, n = _normalize_project_subcircuit_refs(
            xml, names, declared_libs=declared
        )
        if not n:
            continue
        project = splice(project, name, fixed)
        notes.extend(f"{name}: {x}" for x in n)
    return project, notes


def repair_orphan_libs_file(path: str | Path) -> tuple[bool, str]:
    """Fix orphan lib= refs on disk. Returns (ok, message)."""
    path = Path(path)
    try:
        project = load(path)
    except ParseError as exc:
        return False, str(exc)
    bak = backup(path)
    project, notes = repair_orphan_project_libs(project)
    if not notes:
        return True, "No orphan library refs found."
    path.write_bytes(project.raw_bytes)
    return True, (
        f"Fixed {len(notes)} circuit(s); backup at {bak.name}. "
        "Quit Logisim and reopen this file (do not Save from an old window)."
    )


def _normalize_project_subcircuit_refs(
    xml_bytes: bytes,
    circuit_names: set[str],
    *,
    declared_libs: set[str] | None = None,
) -> tuple[bytes, list[str]]:
    """
    Strip orphan ``lib=`` on comps that name a circuit in *circuit_names*.

    Logisim Evolution may tag same-project subcircuits with lib=\"10\"+ without
    writing a matching ``<lib>`` entry. Those survive in the original file but
    drop out after merge into another .circ / show “library N not found”.
    """
    notes: list[str] = []
    try:
        el = parse_circuit_bytes(xml_bytes.strip())
    except ParseError:
        return xml_bytes, notes

    self_name = el.get("name")
    stripped = 0
    for comp in el.iter("comp"):
        name = comp.get("name")
        if not name or name == self_name or name not in circuit_names:
            continue
        lib = comp.get("lib")
        if lib is None or str(lib).strip() == "":
            continue
        lib_s = str(lib).strip()
        # Keep real library comps (Wiring/Gates/…). Only strip undeclared ids.
        if declared_libs is not None and lib_s in declared_libs:
            continue
        del comp.attrib["lib"]
        stripped += 1
    if not stripped:
        return xml_bytes, notes
    notes.append(f"normalized {stripped} subcircuit lib ref(s)")
    return circuit_to_xml_bytes(el), notes


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
