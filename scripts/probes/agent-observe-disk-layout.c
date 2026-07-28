#include "../../os/agent_observe_store.h"
#include "../../os/agent_durable_section.h"
#include "../../os/agent_metadata_disk.h"
#include "../../os/fs.h"
#include "../../os/workflow_lifecycle.h"

#define LAYOUT_DESCRIPTOR_MAGIC 0x41474f42534c5931ULL
#define LAYOUT_DESCRIPTOR_VERSION 2U
#define LAYOUT_WORDS 129U

#define MEMBER_OFFSET(type, member) __builtin_offsetof(type, member)
#define MEMBER_SIZE(type, member) sizeof(((type *)0)->member)

struct agent_observe_disk_layout_descriptor {
	uint64 words[LAYOUT_WORDS];
} __attribute__((packed));

_Static_assert(AGENT_DURABLE_SECTION_OBSERVE == 1,
	       "host verifier requires the observation section kind");
_Static_assert(sizeof(struct agent_durable_arena) ==
	       AGENT_DURABLE_ARENA_BYTES,
	       "durable arena disk ABI drift");
_Static_assert(sizeof(struct agent_observe_checkpoint) == 8024U,
	       "observation checkpoint disk ABI drift");
_Static_assert(sizeof(struct agent_observe_checkpoint_scope) == 1968U,
	       "observation scope disk ABI drift");
_Static_assert(sizeof(struct agent_observe_checkpoint_entry) == 240U,
	       "observation entry disk ABI drift");
_Static_assert(MEMBER_OFFSET(struct agent_observe_checkpoint, reserved) == 76U,
	       "observation header reserved offset drift");
_Static_assert(MEMBER_OFFSET(struct agent_observe_checkpoint, reserved) +
	       MEMBER_SIZE(struct agent_observe_checkpoint, reserved) ==
	       MEMBER_OFFSET(struct agent_observe_checkpoint, lifecycle_lease_ends),
	       "observation header reserved padding drift");
_Static_assert(MEMBER_OFFSET(struct agent_observe_checkpoint_entry,
			     identity_class) == 212U,
	       "observation identity class offset drift");
_Static_assert(MEMBER_OFFSET(struct agent_observe_checkpoint_entry,
			     identity_class) +
	       MEMBER_SIZE(struct agent_observe_checkpoint_entry, identity_class) ==
	       MEMBER_OFFSET(struct agent_observe_checkpoint_entry, link_flags),
	       "observation identity/link layout drift");
_Static_assert(MEMBER_OFFSET(struct agent_observe_checkpoint_entry,
			     link_flags) == 213U,
	       "observation link flags offset drift");
_Static_assert(MEMBER_OFFSET(struct agent_observe_checkpoint_entry,
			     link_flags) +
	       MEMBER_SIZE(struct agent_observe_checkpoint_entry, link_flags) ==
	       MEMBER_OFFSET(struct agent_observe_checkpoint_entry, reserved),
	       "observation link/reserved layout drift");
_Static_assert(MEMBER_OFFSET(struct agent_observe_checkpoint_entry,
			     reserved) == 214U,
	       "observation entry reserved offset drift");
_Static_assert(MEMBER_OFFSET(struct agent_observe_checkpoint_entry, reserved) +
	       MEMBER_SIZE(struct agent_observe_checkpoint_entry, reserved) ==
	       MEMBER_OFFSET(struct agent_observe_checkpoint_entry, principal),
	       "observation reserved/principal layout drift");

