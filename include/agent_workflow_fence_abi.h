#ifndef AGENT_WORKFLOW_FENCE_ABI_H
#define AGENT_WORKFLOW_FENCE_ABI_H

#include "agent_lifecycle_abi.h"

_Static_assert(sizeof(unsigned int) == 4,
	       "workflow fence ABI requires 32-bit unsigned int");
_Static_assert(sizeof(unsigned long long) == 8,
	       "workflow fence ABI requires 64-bit unsigned long long");

#define AGENT_WORKFLOW_FENCE_VERSION 1U

/* Reuses SYS_agent_run with count == 0. */
#define AGENT_RUN_F_FENCE (1ULL << 0)

#define AGENT_WORKFLOW_FENCE_RECEIPT_F_PARTIAL_COVERAGE (1U << 0)
#define AGENT_WORKFLOW_FENCE_RECEIPT_F_CREDIT_EXACT     (1U << 1)
#define AGENT_WORKFLOW_FENCE_RECEIPT_F_EVIDENCE_SEALED  (1U << 2)
#define AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE (1U << 3)

#define AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE 32U
#define AGENT_WORKFLOW_FENCE_ROOT_SIZE 32U
#define AGENT_WORKFLOW_FENCE_RESOURCE_KINDS 8U

struct agent_workflow_fence_request {
	unsigned int version;
	unsigned int struct_size;
	unsigned int flags;
	unsigned int reserved;
	unsigned char challenge[AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE];
	unsigned long long request_id;
};

struct agent_workflow_credit_account_key {
	unsigned int slot;
	unsigned int reserved;
	unsigned long long generation;
};

struct agent_workflow_fence_receipt {
	unsigned int version;
	unsigned int struct_size;
	int status;
	unsigned int flags;
	struct agent_workflow_lifecycle_key key;
	unsigned long long request_id;
	unsigned long long fence_sequence;
	unsigned long long metadata_generation;
	unsigned long long credit_epoch;
	unsigned long long evidence_first_sequence;
	unsigned long long evidence_last_sequence;
	unsigned long long evidence_event_count;
	unsigned long long evidence_dropped_success;
	unsigned long long resource_used[AGENT_WORKFLOW_FENCE_RESOURCE_KINDS];
	struct agent_workflow_credit_account_key credit_exec_account;
	struct agent_workflow_credit_account_key credit_storage_account;
	unsigned char credit_digest[AGENT_WORKFLOW_FENCE_ROOT_SIZE];
	unsigned char challenge[AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE];
	unsigned char previous_root[AGENT_WORKFLOW_FENCE_ROOT_SIZE];
	unsigned char evidence_root[AGENT_WORKFLOW_FENCE_ROOT_SIZE];
};

_Static_assert(sizeof(struct agent_workflow_fence_request) == 56,
	       "workflow fence request ABI layout");
_Static_assert(__builtin_offsetof(struct agent_workflow_fence_receipt, key) ==
	       16, "workflow fence key ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_fence_receipt,
				  resource_used) == 96,
	       "workflow fence resource ABI offset");
_Static_assert(sizeof(struct agent_workflow_credit_account_key) == 16,
	       "workflow credit account key ABI layout");
_Static_assert(__builtin_offsetof(struct agent_workflow_fence_receipt,
				  credit_digest) == 192,
	       "workflow fence credit digest ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_fence_receipt,
				  challenge) == 224,
	       "workflow fence challenge ABI offset");
_Static_assert(sizeof(struct agent_workflow_fence_receipt) == 320,
	       "workflow fence receipt ABI layout");

#endif
