#!/usr/bin/env python3
"""编译后的内核可能超过栈预算时闭锁失败。"""

import argparse
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path


NODE_RE = re.compile(r'node: \{ title: "([^"]+)" label: "([^"]+)"')
EDGE_RE = re.compile(
    r'edge: \{ sourcename: "([^"]+)" targetname: "([^"]+)"'
)
SIZE_RE = re.compile(r'([0-9]+) bytes \(([^)]+)\)')
INDIRECT_NODE = "__indirect_call"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--callgraph-dir", required=True)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--stack-size", required=True, type=int)
    parser.add_argument("--guard-size", required=True, type=int)
    parser.add_argument("--safety-margin", required=True, type=int)
    parser.add_argument("--interrupt-entry", required=True, type=int)
    parser.add_argument("--required-baseline", type=int)
    parser.add_argument("--required-limit", type=int)
    parser.add_argument("--boot-root", required=True)
    parser.add_argument("--boot-stack-size", required=True, type=int)
    parser.add_argument("--boot-required-baseline", type=int)
    parser.add_argument("--boot-required-limit", type=int)
    parser.add_argument("--stack-boundary", action="append", default=[])
    parser.add_argument("--allow-indirect-from", action="append", default=[])
    parser.add_argument("--indirect-call-edge", action="append", default=[])
    parser.add_argument("--translation-unit", action="append", default=[])
    parser.add_argument("--recursion-bound", action="append", default=[])
    return parser.parse_args()


def parse_recursion_bounds(values):
    bounds = {}
    for value in values:
        try:
            name, raw_bound = value.rsplit("=", 1)
            bound = int(raw_bound)
        except (ValueError, TypeError):
            raise ValueError(f"invalid recursion bound: {value}") from None
        if not name or bound < 2:
            raise ValueError(f"invalid recursion bound: {value}")
        if name in bounds and bounds[name] != bound:
            raise ValueError(f"conflicting recursion bounds for {name}")
        bounds[name] = bound
    return bounds


def parse_indirect_call_edges(values):
    edges = defaultdict(set)
    for value in values:
        try:
            caller, target = value.split("=", 1)
        except (ValueError, TypeError):
            raise ValueError(f"invalid indirect call edge: {value}") from None
        if not caller or not target:
            raise ValueError(f"invalid indirect call edge: {value}")
        edges[caller].add(target)
    return edges


def read_callgraphs(directory, source_directory, translation_units=()):
    callgraph_dir = Path(directory)
    source_dir = Path(source_directory)
    paths = sorted(callgraph_dir.glob("*.ci"))
    source_units = {path.stem for path in source_dir.glob("*.c")}
    if translation_units:
        expected = set(translation_units)
        if (
            len(expected) != len(translation_units)
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", unit)
                   for unit in expected)
        ):
            raise ValueError("invalid or duplicate translation-unit inventory")
        missing_sources = sorted(expected - source_units)
        if missing_sources:
            raise ValueError(
                "translation-unit source is missing: "
                + ", ".join(missing_sources)
            )
    else:
        expected = source_units
    actual = {path.stem for path in paths}
    missing = sorted(expected - actual)
    stale = sorted(actual - source_units)
    if missing or stale:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if stale:
            details.append("stale: " + ", ".join(stale))
        raise ValueError("incomplete callgraph set (" + "; ".join(details) + ")")
    if translation_units:
        paths = [path for path in paths if path.stem in expected]
    if not paths:
        raise ValueError(f"no GCC callgraph files found in {directory}")

    title_to_name = {}
    frames = {}
    raw_edges = []
    dynamic = []
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                node = NODE_RE.search(line)
                edge = EDGE_RE.search(line)
                stripped = line.lstrip()
                if stripped.startswith("node:") and node is None:
                    raise ValueError(
                        f"unsupported callgraph node at {path}:{line_number}"
                    )
                if stripped.startswith("edge:") and edge is None:
                    raise ValueError(
                        f"unsupported callgraph edge at {path}:{line_number}"
                    )
                if node:
                    title, label = node.groups()
                    name = label.split(r"\n", 1)[0]
                    title_to_name[title] = name
                    size = SIZE_RE.search(label)
                    if size:
                        frame = int(size.group(1))
                        kind = size.group(2)
                        frames[title] = max(frames.get(title, 0), frame)
                        if kind != "static":
                            dynamic.append((name, kind))
                if edge:
                    raw_edges.append(edge.groups())

    if dynamic:
        details = ", ".join(f"{name} ({kind})" for name, kind in dynamic)
        raise ValueError(f"unbounded kernel stack usage: {details}")
    return title_to_name, frames, raw_edges


