#ifndef AGENT_CONTEXT_PREFETCH_ABI_H
#define AGENT_CONTEXT_PREFETCH_ABI_H

/* Bounded, structured history predictor used by every generic Agent loop. */
#define AGENT_CONTEXT_PREFETCH_SYSCALL 572U

#define AGENT_CONTEXT_PREFETCH_VERSION 1U

#define AGENT_CONTEXT_PREFETCH_CONFIGURE 1U
#define AGENT_CONTEXT_PREFETCH_RECORD    2U
#define AGENT_CONTEXT_PREFETCH_STATUS    3U
#define AGENT_CONTEXT_PREFETCH_CLEAR     4U

#define AGENT_CONTEXT_PREFETCH_F_READ_ONLY (1U << 0)
#define AGENT_CONTEXT_PREFETCH_F_HOST      (1U << 1)
#define AGENT_CONTEXT_PREFETCH_F_SHARED    (1U << 2)
#define AGENT_CONTEXT_PREFETCH_F_ALL \
	(AGENT_CONTEXT_PREFETCH_F_READ_ONLY | \
	 AGENT_CONTEXT_PREFETCH_F_HOST | \
	 AGENT_CONTEXT_PREFETCH_F_SHARED)

#define AGENT_CONTEXT_PREFETCH_POLICY_TRANSITION 1U
#define AGENT_CONTEXT_PREFETCH_CONFIDENCE_SCALE 1000000U
#define AGENT_CONTEXT_PREFETCH_MAX_BYTES 4096U
#define AGENT_CONTEXT_PREFETCH_MAX_INFLIGHT 2U

struct agent_context_prefetch_control {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	unsigned int flags;
	unsigned int policy;
	unsigned int min_observations;
	unsigned int confidence_threshold_ppm;
	unsigned int max_prefetch_bytes;
	unsigned int max_inflight;
	unsigned int operation_type;
	unsigned int tool_id;
	int result_status;
	unsigned int signature_flags;
	unsigned int dev;
	unsigned int inum;
	unsigned int incarnation;
	unsigned long long branch_generation;
	unsigned long long file_revision;
	unsigned long long offset;
	unsigned long long length;
	unsigned long long context_sequence;
	unsigned long long cause_sequence;
	unsigned long long tick;
	unsigned int workflow_lifecycle_id;
	unsigned int agent_id;
	unsigned long long workflow_lifecycle_generation;
	unsigned long long agent_control_id;
	unsigned long long workspace_object_id;
	unsigned char workspace_revision_sha256[32];
	unsigned char query_fingerprint[32];
	unsigned long long reserved_tail[5];
};

struct agent_context_prefetch_result {
	unsigned int version;
	unsigned int size;
	int status;
	unsigned int enabled;
	unsigned int policy;
	unsigned int predicted;
	unsigned int observations;
	unsigned int confidence_ppm;
	unsigned int max_prefetch_bytes;
	unsigned int max_inflight;
	unsigned int inflight;
	unsigned int hits;
	unsigned int misses;
	unsigned int cancelled;
	unsigned int denied;
	unsigned int target_flags;
	unsigned int target_dev;
	unsigned int target_inum;
	unsigned int target_incarnation;
	unsigned long long target_file_revision;
	unsigned long long target_offset;
	unsigned long long target_length;
	unsigned long long target_workspace_object_id;
	unsigned char target_workspace_revision_sha256[32];
	unsigned char target_query_fingerprint[32];
	unsigned long long last_training_sequence;
	unsigned long long reserved_tail[9];
};

_Static_assert(sizeof(struct agent_context_prefetch_control) == 256,
	       "Context prefetch control ABI layout");
_Static_assert(sizeof(struct agent_context_prefetch_result) == 256,
	       "Context prefetch result ABI layout");

#endif
