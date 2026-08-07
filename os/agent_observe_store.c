#include "agent_observe_store.h"
#include "agent_observe_capacity.h"
#include "agent_observe_persist_context.h"
#include "agent_observe_recovery_store.h"
#include "agent_durable_section.h"
#include "agent_identity_lease.h"
#include "agent_internal.h"
#include "agent_lifecycle.h"
#include "agent_metadata_internal.h"
#include "defs.h"
#include "proc.h"
#include "riscv.h"
#include "vfs_security.h"

struct agent_observe_disk_header {
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
};

struct agent_observe_scope_header {
	uint used;
	uint scope_id;
	uint lifecycle_id;
	uint record_count;
	uint64 lifecycle_generation;
	uint64 total_records;
	uint64 admission_drops;
	uint64 ledger_hash;
};

static int agent_observe_active_header(
	struct agent_observe_disk_header *, uint64 *);
static int agent_observe_active_scope(
	uint, struct agent_observe_scope_header *, uint64 *);

static int
agent_observe_lease_persist_bridge(uint64 *serial, uint64 *target)
{
	int replicated;

	if (serial == 0 || target == 0 || agent_metadata_txn_owned(0))
		return 0;
	if (*target == 0) {
		*target = agent_durable_section_mark_dirty_evidence(
			AGENT_DURABLE_SECTION_OBSERVE, VFS_SCOPE_SYSTEM,
			serial, AGENT_DURABLE_DIRTY_URGENT);
		if (*serial == 0)
			return -1;
		if (*target == 0) {
			(void)agent_durable_section_retry_pending();
			return 0;
		}
	}
	(void)agent_durable_section_persist_scope(VFS_SCOPE_SYSTEM);
	replicated = agent_durable_section_replicated(
		VFS_SCOPE_SYSTEM, *target);
	if (replicated > 0)
		return 1;
	if (replicated < 0)
		return -1;
	return 0;
}

static void
agent_observe_lease_snapshot(struct agent_observe_checkpoint *image)
{
	struct agent_identity_lease_snapshot snapshot;

	agent_identity_lease_snapshot(&snapshot);
	image->audit_lease_end = snapshot.ends[AGENT_IDENTITY_ALLOCATOR_AUDIT];
	image->span_lease_end = snapshot.ends[AGENT_IDENTITY_ALLOCATOR_SPAN];
	image->event_lease_end = snapshot.ends[AGENT_IDENTITY_ALLOCATOR_EVENT];
	image->control_lease_end =
		snapshot.ends[AGENT_IDENTITY_ALLOCATOR_CONTROL];
	image->agent_lease_end =
		(uint)snapshot.ends[AGENT_IDENTITY_ALLOCATOR_AGENT];
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++)
		image->lifecycle_lease_ends[i] = snapshot.lifecycle_ends[i];
}

_Static_assert(__builtin_offsetof(struct agent_observe_checkpoint, scopes) ==
	       sizeof(struct agent_observe_disk_header),
	       "observation checkpoint header layout");
_Static_assert(__builtin_offsetof(struct agent_observe_checkpoint_scope,
				  records) ==
	       sizeof(struct agent_observe_scope_header),
	       "observation checkpoint scope layout");
_Static_assert(sizeof(struct agent_observe_checkpoint) <=
	       AGENT_DURABLE_PAYLOAD_BYTES,
	       "observation checkpoint must fit the durable arena payload");
_Static_assert(AGENT_OBSERVE_CHECKPOINT_SCOPES >=
	       AGENT_OBSERVE_RECOVERY_MAX_SCOPES,
	       "checkpoint must cover the recovery ABI window");
_Static_assert(sizeof(struct agent_observe_checkpoint) == 7592 &&
	       sizeof(struct agent_observe_checkpoint_scope) == 1488 &&
	       sizeof(struct agent_observe_checkpoint_entry) == 240,
	       "observation checkpoint v8 disk geometry");

static uint64
agent_observe_store_hash(const struct agent_observe_checkpoint *image)
{
	const uchar *p = (const uchar *)image;
	uint64 hash = 1469598103934665603ULL;
	uint bytes = __builtin_offsetof(struct agent_observe_checkpoint,
					  image_hash);

	for (uint i = 0; i < bytes; i++) {
		hash ^= p[i];
		hash *= 1099511628211ULL;
	}
	return hash ? hash : 1;
}

