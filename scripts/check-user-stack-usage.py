#!/usr/bin/env python3
"""Fail closed when compiled user call paths exceed the one-page stack."""

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath


LOCATION_RE = re.compile(
    r"^(?P<source>.+):(?P<line>[0-9]+):(?P<column>[0-9]+):(?P<function>.+)$"
)
GRAPH_RE = re.compile(r'^graph: \{ title: "([^"]+)"$')
NODE_RE = re.compile(r'node: \{ title: "([^"]+)" label: "([^"]+)"')
EDGE_RE = re.compile(
    r'edge: \{ sourcename: "([^"]+)" targetname: "([^"]+)"'
)
SIZE_RE = re.compile(r"([0-9]+) bytes \(([^)]+)\)")
INDIRECT_NODE = "__indirect_call"
STARTUP_NODE = "__start_main"
CONTRACT_VALUES = {
    "USER_STACK_SIZE_BYTES": 4096,
    "USER_STACK_ARGV_LAYOUT_BYTES": 1024,
    "USER_STACK_CALL_PATH_BYTES": 3072,
}
CONTRACT_DEFINE_RE = re.compile(
    r"^\s*#define\s+([A-Z0-9_]+)\s+([0-9]+)(?:U|UL|ULL|L|LL)?\s*$"
)


@dataclass
class UnitGraph:
    unit: PurePosixPath
    frames: dict
    names: dict
    edges: set


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--usage-dir", required=True)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--contract-header", required=True)
    parser.add_argument("--library-unit", action="append", default=[])
    parser.add_argument("--application-unit", action="append", default=[])
    parser.add_argument("--allow-unresolved", action="append", default=[])
    parser.add_argument("--indirect-call-edge", action="append", default=[])
    parser.add_argument("--recursion-bound", action="append", default=[])
    return parser.parse_args()


def read_stack_contract(path):
    values = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            match = CONTRACT_DEFINE_RE.fullmatch(line.rstrip("\r\n"))
            if match is None or match.group(1) not in CONTRACT_VALUES:
                continue
            name = match.group(1)
            if name in values:
                raise ValueError(f"duplicate user stack contract value: {name}")
            values[name] = int(match.group(2))
    missing = set(CONTRACT_VALUES) - set(values)
    if missing:
        raise ValueError(
            "user stack contract is incomplete: " + ", ".join(sorted(missing))
        )
    drift = [
        f"{name}={values[name]} (expected {expected})"
        for name, expected in CONTRACT_VALUES.items()
        if values[name] != expected
    ]
    if drift:
        raise ValueError("user stack contract drift: " + ", ".join(drift))
    if (
        values["USER_STACK_ARGV_LAYOUT_BYTES"]
        + values["USER_STACK_CALL_PATH_BYTES"]
        != values["USER_STACK_SIZE_BYTES"]
    ):
        raise ValueError("user stack contract partitions do not cover the stack")
    return (
        values["USER_STACK_SIZE_BYTES"],
        values["USER_STACK_ARGV_LAYOUT_BYTES"],
        values["USER_STACK_CALL_PATH_BYTES"],
    )


def normalized_units(raw_units, kind):
    units = []
    for raw in raw_units:
        unit = PurePosixPath(raw.replace("\\", "/"))
        if (
            unit.is_absolute()
            or unit.suffix != ".c"
            or not unit.parts
            or any(part in ("", ".", "..") for part in unit.parts)
        ):
            raise ValueError(f"invalid {kind} unit: {raw}")
        units.append(unit)
    if not units:
        raise ValueError(f"{kind} unit inventory is empty")
    if len(set(units)) != len(units):
        raise ValueError(f"{kind} unit inventory contains duplicates")
    return units


def parse_recursion_bounds(values):
    bounds = {}
    for value in values:
        try:
            name, raw_bound = value.rsplit("=", 1)
            bound = int(raw_bound)
        except (TypeError, ValueError):
            raise ValueError(f"invalid recursion bound: {value}") from None
        if not name or bound < 2:
            raise ValueError(f"invalid recursion bound: {value}")
        if name in bounds and bounds[name] != bound:
            raise ValueError(f"conflicting recursion bounds for {name}")
        bounds[name] = bound
    return bounds


def parse_indirect_call_edges(values):
    edges = defaultdict(set)
    seen = set()
    for value in values:
        try:
            caller, target = value.split("=", 1)
        except (TypeError, ValueError):
            raise ValueError(f"invalid indirect call edge: {value}") from None
        if not caller or not target or value in seen:
            raise ValueError(f"invalid or duplicate indirect call edge: {value}")
        seen.add(value)
        edges[caller].add(target)
    return edges


