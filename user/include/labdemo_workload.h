#ifndef __LABDEMO_WORKLOAD_H__
#define __LABDEMO_WORKLOAD_H__

#define LABDEMO_SCHEMA_VERSION 2
#define LABDEMO_CORPUS_SIZE 24
#define LABDEMO_PROGRESS_MAGIC 0x4c445032U
#define LABDEMO_FENCE_STABLE_ROUNDS 2
#define LABDEMO_FENCE_MAX_ATTEMPTS 16

struct labdemo_workload_metrics {
	uint64 end_to_end_started_us;
	uint64 end_to_end_finished_us;
	uint64 started_us;
	uint64 discovered_us;
	uint64 committed_us;
	uint64 finished_us;
	uint64 workload_syscalls;
	uint64 records_examined;
	uint64 bytes_read;
	uint64 result_items;
	uint64 outcome_hash;
};

struct labdemo_performance_receipt {
	uint64 observer_pid;
	struct agent_performance_snapshot snapshot;
	uint64 metadata_dirty;
	uint64 metadata_durable;
	uint64 metadata_requests;
	uint64 metadata_coalesced;
	uint64 metadata_commits;
	uint64 metadata_pending;
};

struct labdemo_fence_receipt {
	uint64 tick_us;
	uint64 attempts;
	uint64 stable_rounds;
	struct labdemo_performance_receipt performance;
};

struct labdemo_progress_receipt {
	uint magic;
	uint stage;
	uint records_examined;
	uint denied_actions;
	uint duplicate_actions;
	uint recovery_side_effects;
	uint reserved;
	uint64 milestone_us;
	uint64 tool_calls;
	uint64 dispatches;
	uint64 wait_sleeps;
	uint64 wait_wakeups;
};

#endif