static int
agent_observe_key_equal(struct workflow_lifecycle_key key,
			const struct agent_observe_checkpoint_scope *scope)
{
	return scope->lifecycle_id == key.id &&
	       scope->lifecycle_generation == key.generation;
}

static void
agent_observe_store_header_refresh(struct agent_observe_checkpoint *image)
{
	uint scope_count = 0;

	image->magic = AGENT_OBSERVE_CHECKPOINT_MAGIC;
	image->version = AGENT_OBSERVE_CHECKPOINT_VERSION;
	image->bytes = sizeof(*image);
	image->generation = agent_observe_checkpoint_generation_get();
	agent_observe_lease_snapshot(image);
	image->allocator_exhausted = 0;
	if (image->audit_lease_end == 0)
		image->allocator_exhausted |= AGENT_OBSERVE_ALLOC_AUDIT_EXHAUSTED;
	if (image->span_lease_end == 0)
		image->allocator_exhausted |= AGENT_OBSERVE_ALLOC_SPAN_EXHAUSTED;
	if (image->event_lease_end == 0)
		image->allocator_exhausted |= AGENT_OBSERVE_ALLOC_EVENT_EXHAUSTED;
	if (image->control_lease_end == 0)
		image->allocator_exhausted |= AGENT_OBSERVE_ALLOC_CONTROL_EXHAUSTED;
	if (image->agent_lease_end == 0)
		image->allocator_exhausted |= AGENT_OBSERVE_ALLOC_AGENT_EXHAUSTED;
	image->retention_policy = AGENT_OBSERVE_RETENTION_CAUSAL_DIVERSITY;
	image->reserved_scope_slots =
		AGENT_OBSERVE_RESERVED_SCOPE_SLOTS;
	image->reserved = 0;
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++)
		if (image->scopes[i].used & AGENT_OBSERVE_SCOPE_USED)
			scope_count++;
	image->scope_count = scope_count;
	image->image_hash = agent_observe_store_hash(image);
}