def artifact_inventory(usage_dir, source_dir, units):
    missing_sources = [
        unit for unit in units if not (source_dir / Path(*unit.parts)).is_file()
    ]
    if missing_sources:
        raise ValueError(
            "translation-unit source is missing: "
            + ", ".join(str(unit) for unit in missing_sources)
        )

    result = {}
    for suffix in (".su", ".ci"):
        expected = {
            Path(*unit.with_suffix(suffix).parts).as_posix(): unit for unit in units
        }
        actual = {
            path.relative_to(usage_dir).as_posix(): path
            for path in usage_dir.rglob(f"*{suffix}")
            if path.is_file()
        }
        missing = sorted(set(expected) - set(actual))
        stale = sorted(set(actual) - set(expected))
        if missing or stale:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if stale:
                details.append("stale: " + ", ".join(stale))
            raise ValueError(
                f"incomplete user {suffix[1:]} set (" + "; ".join(details) + ")"
            )
        for relative, unit in expected.items():
            result.setdefault(unit, {})[suffix] = actual[relative]
    return result


def read_stack_usage(unit, path, frame_budget):
    records = Counter()
    maximum = None
    violations = []
    unbounded = []
    with path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            fields = raw_line.rstrip("\r\n").split("\t")
            location = LOCATION_RE.fullmatch(fields[0]) if len(fields) == 3 else None
            if location is None:
                raise ValueError(f"unsupported stack-usage record at {path}:{line_number}")
            try:
                usage = int(fields[1])
            except ValueError:
                raise ValueError(
                    f"invalid stack usage at {path}:{line_number}: {fields[1]}"
                ) from None
            if usage < 0:
                raise ValueError(f"negative stack usage at {path}:{line_number}")
            function = location.group("function")
            kind = fields[2]
            entry = (usage, str(unit), function, kind)
            records[(function, usage, kind)] += 1
            if maximum is None or entry > maximum:
                maximum = entry
            if kind != "static":
                unbounded.append(entry)
            if usage > frame_budget:
                violations.append(entry)
    if not records:
        raise ValueError(f"user stack-usage record has no functions: {unit}")
    if unbounded:
        rendered = ", ".join(
            f"{source}:{function} ({kind})"
            for _, source, function, kind in unbounded
        )
        raise ValueError("unbounded user stack usage: " + rendered)
    if violations:
        rendered = ", ".join(
            f"{source}:{function}={usage}"
            for usage, source, function, _ in violations
        )
        raise ValueError(
            f"user frame budget exceeded ({frame_budget} bytes): " + rendered
        )
    return records, maximum


def read_callgraph(unit, path):
    frames = {}
    names = {}
    edges = set()
    records = Counter()
    graph_seen = False
    with path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            line = raw_line.rstrip("\r\n")
            graph = GRAPH_RE.fullmatch(line)
            node = NODE_RE.search(line)
            edge = EDGE_RE.search(line)
            if graph:
                if graph_seen or graph.group(1) != unit.as_posix():
                    raise ValueError(f"callgraph title mismatch at {path}:{line_number}")
                graph_seen = True
                continue
            if node:
                title, label = node.groups()
                name = label.split(r"\n", 1)[0]
                names.setdefault(title, name)
                size = SIZE_RE.search(label)
                if size:
                    if title in frames:
                        raise ValueError(
                            f"duplicate callgraph definition at {path}:{line_number}: {title}"
                        )
                    usage = int(size.group(1))
                    kind = size.group(2)
                    frames[title] = usage
                    names[title] = name
                    records[(name, usage, kind)] += 1
                    if kind != "static":
                        raise ValueError(
                            f"unbounded callgraph frame at {path}:{line_number}: {name}"
                        )
                continue
            if edge:
                edges.add(edge.groups())
                continue
            if line == "}":
                continue
            if line.lstrip().startswith(("graph:", "node:", "edge:")) or line:
                raise ValueError(f"unsupported callgraph record at {path}:{line_number}")
    if not graph_seen or not frames:
        raise ValueError(f"callgraph has no compiled definitions: {unit}")
    return UnitGraph(unit, frames, names, edges), records


def read_units(artifacts, units, frame_budget):
    graphs = {}
    maximum = None
    function_count = 0
    for unit in units:
        usage_records, unit_maximum = read_stack_usage(
            unit, artifacts[unit][".su"], frame_budget
        )
        graph, graph_records = read_callgraph(unit, artifacts[unit][".ci"])
        if usage_records != graph_records:
            raise ValueError(f"stack-usage/callgraph mismatch for {unit}")
        graphs[unit] = graph
        function_count += sum(usage_records.values())
        if maximum is None or unit_maximum > maximum:
            maximum = unit_maximum
    return graphs, maximum, function_count


