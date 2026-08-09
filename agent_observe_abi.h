#ifndef AGENT_OBSERVE_ABI_H
#define AGENT_OBSERVE_ABI_H

/*
 * Compatibility tombstone: the recovery endpoint is unsupported.  Keep the
 * version, opcode and structure values frozen so old binaries fail
 * deterministically instead of being reinterpreted as another ABI.
 */
#define AGENT_OBSERVE_RECOVERY_COMPAT_TOMBSTONE 1U
#define AGENT_OBSERVE_RECOVERY_VERSION_V1 1U
#define AGENT_OBSERVE_RECOVERY_VERSION    2U

#define AGENT_OBSERVE_RECOVERY_LIST   1U
#define AGENT_OBSERVE_RECOVERY_READ   2U
#define AGENT_OBSERVE_RECOVERY_REAP   3U
#define AGENT_OBSERVE_RECOVERY_STATUS 4U

#ifdef AGENT_OBSERVE_TEST_PROFILE
#define AGENT_OBSERVE_RECOVERY_TEST_EXHAUST_EVENT_ID 0x80000001U
#define AGENT_OBSERVE_RECOVERY_TEST_ARM_TIMELINE_WAIT 0x80000002U
#define AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_WAIT_STATUS 0x80000003U
#define AGENT_OBSERVE_RECOVERY_TEST_ARM_TIMELINE_THREADS 0x80000004U
#define AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_THREADS_STATUS 0x80000005U
#define AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_CUT 0x80000006U
#define AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_SUCCESSOR 0x80000007U
#define AGENT_AUDIT_RECEIPT_TEST_EVICT_BEFORE_PERSIST 0x80000001U

struct agent_observe_test_identity_ids {
	unsigned long long audit_sequence;
	unsigned long long span_id;
	unsigned long long event_id;
	unsigned long long control_id;
	unsigned long long lifecycle_generation;
	unsigned int agent_id;
	unsigned int lifecycle_slot;
};

_Static_assert(sizeof(struct agent_observe_test_identity_ids) == 48,
	       "observe identity cut profile ABI layout");
#endif

#define AGENT_OBSERVE_RECOVERY_F_NONE 0U
#define AGENT_OBSERVE_RECOVERY_MAX_SCOPES 5U
#define AGENT_AUDIT_LOW_PRINCIPAL_MAX 16U

#define AGENT_AUDIT_RECEIPT_VERSION 1U
#define AGENT_AUDIT_RECEIPT_STATUS 1U
#define AGENT_AUDIT_RECEIPT_WAIT   2U
#define AGENT_AUDIT_RECEIPT_F_NONE 0U

#define AGENT_AUDIT_DURABILITY_NOT_FOUND 0U
#define AGENT_AUDIT_DURABILITY_PENDING   1U
#define AGENT_AUDIT_DURABILITY_FENCE_SEALED 2U
/* Source compatibility only: the value now means fence-sealed, not on-disk. */
#define AGENT_AUDIT_DURABILITY_DURABLE \
	AGENT_AUDIT_DURABILITY_FENCE_SEALED
#define AGENT_AUDIT_DURABILITY_FAILED    3U
#define AGENT_AUDIT_RECEIPT_WAIT_MAX_TICKS 1000

/*
 * 肯定的证据证明只能是 (OK, FENCE_SEALED, supplied receipt)。它表示记录
 * 已进入 challenge-bound workflow fence root，不表示元数据或日志已落盘。
 * 精确保留绑定可报告 (OK, FAILED, supplied receipt)。有界绑定消失后，非零回执返回
 * (STALE, NOT_FOUND, 0)，但不能证明令牌曾存在；零回执发现返回
 * (NOT_FOUND, NOT_FOUND, 0)。
 */

struct agent_observe_recovery_scope {
	unsigned int scope_id;
	unsigned int record_count;
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long total_records;
	unsigned long long dropped_records;
	unsigned long long ledger_hash;
};

struct agent_observe_recovery_request {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	struct agent_workflow_lifecycle_key evidence;
	unsigned long long after_sequence;
	unsigned long long completion_token;
	unsigned long long bank_generation;
	unsigned int max_records;
	unsigned int returned;
	int status;
	unsigned int reserved;
};

struct agent_audit_receipt_request {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	struct agent_workflow_lifecycle_key lifecycle;
	unsigned long long sequence;
	unsigned long long record_hash;
	unsigned long long receipt_id;
	int timeout_ticks;
	unsigned int durability;
	int status;
	unsigned int reserved;
};

/* Frozen compatibility layout only; no active bank or durable recovery exists. */
struct agent_observe_recovery_record {
	struct agent_audit_record record;
	unsigned long long receipt_id;
	unsigned long long bank_generation;
	unsigned int durability;
	unsigned int reserved;
};

_Static_assert(sizeof(struct agent_observe_recovery_scope) == 48,
	       "observation recovery scope ABI layout");
_Static_assert(sizeof(struct agent_observe_recovery_request) == 72,
	       "observation recovery request ABI layout");
_Static_assert(sizeof(struct agent_audit_receipt_request) == 72,
	       "audit durability receipt ABI layout");
_Static_assert(__builtin_offsetof(struct agent_observe_recovery_record,
				  receipt_id) == sizeof(struct agent_audit_record),
	       "observation recovery receipt follows its exact record");

#endif