static int
agent_observe_store_validate(const void *src, uint bytes)
{
	const struct agent_observe_checkpoint *image = src;
	uint used = 0;
	uint64 max_sequence = 0;
	uint64 max_span = 0;
	uint64 max_event = 0;
	uint64 max_control = 0;
	uint64 max_lifecycle[WORKFLOW_LIFECYCLE_CAP] = {0};
	uint max_agent = 0;

	if (image == 0 || bytes != sizeof(*image) ||
	    image->magic != AGENT_OBSERVE_CHECKPOINT_MAGIC ||
	    image->version != AGENT_OBSERVE_CHECKPOINT_VERSION ||
	    image->bytes != sizeof(*image) || image->generation == 0 ||
	    image->retention_policy !=
		    AGENT_OBSERVE_RETENTION_CAUSAL_DIVERSITY ||
	    image->reserved_scope_slots !=
		    AGENT_OBSERVE_RESERVED_SCOPE_SLOTS ||
	    image->reserved != 0 ||
	    image->scope_count > AGENT_OBSERVE_CHECKPOINT_SCOPES ||
	    (image->allocator_exhausted &
	     ~AGENT_OBSERVE_ALLOC_EXHAUSTED_ALL) != 0 ||
	    ((image->audit_lease_end == 0) !=
	     !!(image->allocator_exhausted &
		AGENT_OBSERVE_ALLOC_AUDIT_EXHAUSTED)) ||
	    ((image->span_lease_end == 0) !=
	     !!(image->allocator_exhausted &
		AGENT_OBSERVE_ALLOC_SPAN_EXHAUSTED)) ||
	    ((image->event_lease_end == 0) !=
	     !!(image->allocator_exhausted &
		AGENT_OBSERVE_ALLOC_EVENT_EXHAUSTED)) ||
	    ((image->control_lease_end == 0) !=
	     !!(image->allocator_exhausted &
		AGENT_OBSERVE_ALLOC_CONTROL_EXHAUSTED)) ||
	    ((image->agent_lease_end == 0) !=
	     !!(image->allocator_exhausted &
		AGENT_OBSERVE_ALLOC_AGENT_EXHAUSTED)) ||
	    image->image_hash != agent_observe_store_hash(image))
		return -1;
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		const struct agent_observe_checkpoint_scope *scope =
			&image->scopes[i];
		uint64 successful_records;
		uint64 hashed_omitted;
		int gap = 0;

		if (scope->used == 0) {
			for (uint j = 0; j < sizeof(*scope); j++)
				if (((const uchar *)scope)[j] != 0)
					return -1;
			continue;
		}
		used++;
		if ((scope->used & AGENT_OBSERVE_SCOPE_USED) == 0 ||
		    (scope->used & ~AGENT_OBSERVE_SCOPE_FLAGS_ALL) != 0 ||
		    (i == AGENT_OBSERVE_RECOVERY_SCOPE_SLOT) !=
			    !!(scope->used &
			       AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR) ||
		    scope->scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
		    scope->scope_id >= FS_OWNER_SCOPE_FLAG ||
		    scope->lifecycle_id == 0 ||
		    scope->lifecycle_id > WORKFLOW_LIFECYCLE_CAP ||
		    scope->lifecycle_generation == 0 ||
		    scope->record_count > AGENT_OBSERVE_CHECKPOINT_PER_SCOPE ||
		    scope->total_records == 0 ||
		    scope->total_records < scope->record_count ||
		    scope->admission_drops >
			    scope->total_records - scope->record_count)
			return -1;
		successful_records =
			scope->total_records - scope->admission_drops;
		hashed_omitted = successful_records - scope->record_count;
		if ((scope->record_count == 0 &&
		     (successful_records != 0 || scope->ledger_hash != 0)) ||
		    (scope->record_count != 0 &&
		     (successful_records < scope->record_count ||
		      scope->ledger_hash == 0)))
			return -1;
		if (scope->lifecycle_generation >
		    max_lifecycle[scope->lifecycle_id - 1])
			max_lifecycle[scope->lifecycle_id - 1] =
				scope->lifecycle_generation;
		for (uint j = 0; j < i; j++) {
			const struct agent_observe_checkpoint_scope *prior =
				&image->scopes[j];
			if ((prior->used & AGENT_OBSERVE_SCOPE_USED) &&
			    (prior->lifecycle_id == scope->lifecycle_id &&
			      prior->lifecycle_generation ==
				      scope->lifecycle_generation))
				return -1;
		}
		for (uint j = 0; j < AGENT_OBSERVE_CHECKPOINT_PER_SCOPE; j++) {
			const struct agent_observe_checkpoint_entry *entry =
				&scope->records[j];
			const struct agent_audit_record *record = &entry->record;

			if (j >= scope->record_count) {
				for (uint k = 0; k < sizeof(*entry); k++)
					if (((const uchar *)entry)[k] != 0)
						return -1;
				continue;
			}
			if (agent_observe_checkpoint_entry_validate(
				    scope, j, entry,
				    j == 0 ? 0 : &scope->records[j - 1],
				    &gap) < 0)
				return -1;
			for (uint pi = 0; pi <= i; pi++) {
				const struct agent_observe_checkpoint_scope *prior =
					&image->scopes[pi];
				uint limit = pi == i ? j :
					prior->record_count;

				if (!(prior->used & AGENT_OBSERVE_SCOPE_USED))
					continue;
				for (uint pj = 0; pj < limit; pj++)
					if (prior->records[pj].receipt_id ==
						    entry->receipt_id ||
					    prior->records[pj].record.sequence ==
						    record->sequence)
						return -1;
			}
			if (record->sequence > max_sequence)
				max_sequence = record->sequence;
			if (record->span_id > max_span)
				max_span = record->span_id;
			if ((record->kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE ||
			     record->kind == AGENT_AUDIT_KIND_EVENT_CONSUME) &&
			    record->value0 > max_event)
				max_event = record->value0;
			if (record->actor_control_id > max_control)
				max_control = record->actor_control_id;
			if (record->cause_control_id > max_control)
				max_control = record->cause_control_id;
			if (entry->principal > max_control)
				max_control = entry->principal;
			if (entry->span_owner > max_control)
				max_control = entry->span_owner;
			if (record->agent_id > 0 &&
			    (uint)record->agent_id > max_agent)
				max_agent = record->agent_id;
		}
		if (scope->record_count != 0 &&
		    (scope->ledger_hash !=
			     scope->records[scope->record_count - 1]
				     .record.record_hash ||
		     (hashed_omitted == 0 ?
			      (gap || scope->records[0].record.prev_hash != 0) :
			      !gap)))
			return -1;
	}
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++)
		if (image->lifecycle_lease_ends[i] != 0 &&
		    image->lifecycle_lease_ends[i] <= max_lifecycle[i])
			return -1;
	if (used != image->scope_count ||
	    (image->audit_lease_end != 0 &&
	     image->audit_lease_end <= max_sequence) ||
	    (image->span_lease_end != 0 &&
	     image->span_lease_end <= max_span) ||
	    (image->event_lease_end != 0 &&
	     image->event_lease_end <= max_event) ||
	    (image->control_lease_end != 0 &&
	     image->control_lease_end <= max_control) ||
	    (image->agent_lease_end != 0 &&
	     image->agent_lease_end <= max_agent) ||
	    image->agent_lease_end > 0x7fffffffU)
		return -1;
	return 0;
}