def combine_graph(app, libraries):
    units = tuple(libraries) + (app,)
    frames = {}
    names = {}
    owners = {}
    definitions_by_name = defaultdict(list)
    for unit in units:
        for title, frame in unit.frames.items():
            if title in frames:
                raise ValueError(
                    f"ambiguous external definition for {title}: "
                    f"{owners[title]}, {unit.unit}"
                )
            frames[title] = frame
            names[title] = unit.names[title]
            owners[title] = unit.unit
            definitions_by_name[unit.names[title]].append(title)

    graph = defaultdict(set)
    incoming = defaultdict(set)
    external_names = {}
    for unit in units:
        for source, raw_target in unit.edges:
            if source not in frames:
                raise ValueError(f"callgraph edge source is unresolved: {unit.unit}:{source}")
            target = raw_target
            if target not in frames:
                external_name = unit.names.get(raw_target, raw_target)
                candidates = definitions_by_name.get(external_name, ())
                if len(candidates) == 1:
                    target = candidates[0]
                elif len(candidates) > 1:
                    raise ValueError(
                        f"ambiguous external call {raw_target} from {unit.unit}"
                    )
                else:
                    external_names[target] = external_name
            graph[source].add(target)
            incoming[target].add(source)
    return frames, names, owners, graph, incoming, external_names


def reachable_from(roots, graph):
    reachable = set()
    pending = list(roots)
    while pending:
        node = pending.pop()
        if node in reachable:
            continue
        reachable.add(node)
        pending.extend(graph.get(node, ()))
    return reachable


