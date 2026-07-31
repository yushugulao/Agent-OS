#ifndef RP_RESOURCE_STABILITY_H
#define RP_RESOURCE_STABILITY_H

#define RP_RESOURCE_STABILITY_MAGIC 0x52505354U
#define RP_RESOURCE_STABILITY_VERSION 2U
#define RP_RESOURCE_STABILITY_REPORT_SIZE 224U
#define RP_RESOURCE_STABILITY_LOAD_WORKFLOWS 4U
#define RP_RESOURCE_STABILITY_TERMINAL_WORKFLOWS 1U
#define RP_RESOURCE_STABILITY_WORKFLOWS \
	(RP_RESOURCE_STABILITY_LOAD_WORKFLOWS + \
	 RP_RESOURCE_STABILITY_TERMINAL_WORKFLOWS)
#define RP_RESOURCE_STABILITY_CHILD_ROUNDS 12U
#define RP_RESOURCE_STABILITY_MEMORY_PAGES 128U
#define RP_RESOURCE_STABILITY_FILE_OBJECTS 12U
#define RP_RESOURCE_STABILITY_METADATA_OPS 3U

#define RP_RESOURCE_STABILITY_FS_BLOCK_GROWTH_BOUND 32U
#define RP_RESOURCE_STABILITY_BUFFER_GROWTH_BOUND 16U

#define RP_RESOURCE_STABILITY_MODE_LOAD 1U
#define RP_RESOURCE_STABILITY_MODE_TERMINAL 2U

#define RP_RESOURCE_STABILITY_REPORT_PREFIX "--rp-stability-report-fd="
#define RP_RESOURCE_STABILITY_INDEX_PREFIX "--rp-stability-index="
#define RP_RESOURCE_STABILITY_MODE_PREFIX "--rp-stability-mode="
#define RP_RESOURCE_STABILITY_NONCE_PREFIX "--rp-stability-nonce="

struct rp_resource_stability_report {
	unsigned int magic;
	unsigned int version;
	unsigned int struct_size;
	unsigned int workflow_index;
	unsigned int mode;
	unsigned long long challenge_nonce;
	unsigned int lifecycle_id;
	unsigned long long lifecycle_generation;
	unsigned int scope_id;
	unsigned int io_owner;
	unsigned int resource_account_slot;
	unsigned int resource_account_reserved;
	unsigned long long resource_account_generation;
	unsigned int initial_cache_resident;
	unsigned int initial_leased;
	unsigned int initial_debt;
	unsigned int initial_waiters;
	unsigned int initial_debt_waiters;
	unsigned int initial_admission_waiters;
	unsigned int initial_context_lane_depth;
	unsigned int initial_context_lane_waiters;
	unsigned int initial_metadata_owned;
	unsigned int initial_metadata_waiters;
	unsigned long long initial_agent_calls;
	unsigned long long initial_context_records;
	unsigned int final_cache_resident;
	unsigned int final_leased;
	unsigned int final_debt;
	unsigned int final_waiters;
	unsigned int final_debt_waiters;
	unsigned int final_admission_waiters;
	unsigned int final_context_lane_depth;
	unsigned int final_context_lane_waiters;
	unsigned int final_metadata_owned;
	unsigned int final_metadata_waiters;
	unsigned long long final_agent_calls;
	unsigned long long final_context_records;
	unsigned long long initial_completion_sequence;
	unsigned long long final_completion_sequence;
	unsigned int process_rounds;
	unsigned int file_rounds;
	unsigned int memory_rounds;
	unsigned int metadata_rounds;
	unsigned long long guard;
};

_Static_assert(__builtin_offsetof(struct rp_resource_stability_report,
				  challenge_nonce) == 24,
	       "resource stability nonce ABI offset");
_Static_assert(__builtin_offsetof(struct rp_resource_stability_report,
				  resource_account_generation) == 64,
	       "resource stability account ABI offset");
_Static_assert(__builtin_offsetof(struct rp_resource_stability_report,
				  guard) == 216,
	       "resource stability guard ABI offset");
_Static_assert(sizeof(struct rp_resource_stability_report) ==
	       RP_RESOURCE_STABILITY_REPORT_SIZE,
	       "resource stability report ABI size");

static inline unsigned long long
rp_resource_stability_mix(unsigned long long hash, unsigned long long value)
{
	for (int i = 0; i < 8; i++) {
		hash ^= value & 0xffU;
		hash *= 1099511628211ULL;
		value >>= 8;
	}
	return hash;
}

static inline unsigned long long
rp_resource_stability_guard(const struct rp_resource_stability_report *report)
{
	unsigned long long hash = 1469598103934665603ULL;

#define RP_RESOURCE_STABILITY_GUARD_FIELD(field) \
	hash = rp_resource_stability_mix(hash, report->field)
	RP_RESOURCE_STABILITY_GUARD_FIELD(magic);
	RP_RESOURCE_STABILITY_GUARD_FIELD(version);
	RP_RESOURCE_STABILITY_GUARD_FIELD(struct_size);
	RP_RESOURCE_STABILITY_GUARD_FIELD(workflow_index);
	RP_RESOURCE_STABILITY_GUARD_FIELD(mode);
	RP_RESOURCE_STABILITY_GUARD_FIELD(challenge_nonce);
	RP_RESOURCE_STABILITY_GUARD_FIELD(lifecycle_id);
	RP_RESOURCE_STABILITY_GUARD_FIELD(lifecycle_generation);
	RP_RESOURCE_STABILITY_GUARD_FIELD(scope_id);
	RP_RESOURCE_STABILITY_GUARD_FIELD(io_owner);
	RP_RESOURCE_STABILITY_GUARD_FIELD(resource_account_slot);
	RP_RESOURCE_STABILITY_GUARD_FIELD(resource_account_reserved);
	RP_RESOURCE_STABILITY_GUARD_FIELD(resource_account_generation);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_cache_resident);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_leased);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_debt);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_debt_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_admission_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_context_lane_depth);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_context_lane_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_metadata_owned);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_metadata_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_agent_calls);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_context_records);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_cache_resident);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_leased);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_debt);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_debt_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_admission_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_context_lane_depth);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_context_lane_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_metadata_owned);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_metadata_waiters);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_agent_calls);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_context_records);
	RP_RESOURCE_STABILITY_GUARD_FIELD(initial_completion_sequence);
	RP_RESOURCE_STABILITY_GUARD_FIELD(final_completion_sequence);
	RP_RESOURCE_STABILITY_GUARD_FIELD(process_rounds);
	RP_RESOURCE_STABILITY_GUARD_FIELD(file_rounds);
	RP_RESOURCE_STABILITY_GUARD_FIELD(memory_rounds);
	RP_RESOURCE_STABILITY_GUARD_FIELD(metadata_rounds);
#undef RP_RESOURCE_STABILITY_GUARD_FIELD
	return hash;
}

static inline unsigned long long
rp_resource_stability_nonce(unsigned long long challenge_request_id,
			    unsigned int workflow_index, unsigned int mode)
{
	unsigned long long hash = 1469598103934665603ULL;

	hash = rp_resource_stability_mix(hash, RP_RESOURCE_STABILITY_MAGIC);
	hash = rp_resource_stability_mix(hash, RP_RESOURCE_STABILITY_VERSION);
	hash = rp_resource_stability_mix(hash, challenge_request_id);
	hash = rp_resource_stability_mix(hash, workflow_index);
	hash = rp_resource_stability_mix(hash, mode);
	return hash != 0 ? hash : 1;
}

#endif