static int
agent_observe_store_update_scope(void *dst, uint bytes, uint scope_id,
				 struct workflow_lifecycle_key lifecycle,
				 uint64 *generation)
{
	struct agent_observe_checkpoint *image = dst;
	struct agent_observe_checkpoint_scope *target = 0;
	uint64 old_hash;
	int captured = 0;
	int reap_applied;

	if (image == 0 || generation == 0 || bytes != sizeof(*image))
		return -1;
	if (image->magic == 0) {
		memset(image, 0, sizeof(*image));
		agent_observe_store_header_refresh(image);
	} else if (agent_observe_store_validate(image, bytes) < 0)
		return -1;
	old_hash = image->image_hash;
	reap_applied = workflow_lifecycle_key_valid(lifecycle) &&
		agent_observe_capacity_suppresses_capture(
			scope_id, lifecycle, scope_id);
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		struct agent_observe_checkpoint_scope *scope = &image->scopes[i];
		struct workflow_lifecycle_key key;
		uint stored_scope_id;
		int action;

		if (!(scope->used & AGENT_OBSERVE_SCOPE_USED))
			continue;
		stored_scope_id = scope->scope_id;
		key.id = scope->lifecycle_id;
		key.generation = scope->lifecycle_generation;
		action = agent_observe_capacity_reap_action(
			i, stored_scope_id, key, scope_id);
		if (action != AGENT_OBSERVE_REAP_NONE &&
		    workflow_lifecycle_key_valid(lifecycle) &&
		    stored_scope_id == scope_id &&
		    workflow_lifecycle_key_equal(lifecycle, key))
			reap_applied = 1;
		if (action == AGENT_OBSERVE_REAP_AUTHORIZE)
			scope->used |= AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED;
		else if (action == AGENT_OBSERVE_REAP_ERASE)
			memset(scope, 0, sizeof(*scope));
	}
	if (workflow_lifecycle_key_valid(lifecycle) && !reap_applied) {
		struct agent_observe_capacity_claim claim;
		uint expected_flags;

		if (agent_observe_capacity_claim(
			    scope_id, lifecycle, &claim) < 0 ||
		    claim.slot >= AGENT_OBSERVE_CHECKPOINT_SCOPES)
			return -1;
		target = &image->scopes[claim.slot];
		expected_flags = AGENT_OBSERVE_SCOPE_USED |
			(claim.recovery ?
			 AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR : 0);
		for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++)
			if (i != claim.slot &&
			    (image->scopes[i].used & AGENT_OBSERVE_SCOPE_USED) &&
			    image->scopes[i].scope_id == scope_id &&
			    agent_observe_key_equal(lifecycle, &image->scopes[i]))
				return -1;
		if ((target->used & AGENT_OBSERVE_SCOPE_USED) &&
		    !((target->used == expected_flags &&
		       target->scope_id == scope_id &&
		       agent_observe_key_equal(lifecycle, target)) ||
		      (claim.replace &&
		       target->used ==
			       (AGENT_OBSERVE_SCOPE_USED |
				AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR) &&
		       target->scope_id == claim.expected_scope_id &&
		       agent_observe_key_equal(
			       claim.expected_lifecycle, target))))
			return -1;
		captured = agent_observe_checkpoint_capture_scope(
			scope_id, lifecycle, target);
		if (captured < 0)
			return -1;
		if (captured == 0)
			memset(target, 0, sizeof(*target));
		else
			target->used = expected_flags;
	}
	agent_observe_store_header_refresh(image);
	if (agent_observe_store_validate(image, bytes) < 0)
		return -1;
	*generation = image->generation;
	return old_hash != image->image_hash;
}