def node_name(node, title_to_name):
    return title_to_name.get(node, node.rsplit(":", 1)[-1])


def build_graph(title_to_name, frames, raw_edges, stack_boundaries):
    definitions = defaultdict(list)
    for title in frames:
        definitions[title_to_name[title]].append(title)

    def resolve(title):
        if title in frames:
            return title
        name = node_name(title, title_to_name)
        matches = definitions.get(name, ())
        if len(matches) == 1:
            return matches[0]
        return title

    graph = defaultdict(set)
    incoming = defaultdict(set)
    nodes = set(frames)
    for source, target in raw_edges:
        source = resolve(source)
        target = resolve(target)
        nodes.add(source)
        nodes.add(target)
        if node_name(source, title_to_name) not in stack_boundaries:
            graph[source].add(target)
            incoming[target].add(source)
    return graph, incoming, nodes, definitions


def resolve_indirect_call_edges(
    graph, incoming, definitions, title_to_name, declared_edges
):
    for caller_name, target_names in declared_edges.items():
        callers = definitions.get(caller_name, ())
        if len(callers) != 1:
            raise ValueError(
                f"indirect call edge caller must resolve once: {caller_name}"
            )
        caller = callers[0]
        indirect = {
            target
            for target in graph.get(caller, ())
            if target == INDIRECT_NODE
        }
        if not indirect:
            raise ValueError(
                f"declared indirect call edge has no compiled call: {caller_name}"
            )
        for target in indirect:
            graph[caller].remove(target)
            incoming[target].discard(caller)
        for target_name in target_names:
            targets = definitions.get(target_name, ())
            if len(targets) != 1:
                raise ValueError(
                    "indirect call edge target must resolve once: "
                    f"{caller_name}={target_name}"
                )
            target = targets[0]
            graph.setdefault(caller, set()).add(target)
            incoming.setdefault(target, set()).add(caller)


def reachable_from(root, graph):
    reachable = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if node in reachable:
            continue
        reachable.add(node)
        pending.extend(graph.get(node, ()))
    return reachable


def validate_reachable_nodes(
    reachable,
    incoming,
    frames,
    title_to_name,
    stack_boundaries,
    allowed_indirect_callers,
):
    unknown = []
    for node in reachable:
        if node in frames or node_name(node, title_to_name) in stack_boundaries:
            continue
        if node == INDIRECT_NODE:
            callers = {
                node_name(source, title_to_name)
                for source in incoming.get(node, ())
                if source in reachable
            }
            if callers and callers <= allowed_indirect_callers:
                continue
            rendered = ", ".join(sorted(callers)) or "unknown caller"
            unknown.append(f"indirect call from {rendered}")
            continue
        unknown.append(node_name(node, title_to_name))
    if unknown:
        raise ValueError("unresolved reachable calls: " + ", ".join(sorted(unknown)))


def strongly_connected_components(graph, nodes):
    index = 0
    indexes = {}
    lowlinks = {}
    stack = []
    on_stack = set()
    components = []

    def visit(node):
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
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


