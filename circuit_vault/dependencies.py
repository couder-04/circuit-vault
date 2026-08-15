"""Circuit dependency graph and restore-set resolution."""

from __future__ import annotations

from circuit_vault.parser import Project, get_circuit_element, list_circuits
from circuit_vault.validator import HealthState, is_subcircuit_instance, validate_circuit


class DependencyError(Exception):
    """Raised for unrecoverable dependency problems (e.g. hard cycles)."""


def build_graph(project: Project) -> dict[str, set[str]]:
    """
    Build circuit -> set of subcircuits it instantiates.

    A <comp> is a dependency edge when its name matches another <circuit> and
    it has no library index (lib absent/empty).
    """
    names = list_circuits(project)
    name_set = set(names)
    graph: dict[str, set[str]] = {n: set() for n in names}

    for name in names:
        el = get_circuit_element(project, name)
        for comp in el.iter():
            ref = is_subcircuit_instance(comp, name_set)
            if ref is None:
                continue
            if ref in name_set and ref != name:
                graph[name].add(ref)
    return graph


def resolve_restore_set(
    project: Project,
    target: str,
    *,
    health_fn=None,
) -> list[str]:
    """
    Return target plus any dependency that is currently BROKEN or missing,
    topologically ordered so dependencies restore before dependents.

    Cycles are reported gracefully: remaining nodes appended in sorted order
    (Logisim disallows cycles; we must not crash).
    """
    names = list_circuits(project)
    name_set = set(names)
    if target not in name_set:
        raise KeyError(f"Circuit not found: {target!r}")

    graph = build_graph(project)

    def _is_broken_or_missing(name: str) -> bool:
        if name not in name_set:
            return True
        if health_fn is not None:
            return health_fn(name) == HealthState.BROKEN
        return not validate_circuit(project, name).ok

    # Transitive dependencies of target that are broken/missing, plus target.
    restore: set[str] = {target}
    stack = list(graph.get(target, set()))
    seen: set[str] = set()
    while stack:
        dep = stack.pop()
        if dep in seen:
            continue
        seen.add(dep)
        if _is_broken_or_missing(dep):
            restore.add(dep)
        stack.extend(graph.get(dep, set()) - seen)

    return _topo_sort(restore, graph)


def _topo_sort(nodes: set[str], graph: dict[str, set[str]]) -> list[str]:
    """Deps before dependents (Kahn). On cycle, append remaining sorted."""
    subgraph = {n: (graph.get(n, set()) & nodes) for n in nodes}
    indegree = {n: len(subgraph[n]) for n in nodes}
    ready = sorted(n for n, deg in indegree.items() if deg == 0)
    result: list[str] = []
    while ready:
        n = ready.pop(0)
        result.append(n)
        for m in sorted(nodes):
            if n in subgraph[m]:
                indegree[m] -= 1
                if indegree[m] == 0:
                    ready.append(m)
                    ready.sort()
    if len(result) < len(nodes):
        result.extend(sorted(nodes - set(result)))
    return result


def detect_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return list of cycles (each as a list of circuit names). Never raises."""
    cycles: list[list[str]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    path: list[str] = []

    def dfs(u: str) -> None:
        color[u] = GRAY
        path.append(u)
        for v in graph.get(u, set()):
            if v not in color:
                continue
            if color[v] == GRAY:
                if v in path:
                    idx = path.index(v)
                    cycles.append(path[idx:] + [v])
            elif color[v] == WHITE:
                dfs(v)
        path.pop()
        color[u] = BLACK

    for n in graph:
        if color[n] == WHITE:
            dfs(n)
    return cycles