static int
agent_observe_store_recover(const void *src, uint bytes)
{
	const struct agent_observe_checkpoint *image = src;
	if (agent_observe_store_validate(src, bytes) < 0)
		return -1;
	/* 身份分配器只抬高水位，不负责激活生命周期。 */
	for (uint i = 0; i < WORKFLOW_LIFECYCLE_CAP; i++) {
		agent_identity_lease_recover_lifecycle(
			i, image->lifecycle_lease_ends[i]);
		if (workflow_lifecycle_generation_lease_floor(
			    i, image->lifecycle_lease_ends[i]) < 0)
			return -1;
	}
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		const struct agent_observe_checkpoint_scope *scope =
			&image->scopes[i];
		struct workflow_lifecycle_key key;

		if (!(scope->used & AGENT_OBSERVE_SCOPE_USED))
			continue;
		key.id = scope->lifecycle_id;
		key.generation = scope->lifecycle_generation;
		if (workflow_lifecycle_generation_floor(key) < 0)
			return -1;
	}
	agent_identity_lease_recover_allocator(
		AGENT_IDENTITY_ALLOCATOR_AUDIT, image->audit_lease_end);
	agent_identity_lease_recover_allocator(
		AGENT_IDENTITY_ALLOCATOR_SPAN, image->span_lease_end);
	agent_identity_lease_recover_allocator(
		AGENT_IDENTITY_ALLOCATOR_EVENT, image->event_lease_end);
	agent_identity_lease_recover_allocator(
		AGENT_IDENTITY_ALLOCATOR_CONTROL, image->control_lease_end);
	agent_identity_lease_recover_allocator(
		AGENT_IDENTITY_ALLOCATOR_AGENT, image->agent_lease_end);
	if (image->control_lease_end != 0 ||
	    (image->allocator_exhausted &
	     AGENT_OBSERVE_ALLOC_CONTROL_EXHAUSTED))
		agent_lifecycle_control_id_floor(image->control_lease_end);
	agent_observe_checkpoint_generation_floor(image->generation);
	agent_observe_checkpoint_raise_highwater(
		image->audit_lease_end, image->span_lease_end,
		image->event_lease_end, image->agent_lease_end);
	agent_observe_checkpoint_exhaust_highwater(
		image->allocator_exhausted);
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++) {
		const struct agent_observe_checkpoint_scope *scope =
			&image->scopes[i];
		struct workflow_lifecycle_key key;
		uint bound_scope;

		if (!(scope->used & AGENT_OBSERVE_SCOPE_USED))
			continue;
		key.id = scope->lifecycle_id;
		key.generation = scope->lifecycle_generation;
		if (scope->used & AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED) {
			if (agent_observe_capacity_recover_reap(
				    i, scope->scope_id, key) < 0)
				return -1;
			continue;
		}
		if (workflow_lifecycle_active(key) &&
		    workflow_lifecycle_scope(key, &bound_scope) == 0 &&
		    bound_scope == scope->scope_id &&
		    agent_observe_checkpoint_restore_scope(scope) < 0)
			return -1;
	}
	return 0;
}

static int
agent_observe_store_has_scope(const void *src, uint bytes, uint scope_id)
{
	const struct agent_observe_checkpoint *image = src;

	if (agent_observe_store_validate(src, bytes) < 0)
		return 0;
	for (uint i = 0; i < AGENT_OBSERVE_CHECKPOINT_SCOPES; i++)
		if ((image->scopes[i].used & AGENT_OBSERVE_SCOPE_USED) &&
		    image->scopes[i].scope_id == scope_id)
			return 1;
	return 0;
}

static void
agent_observe_store_replicated_scope(uint scope_id)
{
	agent_observe_capacity_replicated(scope_id);
}
static const struct agent_durable_section_ops agent_observe_store_ops = {
	.kind = AGENT_DURABLE_SECTION_OBSERVE,
	.version = AGENT_OBSERVE_CHECKPOINT_VERSION,
	.image_bytes = sizeof(struct agent_observe_checkpoint),
	.update_scope = agent_observe_store_update_scope,
	.validate = agent_observe_store_validate,
	.recover = agent_observe_store_recover,
	.has_scope = agent_observe_store_has_scope,
	.replicated_scope = agent_observe_store_replicated_scope,
};
void
agent_obsstore_init(void)
{
	agent_identity_lease_set_persist(agent_observe_lease_persist_bridge);
	if (agent_durable_section_register(&agent_observe_store_ops) < 0)
		panic("observation durable section registration");
}