def strongly_connected_components(graph, nodes):
    index = 0
    indexes = {}
    lowlinks = {}
    stack = []
    on_stack = set()
    components = []

    def visit(node):
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, ()):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] == indexes[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(component)

    for node in nodes:
        if node not in indexes:
            visit(node)
    return components


def longest_app_path(
    app, libraries, allowed_unresolved, indirect_call_edges, recursion_bounds
):
    frames, names, owners, graph, incoming, external_names = combine_graph(
        app, libraries
    )
    if "main" not in app.frames:
        raise ValueError(f"application has no compiled main entry: {app.unit}")
    if STARTUP_NODE not in frames or "main" not in graph.get(STARTUP_NODE, ()):
        raise ValueError(f"startup chain does not reach {app.unit}:main")
    # _start tail-calls this C frame. Keep every app function as a potential
    # callback entry, but also account for the frame retained below main().
    roots = set(app.frames) | {STARTUP_NODE}
    applied_indirect_callers = set()
    for caller in tuple(frames):
        if INDIRECT_NODE not in graph.get(caller, ()):
            continue
        targets = indirect_call_edges.get(caller)
        if not targets:
            continue
        missing = sorted(target for target in targets if target not in frames)
        if missing:
            raise ValueError(
                f"{app.unit} indirect edge targets are unresolved for {caller}: "
                + ", ".join(missing)
            )
        graph[caller].remove(INDIRECT_NODE)
        incoming[INDIRECT_NODE].discard(caller)
        for target in targets:
            graph[caller].add(target)
            incoming[target].add(caller)
        applied_indirect_callers.add(caller)
    reachable = reachable_from(roots, graph)
    used_indirect_callers = applied_indirect_callers & reachable
    used_unresolved = set()
    failures = []
    for node in reachable:
        if node in frames:
            continue
        callers = sorted(names.get(source, source) for source in incoming.get(node, ()))
        if node == INDIRECT_NODE:
            failures.append("indirect call from " + ", ".join(callers))
        elif node in allowed_unresolved:
            used_unresolved.add(node)
        else:
            external_name = external_names.get(node, node)
            failures.append(
                f"unresolved {node}/{external_name} from " + ", ".join(callers)
            )
    if failures:
        raise ValueError(
            f"{app.unit} has unresolved reachable calls: " + "; ".join(failures)
        )

    reachable_graph = {
        source: {target for target in graph.get(source, ()) if target in reachable}
        for source in reachable
    }
    components = strongly_connected_components(reachable_graph, reachable)
    component_of = {}
    weights = []
    labels = []
    used_bounds = set()
    for component_id, members in enumerate(components):
        for member in members:
            component_of[member] = component_id
        recursive = len(members) > 1 or (
            len(members) == 1 and members[0] in reachable_graph.get(members[0], ())
        )
        if len(members) > 1:
            rendered = "/".join(sorted(names.get(member, member) for member in members))
            raise ValueError(f"mutual recursion has no audited bound: {app.unit}:{rendered}")
        member = members[0]
        weight = frames.get(member, 0)
        label = names.get(member, external_names.get(member, member))
        if recursive:
            bound = recursion_bounds.get(member)
            if bound is None:
                raise ValueError(
                    f"recursion has no audited bound: {app.unit}:{member}"
                )
            used_bounds.add(member)
            weight *= bound
            label += f"[x{bound}]"
        if member in frames:
            label += f"[{weight}]"
        weights.append(weight)
        labels.append(label)

    dag = defaultdict(set)
    for source, targets in reachable_graph.items():
        source_component = component_of[source]
        for target in targets:
            target_component = component_of[target]
            if source_component != target_component:
                dag[source_component].add(target_component)

    @lru_cache(maxsize=None)
    def visit(component_id):
        best_size = 0
        best_path = ()
        for target in dag.get(component_id, ()):
            target_size, target_path = visit(target)
            if target_size > best_size:
                best_size, best_path = target_size, target_path
        return weights[component_id] + best_size, (component_id,) + best_path

    best = None
    for root in roots:
        size, path = visit(component_of[root])
        candidate = (size, names[root], root, path)
        if best is None or candidate > best:
            best = candidate
    size, entry, _, component_path = best
    path = [labels[component_id] for component_id in component_path]
    return (
        size,
        entry,
        path,
        used_unresolved,
        used_bounds,
        used_indirect_callers,
    )


def main():
    args = parse_args()
    try:
        stack_size, argv_budget, frame_budget = read_stack_contract(
            Path(args.contract_header)
        )
        usage_dir = Path(args.usage_dir)
        source_dir = Path(args.source_dir)
        if not usage_dir.is_dir():
            raise ValueError(f"stack-usage directory is missing: {usage_dir}")
        if not source_dir.is_dir():
            raise ValueError(f"source directory is missing: {source_dir}")
        libraries = normalized_units(args.library_unit, "library")
        applications = normalized_units(args.application_unit, "application")
        overlap = set(libraries) & set(applications)
        if overlap:
            raise ValueError("units appear in both inventories: " + ", ".join(map(str, overlap)))
        units = libraries + applications
        artifacts = artifact_inventory(usage_dir, source_dir, units)
        graphs, maximum, function_count = read_units(
            artifacts, units, frame_budget
        )
        recursion_bounds = parse_recursion_bounds(args.recursion_bound)
        indirect_call_edges = parse_indirect_call_edges(args.indirect_call_edge)
        allowed_unresolved = set(args.allow_unresolved)
        if len(allowed_unresolved) != len(args.allow_unresolved):
            raise ValueError("unresolved-call allowlist contains duplicates")
        library_graphs = [graphs[unit] for unit in libraries]
        best_path = None
        used_unresolved = set()
        used_bounds = set()
        used_indirect_callers = set()
        for unit in applications:
            (
                size,
                entry,
                path,
                app_unresolved,
                app_bounds,
                app_indirect_callers,
            ) = longest_app_path(
                graphs[unit], library_graphs, allowed_unresolved,
                indirect_call_edges, recursion_bounds
            )
            candidate = (size, str(unit), entry, path)
            if best_path is None or candidate > best_path:
                best_path = candidate
            used_unresolved.update(app_unresolved)
            used_bounds.update(app_bounds)
            used_indirect_callers.update(app_indirect_callers)
        stale_unresolved = allowed_unresolved - used_unresolved
        stale_bounds = set(recursion_bounds) - used_bounds
        stale_indirect = set(indirect_call_edges) - used_indirect_callers
        if stale_unresolved:
            raise ValueError(
                "unused unresolved-call allowlist: " + ", ".join(sorted(stale_unresolved))
            )
        if stale_bounds:
            raise ValueError("unused recursion bounds: " + ", ".join(sorted(stale_bounds)))
        if stale_indirect:
            raise ValueError(
                "unused indirect-call declarations: "
                + ", ".join(sorted(stale_indirect))
            )
        path_size, app_unit, entry, path = best_path
        if path_size > frame_budget:
            raise ValueError(
                f"user call-path budget exceeded ({frame_budget} bytes): "
                f"{app_unit}:{entry}={path_size} via " + " -> ".join(path)
            )
    except (OSError, ValueError) as error:
        print(f"user stack check failed: {error}", file=sys.stderr)
        return 1

    usage, unit, function, _ = maximum
    print(
        "user stack frame budget: "
        f"units={len(units)} functions={function_count} "
        f"max={usage} ({unit}:{function}) budget={frame_budget}"
    )
    print(
        "user stack call-path budget: "
        f"apps={len(applications)} max={path_size} "
        f"({app_unit}:{entry}) budget={frame_budget} "
        f"stack={stack_size} reserve={argv_budget}"
    )
    print("user stack longest path: " + " -> ".join(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