def longest_path(
    root_name,
    graph,
    incoming,
    definitions,
    frames,
    title_to_name,
    stack_boundaries,
    allowed_indirect_callers,
    recursion_bounds,
):
    roots = definitions.get(root_name, ())
    if len(roots) != 1:
        raise ValueError(f"expected one {root_name} definition, found {len(roots)}")
    root = roots[0]
    if not graph.get(root):
        raise ValueError(f"{root_name} has no parsed outgoing calls")

    reachable = reachable_from(root, graph)
    validate_reachable_nodes(
        reachable,
        incoming,
        frames,
        title_to_name,
        stack_boundaries,
        allowed_indirect_callers,
    )
    reachable_graph = {
        source: {target for target in graph.get(source, ()) if target in reachable}
        for source in reachable
    }
    components = strongly_connected_components(reachable_graph, reachable)
    component_of = {}
    for component_id, members in enumerate(components):
        for member in members:
            component_of[member] = component_id

    weights = []
    labels = []
    for members in components:
        names = [node_name(member, title_to_name) for member in members]
        label = "/".join(sorted(names)) if names else "external"
        recursive = len(members) > 1 or (
            len(members) == 1 and members[0] in reachable_graph.get(members[0], ())
        )
        weight = sum(frames.get(member, 0) for member in members)
        if recursive:
            if len(members) != 1:
                raise ValueError(
                    "mutual recursion has no audited bound: "
                    + "/".join(sorted(names))
                )
            bound = recursion_bounds.get(names[0])
            if bound is None:
                raise ValueError(f"recursion has no audited bound: {names[0]}")
            weight *= bound
            label += f"[x{bound}]"
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
                best_size = target_size
                best_path = target_path
        return weights[component_id] + best_size, (component_id,) + best_path

    size, component_path = visit(component_of[root])
    return size, [labels[component_id] for component_id in component_path]