int
agent_obsstore_storage_ready(void)
{
	return agent_identity_lease_storage_ready();
}

void
agent_obsstore_lease_maintain(void)
{
	agent_observe_capacity_maintain();
	agent_identity_lease_maintain();
}
void
agent_obsstore_mark_dirty(uint scope_id)
{
	(void)agent_durable_section_mark_dirty(
		AGENT_DURABLE_SECTION_OBSERVE, scope_id);
}

int
agent_obsstore_mark_dirty_receipt(
	uint scope_id, struct workflow_lifecycle_key lifecycle, uint64 *serial,
	uint64 *target)
{
	struct agent_observe_capacity_claim claim;
	uint64 captured_serial = 0;
	uint64 captured_target;

	if (serial == 0 || target == 0 ||
	    scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    agent_observe_capacity_claim(
		    scope_id, lifecycle, &claim) < 0)
		return -1;
	*serial = 0;
	*target = 0;
	captured_target = agent_durable_section_mark_dirty_evidence(
		AGENT_DURABLE_SECTION_OBSERVE, scope_id, &captured_serial, 0);
	*serial = captured_serial;
	*target = captured_target;
	return captured_serial != 0 && captured_target != 0 ? 0 : -1;
}

int
agent_obsstore_receipt_replicated(uint scope_id, uint64 target)
{
	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG || target == 0)
		return -1;
	return agent_durable_section_replicated(scope_id, target);
}

int
agent_obsstore_receipt_persist(uint scope_id)
{
	struct thread *t = curr_thread();
	struct agent_observe_persist_context context;

	if (t == 0)
		return -1;
	context.running = t->tid >= 0 && t->state == RUNNING &&
			  t->process != 0;
	context.kernel_work_depth = t->kernel_work_depth;
	context.io_request_depth = t->io_request_depth;
	context.buffer_holds = t->bio_buffer_holds;
	context.fs_atomic_depth = t->bio_fs_atomic_depth;
	context.sstatus = r_sstatus();
	context.supervisor_previous_mask = SSTATUS_SPP;
	context.metadata_txn_owned = agent_metadata_txn_owned(0);
	context.exit_requested = proc_thread_exit_requested();
	if (!agent_observe_receipt_persist_context_safe(&context))
		return -1;
	return agent_durable_section_persist_scope(scope_id);
}

int
agent_obsstore_mark_reap(uint scope_id, struct workflow_lifecycle_key lifecycle)
{
	return agent_observe_capacity_reap_begin(
		scope_id, lifecycle, 0);
}

static int
agent_observe_active_copy(uint offset, void *dst, uint bytes,
			  uint64 *bank_generation)
{
	const uchar *data;
	uint available = 0;
	uint64 generation = 0;
	int result = -1;
	int enabled;

	if (dst == 0 || bank_generation == 0)
		return -1;
	enabled = intr_save();
	data = agent_durable_section_active_view(
		AGENT_DURABLE_SECTION_OBSERVE, &available, &generation);
	if (data != 0 && offset <= available && bytes <= available - offset) {
		memmove(dst, data + offset, bytes);
		*bank_generation = generation;
		result = 0;
	}
	intr_restore(enabled);
	return result;
}

static int
agent_observe_active_header(struct agent_observe_disk_header *header,
			    uint64 *bank_generation)
{
	return agent_observe_active_copy(
		0, header, sizeof(*header), bank_generation);
}

static int
agent_observe_active_scope(uint slot, struct agent_observe_scope_header *scope,
			   uint64 *bank_generation)
{
	uint offset;

	if (slot >= AGENT_OBSERVE_CHECKPOINT_SCOPES)
		return -1;
	offset = __builtin_offsetof(struct agent_observe_checkpoint, scopes) +
		slot * sizeof(struct agent_observe_checkpoint_scope);
	return agent_observe_active_copy(
		offset, scope, sizeof(*scope), bank_generation);
}

