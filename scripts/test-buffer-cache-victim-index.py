#!/usr/bin/env python3
"""有界 buffer-cache 牺牲项选择的静态与变异契约。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIO = (ROOT / "os/bio.c").read_text(encoding="utf-8")
BIO_H = (ROOT / "os/bio.h").read_text(encoding="utf-8")


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
        if closing >= 0 and brace >= 0 and (semicolon < 0 or brace < semicolon):
            depth = 0
            for index in range(brace, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[brace + 1:index]
            raise ContractError(f"unterminated function {name}")
        search = max(closing + 1, start + len(marker))


def compact(value: str) -> str:
    return " ".join(value.split())


def replace_in_function(source: str, name: str, old: str, new: str) -> str:
    body = function_body(source, name)
    if old not in body:
        raise ContractError(f"mutation anchor drift: {name}: {old}")
    return source.replace(body, body.replace(old, new, 1), 1)


def ordered(body: str, *tokens: str) -> None:
    cursor = 0
    for token in tokens:
        position = body.find(token, cursor)
        if position < 0:
            raise ContractError(f"ordering contract missing: {tokens}")
        cursor = position + len(token)


def validate(bio: str, bio_h: str) -> None:
    for token in (
        "struct bio_idle_queue {",
        "struct bio_idle_queue idle;",
        "struct bio_idle_queue free_idle;",
        "struct bio_idle_queue reserved_idle;",
        "uint cache_donor_cursor;",
        "uint deferred_references;",
        "victim_candidates_examined",
        "max_victim_candidates_per_miss",
        "bio_cache_integrity_seen[(NBUF + 63U) / 64U]",
        "#define IO_OWNER_SLOTS (VFS_SCOPE_LIFECYCLE_CAP + 2)",
    ):
        if token not in bio:
            raise ContractError(f"victim index state missing {token}")
    for token in (
        "unsigned int lru_promote : 1;",
        "unsigned int transient : 1;",
        "unsigned int background_reserved : 1;",
        "unsigned int idle_class : 2;",
        "uint64 victim_candidates_examined;",
        "uint max_victim_candidates_per_miss;",
    ):
        if token not in bio_h:
            raise ContractError(f"victim evidence ABI missing {token}")

    push = compact(function_body(bio, "bio_idle_push"))
    remove = compact(function_body(bio, "bio_cache_idle_remove"))
    if "for (" in push or "for (" in remove:
        raise ContractError("idle queue primitives are not constant time")
    for token in (
        "queue->head = b",
        "queue->tail = b",
        "queue->count++",
    ):
        if token not in push:
            raise ContractError(f"idle enqueue missing {token}")
    for token in (
        "b->prev->next = b->next",
        "b->next->prev = b->prev",
        "queue->count--",
        "b->idle_class = BIO_IDLE_NONE",
    ):
        if token not in remove:
            raise ContractError(f"idle removal missing {token}")

    donor = compact(function_body(bio, "bio_cache_donor_candidate"))
    for token in (
        "offset < IO_OWNER_SLOTS",
        "io_policy.cache_donor_cursor + offset",
        "state->idle.tail == 0",
        "!bio_cache_state_retained(state)",
        "count <= bio_cache_state_floor(state)",
        "io_policy.cache_donor_cursor = (slot + 1) % IO_OWNER_SLOTS",
        "return state->idle.tail",
    ):
        if token not in donor:
            raise ContractError(f"bounded donor selection missing {token}")
    if "< NBUF" in donor or "bcache.buf" in donor:
        raise ContractError("donor selection scans cache capacity")
    if "bio_cache_owner_retained" in donor:
        raise ContractError("donor selection nests an owner-table scan")

    bget = compact(function_body(bio, "bget"))
    for forbidden in ("bcache.head", "bcache.buf", "scanned++", "< NBUF"):
        if forbidden in bget:
            raise ContractError(f"cache miss regressed to a full scan: {forbidden}")
    ordered(
        bget,
        "b = bio_cache_hash_find(dev, blockno)",
        "if (b->refcnt == 0) bio_cache_idle_remove(b)",
        "owner_count = bio_cache_state_count(owner_state)",
        "b = bcache.free_idle.tail",
        "b = bio_cache_donor_candidate(owner, &examined)",
        "b = owner_state->idle.tail",
        "bio_cache_record_victim_probe(examined)",
        "bio_cache_idle_remove(b)",
        "bio_cache_assign( b, owner, !transient",
    )
    for token in (
        "if (bio_background_current())",
        "b = bcache.reserved_idle.tail",
        "owner_count < owner_cap",
        "free_scanned = 1",
        "if (b != 0) transient = 1",
        "wait_queue_sleep_irq_uninterruptible(&cache_waiters)",
        "bio_cache_idle_enqueue(b, 0); bio_background_cache_blocked()",
    ):
        if token not in bget:
            raise ContractError(f"victim policy missing {token}")
    if bget.count("transient = 1") != 3:
        raise ContractError("free and donor transient fallbacks are incomplete")

    probe = compact(function_body(bio, "bio_cache_record_victim_probe"))
    for token in (
        "examined > IO_OWNER_SLOTS + BIO_CACHE_VICTIM_PROBE_OVERHEAD",
        "io_policy.victim_candidates_examined += examined",
        "examined > io_policy.max_victim_candidates_per_miss",
    ):
        if token not in probe:
            raise ContractError(f"bounded victim evidence missing {token}")

    invalidate = compact(function_body(bio, "bio_cache_invalidate"))
    ordered(
        invalidate,
        "bio_cache_idle_remove(b)",
        "bio_cache_hash_remove(b)",
        "bio_cache_uncharge(b)",
        "b->cache_owner = FS_OWNER_NONE",
        "bio_cache_idle_enqueue(b, 1)",
        "bio_cache_note_progress()",
    )
    release = compact(function_body(bio, "bio_cache_release_closed_owner"))
    for token in (
        "state = bio_cache_owner_state(owner)",
        "while (state->idle.tail != 0)",
        "bio_cache_invalidate(state->idle.tail)",
    ):
        if token not in release:
            raise ContractError(f"closed owner still needs a cache scan: {token}")
    if "NBUF" in release or "bcache.buf" in release:
        raise ContractError("closed owner release scans cache capacity")

    reserve = compact(function_body(bio, "bio_background_reserve_buffers"))
    release_reserved = compact(function_body(bio, "bio_background_release_buffers"))
    for body, label in ((reserve, "reserve"), (release_reserved, "release")):
        if "bcache.buf" in body or "< NBUF" in body or "bcache.head" in body:
            raise ContractError(f"background {label} scans cache capacity")
    ordered(
        reserve,
        "candidate = bcache.free_idle.tail",
        "candidate = bio_cache_donor_candidate(owner, 0)",
        "candidate = owner_state->idle.tail",
        "bio_cache_idle_remove(candidate)",
        "bio_cache_assign(candidate, owner, 1, 1)",
        "bio_cache_hash_remove(candidate)",
        "candidate->background_reserved = 1",
        "bio_cache_idle_enqueue(candidate, 1)",
    )
    if "while (bcache.reserved_idle.tail != 0)" not in release_reserved:
        raise ContractError("reserved cleanup buffers lack a dedicated queue")

    brelse = compact(function_body(bio, "brelse"))
    for token in (
        "if (!b->valid || b->transient || !bio_cache_owner_retained(b->cache_owner))",
        "bio_cache_invalidate(b)",
        "bio_cache_idle_enqueue(b, promote)",
        "wait_queue_wake_all(&cache_waiters)",
    ):
        if token not in brelse:
            raise ContractError(f"release transition missing {token}")
    bpin = compact(function_body(bio, "bpin"))
    bunpin = compact(function_body(bio, "bunpin"))
    if "if (b->refcnt == 0) bio_cache_idle_remove(b)" not in bpin:
        raise ContractError("pin can leave an active buffer on an idle queue")
    if "bio_cache_idle_enqueue(b, 0)" not in bunpin:
        raise ContractError("final unpin can strand an idle buffer")

    integrity = compact(function_body(bio, "bio_cache_assert_integrity"))
    for token in (
        "BIO_IDLE_FREE",
        "BIO_IDLE_RESERVED",
        "BIO_IDLE_OWNER",
        "linked != marked",
        "memset(bio_cache_integrity_seen, 0",
        "panic(\"buffer idle reachability\")",
        "(b->refcnt == 0) != (b->idle_class != BIO_IDLE_NONE)",
    ):
        if token not in integrity:
            raise ContractError(f"queue integrity proof missing {token}")
    for hot in ("bget", "brelse", "bpin", "bunpin"):
        if "bio_cache_assert_integrity()" in function_body(bio, hot):
            raise ContractError(f"capacity-sized integrity scan entered {hot}")
    for transition in ("binit", "bio_scope_quiesce", "bio_scope_retire",
                       "bio_background_end"):
        if "bio_cache_assert_integrity()" not in function_body(bio, transition):
            raise ContractError(f"{transition} omits cold-path integrity check")

    reap = compact(function_body(bio, "io_owner_reap_retired"))
    if ("state->idle.count != 0" not in reap or
            "state->deferred_references != 0" not in reap):
        raise ContractError("owner state can be reaped with linked idle buffers")
    retained = compact(function_body(bio, "bio_cache_state_retained"))
    if "if (state->deferred_references != 0) return 1" not in retained:
        raise ContractError("deferred cleanup loses its closing cache floor")
    for name in ("bio_deferred_owner_retain_current",
                 "bio_deferred_owner_retain_cleanup"):
        retain = compact(function_body(bio, name))
        if ("state->deferred_references++" not in retain or
                "io_active_request_acquire(state)" not in retain):
            raise ContractError(f"{name} omits cleanup cache retention")
    deferred_release = compact(function_body(bio, "bio_deferred_owner_release"))
    ordered(
        deferred_release,
        "state->deferred_references--",
        "bio_cache_release_closed_owner(owner)",
        "bio_cache_note_progress()",
        "io_active_request_release(state)",
        "io_owner_reap_retired()",
    )
    snapshot = compact(function_body(bio, "bio_physical_snapshot"))
    for token in (
        "stats->victim_candidates_examined",
        "stats->max_victim_candidates_per_miss",
    ):
        if token not in snapshot:
            raise ContractError(f"victim evidence snapshot missing {token}")

    if "int bread_batch(uint dev" not in bio:
        raise ContractError("read batching was removed while indexing victims")


validate(BIO, BIO_H)

MUTATIONS = (
    (BIO.replace("struct bio_idle_queue free_idle;", "", 1), BIO_H,
     "free queue removed"),
    (BIO.replace("struct bio_idle_queue idle;", "", 1), BIO_H,
     "owner queue removed"),
    (replace_in_function(BIO, "bio_cache_donor_candidate",
                         "offset < IO_OWNER_SLOTS", "offset < NBUF"), BIO_H,
     "donor bound widened"),
    (replace_in_function(BIO, "bio_cache_donor_candidate",
                         "count <= bio_cache_state_floor(state)", "count == 0"), BIO_H,
     "owner floor bypassed"),
    (replace_in_function(BIO, "bget", "b = bcache.free_idle.tail;", "b = 0;"), BIO_H,
     "free queue bypassed"),
    (replace_in_function(BIO, "bget",
                         "b = bio_cache_donor_candidate(owner, &examined);", "b = 0;"), BIO_H,
     "donor queue bypassed"),
    (replace_in_function(BIO, "bget", "bio_cache_idle_remove(b);", ""), BIO_H,
     "active buffer left idle"),
    (replace_in_function(BIO, "bget", "transient = 1;", "transient = 0;"), BIO_H,
     "transient fallback removed"),
    (replace_in_function(BIO, "bio_cache_record_victim_probe",
                         "examined > IO_OWNER_SLOTS + BIO_CACHE_VICTIM_PROBE_OVERHEAD", "0"), BIO_H,
     "probe bound removed"),
    (replace_in_function(BIO, "bio_cache_invalidate",
                         "bio_cache_idle_enqueue(b, 1);", ""), BIO_H,
     "free recycle removed"),
    (replace_in_function(BIO, "bio_cache_release_closed_owner",
                         "while (state->idle.tail != 0)", "while (0)"), BIO_H,
     "closed owner recycle removed"),
    (replace_in_function(BIO, "bio_background_reserve_buffers",
                         "bio_cache_idle_remove(candidate);", ""), BIO_H,
     "reserved candidate not unlinked"),
    (replace_in_function(BIO, "bio_background_reserve_buffers",
                         "bio_cache_idle_enqueue(candidate, 1);", ""), BIO_H,
     "reserved queue publication removed"),
    (replace_in_function(BIO, "brelse", "bio_cache_idle_enqueue(b, promote);", ""), BIO_H,
     "release enqueue removed"),
    (replace_in_function(BIO, "bpin", "bio_cache_idle_remove(b);", ""), BIO_H,
     "pin dequeue removed"),
    (replace_in_function(BIO, "bunpin", "bio_cache_idle_enqueue(b, 0);", ""), BIO_H,
     "unpin enqueue removed"),
    (replace_in_function(BIO, "io_owner_reap_retired",
                         "state->idle.count != 0", "0"), BIO_H,
     "owner reap queue assertion removed"),
    (replace_in_function(BIO, "bio_cache_state_retained",
                         "if (state->deferred_references != 0)", "if (0)"), BIO_H,
     "deferred cleanup cache retention removed"),
    (replace_in_function(BIO, "bio_deferred_owner_release",
                         "bio_cache_release_closed_owner(owner);", ""), BIO_H,
     "deferred final cache release removed"),
    (replace_in_function(BIO, "bio_cache_assert_integrity",
                         "memset(bio_cache_integrity_seen, 0,", "memset(0, 0,"), BIO_H,
     "integrity visit bitmap removed"),
    (BIO, BIO_H.replace("uint max_victim_candidates_per_miss;", "", 1),
     "snapshot maximum removed"),
)

for mutated_bio, mutated_header, label in MUTATIONS:
    try:
        validate(mutated_bio, mutated_header)
    except ContractError:
        continue
    raise SystemExit(f"buffer victim mutation survived: {label}")

print(f"[buffer-cache-victim-index] {len(MUTATIONS)} mutations passed")