const struct agent_observe_disk_layout_descriptor
	agent_observe_disk_layout_descriptor
	__attribute__((used, section(".agent_observe_layout"))) = {
	.words = {
		LAYOUT_DESCRIPTOR_MAGIC,
		LAYOUT_DESCRIPTOR_VERSION,
		sizeof(struct agent_observe_disk_layout_descriptor),
		AGENT_META_STORE_HASH_ALGORITHM,
		AGENT_META_STORE_HASH_INITIAL,
		AGENT_META_STORE_HASH_PRIME,

		AGENT_DURABLE_ARENA_MAGIC,
		AGENT_DURABLE_ARENA_VERSION,
		sizeof(struct agent_durable_arena),
		AGENT_DURABLE_SECTION_MAX,
		AGENT_DURABLE_PAYLOAD_BYTES,
		MEMBER_OFFSET(struct agent_durable_arena, magic),
		MEMBER_OFFSET(struct agent_durable_arena, version),
		MEMBER_OFFSET(struct agent_durable_arena, bytes),
		MEMBER_OFFSET(struct agent_durable_arena, section_count),
		MEMBER_OFFSET(struct agent_durable_arena, used_bytes),
		MEMBER_OFFSET(struct agent_durable_arena, generation),
		MEMBER_OFFSET(struct agent_durable_arena, sections),
		MEMBER_OFFSET(struct agent_durable_arena, payload),
		MEMBER_OFFSET(struct agent_durable_arena, image_hash),
		sizeof(struct agent_durable_section_desc),
		MEMBER_OFFSET(struct agent_durable_section_desc, kind),
		MEMBER_OFFSET(struct agent_durable_section_desc, version),
		MEMBER_OFFSET(struct agent_durable_section_desc, offset),
		MEMBER_OFFSET(struct agent_durable_section_desc, bytes),
		MEMBER_OFFSET(struct agent_durable_section_desc, generation),
		MEMBER_OFFSET(struct agent_durable_section_desc, payload_hash),

		AGENT_DURABLE_SECTION_OBSERVE,
		AGENT_OBSERVE_CHECKPOINT_MAGIC,
		AGENT_OBSERVE_CHECKPOINT_VERSION,
		sizeof(struct agent_observe_checkpoint),
		AGENT_OBSERVE_CHECKPOINT_SCOPES,
		AGENT_OBSERVE_CHECKPOINT_PER_SCOPE,
		AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL,
		AGENT_OBSERVE_CHECKPOINT_DIVERSITY_ANCHORS,
		AGENT_OBSERVE_RETENTION_CAUSAL_DIVERSITY,
		AGENT_OBSERVE_RESERVED_SCOPE_SLOTS,
		AGENT_OBSERVE_RECOVERY_SCOPE_SLOT,
		AGENT_OBSERVE_IDENTITY_TELEMETRY,
		AGENT_OBSERVE_IDENTITY_CAUSAL,
		AGENT_OBSERVE_IDENTITY_AUTHORITY,
		AGENT_OBSERVE_LINK_PREV_RETAINED,
		AGENT_OBSERVE_LINK_LATEST_TAIL,
		AGENT_OBSERVE_LINK_FLAGS_ALL,
		AGENT_OBSERVE_SCOPE_FLAGS_ALL,
		AGENT_OBSERVE_SCOPE_USED,
		AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR,
		AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED,
		AGENT_OBSERVE_ALLOC_EXHAUSTED_ALL,
		WORKFLOW_LIFECYCLE_CAP,
		VFS_SCOPE_FIRST_DYNAMIC,
		FS_OWNER_SCOPE_FLAG,
		MEMBER_OFFSET(struct agent_observe_checkpoint, magic),
		MEMBER_OFFSET(struct agent_observe_checkpoint, version),
		MEMBER_OFFSET(struct agent_observe_checkpoint, bytes),
		MEMBER_OFFSET(struct agent_observe_checkpoint, generation),
		MEMBER_OFFSET(struct agent_observe_checkpoint, audit_lease_end),
		MEMBER_OFFSET(struct agent_observe_checkpoint, span_lease_end),
		MEMBER_OFFSET(struct agent_observe_checkpoint, event_lease_end),
		MEMBER_OFFSET(struct agent_observe_checkpoint, control_lease_end),
		MEMBER_OFFSET(struct agent_observe_checkpoint, agent_lease_end),
		MEMBER_OFFSET(struct agent_observe_checkpoint, retention_policy),
		MEMBER_OFFSET(struct agent_observe_checkpoint, scope_count),
		MEMBER_OFFSET(struct agent_observe_checkpoint, allocator_exhausted),
		MEMBER_OFFSET(struct agent_observe_checkpoint, reserved_scope_slots),
		MEMBER_OFFSET(struct agent_observe_checkpoint, reserved),
		MEMBER_OFFSET(struct agent_observe_checkpoint, lifecycle_lease_ends),
		MEMBER_OFFSET(struct agent_observe_checkpoint, scopes),
		MEMBER_OFFSET(struct agent_observe_checkpoint, image_hash),

		sizeof(struct agent_observe_checkpoint_scope),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope, used),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope, scope_id),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope, lifecycle_id),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope, record_count),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope,
			      lifecycle_generation),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope, total_records),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope, admission_drops),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope, ledger_hash),
		MEMBER_OFFSET(struct agent_observe_checkpoint_scope, records),

		sizeof(struct agent_observe_checkpoint_entry),
		MEMBER_OFFSET(struct agent_observe_checkpoint_entry, record),
		MEMBER_OFFSET(struct agent_observe_checkpoint_entry, scope_id),
		MEMBER_OFFSET(struct agent_observe_checkpoint_entry, identity_class),
		MEMBER_SIZE(struct agent_observe_checkpoint_entry, identity_class),
		MEMBER_OFFSET(struct agent_observe_checkpoint_entry, link_flags),
		MEMBER_SIZE(struct agent_observe_checkpoint_entry, link_flags),
		MEMBER_OFFSET(struct agent_observe_checkpoint_entry, reserved),
		MEMBER_SIZE(struct agent_observe_checkpoint_entry, reserved),
		MEMBER_OFFSET(struct agent_observe_checkpoint_entry, principal),
		MEMBER_OFFSET(struct agent_observe_checkpoint_entry, span_owner),
		MEMBER_OFFSET(struct agent_observe_checkpoint_entry, receipt_id),

		sizeof(struct agent_audit_record),
		MEMBER_OFFSET(struct agent_audit_record, sequence),
		MEMBER_OFFSET(struct agent_audit_record, tick),
		MEMBER_OFFSET(struct agent_audit_record, cause_sequence),
		MEMBER_OFFSET(struct agent_audit_record, span_id),
		MEMBER_OFFSET(struct agent_audit_record,
			      workflow_lifecycle_generation),
		MEMBER_OFFSET(struct agent_audit_record, branch_generation),
		MEMBER_OFFSET(struct agent_audit_record,
			      cause_branch_generation),
		MEMBER_OFFSET(struct agent_audit_record, actor_control_id),
		MEMBER_OFFSET(struct agent_audit_record, cause_control_id),
		MEMBER_OFFSET(struct agent_audit_record, cause_record_hash),
		MEMBER_OFFSET(struct agent_audit_record, prev_hash),
		MEMBER_OFFSET(struct agent_audit_record, record_hash),
		MEMBER_OFFSET(struct agent_audit_record, value0),
		MEMBER_OFFSET(struct agent_audit_record, value1),
		MEMBER_OFFSET(struct agent_audit_record, value2),
		MEMBER_OFFSET(struct agent_audit_record, flags),
		MEMBER_OFFSET(struct agent_audit_record, kind),
		MEMBER_OFFSET(struct agent_audit_record, workflow_lifecycle_id),
		MEMBER_OFFSET(struct agent_audit_record, pid),
		MEMBER_OFFSET(struct agent_audit_record, tid),
		MEMBER_OFFSET(struct agent_audit_record, source_pid),
		MEMBER_OFFSET(struct agent_audit_record, target_pid),
		MEMBER_OFFSET(struct agent_audit_record, agent_id),
		MEMBER_OFFSET(struct agent_audit_record, role),
		MEMBER_OFFSET(struct agent_audit_record, loop_state),
		MEMBER_OFFSET(struct agent_audit_record, tool_id),
		MEMBER_OFFSET(struct agent_audit_record, event_type),
		MEMBER_OFFSET(struct agent_audit_record, status),
		MEMBER_OFFSET(struct agent_audit_record, text),
		MEMBER_SIZE(struct agent_audit_record, text),
		MEMBER_SIZE(struct agent_audit_record, kind),
		MEMBER_SIZE(struct agent_audit_record, workflow_lifecycle_id),
		MEMBER_SIZE(struct agent_audit_record, sequence),
		AGENT_AUDIT_KIND_EVENT_ENQUEUE,
		AGENT_AUDIT_KIND_EVENT_CONSUME,
		AGENT_AUDIT_KIND_PREFETCH,
		0x7fffffffU,
	},
};