int
agent_obsstore_receipt_record_status(
	uint scope_id, struct workflow_lifecycle_key lifecycle, uint64 sequence,
	uint64 record_hash, uint64 receipt_id, uint64 target)
{
	const struct agent_observe_checkpoint *image;
	uint bytes = 0;
	uint64 generation = 0;
	int result = -1;
	int replicated;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG ||
	    !workflow_lifecycle_key_valid(lifecycle) || sequence == 0 ||
	    record_hash == 0 || receipt_id == 0)
		return -1;
	if (target == 0) {
		if (!agent_identity_lease_admission_ready())
			return 0;
	} else {
		replicated = agent_obsstore_receipt_replicated(scope_id, target);
		if (replicated <= 0)
			return replicated;
	}
	enabled = intr_save();
	image = (const struct agent_observe_checkpoint *)
		agent_durable_section_active_view(
			AGENT_DURABLE_SECTION_OBSERVE, &bytes, &generation);
	if (image == 0 || bytes != sizeof(*image) ||
	    image->magic != AGENT_OBSERVE_CHECKPOINT_MAGIC ||
	    image->version != AGENT_OBSERVE_CHECKPOINT_VERSION ||
	    image->bytes != sizeof(*image))
		goto out;
	if (target == 0) {
		replicated = agent_durable_section_active_replicated(generation);
		if (replicated <= 0) {
			result = replicated;
			goto out;
		}
	}
	for (uint slot = 0; slot < AGENT_OBSERVE_CHECKPOINT_SCOPES; slot++) {
		const struct agent_observe_checkpoint_scope *scope =
			&image->scopes[slot];

		if (!(scope->used & AGENT_OBSERVE_SCOPE_USED) ||
		    scope->scope_id != scope_id ||
		    scope->lifecycle_id != lifecycle.id ||
		    scope->lifecycle_generation != lifecycle.generation)
			continue;
		if (scope->record_count > AGENT_OBSERVE_CHECKPOINT_PER_SCOPE)
			goto out;
		for (uint i = 0; i < scope->record_count; i++) {
			const struct agent_observe_checkpoint_entry *entry =
				&scope->records[i];

			if (entry->scope_id == scope_id &&
			    entry->record.workflow_lifecycle_id == lifecycle.id &&
			    entry->record.workflow_lifecycle_generation ==
				    lifecycle.generation &&
			    entry->record.sequence == sequence &&
			    entry->record.record_hash == record_hash &&
			    entry->receipt_id == receipt_id) {
				result = 1;
				goto out;
			}
		}
		break;
	}
out:
	intr_restore(enabled);
	return result;
}

static int
agent_observe_scope_sealed(const struct agent_observe_scope_header *scope)
{
	struct workflow_lifecycle_key key;

	if (scope == 0 || !(scope->used & AGENT_OBSERVE_SCOPE_USED))
		return 0;
	key.id = scope->lifecycle_id;
	key.generation = scope->lifecycle_generation;
	if (workflow_lifecycle_active(key) || workflow_lifecycle_closing(key))
		return 0;
	/* RETIRING 或已回收代际都越过了静默屏障。 */
	return 1;
}

int
agent_obsstore_snapshot_begin(uint64 *bank_generation)
{
	struct agent_observe_disk_header header;

	if (bank_generation == 0 ||
	    agent_observe_active_header(&header, bank_generation) < 0)
		return -1;
	if (header.magic != AGENT_OBSERVE_CHECKPOINT_MAGIC ||
	    header.version != AGENT_OBSERVE_CHECKPOINT_VERSION ||
	    header.bytes != sizeof(struct agent_observe_checkpoint))
		return -1;
	return 0;
}

uint
agent_obsstore_snapshot_scope_capacity(void)
{
	return AGENT_OBSERVE_CHECKPOINT_SCOPES;
}

uint
agent_obsstore_snapshot_record_capacity(void)
{
	return AGENT_OBSERVE_CHECKPOINT_PER_SCOPE;
}

int
agent_obsstore_snapshot_confirm(uint64 bank_generation)
{
	struct agent_observe_disk_header header;
	uint64 confirmed_generation = 0;

	if (agent_observe_active_header(&header, &confirmed_generation) < 0 ||
	    header.magic != AGENT_OBSERVE_CHECKPOINT_MAGIC ||
	    header.version != AGENT_OBSERVE_CHECKPOINT_VERSION ||
	    header.bytes != sizeof(struct agent_observe_checkpoint))
		return -1;
	return confirmed_generation == bank_generation ?
		AGENT_OBSSTORE_SNAPSHOT_EMPTY : AGENT_OBSSTORE_SNAPSHOT_RETRY;
}

