#!/usr/bin/env python3
"""Mutation and model guards for causal audit-record retention."""

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = (ROOT / "os/agent_observe_ledger.c").read_text(encoding="utf-8")
STORE = (ROOT / "os/agent_observe_store.c").read_text(encoding="utf-8")
EVIDENCE = (ROOT / "host_tools/agent_observe_disk_evidence.py").read_text(
    encoding="utf-8"
)


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    marker = f"{name}("
    search = 0
    while True:
        start = source.find(marker, search)
        if start < 0:
            raise ContractError(f"missing function {name}")
        opening = source.find("(", start)
        depth = 0
        closing = -1
        for index in range(opening, len(source)):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        brace = source.find("{", closing)
        semicolon = source.find(";", closing)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            depth = 0
            for index in range(brace, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[brace + 1 : index]
            raise ContractError(f"unterminated function {name}")
        search = closing + 1


def compact(text: str) -> str:
    return " ".join(text.split())


def require(text: str, token: str, label: str) -> None:
    if token not in text:
        raise ContractError(f"{label}: missing {token}")


def validate(ledger: str, store: str, evidence: str) -> None:
    compact_ledger = compact(ledger)
    for token in (
        "#define AGENT_AUDIT_LOW_PRINCIPAL_RESERVE \\",
        "(AGENT_AUDIT_LOW_SCOPE_LIMIT / AGENT_AUDIT_SCOPE_PRINCIPALS)",
        "#define AGENT_AUDIT_LOW_PRINCIPAL_LIMIT \\",
        "(2 * AGENT_AUDIT_LOW_PRINCIPAL_RESERVE)",
        "#define AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT AGENT_AUDIT_LOW_PRINCIPAL_LIMIT",
        "AGENT_AUDIT_LOW_SCOPE_LIMIT % AGENT_AUDIT_SCOPE_PRINCIPALS == 0",
        "AGENT_AUDIT_LOW_PRINCIPAL_RESERVE > AGENT_AUDIT_KIND_PREFETCH",
        "AGENT_AUDIT_LOW_PRINCIPAL_LIMIT > AGENT_OBSERVE_CHECKPOINT_PER_SCOPE",
    ):
        require(compact_ledger, token, "derived low-principal capacity")

    principal = compact(
        function_body(ledger, "agent_observe_audit_principal_victim")
    )
    for token in (
        "ushort principal_slots[AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT];",
        "principal_slot_count < AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT",
        "int correlated = span_id != 0 && span_owner != 0;",
        "record_correlated = record->span_id != 0 && agent_audit_span_owners[slot] != 0;",
        "if (!low_class) return oldest_principal;",
        "if (!correlated) return oldest_spanless;",
        "if (oldest_spanless >= 0) return oldest_spanless;",
        "if (oldest_same_kind >= 0) return oldest_same_kind;",
        "left->span_id == right->span_id",
        "left->kind == right->kind",
        "if (oldest_other_same_kind >= 0) return oldest_other_same_kind;",
        "if (span_records > 1) return left_slot;",
    ):
        require(principal, token, "principal causal victim policy")
    order = [
        principal.index("if (!correlated) return oldest_spanless;"),
        principal.index("if (oldest_spanless >= 0) return oldest_spanless;"),
        principal.index("if (oldest_same_kind >= 0) return oldest_same_kind;"),
        principal.index("left->span_id == right->span_id"),
        principal.index("if (oldest_other_same_kind >= 0) return oldest_other_same_kind;"),
        principal.index("if (span_records > 1) return left_slot;"),
    ]
    if order != sorted(order):
        raise ContractError("principal causal victim priority changed")

    departed = compact(
        function_body(ledger, "agent_observe_audit_departed_principal_victim")
    )
    for token in (
        "agent_audit_low_class[slot] != low_class",
        "agent_audit_live_principals(state->scope_id, live_principals, &live_principal_count)",
        "live_principals[i] == record_principal",
        "if (departed_principal == 0) departed_principal = record_principal;",
        "if (low_class && oldest_spanless >= 0) return oldest_spanless;",
        "return agent_observe_audit_principal_victim( state, departed_principal, low_class, span_id, span_owner, kind);",
    ):
        require(departed, token, "departed-principal isolation")
    if "agent_audit_principal_live(" in departed:
        raise ContractError("departed selection rescans the process table per record")

    overflow = compact(
        function_body(ledger, "agent_observe_audit_overflow_principal_victim")
    )
    for token in (
        "candidate == 0 || candidate == principal",
        "agent_audit_principals[other] == candidate) owned++;",
        "if (owned <= AGENT_AUDIT_LOW_PRINCIPAL_RESERVE) continue;",
        "agent_observe_audit_principal_victim( state, candidate, 1, span_id, span_owner, kind)",
        "if (victim >= 0) return victim;",
    ):
        require(overflow, token, "borrowed-overflow reclaim policy")
    live_snapshot = compact(function_body(ledger, "agent_audit_live_principals"))
    for token in (
        "for (struct proc *p = pool; p < &pool[NPROC]; p++)",
        "if (count >= AGENT_AUDIT_SCOPE_PRINCIPALS) return -1;",
        "principals[count++] = principal;",
    ):
        require(live_snapshot, token, "bounded live-principal snapshot")

    allocator = compact(function_body(ledger, "agent_observe_audit_slot_alloc"))
    for token in (
        "principal_owned >= (low_class ? AGENT_AUDIT_LOW_PRINCIPAL_LIMIT : AGENT_AUDIT_HIGH_PRINCIPAL_LIMIT)",
        "agent_observe_audit_principal_victim( state, principal, low_class, span_id, span_owner, kind)",
        "if (low_class && low_owned >= AGENT_AUDIT_LOW_SCOPE_LIMIT)",
        "principal_owned != 0 ?",
        "agent_observe_audit_departed_principal_victim( state, principal, low_class, span_id, span_owner, kind)",
        "agent_observe_audit_overflow_principal_victim( state, principal, span_id, span_owner, kind)",
        "return -1;",
    ):
        require(allocator, token, "scope/principal allocation policy")
    if "oldest_low_slot" in allocator:
        raise ContractError("scope-full low allocation can still evict arbitrary evidence")
    low_start = allocator.index(
        "if (low_class && low_owned >= AGENT_AUDIT_LOW_SCOPE_LIMIT)"
    )
    high_start = allocator.index(
        "if (!low_class && high_owned >= AGENT_AUDIT_HIGH_SCOPE_LIMIT)",
        low_start,
    )
    low_full = allocator[low_start:high_start]
    require(
        low_full,
        "agent_observe_audit_departed_principal_victim( state, principal, low_class, span_id, span_owner, kind)",
        "low scope-full departed isolation",
    )
    departed_call = low_full.index(
        "agent_observe_audit_departed_principal_victim("
    )
    overflow_call = low_full.index(
        "agent_observe_audit_overflow_principal_victim(", departed_call
    )
    self_roll = low_full.index("principal_owned != 0 ?", overflow_call)
    if not departed_call < overflow_call < self_roll:
        raise ContractError(
            "low scope must reclaim departed state, then borrowed overflow, "
            "before rolling its own share"
        )

    restore = compact(function_body(ledger, "agent_observe_checkpoint_restore_scope"))
    entry_validate = compact(
        function_body(ledger, "agent_observe_checkpoint_entry_validate")
    )
    emit = compact(function_body(ledger, "agent_audit_emit"))
    require(
        restore,
        "agent_audit_scopes[slot] == VFS_SCOPE_NONE",
        "checkpoint restore free-slot preflight",
    )
    require(
        restore,
        "agent_audit_identity_classes[slot] = entry->identity_class",
        "checkpoint restore identity class",
    )
    if "agent_observe_audit_slot_alloc(" in restore:
        raise ContractError("checkpoint restore may evict live audit evidence")
    require(
        emit,
        "scope_state, principal, low_class, span_id, span_owner, kind",
        "runtime emit victim key",
    )
    require(
        emit,
        "if (span_id == 0 || span_owner == 0) { span_id = 0; span_owner = 0; }",
        "runtime span-key normalization",
    )

    require(
        entry_validate,
        "((record->span_id == 0) != (entry->span_owner == 0))",
        "kernel disk span-key validation",
    )
    require(
        compact(evidence),
        "or ((span_id == 0) != (span_owner == 0))",
        "host disk span-key validation",
    )


@dataclass(frozen=True)
class Record:
    principal: int
    span: int
    owner: int
    kind: int
    low: bool = True


class RetentionModel:
    principal_reserve = 8
    principal_limit = 16
    low_scope_limit = 64

    def __init__(self) -> None:
        self.records: list[Record] = []
        self.live: set[int] = set()

    @staticmethod
    def correlated(record: Record) -> bool:
        return record.span != 0 and record.owner != 0

    def principal_victim(
        self, incoming: Record, victim_principal: int | None = None
    ) -> int | None:
        principal = incoming.principal if victim_principal is None else victim_principal
        slots = [
            index
            for index, record in enumerate(self.records)
            if record.principal == principal and record.low == incoming.low
        ]
        if not incoming.low:
            return slots[0] if slots else None
        incoming_correlated = self.correlated(incoming)
        spanless = [index for index in slots if not self.correlated(self.records[index])]
        if not incoming_correlated:
            return spanless[0] if spanless else None
        if spanless:
            return spanless[0]
        same_kind = [
            index
            for index in slots
            if (self.records[index].span, self.records[index].owner,
                self.records[index].kind)
            == (incoming.span, incoming.owner, incoming.kind)
        ]
        if same_kind:
            return same_kind[0]
        bucket_counts: dict[tuple[int, int, int], int] = {}
        for index in slots:
            record = self.records[index]
            key = (record.span, record.owner, record.kind)
            bucket_counts[key] = bucket_counts.get(key, 0) + 1
        duplicate = [
            index
            for index in slots
            if bucket_counts[(self.records[index].span,
                              self.records[index].owner,
                              self.records[index].kind)] > 1
        ]
        if duplicate:
            return duplicate[0]
        other_same_kind = [
            index for index in slots if self.records[index].kind == incoming.kind
        ]
        if other_same_kind:
            return other_same_kind[0]
        span_counts: dict[tuple[int, int], int] = {}
        for index in slots:
            record = self.records[index]
            key = (record.span, record.owner)
            span_counts[key] = span_counts.get(key, 0) + 1
        multi_span = [
            index
            for index in slots
            if span_counts[(self.records[index].span,
                            self.records[index].owner)] > 1
        ]
        if multi_span:
            return multi_span[0]
        other = [
            index
            for index in slots
            if (self.records[index].span, self.records[index].owner)
            != (incoming.span, incoming.owner)
        ]
        return other[0] if other else (slots[0] if slots else None)

    def departed_victim(self, incoming: Record) -> int | None:
        departed = [
            index
            for index, record in enumerate(self.records)
            if record.low == incoming.low
            and record.principal != incoming.principal
            and record.principal not in self.live
        ]
        if not departed:
            return None
        if incoming.low:
            spanless = [
                index for index in departed if not self.correlated(self.records[index])
            ]
            if spanless:
                return spanless[0]
            if not self.correlated(incoming):
                return None
        return self.principal_victim(
            incoming, self.records[departed[0]].principal
        )

    def overflow_victim(self, incoming: Record) -> int | None:
        for record in self.records:
            candidate = record.principal
            if not record.low or candidate == incoming.principal:
                continue
            owned = sum(
                other.low and other.principal == candidate
                for other in self.records
            )
            if owned <= self.principal_reserve:
                continue
            victim = self.principal_victim(incoming, candidate)
            if victim is not None:
                return victim
        return None

    def add(self, incoming: Record) -> bool:
        owned = sum(
            record.principal == incoming.principal and record.low == incoming.low
            for record in self.records
        )
        class_limit = self.low_scope_limit if incoming.low else 64
        class_count = sum(record.low == incoming.low for record in self.records)
        victim = None
        if owned >= self.principal_limit:
            victim = self.principal_victim(incoming)
        elif class_count >= class_limit:
            victim = self.departed_victim(incoming)
            if victim is None and incoming.low:
                victim = self.overflow_victim(incoming)
            if victim is None and owned:
                victim = self.principal_victim(incoming)
        if owned >= self.principal_limit or class_count >= class_limit:
            if victim is None:
                return False
            self.records.pop(victim)
        self.records.append(incoming)
        return True


def model_tests() -> None:
    event_enqueue, event_consume, context, prefetch, sched = range(1, 6)
    model = RetentionModel()
    model.live.add(1)
    anchors = (
        Record(1, 10, 100, event_enqueue),
        Record(1, 10, 100, event_consume),
        Record(1, 10, 100, context),
        Record(1, 10, 100, prefetch),
    )
    for record in anchors + (Record(1, 10, 100, context),) * 4:
        assert model.add(record)
    for _ in range(40):
        assert model.add(Record(1, 10, 100, context))
    kinds = {record.kind for record in model.records}
    assert {event_enqueue, event_consume, context, prefetch} <= kinds
    assert len(model.records) == model.principal_limit

    for _ in range(40):
        assert not model.add(Record(1, 0, 0, sched))
    assert all(RetentionModel.correlated(record) for record in model.records)

    for kind in (event_enqueue, event_consume, context, prefetch):
        assert model.add(Record(1, 11, 101, kind))
    assert sum(record.span == 11 for record in model.records) == 4
    assert sum(record.span == 10 for record in model.records) == 12
    for _ in range(40):
        assert model.add(Record(1, 11, 101, context))
    old_kinds = {record.kind for record in model.records if record.span == 10}
    new_kinds = {record.kind for record in model.records if record.span == 11}
    assert {event_enqueue, event_consume} <= old_kinds
    assert {event_enqueue, event_consume, context, prefetch} <= new_kinds

    burst = RetentionModel()
    burst.live.add(1)
    for offset in range(burst.principal_limit):
        assert burst.add(Record(1, 100 + offset, 1, offset % 5 + 1))
    assert sum(record.principal == 1 for record in burst.records) == 16
    assert len(burst.records) > burst.principal_reserve

    late_duplicate = RetentionModel()
    late_duplicate.live.add(1)
    for offset in range(8):
        late_duplicate.records.append(Record(1, offset + 1, 1, offset + 1))
    late_duplicate.records.extend((Record(1, 90, 1, 90),) * 2)
    for offset in range(10, late_duplicate.principal_limit):
        late_duplicate.records.append(Record(1, offset + 1, 1, offset + 1))
    assert len(late_duplicate.records) == late_duplicate.principal_limit
    assert late_duplicate.principal_victim(Record(1, 100, 1, 100)) == 8

    overflow_scope = RetentionModel()
    overflow_scope.live = set(range(1, 9))
    for offset in range(overflow_scope.principal_limit):
        assert overflow_scope.add(
            Record(1, 100 + offset, 1, offset % 5 + 1)
        )
    for principal in range(2, 8):
        for offset in range(overflow_scope.principal_reserve):
            assert overflow_scope.add(
                Record(principal, principal * 100 + offset, principal,
                       offset % 5 + 1)
            )
    assert len(overflow_scope.records) == overflow_scope.low_scope_limit
    protected = {
        principal: sum(
            record.principal == principal for record in overflow_scope.records
        )
        for principal in range(2, 8)
    }
    for offset in range(overflow_scope.principal_reserve):
        assert overflow_scope.add(
            Record(8, 800 + offset, 8, offset % 5 + 1)
        )
        assert sum(
            record.principal == 1 for record in overflow_scope.records
        ) == overflow_scope.principal_limit - offset - 1
        assert all(
            sum(record.principal == principal
                for record in overflow_scope.records) == count
            for principal, count in protected.items()
        )
    before_guarantees = {
        principal: sum(
            record.principal == principal for record in overflow_scope.records
        )
        for principal in range(1, 8)
    }
    assert overflow_scope.add(Record(8, 900, 8, event_enqueue))
    assert all(
        sum(record.principal == principal
            for record in overflow_scope.records) == count
        for principal, count in before_guarantees.items()
    )
    assert sum(record.principal == 8 for record in overflow_scope.records) == 8

    scope = RetentionModel()
    scope.live = set(range(1, 10))
    for principal in range(1, 9):
        for offset in range(8):
            assert scope.add(Record(principal, principal * 10 + offset,
                                    principal, offset % 5 + 1))
    before = tuple(scope.records)
    assert not scope.add(Record(9, 9, 9, event_enqueue))
    assert tuple(scope.records) == before
    scope.live.remove(8)
    for offset in range(8):
        assert scope.add(Record(9, 90, 9, offset % 5 + 1))
        assert sum(record.principal == 9 for record in scope.records) == offset + 1
        assert sum(record.principal == 8 for record in scope.records) == 7 - offset
    assert len(scope.records) == scope.low_scope_limit
    assert any(record.principal == 9 for record in scope.records)
    assert all(record.principal != 8 for record in scope.records)

    causal_takeover = RetentionModel()
    causal_takeover.live = set(range(1, 8)) | {9}
    for principal in range(1, 8):
        for offset in range(8):
            assert causal_takeover.add(
                Record(principal, principal * 10 + offset, principal,
                       offset % 5 + 1)
            )
    assert causal_takeover.add(Record(8, 80, 8, event_enqueue))
    for _ in range(7):
        assert causal_takeover.add(Record(8, 80, 8, context))
    assert causal_takeover.add(Record(9, 90, 9, event_enqueue))
    assert any(
        record.principal == 8 and record.kind == event_enqueue
        for record in causal_takeover.records
    )

    high = RetentionModel()
    high.live.add(1)
    for offset in range(8):
        assert high.add(Record(1, 20 + offset, 200,
                               offset % 5 + 1, low=False))
    assert high.add(Record(1, 0, 0, sched, low=False))
    assert high.records[-1].kind == sched


validate(LEDGER, STORE, EVIDENCE)
model_tests()

mutations = (
    (
        LEDGER.replace(
            "#define AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT AGENT_AUDIT_LOW_PRINCIPAL_LIMIT",
            "#define AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT AGENT_AUDIT_SCOPE_PRINCIPALS",
            1,
        ),
        STORE,
        EVIDENCE,
        "causal victim scan ignores borrowed burst slots",
    ),
    (
        LEDGER.replace(
            "#define AGENT_AUDIT_LOW_PRINCIPAL_LIMIT \\\n\t(2 * AGENT_AUDIT_LOW_PRINCIPAL_RESERVE)",
            "#define AGENT_AUDIT_LOW_PRINCIPAL_LIMIT \\\n\tAGENT_OBSERVE_CHECKPOINT_PER_SCOPE",
            1,
        ),
        STORE,
        EVIDENCE,
        "borrowable burst collapsed to the checkpoint window",
    ),
    (
        LEDGER.replace(
            "int correlated = span_id != 0 && span_owner != 0;",
            "int correlated = span_id != 0 || span_owner != 0;",
            1,
        ),
        STORE,
        EVIDENCE,
        "half-correlated runtime key accepted",
    ),
    (
        LEDGER.replace(
            "if (!correlated)\n\t\treturn oldest_spanless;",
            "if (!correlated)\n\t\treturn oldest_principal;",
            1,
        ),
        STORE,
        EVIDENCE,
        "spanless telemetry can erase a trace",
    ),
    (
        LEDGER.replace(
            "victim = agent_observe_audit_departed_principal_victim(\n\t\t\tstate, principal, low_class, span_id, span_owner, kind);",
            "return state->sequence_slots[0];",
            1,
        ),
        STORE,
        EVIDENCE,
        "scope-full allocation steals an arbitrary principal",
    ),
    (
        LEDGER.replace(
            "\t\tvictim = agent_observe_audit_overflow_principal_victim(\n"
            "\t\t\tstate, principal, span_id, span_owner, kind);\n"
            "\t\tif (victim >= 0)\n"
            "\t\t\treturn victim;\n",
            "",
            1,
        ),
        STORE,
        EVIDENCE,
        "scope-full allocation cannot reclaim borrowed overflow",
    ),
    (
        LEDGER.replace(
            "if (!low_class)\n\t\treturn oldest_principal;",
            "if (!low_class)\n\t\treturn oldest_spanless;",
            1,
        ),
        STORE,
        EVIDENCE,
        "protected spanless effect can be dropped",
    ),
    (
        LEDGER.replace(
            "agent_audit_identity_classes[slot] = entry->identity_class;",
            "agent_audit_identity_classes[slot] = AGENT_OBSERVE_IDENTITY_TELEMETRY;",
            1,
        ),
        STORE,
        EVIDENCE,
        "restore discarded the persisted identity class",
    ),
    (
        LEDGER.replace(
            "\t    ((record->span_id == 0) != (entry->span_owner == 0)) ||\n",
            "",
            1,
        ),
        STORE,
        EVIDENCE,
        "kernel restore accepts a half span key",
    ),
    (
        LEDGER,
        STORE,
        EVIDENCE.replace(
            "                or ((span_id == 0) != (span_owner == 0))\n",
            "",
            1,
        ),
        "host verifier accepts a half span key",
    ),
)

for ledger, store, evidence, label in mutations:
    try:
        validate(ledger, store, evidence)
    except ContractError:
        continue
    raise SystemExit(f"mutation survived: {label}")

print(f"[observe-span-retention] model and {len(mutations)} mutations passed")