def main():
    args = parse_args()
    try:
        if (
            args.stack_size <= 0
            or args.boot_stack_size <= 0
            or args.guard_size <= 0
            or args.safety_margin < 0
        ):
            raise ValueError("stack, guard, and margin sizes must be valid")
        if (
            args.required_limit is not None
            and (
                args.required_limit <= 0
                or args.required_limit > args.stack_size
            )
        ):
            raise ValueError("required stack limit must fit the configured stack")
        if (
            args.required_baseline is not None
            and (
                args.required_baseline <= 0
                or args.required_baseline > args.stack_size
                or (
                    args.required_limit is not None
                    and args.required_baseline > args.required_limit
                )
            )
        ):
            raise ValueError("required stack baseline must fit the growth limit")
        if (
            args.boot_required_limit is not None
            and (
                args.boot_required_limit <= 0
                or args.boot_required_limit > args.boot_stack_size
            )
        ):
            raise ValueError(
                "boot stack required limit must fit the configured stack"
            )
        if (
            args.boot_required_baseline is not None
            and (
                args.boot_required_baseline <= 0
                or args.boot_required_baseline > args.boot_stack_size
                or (
                    args.boot_required_limit is not None
                    and args.boot_required_baseline
                    > args.boot_required_limit
                )
            )
        ):
            raise ValueError(
                "boot stack required baseline must fit the growth limit"
            )
        if (
            args.interrupt_entry < 256
            or args.interrupt_entry > 2047
            or args.interrupt_entry % 16 != 0
            or args.interrupt_entry > args.guard_size
        ):
            raise ValueError("interrupt entry frame is unsafe or exceeds the guard")
        recursion_bounds = parse_recursion_bounds(args.recursion_bound)
        indirect_call_edges = parse_indirect_call_edges(
            args.indirect_call_edge
        )
        stack_boundaries = set(args.stack_boundary)
        allowed_indirect_callers = set(args.allow_indirect_from)
        title_to_name, frames, raw_edges = read_callgraphs(
            args.callgraph_dir, args.source_dir, args.translation_unit
        )
        oversized = sorted(
            (node_name(title, title_to_name), size)
            for title, size in frames.items()
            if size > args.guard_size
        )
        if oversized:
            details = ", ".join(f"{name} ({size})" for name, size in oversized)
            raise ValueError(f"stack frames exceed guard size: {details}")
        graph, incoming, nodes, definitions = build_graph(
            title_to_name, frames, raw_edges, stack_boundaries
        )
        resolve_indirect_call_edges(
            graph, incoming, definitions, title_to_name,
            indirect_call_edges,
        )
        user_size, user_path = longest_path(
            "usertrap",
            graph,
            incoming,
            definitions,
            frames,
            title_to_name,
            stack_boundaries,
            allowed_indirect_callers,
            recursion_bounds,
        )
        interrupt_size, interrupt_path = longest_path(
            "kerneltrap",
            graph,
            incoming,
            definitions,
            frames,
            title_to_name,
            stack_boundaries,
            allowed_indirect_callers,
            recursion_bounds,
        )
        boot_size, boot_path = longest_path(
            args.boot_root,
            graph,
            incoming,
            definitions,
            frames,
            title_to_name,
            stack_boundaries,
            allowed_indirect_callers,
            recursion_bounds,
        )
    except ValueError as error:
        print(f"kernel stack check failed: {error}", file=sys.stderr)
        return 1

    required = user_size + args.interrupt_entry + interrupt_size + args.safety_margin
    boot_required = (
        boot_size + args.interrupt_entry + interrupt_size + args.safety_margin
    )
    print(
        "kernel stack budget: "
        f"user={user_size} interrupt={args.interrupt_entry + interrupt_size} "
        f"margin={args.safety_margin} required={required} limit={args.stack_size}"
    )
    if args.required_limit is not None:
        print(f"kernel stack growth limit: {args.required_limit}")
    if args.required_baseline is not None:
        print(f"kernel stack calibrated baseline: {args.required_baseline}")
    print("kernel stack user path: " + " -> ".join(user_path))
    print("kernel stack interrupt path: kernelvec -> " + " -> ".join(interrupt_path))
    print(
        "boot stack budget: "
        f"root={args.boot_root} path={boot_size} "
        f"interrupt={args.interrupt_entry + interrupt_size} "
        f"margin={args.safety_margin} required={boot_required} "
        f"limit={args.boot_stack_size}"
    )
    if args.boot_required_limit is not None:
        print(f"boot stack growth limit: {args.boot_required_limit}")
    if args.boot_required_baseline is not None:
        print(
            "boot stack calibrated baseline: "
            f"{args.boot_required_baseline}"
        )
    print("boot stack root path: " + " -> ".join(boot_path))
    if required > args.stack_size:
        print(
            f"kernel stack check failed: required {required} bytes, "
            f"configured {args.stack_size}",
            file=sys.stderr,
        )
        return 1
    if args.required_limit is not None and required > args.required_limit:
        print(
            f"kernel stack check failed: required {required} bytes, "
            f"growth limit {args.required_limit}",
            file=sys.stderr,
        )
        return 1
    if (
        args.required_baseline is not None
        and required * 100 < args.required_baseline * 98
    ):
        print(
            f"kernel stack check failed: required {required} bytes is below "
            f"98% of baseline {args.required_baseline}; tighten the budget",
            file=sys.stderr,
        )
        return 1
    if boot_required > args.boot_stack_size:
        print(
            f"boot stack check failed: required {boot_required} bytes, "
            f"configured {args.boot_stack_size}",
            file=sys.stderr,
        )
        return 1
    if (
        args.boot_required_limit is not None
        and boot_required > args.boot_required_limit
    ):
        print(
            f"boot stack check failed: required {boot_required} bytes, "
            f"growth limit {args.boot_required_limit}",
            file=sys.stderr,
        )
        return 1
    if (
        args.boot_required_baseline is not None
        and boot_required * 100 < args.boot_required_baseline * 98
    ):
        print(
            f"boot stack check failed: required {boot_required} bytes is below "
            f"98% of baseline {args.boot_required_baseline}; "
            "tighten the budget",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