int
agent_obsstore_snapshot_scope(uint64 bank_generation, uint slot,
			      struct agent_obsstore_scope_view *view)
{
	struct agent_observe_scope_header scope;
	uint64 observed_generation = 0;

	if (view == 0 ||
	    agent_observe_active_scope(slot, &scope, &observed_generation) < 0)
		return -1;
	if (observed_generation != bank_generation)
		return AGENT_OBSSTORE_SNAPSHOT_RETRY;
	if (!agent_observe_scope_sealed(&scope))
		return AGENT_OBSSTORE_SNAPSHOT_EMPTY;
	if (scope.record_count > AGENT_OBSERVE_CHECKPOINT_PER_SCOPE)
		return -1;
	view->scope_id = scope.scope_id;
	view->record_count = scope.record_count;
	view->lifecycle.id = scope.lifecycle_id;
	view->lifecycle.generation = scope.lifecycle_generation;
	view->total_records = scope.total_records;
	view->dropped_records = scope.total_records - scope.record_count;
	view->ledger_hash = scope.ledger_hash;
	return AGENT_OBSSTORE_SNAPSHOT_READY;
}

int
agent_obsstore_snapshot_record(
	uint64 bank_generation, uint slot, uint index, uint scope_id,
	struct workflow_lifecycle_key lifecycle,
	struct agent_obsstore_record_view *view)
{
	struct agent_observe_checkpoint_entry entry;
	uint64 observed_generation = 0;
	uint offset;

	if (view == 0 || slot >= AGENT_OBSERVE_CHECKPOINT_SCOPES ||
	    index >= AGENT_OBSERVE_CHECKPOINT_PER_SCOPE ||
	    !workflow_lifecycle_key_valid(lifecycle))
		return -1;
	offset = __builtin_offsetof(struct agent_observe_checkpoint, scopes) +
		slot * sizeof(struct agent_observe_checkpoint_scope) +
		__builtin_offsetof(struct agent_observe_checkpoint_scope, records) +
		index * sizeof(entry);
	if (agent_observe_active_copy(
		    offset, &entry, sizeof(entry), &observed_generation) < 0)
		return -1;
	if (observed_generation != bank_generation)
		return AGENT_OBSSTORE_SNAPSHOT_RETRY;
	if (entry.scope_id != scope_id || entry.receipt_id == 0 ||
	    entry.record.workflow_lifecycle_id != lifecycle.id ||
	    entry.record.workflow_lifecycle_generation != lifecycle.generation ||
	    entry.record.record_hash !=
		    agent_observe_checkpoint_record_hash(&entry.record))
		return -1;
	view->record = entry.record;
	view->receipt_id = entry.receipt_id;
	return AGENT_OBSSTORE_SNAPSHOT_READY;
}

int
agent_obsstore_recovery_reap(
	uint scope_id, struct workflow_lifecycle_key lifecycle, uint64 *token,
	uint64 *bank_generation)
{
	if (token == 0 || bank_generation == 0)
		return -1;
	*bank_generation = 0;
	if (agent_observe_capacity_reap_begin(
		    scope_id, lifecycle, token) < 0)
		return -1;
	return agent_observe_capacity_reap_resume(
		       lifecycle, token, bank_generation) == 1 ? 0 : -1;
}

int
agent_obsstore_recovery_reap_resume(
	struct workflow_lifecycle_key lifecycle, uint64 *token,
	uint64 *bank_generation)
{
	return agent_observe_capacity_reap_resume(
		lifecycle, token, bank_generation);
}

int
agent_obsstore_reap_query(
	struct workflow_lifecycle_key lifecycle, uint64 token, int *replicated,
	uint64 *bank_generation, struct agent_observe_reap_cookie *cookie)
{
	return agent_observe_capacity_reap_query(
		lifecycle, token, replicated, bank_generation, cookie);
}

int
agent_obsstore_reap_consume(const struct agent_observe_reap_cookie *cookie)
{
	return agent_observe_capacity_reap_consume(cookie);
}
