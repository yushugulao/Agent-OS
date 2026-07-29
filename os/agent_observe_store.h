#ifndef AGENT_OBSERVE_STORE_H
#define AGENT_OBSERVE_STORE_H

#include "agent.h"
#include "workflow_lifecycle.h"

#define AGENT_OBSERVE_CHECKPOINT_MAGIC 0x41474f4253323031ULL
#define AGENT_OBSERVE_CHECKPOINT_VERSION 7U
#define AGENT_OBSERVE_CHECKPOINT_SCOPES 4U
#define AGENT_OBSERVE_CHECKPOINT_PER_SCOPE 8U
#define AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL 4U
#define AGENT_OBSERVE_CHECKPOINT_DIVERSITY_ANCHORS \
	(AGENT_OBSERVE_CHECKPOINT_PER_SCOPE - \
	 AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL)
#define AGENT_OBSERVE_CHECKPOINT_RECORDS \
	(AGENT_OBSERVE_CHECKPOINT_SCOPES * AGENT_OBSERVE_CHECKPOINT_PER_SCOPE)
#define AGENT_OBSERVE_RETENTION_CAUSAL_DIVERSITY 3U
#define AGENT_OBSERVE_RESERVED_SCOPE_SLOTS 1U
#define AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS \
	(AGENT_OBSERVE_CHECKPOINT_SCOPES - AGENT_OBSERVE_RESERVED_SCOPE_SLOTS)
#define AGENT_OBSERVE_RECOVERY_SCOPE_SLOT AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS

#define AGENT_OBSERVE_SCOPE_USED               (1U << 0)
#define AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR (1U << 1)
#define AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED    (1U << 2)
#define AGENT_OBSERVE_SCOPE_FLAGS_ALL \
	(AGENT_OBSERVE_SCOPE_USED | AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR | \
	 AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED)

#define AGENT_OBSERVE_ALLOC_AUDIT_EXHAUSTED   (1U << 0)
#define AGENT_OBSERVE_ALLOC_SPAN_EXHAUSTED    (1U << 1)
#define AGENT_OBSERVE_ALLOC_EVENT_EXHAUSTED   (1U << 2)
#define AGENT_OBSERVE_ALLOC_CONTROL_EXHAUSTED (1U << 3)
#define AGENT_OBSERVE_ALLOC_AGENT_EXHAUSTED   (1U << 4)
#define AGENT_OBSERVE_ALLOC_EXHAUSTED_ALL \
	(AGENT_OBSERVE_ALLOC_AUDIT_EXHAUSTED | \
	 AGENT_OBSERVE_ALLOC_SPAN_EXHAUSTED | \
	 AGENT_OBSERVE_ALLOC_EVENT_EXHAUSTED | \
	 AGENT_OBSERVE_ALLOC_CONTROL_EXHAUSTED | \
	 AGENT_OBSERVE_ALLOC_AGENT_EXHAUSTED)

#define AGENT_OBSERVE_IDENTITY_TELEMETRY 0U
#define AGENT_OBSERVE_IDENTITY_CAUSAL    1U
#define AGENT_OBSERVE_IDENTITY_AUTHORITY 2U
#define AGENT_OBSERVE_IDENTITY_MAX       AGENT_OBSERVE_IDENTITY_AUTHORITY

#define AGENT_OBSERVE_LINK_PREV_RETAINED (1U << 0)
#define AGENT_OBSERVE_LINK_LATEST_TAIL   (1U << 1)
#define AGENT_OBSERVE_LINK_FLAGS_ALL \
	(AGENT_OBSERVE_LINK_PREV_RETAINED | AGENT_OBSERVE_LINK_LATEST_TAIL)

struct agent_observe_checkpoint_scope {
	uint used;
	uint scope_id;
	uint lifecycle_id;
	uint record_count;
	uint64 lifecycle_generation;
	uint64 total_records;
	uint64 admission_drops;
	uint64 ledger_hash;
	struct agent_observe_checkpoint_entry {
		struct agent_audit_record record;
		uint scope_id;
		uchar identity_class;
		uchar link_flags;
		uchar reserved[2];
		uint64 principal;
		uint64 span_owner;
		uint64 receipt_id;
	} records[AGENT_OBSERVE_CHECKPOINT_PER_SCOPE];
};

struct agent_observe_checkpoint {
	uint64 magic;
	uint version;
	uint bytes;
	uint64 generation;
	uint64 audit_lease_end;
	uint64 span_lease_end;
	uint64 event_lease_end;
	uint64 control_lease_end;
	uint agent_lease_end;
	uint retention_policy;
	uint scope_count;
	uint allocator_exhausted;
	uint reserved_scope_slots;
	uint reserved;
	uint64 lifecycle_lease_ends[WORKFLOW_LIFECYCLE_CAP];
	struct agent_observe_checkpoint_scope
		scopes[AGENT_OBSERVE_CHECKPOINT_SCOPES];
	uint64 image_hash;
};

_Static_assert(sizeof(struct agent_observe_checkpoint) <= 8U * 1024U,
	       "observation checkpoint must fit the durable arena");

void agent_obsstore_init(void);
int agent_obsstore_storage_ready(void);
void agent_obsstore_lease_maintain(void);
void agent_obsstore_mark_dirty(uint);
int agent_obsstore_mark_dirty_receipt(
	uint, struct workflow_lifecycle_key, uint64 *, uint64 *);
int agent_obsstore_receipt_replicated(uint, uint64);
int agent_obsstore_receipt_persist(uint);
int agent_obsstore_receipt_record_status(
	uint, struct workflow_lifecycle_key, uint64, uint64, uint64, uint64);
int agent_obsstore_mark_reap(uint, struct workflow_lifecycle_key);

/* Accessors implemented by the authoritative in-memory observation owner. */
int agent_observe_checkpoint_capture_scope(
	uint, struct workflow_lifecycle_key,
	struct agent_observe_checkpoint_scope *);
int agent_observe_checkpoint_restore_scope(
	const struct agent_observe_checkpoint_scope *);
void agent_observe_checkpoint_raise_highwater(uint64, uint64, uint64, uint);
void agent_observe_checkpoint_exhaust_highwater(uint);
uint64 agent_observe_checkpoint_generation_get(void);
void agent_observe_checkpoint_generation_floor(uint64);
uint64 agent_observe_checkpoint_record_hash(
	const struct agent_audit_record *);
int agent_observe_checkpoint_entry_validate(
	const struct agent_observe_checkpoint_scope *, uint,
	const struct agent_observe_checkpoint_entry *,
	const struct agent_observe_checkpoint_entry *, int *);

#endif
