#ifndef AGENT_LIFECYCLE_ABI_H
#define AGENT_LIFECYCLE_ABI_H

#define AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION 3U
#define AGENT_WORKFLOW_LIFECYCLE_INFO_V2_VERSION 2U
#define AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE 64U

#define AGENT_WORKFLOW_SCHED_MODE_NONE     0U
#define AGENT_WORKFLOW_SCHED_MODE_EEVDF    1U
#define AGENT_WORKFLOW_SCHED_MODE_FALLBACK 2U

#define AGENT_WORKFLOW_SCHED_F_ACTIVE   (1U << 0)
#define AGENT_WORKFLOW_SCHED_F_RUNNABLE (1U << 1)
#define AGENT_WORKFLOW_SCHED_F_ELIGIBLE (1U << 2)
#define AGENT_WORKFLOW_SCHED_F_SLEEPING (1U << 3)
#define AGENT_WORKFLOW_SCHED_F_FALLBACK (1U << 4)

#define AGENT_WORKFLOW_LATENCY_URGENT      0U
#define AGENT_WORKFLOW_LATENCY_INTERACTIVE 1U
#define AGENT_WORKFLOW_LATENCY_NORMAL      2U
#define AGENT_WORKFLOW_LATENCY_BATCH       3U

#define AGENT_WORKFLOW_WAKE_BUCKET_LE_1_TICK 0U
#define AGENT_WORKFLOW_WAKE_BUCKET_LE_2_TICKS 1U
#define AGENT_WORKFLOW_WAKE_BUCKET_LE_8_TICKS 2U
#define AGENT_WORKFLOW_WAKE_BUCKET_GT_8_TICKS 3U
#define AGENT_WORKFLOW_WAKE_BUCKET_COUNT 4U

#define AGENT_WORKFLOW_LIFECYCLE_INFO_F_MATCH_CURRENT (1U << 0)

/* 仅含身份与比较数据，不是可转让的持有者凭据。 */
struct agent_workflow_lifecycle_key {
	unsigned int id;
	unsigned int reserved;
	unsigned long long generation;
};

struct agent_workflow_lifecycle_info {
	unsigned int version;
	unsigned int struct_size;
	unsigned int charged;
	unsigned int reserved;
	struct agent_workflow_lifecycle_key key;
	unsigned int context_lane_depth;
	unsigned int context_lane_waiters;
	unsigned int metadata_txn_owned;
	unsigned int metadata_txn_waiters;
	/* 仅用于查询自身身份；系统调用不会把它当作权限。 */
	unsigned int resource_account_valid;
	unsigned int resource_account_slot;
	unsigned long long resource_account_generation;
	/* Version 3 appends workflow-level EEVDF metrics to the frozen v2 prefix. */
	unsigned int scheduler_mode;
	unsigned int scheduler_flags;
	unsigned int scheduler_latency_class;
	unsigned int scheduler_weight;
	unsigned int scheduler_runnable;
	unsigned int scheduler_request_ticks;
	unsigned long long scheduler_remaining_cycles;
	signed long long scheduler_lag_cycles;
	unsigned long long scheduler_vruntime;
	unsigned long long scheduler_virtual_deadline;
	unsigned long long scheduler_dispatches;
	unsigned long long scheduler_service_cycles;
	unsigned long long scheduler_sleep_decays;
	unsigned long long scheduler_eligibility_misses;
	unsigned long long scheduler_fallbacks;
	unsigned long long scheduler_max_wakeup_ticks;
	unsigned long long scheduler_deadline_misses;
	unsigned long long scheduler_wakeup_samples;
	unsigned long long scheduler_wakeup_latency_buckets[
		AGENT_WORKFLOW_WAKE_BUCKET_COUNT];
};

_Static_assert(sizeof(unsigned int) == 4,
	       "workflow lifecycle ABI requires 32-bit unsigned int");
_Static_assert(sizeof(unsigned long long) == 8,
	       "workflow lifecycle ABI requires 64-bit unsigned long long");
_Static_assert(sizeof(struct agent_workflow_lifecycle_key) == 16,
	       "workflow lifecycle key ABI layout");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_key,
				  generation) == 8,
	       "workflow lifecycle generation ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info, key) ==
	       16,
	       "workflow lifecycle key ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  context_lane_depth) == 32,
	       "workflow lifecycle runtime ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  metadata_txn_owned) == 40,
	       "workflow lifecycle metadata ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  metadata_txn_waiters) == 44,
	       "workflow lifecycle metadata waiter ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  resource_account_valid) == 48,
	       "workflow lifecycle resource account validity ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  resource_account_slot) == 52,
	       "workflow lifecycle resource account slot ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  resource_account_generation) == 56,
	       "workflow lifecycle resource account generation ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  scheduler_mode) ==
		       AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE,
	       "workflow lifecycle v2 prefix ABI layout");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  scheduler_remaining_cycles) == 88,
	       "workflow scheduler counter ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  scheduler_lag_cycles) == 96,
	       "workflow scheduler lag ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  scheduler_deadline_misses) == 168,
	       "workflow scheduler deadline miss ABI offset");
_Static_assert(__builtin_offsetof(struct agent_workflow_lifecycle_info,
				  scheduler_wakeup_latency_buckets) == 184,
	       "workflow scheduler wake histogram ABI offset");
_Static_assert(sizeof(struct agent_workflow_lifecycle_info) == 216,
	       "workflow lifecycle info v3 ABI layout");

#endif
