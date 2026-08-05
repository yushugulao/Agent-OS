#include <agent.h>
#include <fcntl.h>
#include <labdemo_workload.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DEMO_OBSERVE_PAGE_RECORDS 128
#define DEMO_PROVENANCE_MAX      128

_Static_assert(DEMO_OBSERVE_PAGE_RECORDS <= AGENT_AUDIT_MAX_RECORDS,
	       "demo audit page exceeds the UAPI limit");
_Static_assert(DEMO_OBSERVE_PAGE_RECORDS <= AGENT_TIMELINE_MAX_RECORDS,
	       "demo timeline page exceeds the UAPI limit");
_Static_assert(DEMO_PROVENANCE_MAX <= AGENT_PROVENANCE_MAX_EDGES,
	       "demo provenance page exceeds the UAPI limit");

static int recovery_pid;
static int investigator_pid;
static int ready_fd = -1;
static int start_fd = -1;
static int progress_fd = -1;
/* Snapshot formats are sequential; one scratch avoids duplicate eager copies. */
static union {
	struct agent_audit_record audit[DEMO_OBSERVE_PAGE_RECORDS];
	struct agent_timeline_record timeline[DEMO_OBSERVE_PAGE_RECORDS];
	struct agent_provenance_edge provenance[DEMO_PROVENANCE_MAX];
	struct agent_file_prefetch_hint
		span_prefetch[AGENT_FILE_PREFETCH_SPAN_MAX];
} demo_observe_scratch;
#define demo_audit_records demo_observe_scratch.audit
#define demo_timeline_records demo_observe_scratch.timeline
#define demo_provenance_edges demo_observe_scratch.provenance
#define demo_span_prefetch demo_observe_scratch.span_prefetch
static struct agent_audit_filter demo_audit_filter;
static struct agent_timeline_filter demo_timeline_filter;

#define DEMO_PROJECT "lab-gene-x"
#define DEMO_WORKFLOW "nightly-regression"
#define DEMO_RUN "RUN-042"
#define DEMO_INCIDENT "INC-RUN-042-ALIGN-OOM"
#define DEMO_PLAN "PLAN-RUN-042-RECOVER-1"
#define DEMO_ALIGN_CORR "RUN-042-align-rerun-1"
#define DEMO_REPORT_CORR "RUN-042-report-write-1"
#define DEMO_LLM_REQUEST "LLM-RUN-042-RCA-1"
#define DEMO_ALIGN_LOG "labalignerr"
#define DEMO_ALIGN_LOG_BODY "align memory_limit evidence"
#define DEMO_BENCH_RUN "RUN-042-SHOWCASE"
#define DEMO_BENCH_TARGET 17
#define DEMO_BENCH_FID_BASE 100
#define DEMO_BENCH_OK_BODY \
	"project=lab-gene-x;workflow=nightly-regression;run=RUN-042;stage=other;status=ok"
#define DEMO_BENCH_FAILED_BODY \
	"project=lab-gene-x;workflow=nightly-regression;run=RUN-042;stage=align;status=failed;reason=memory_limit"
#define DEMO_BENCH_RECOVERED_BODY \
	"project=lab-gene-x;workflow=nightly-regression;run=RUN-042;stage=align;status=recovered;reason=memory_limit"

#ifndef LABDEMO_RUN_NONCE
#define LABDEMO_RUN_NONCE 1ULL
#endif

#ifndef LABDEMO_SAMPLE_ID
#define LABDEMO_SAMPLE_ID 1
#endif

#ifndef LABDEMO_NATIVE_FIRST
#define LABDEMO_NATIVE_FIRST 0
#endif

static struct labdemo_workload_metrics compat_metrics;
static struct labdemo_workload_metrics native_metrics;
static volatile uint64 demo_cow_probe_word;

struct labdemo_lane_measurement_state {
	struct labdemo_fence_receipt e2e_start;
	struct labdemo_fence_receipt core_start;
	struct labdemo_fence_receipt ack_settled;
	struct labdemo_fence_receipt e2e_end;
	struct labdemo_performance_receipt core_ack;
};

static struct labdemo_lane_measurement_state compat_measurement;
static struct labdemo_lane_measurement_state native_measurement;
static struct labdemo_performance_receipt workflow_perf_before;
static struct labdemo_performance_receipt workflow_perf_after;
static struct labdemo_performance_receipt measurement_warmup;

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("labdemo_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static int timeline_after_cursor(struct agent_timeline_record *record,
				 uint64 tick, int source, uint64 sequence)
{
	if (record->tick != tick)
		return record->tick > tick;
	if (record->source != source)
		return record->source > source;
	return record->sequence > sequence;
}

static int event_tick(void)
{
	return (int)get_mtime();
}

static uint64 digest_text(const char *text)
{
	uint64 hash = 1469598103934665603ULL;

	while (*text) {
		hash ^= (unsigned char)*text++;
		hash *= 1099511628211ULL;
	}
	return hash;
}

static uint64 demo_now_us(void)
{
	TimeVal now;

	check(sys_get_time(&now, 0) == 0, "demo monotonic clock");
	return now.sec * 1000000ULL + now.usec;
}

static uint64 demo_hash_part(uint64 hash, const char *text)
{
	while (*text) {
		hash ^= (unsigned char)*text++;
		hash *= 1099511628211ULL;
	}
	return hash;
}

static uint64 demo_outcome_hash(const char *stage, const char *status)
{
	uint64 hash = 1469598103934665603ULL;

	hash = demo_hash_part(hash, "agentos-showcase-v2|");
	hash = demo_hash_part(hash, DEMO_PROJECT "|");
	hash = demo_hash_part(hash, DEMO_WORKFLOW "|");
	hash = demo_hash_part(hash, DEMO_RUN "|");
	hash = demo_hash_part(hash, stage);
	hash = demo_hash_part(hash, "|memory_limit|");
	hash = demo_hash_part(hash, status);
	return hash;
}

static void take_performance_snapshot(
	struct labdemo_performance_receipt *receipt)
{
	struct agent_info info;

	memset(receipt, 0, sizeof(*receipt));
	memset(&info, 0, sizeof(info));
	receipt->observer_pid = (uint64)getpid();
	check(agent_performance_snapshot(&receipt->snapshot) ==
		      AGENT_STATUS_OK,
	      "bootstrap performance snapshot");
	check(receipt->observer_pid != 0,
	      "performance observer identity");
	check(receipt->snapshot.version == AGENT_PERFORMANCE_SNAPSHOT_VERSION &&
	      receipt->snapshot.struct_size == sizeof(receipt->snapshot),
	      "performance snapshot ABI");
	check(receipt->snapshot.counter_scope ==
		      AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL,
	      "performance counter scope");
	check(agent_info(&info) == 0, "performance metadata snapshot");
	receipt->metadata_dirty = info.metadata_writeback_dirty;
	receipt->metadata_durable = info.metadata_writeback_durable;
	receipt->metadata_requests = info.metadata_writeback_requests;
	receipt->metadata_coalesced = info.metadata_writeback_coalesced;
	receipt->metadata_commits = info.metadata_writeback_commits;
	receipt->metadata_pending = info.metadata_writeback_pending;
}

static uint64 performance_delta(uint64 before, uint64 after)
{
	check(after >= before, "monotonic performance counter");
	return after - before;
}

#ifdef LABDEMO_DIAGNOSTICS
struct labdemo_diag_snapshot {
	struct labdemo_performance_receipt performance;
	struct agent_info info;
};

static void demo_diag_take(struct labdemo_diag_snapshot *snapshot)
{
	memset(snapshot, 0, sizeof(*snapshot));
	take_performance_snapshot(&snapshot->performance);
	check(agent_info(&snapshot->info) == 0, "diagnostic agent info");
}

static void demo_diag_print(const char *mode, const char *step,
			    const struct labdemo_diag_snapshot *before,
			    const struct labdemo_diag_snapshot *after)
{
	printf("agentos:diag nonce=%llu mode=%s step=%s epoch_commits=%llu epoch_buffers=%llu writes=%llu flushes=%llu deduplicated=%llu metadata_requests=%llu metadata_coalesced=%llu metadata_commits=%llu metadata_pending_before=%llu metadata_pending_after=%llu metadata_dirty_before=%llu metadata_dirty_after=%llu metadata_durable_before=%llu metadata_durable_after=%llu\n",
	       (uint64)LABDEMO_RUN_NONCE, mode, step,
	       performance_delta(before->performance.snapshot.fs_epoch_commits,
				 after->performance.snapshot.fs_epoch_commits),
	       performance_delta(
		       before->performance.snapshot.fs_epoch_buffers_staged,
		       after->performance.snapshot.fs_epoch_buffers_staged),
	       performance_delta(
		       before->performance.snapshot.block_physical_writes,
		       after->performance.snapshot.block_physical_writes),
	       performance_delta(
		       before->performance.snapshot.block_durable_flushes,
		       after->performance.snapshot.block_durable_flushes),
	       performance_delta(
		       before->performance.snapshot.fs_epoch_deduplicated_stages,
		       after->performance.snapshot.fs_epoch_deduplicated_stages),
	       performance_delta(before->info.metadata_writeback_requests,
				 after->info.metadata_writeback_requests),
	       performance_delta(before->info.metadata_writeback_coalesced,
				 after->info.metadata_writeback_coalesced),
	       performance_delta(before->info.metadata_writeback_commits,
				 after->info.metadata_writeback_commits),
	       before->info.metadata_writeback_pending,
	       after->info.metadata_writeback_pending,
	       before->info.metadata_writeback_dirty,
	       after->info.metadata_writeback_dirty,
	       before->info.metadata_writeback_durable,
	       after->info.metadata_writeback_durable);
}

static struct labdemo_diag_snapshot demo_diag_step_before;
static struct labdemo_diag_snapshot demo_diag_step_after;

#define DEMO_DIAG_BEGIN() demo_diag_take(&demo_diag_step_before)
#define DEMO_DIAG_END(mode, step) do { \
	demo_diag_take(&demo_diag_step_after); \
	demo_diag_print((mode), (step), &demo_diag_step_before, \
			&demo_diag_step_after); \
} while (0)
#else
#define DEMO_DIAG_BEGIN() do { } while (0)
#define DEMO_DIAG_END(mode, step) do { (void)(mode); (void)(step); } while (0)
#endif

static int performance_storage_equal(
	const struct agent_performance_snapshot *left,
	const struct agent_performance_snapshot *right)
{
	return left->fs_epoch_commits == right->fs_epoch_commits &&
	       left->fs_epoch_buffers_staged == right->fs_epoch_buffers_staged &&
	       left->block_physical_writes == right->block_physical_writes &&
	       left->block_physical_reads == right->block_physical_reads &&
	       left->block_durable_flushes == right->block_durable_flushes &&
	       left->fs_epoch_deduplicated_stages ==
		       right->fs_epoch_deduplicated_stages &&
	       left->directory_block_probes == right->directory_block_probes &&
	       left->directory_entries_examined ==
		       right->directory_entries_examined &&
	       left->virtio_notifications == right->virtio_notifications &&
	       left->virtio_submitted_requests ==
		       right->virtio_submitted_requests &&
	       left->virtio_write_batch_calls ==
		       right->virtio_write_batch_calls &&
	       left->virtio_batched_write_requests ==
		       right->virtio_batched_write_requests &&
	       left->virtio_indirect_write_batch_calls ==
		       right->virtio_indirect_write_batch_calls &&
	       left->virtio_read_batch_calls ==
		       right->virtio_read_batch_calls &&
	       left->virtio_batched_read_requests ==
		       right->virtio_batched_read_requests &&
	       left->overwrite_prereads_skipped ==
		       right->overwrite_prereads_skipped;
}

static int metadata_state_equal(
	const struct labdemo_performance_receipt *left,
	const struct labdemo_performance_receipt *right)
{
	return left->metadata_dirty == right->metadata_dirty &&
	       left->metadata_durable == right->metadata_durable &&
	       left->metadata_requests == right->metadata_requests &&
	       left->metadata_coalesced == right->metadata_coalesced &&
	       left->metadata_commits == right->metadata_commits &&
	       left->metadata_pending == right->metadata_pending;
}

static void demo_quiescence_fence(const char *mode, const char *point,
				  int sequence,
				  struct labdemo_fence_receipt *receipt)
{
	static struct labdemo_performance_receipt previous;
	static struct labdemo_performance_receipt current;
#ifdef LABDEMO_DIAGNOSTICS
	static struct labdemo_diag_snapshot diag_before;
	static struct labdemo_diag_snapshot diag_after;
#endif
	uint64 stable_rounds = 0;

#ifdef LABDEMO_DIAGNOSTICS
	demo_diag_take(&diag_before);
#endif
	memset(receipt, 0, sizeof(*receipt));
	memset(&previous, 0, sizeof(previous));
	for (int attempt = 1; attempt <= LABDEMO_FENCE_MAX_ATTEMPTS; attempt++) {
		check(sync() == 0, "quiescence sync");
		check(sched_yield() == 0, "quiescence maintenance yield");
		take_performance_snapshot(&current);
		if (attempt > 1 &&
		    current.observer_pid == previous.observer_pid &&
		    current.snapshot.observer_lifecycle_id ==
			    previous.snapshot.observer_lifecycle_id &&
		    current.snapshot.observer_lifecycle_generation ==
			    previous.snapshot.observer_lifecycle_generation &&
		    current.snapshot.sample_tick > previous.snapshot.sample_tick &&
		    performance_storage_equal(&previous.snapshot,
				      &current.snapshot) &&
		    metadata_state_equal(&previous, &current) &&
		    current.metadata_pending == 0 &&
		    current.metadata_dirty == current.metadata_durable)
			stable_rounds++;
		else
			stable_rounds = 0;
		memcpy(&previous, &current, sizeof(previous));
		if (stable_rounds == LABDEMO_FENCE_STABLE_ROUNDS) {
			receipt->tick_us = demo_now_us();
			receipt->attempts = (uint64)attempt;
			receipt->stable_rounds = stable_rounds;
			memcpy(&receipt->performance, &current, sizeof(current));
			printf("agentos:demo schema=2 nonce=%llu kind=fence mode=%s seq=%d point=%s tick_us=%llu attempts=%llu stable_rounds=%llu observer_pid=%llu observer_tick=%llu observer_lifecycle_id=%llu observer_lifecycle_generation=%llu counter_scope=global epoch_commits=%llu epoch_buffers_staged=%llu physical_writes=%llu physical_reads=%llu durable_flushes=%llu deduplicated_stages=%llu workload_syscalls=%llu directory_block_probes=%llu directory_entries_examined=%llu virtio_notifications=%llu virtio_submitted_requests=%llu virtio_write_batch_calls=%llu virtio_batched_write_requests=%llu virtio_indirect_write_batch_calls=%llu virtio_read_batch_calls=%llu virtio_batched_read_requests=%llu overwrite_prereads_skipped=%llu metadata_dirty=%llu metadata_durable=%llu metadata_requests=%llu metadata_coalesced=%llu metadata_commits=%llu metadata_pending=%llu\n",
			       (uint64)LABDEMO_RUN_NONCE, mode, sequence, point,
			       receipt->tick_us, receipt->attempts,
			       receipt->stable_rounds, current.observer_pid,
			       current.snapshot.sample_tick,
			       current.snapshot.observer_lifecycle_id,
			       current.snapshot.observer_lifecycle_generation,
			       current.snapshot.fs_epoch_commits,
			       current.snapshot.fs_epoch_buffers_staged,
			       current.snapshot.block_physical_writes,
			       current.snapshot.block_physical_reads,
			       current.snapshot.block_durable_flushes,
			       current.snapshot.fs_epoch_deduplicated_stages,
			       current.snapshot.observer_workload_syscalls,
			       current.snapshot.directory_block_probes,
			       current.snapshot.directory_entries_examined,
			       current.snapshot.virtio_notifications,
			       current.snapshot.virtio_submitted_requests,
			       current.snapshot.virtio_write_batch_calls,
			       current.snapshot.virtio_batched_write_requests,
			       current.snapshot.virtio_indirect_write_batch_calls,
			       current.snapshot.virtio_read_batch_calls,
			       current.snapshot.virtio_batched_read_requests,
			       current.snapshot.overwrite_prereads_skipped,
			       current.metadata_dirty, current.metadata_durable,
			       current.metadata_requests,
			       current.metadata_coalesced,
			       current.metadata_commits,
			       current.metadata_pending);
#ifdef LABDEMO_DIAGNOSTICS
			demo_diag_take(&diag_after);
			demo_diag_print(mode, point, &diag_before, &diag_after);
#endif
			return;
		}
	}
	check(0, "quiescence fence bound");
}

static void print_mechanism_delta(
	const char *mode, const char *scope,
	const struct labdemo_performance_receipt *before_receipt,
	const struct labdemo_performance_receipt *after_receipt)
{
	const struct agent_performance_snapshot *before =
		&before_receipt->snapshot;
	const struct agent_performance_snapshot *after =
		&after_receipt->snapshot;

	check(before->counter_scope == after->counter_scope,
	      "stable performance counter scope");
	check(before_receipt->observer_pid == after_receipt->observer_pid &&
	      before->observer_lifecycle_id == after->observer_lifecycle_id &&
	      before->observer_lifecycle_generation ==
		      after->observer_lifecycle_generation &&
	      before->sample_tick < after->sample_tick,
	      "stable performance observer");
	/* Validate every raw pair before the Host recomputes its own deltas. */
	(void)performance_delta(before->fs_epoch_commits,
				after->fs_epoch_commits);
	(void)performance_delta(before->fs_epoch_buffers_staged,
				after->fs_epoch_buffers_staged);
	(void)performance_delta(before->block_physical_writes,
				after->block_physical_writes);
	(void)performance_delta(before->block_physical_reads,
				after->block_physical_reads);
	(void)performance_delta(before->block_durable_flushes,
				after->block_durable_flushes);
	(void)performance_delta(before->fs_epoch_deduplicated_stages,
				after->fs_epoch_deduplicated_stages);
	(void)performance_delta(before->cow_pages_shared,
				after->cow_pages_shared);
	(void)performance_delta(before->cow_pages_copied,
				after->cow_pages_copied);
	(void)performance_delta(before->cow_fault_promotions,
				after->cow_fault_promotions);
	(void)performance_delta(before->exec_cache_hits,
				after->exec_cache_hits);
	(void)performance_delta(before->exec_cache_misses,
				after->exec_cache_misses);
	(void)performance_delta(before->exec_cache_shared_pages,
				after->exec_cache_shared_pages);
	(void)performance_delta(before->exec_cache_evictions,
				after->exec_cache_evictions);
	(void)performance_delta(before->observer_workload_syscalls,
				after->observer_workload_syscalls);
	(void)performance_delta(before->directory_block_probes,
				after->directory_block_probes);
	(void)performance_delta(before->directory_entries_examined,
				after->directory_entries_examined);
	(void)performance_delta(before->virtio_notifications,
				after->virtio_notifications);
	(void)performance_delta(before->virtio_submitted_requests,
				after->virtio_submitted_requests);
	(void)performance_delta(before->virtio_write_batch_calls,
				after->virtio_write_batch_calls);
	(void)performance_delta(before->virtio_batched_write_requests,
				after->virtio_batched_write_requests);
	(void)performance_delta(before->virtio_indirect_write_batch_calls,
				after->virtio_indirect_write_batch_calls);
	(void)performance_delta(before->virtio_read_batch_calls,
				after->virtio_read_batch_calls);
	(void)performance_delta(before->virtio_batched_read_requests,
				after->virtio_batched_read_requests);
	(void)performance_delta(before->overwrite_prereads_skipped,
				after->overwrite_prereads_skipped);
	(void)performance_delta(before_receipt->metadata_dirty,
				after_receipt->metadata_dirty);
	(void)performance_delta(before_receipt->metadata_durable,
				after_receipt->metadata_durable);
	(void)performance_delta(before_receipt->metadata_requests,
				after_receipt->metadata_requests);
	(void)performance_delta(before_receipt->metadata_coalesced,
				after_receipt->metadata_coalesced);
	(void)performance_delta(before_receipt->metadata_commits,
				after_receipt->metadata_commits);
	printf("agentos:demo schema=2 nonce=%llu kind=mechanism mode=%s scope=%s observer_pid=%llu before_tick=%llu after_tick=%llu observer_lifecycle_id=%llu observer_lifecycle_generation=%llu counter_scope=global before_epoch_commits=%llu after_epoch_commits=%llu before_epoch_buffers_staged=%llu after_epoch_buffers_staged=%llu before_physical_writes=%llu after_physical_writes=%llu before_physical_reads=%llu after_physical_reads=%llu before_durable_flushes=%llu after_durable_flushes=%llu before_deduplicated_stages=%llu after_deduplicated_stages=%llu before_cow_shared_pages=%llu after_cow_shared_pages=%llu before_cow_copied_pages=%llu after_cow_copied_pages=%llu before_cow_fault_promotions=%llu after_cow_fault_promotions=%llu before_exec_cache_hits=%llu after_exec_cache_hits=%llu before_exec_cache_misses=%llu after_exec_cache_misses=%llu before_exec_cache_shared_pages=%llu after_exec_cache_shared_pages=%llu before_exec_cache_evictions=%llu after_exec_cache_evictions=%llu before_workload_syscalls=%llu after_workload_syscalls=%llu before_directory_block_probes=%llu after_directory_block_probes=%llu before_directory_entries_examined=%llu after_directory_entries_examined=%llu before_virtio_notifications=%llu after_virtio_notifications=%llu before_virtio_submitted_requests=%llu after_virtio_submitted_requests=%llu before_virtio_write_batch_calls=%llu after_virtio_write_batch_calls=%llu before_virtio_batched_write_requests=%llu after_virtio_batched_write_requests=%llu before_virtio_indirect_write_batch_calls=%llu after_virtio_indirect_write_batch_calls=%llu before_virtio_read_batch_calls=%llu after_virtio_read_batch_calls=%llu before_virtio_batched_read_requests=%llu after_virtio_batched_read_requests=%llu before_overwrite_prereads_skipped=%llu after_overwrite_prereads_skipped=%llu before_metadata_dirty=%llu after_metadata_dirty=%llu before_metadata_durable=%llu after_metadata_durable=%llu before_metadata_requests=%llu after_metadata_requests=%llu before_metadata_coalesced=%llu after_metadata_coalesced=%llu before_metadata_commits=%llu after_metadata_commits=%llu before_metadata_pending=%llu after_metadata_pending=%llu\n",
	       (uint64)LABDEMO_RUN_NONCE, mode, scope,
	       before_receipt->observer_pid, before->sample_tick,
	       after->sample_tick, before->observer_lifecycle_id,
	       before->observer_lifecycle_generation,
	       before->fs_epoch_commits, after->fs_epoch_commits,
	       before->fs_epoch_buffers_staged,
	       after->fs_epoch_buffers_staged,
	       before->block_physical_writes, after->block_physical_writes,
	       before->block_physical_reads, after->block_physical_reads,
	       before->block_durable_flushes, after->block_durable_flushes,
	       before->fs_epoch_deduplicated_stages,
	       after->fs_epoch_deduplicated_stages,
	       before->cow_pages_shared, after->cow_pages_shared,
	       before->cow_pages_copied, after->cow_pages_copied,
	       before->cow_fault_promotions, after->cow_fault_promotions,
	       before->exec_cache_hits, after->exec_cache_hits,
	       before->exec_cache_misses, after->exec_cache_misses,
	       before->exec_cache_shared_pages,
	       after->exec_cache_shared_pages,
	       before->exec_cache_evictions, after->exec_cache_evictions,
	       before->observer_workload_syscalls,
	       after->observer_workload_syscalls,
	       before->directory_block_probes, after->directory_block_probes,
	       before->directory_entries_examined,
	       after->directory_entries_examined,
	       before->virtio_notifications, after->virtio_notifications,
	       before->virtio_submitted_requests,
	       after->virtio_submitted_requests,
	       before->virtio_write_batch_calls,
	       after->virtio_write_batch_calls,
	       before->virtio_batched_write_requests,
	       after->virtio_batched_write_requests,
	       before->virtio_indirect_write_batch_calls,
	       after->virtio_indirect_write_batch_calls,
	       before->virtio_read_batch_calls,
	       after->virtio_read_batch_calls,
	       before->virtio_batched_read_requests,
	       after->virtio_batched_read_requests,
	       before->overwrite_prereads_skipped,
	       after->overwrite_prereads_skipped,
	       before_receipt->metadata_dirty,
	       after_receipt->metadata_dirty,
	       before_receipt->metadata_durable,
	       after_receipt->metadata_durable,
	       before_receipt->metadata_requests,
	       after_receipt->metadata_requests,
	       before_receipt->metadata_coalesced,
	       after_receipt->metadata_coalesced,
	       before_receipt->metadata_commits,
	       after_receipt->metadata_commits,
	       before_receipt->metadata_pending,
	       after_receipt->metadata_pending);
}

static void demo_corpus_name(char name[6], char prefix, int index)
{
	check(index >= 0 && index < 100, "demo corpus index");
	name[0] = prefix;
	name[1] = 'w';
	name[2] = 'f';
	name[3] = '0' + index / 10;
	name[4] = '0' + index % 10;
	name[5] = 0;
}

static void check_investigator_digest_observation(uint64 digest_sequence)
{
	int timeline_count;
	int provenance_count;
	int timeline_verified = 0;
	int provenance_verified = 0;

	memset(&demo_timeline_filter, 0, sizeof(demo_timeline_filter));
	demo_timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
				     AGENT_TIMELINE_FILTER_KIND |
				     AGENT_TIMELINE_FILTER_TOOL_ID |
				     AGENT_TIMELINE_FILTER_STATUS;
	demo_timeline_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_CONTEXT;
	demo_timeline_filter.kind = AGENT_AUDIT_KIND_CONTEXT;
	demo_timeline_filter.tool_id = AGENT_TOOL_READ_FILE_DIGEST;
	demo_timeline_filter.status = AGENT_STATUS_OK;
	timeline_count = agent_timeline_query(&demo_timeline_filter,
					      demo_timeline_records,
					      DEMO_OBSERVE_PAGE_RECORDS);
	check(timeline_count >= 1, "investigator timeline digest");
	for (int i = 0; i < timeline_count; i++) {
		struct agent_timeline_record *record = &demo_timeline_records[i];

		check(record->source == AGENT_TIMELINE_SOURCE_CONTEXT,
		      "investigator timeline digest source");
		check(record->kind == AGENT_AUDIT_KIND_CONTEXT,
		      "investigator timeline digest kind");
		check(record->tool_id == AGENT_TOOL_READ_FILE_DIGEST,
		      "investigator timeline digest tool");
		if (record->pid == getpid() &&
		    record->source_pid == getpid() &&
		    record->target_pid == getpid() &&
		    record->sequence == digest_sequence &&
		    record->value0 == strlen(DEMO_ALIGN_LOG_BODY) &&
		    record->value1 == strlen(DEMO_ALIGN_LOG_BODY) &&
		    record->value2 == digest_text(DEMO_ALIGN_LOG_BODY) &&
		    (record->flags & AGENT_CONTEXT_RECORD_F_TRUNCATED) != 0 &&
		    strlen(record->text) == AGENT_CONTEXT_TEXT_SIZE - 1 &&
		    strncmp(DEMO_ALIGN_LOG_BODY, record->text,
			    strlen(record->text)) == 0)
			timeline_verified = 1;
	}
	check(timeline_verified, "investigator timeline digest value");

	provenance_count = agent_provenance_snapshot(demo_provenance_edges,
						      DEMO_PROVENANCE_MAX);
	check(provenance_count >= 1, "investigator digest provenance");
	for (int i = 0; i < provenance_count; i++) {
		struct agent_provenance_edge *edge = &demo_provenance_edges[i];

		if (edge->kind == AGENT_PROVENANCE_EDGE_CONTEXT &&
		    edge->source_pid == getpid() &&
		    edge->target_pid == getpid() &&
		    edge->target_sequence == digest_sequence &&
		    edge->tool_id == AGENT_TOOL_READ_FILE_DIGEST &&
		    edge->status == AGENT_STATUS_OK &&
		    edge->value0 == strlen(DEMO_ALIGN_LOG_BODY) &&
		    edge->value1 == strlen(DEMO_ALIGN_LOG_BODY) &&
		    (edge->flags & AGENT_CONTEXT_RECORD_F_TRUNCATED) != 0 &&
		    strlen(edge->text) == AGENT_CONTEXT_TEXT_SIZE - 1 &&
		    strncmp(DEMO_ALIGN_LOG_BODY, edge->text,
			    strlen(edge->text)) == 0)
			provenance_verified = 1;
	}
	check(provenance_verified, "investigator digest provenance value");
	printf("labdemo_ucore: investigator digest_observation timeline=%d provenance=%d verified=1\n",
	       timeline_count, provenance_count);
}

static void write_demo_file(const char *name, const char *body)
{
	int fd;

	fd = open(name, O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0, "demo file open");
	check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
	      "demo file write");
	check(close(fd) == 0, "demo file close");
}

static int demo_contains(const char *text, const char *needle)
{
	size_t text_len = strlen(text);
	size_t needle_len = strlen(needle);

	if (needle_len == 0 || text_len < needle_len)
		return 0;
	for (size_t i = 0; i + needle_len <= text_len; i++) {
		if (strncmp(text + i, needle, needle_len) == 0)
			return 1;
	}
	return 0;
}

static void print_workload_lane(const char *mode,
				const struct labdemo_workload_metrics *metrics)
{
	const char *discovery_role = strcmp(mode, "native") == 0 ?
		"sentinel" : "orchestrator";

	check(metrics->started_us <= metrics->discovered_us &&
	      metrics->discovered_us <= metrics->committed_us &&
	      metrics->committed_us <= metrics->finished_us,
	      "showcase event order");
	check(metrics->end_to_end_started_us <= metrics->started_us &&
	      metrics->finished_us <= metrics->end_to_end_finished_us,
	      "showcase timing scopes");
	printf("agentos:demo schema=2 nonce=%llu kind=event mode=%s seq=1 tick_us=%llu role=orchestrator event=INCIDENT value0=0 value1=0\n",
	       (uint64)LABDEMO_RUN_NONCE, mode, metrics->started_us);
	printf("agentos:demo schema=2 nonce=%llu kind=event mode=%s seq=2 tick_us=%llu role=%s event=DISCOVERED value0=%llu value1=%llu\n",
	       (uint64)LABDEMO_RUN_NONCE, mode, metrics->discovered_us, discovery_role,
	       metrics->records_examined, metrics->bytes_read);
	printf("agentos:demo schema=2 nonce=%llu kind=event mode=%s seq=3 tick_us=%llu role=recovery event=RECOVERY_COMMITTED value0=%llu value1=1\n",
	       (uint64)LABDEMO_RUN_NONCE, mode, metrics->committed_us,
	       metrics->workload_syscalls);
	printf("agentos:demo schema=2 nonce=%llu kind=event mode=%s seq=4 tick_us=%llu role=orchestrator event=RECOVERED value0=1 value1=0\n",
	       (uint64)LABDEMO_RUN_NONCE, mode, metrics->finished_us);
	printf("agentos:demo schema=2 nonce=%llu kind=metric mode=%s actor_pid=%d core_duration_us=%llu end_to_end_duration_us=%llu end_to_end_started_us=%llu end_to_end_finished_us=%llu workload_syscalls=%llu records_examined=%llu bytes_read=%llu result_items=%llu outcome_hash=%llu\n",
	       (uint64)LABDEMO_RUN_NONCE, mode, getpid(),
	       metrics->finished_us - metrics->started_us,
	       metrics->end_to_end_finished_us -
		       metrics->end_to_end_started_us,
	       metrics->end_to_end_started_us,
	       metrics->end_to_end_finished_us,
	       metrics->workload_syscalls, metrics->records_examined,
	       metrics->bytes_read, metrics->result_items,
	       metrics->outcome_hash);
}

static void run_compat_workload(struct labdemo_workload_metrics *metrics)
{
	static char name[6];
	static char buffer[160];
	struct labdemo_lane_measurement_state *state = &compat_measurement;
	int found = 0;

	memset(metrics, 0, sizeof(*metrics));
	demo_quiescence_fence("compat", "E2E_START", 1, &state->e2e_start);
	metrics->end_to_end_started_us = demo_now_us();
	DEMO_DIAG_BEGIN();
	for (int i = 0; i < LABDEMO_CORPUS_SIZE; i++) {
		demo_corpus_name(name, 'c', i);
		write_demo_file(name, i == DEMO_BENCH_TARGET ?
				DEMO_BENCH_FAILED_BODY : DEMO_BENCH_OK_BODY);
	}
	DEMO_DIAG_END("compat", "seed_file_write_close");
	demo_quiescence_fence("compat", "CORE_START", 2, &state->core_start);
	metrics->started_us = demo_now_us();
	DEMO_DIAG_BEGIN();
	for (int i = 0; i < LABDEMO_CORPUS_SIZE; i++) {
		int fd;
		ssize_t bytes;

		demo_corpus_name(name, 'c', i);
		fd = open(name, O_RDONLY);
		check(fd >= 0, "compat corpus open");
		memset(buffer, 0, sizeof(buffer));
		bytes = read(fd, buffer, sizeof(buffer) - 1);
		check(bytes > 0, "compat corpus read");
		metrics->bytes_read += (uint64)bytes;
		metrics->records_examined++;
		check(close(fd) == 0, "compat corpus close");
		if (demo_contains(buffer, "stage=align;status=failed") &&
		    demo_contains(buffer, "reason=memory_limit"))
			found++;
	}
	DEMO_DIAG_END("compat", "corpus_scan");
	check(found == 1, "compat unique failed stage");
	metrics->discovered_us = demo_now_us();
	demo_corpus_name(name, 'c', DEMO_BENCH_TARGET);
	DEMO_DIAG_BEGIN();
	{
		int fd = open(name, O_WRONLY | O_TRUNC);

		check(fd >= 0, "compat recovery open");
		check(write(fd, DEMO_BENCH_RECOVERED_BODY,
			    strlen(DEMO_BENCH_RECOVERED_BODY)) ==
			      (ssize_t)strlen(DEMO_BENCH_RECOVERED_BODY),
		      "compat recovery write");
		check(fsync(fd) == 0, "compat recovery primary ack");
		check(close(fd) == 0, "compat recovery close");
	}
	DEMO_DIAG_END("compat", "recovery_file_write_close");
	metrics->committed_us = demo_now_us();
	DEMO_DIAG_BEGIN();
	{
		int fd = open(name, O_RDONLY);
		ssize_t bytes;

		check(fd >= 0, "compat final open");
		memset(buffer, 0, sizeof(buffer));
		bytes = read(fd, buffer, sizeof(buffer) - 1);
		check(bytes > 0, "compat final read");
		metrics->bytes_read += (uint64)bytes;
		metrics->records_examined++;
		check(close(fd) == 0, "compat final close");
	}
	DEMO_DIAG_END("compat", "verification_read_close");
	check(demo_contains(buffer, "stage=align;status=recovered"),
	      "compat recovered outcome");
	metrics->result_items = 1;
	metrics->outcome_hash = demo_outcome_hash("align", "recovered");
	metrics->finished_us = demo_now_us();
	take_performance_snapshot(&state->core_ack);
	metrics->workload_syscalls = performance_delta(
		state->core_start.performance.snapshot.observer_workload_syscalls,
		state->core_ack.snapshot.observer_workload_syscalls);
	check(metrics->workload_syscalls != 0, "compat workload syscall receipt");
	demo_quiescence_fence("compat", "ACK_SETTLED", 3,
			      &state->ack_settled);
	DEMO_DIAG_BEGIN();
	for (int i = 0; i < LABDEMO_CORPUS_SIZE; i++) {
		demo_corpus_name(name, 'c', i);
		check(unlink(name) == 0, "compat corpus cleanup");
	}
	DEMO_DIAG_END("compat", "cleanup_unlink");
	demo_quiescence_fence("compat", "E2E_END", 4, &state->e2e_end);
	metrics->end_to_end_finished_us = demo_now_us();
	print_workload_lane("compat", metrics);
	print_mechanism_delta("compat", "core", &state->core_start.performance,
			      &state->core_ack);
	print_mechanism_delta("compat", "end_to_end",
			      &state->e2e_start.performance,
			      &state->e2e_end.performance);
}

static void seed_native_workload(struct agent_file_meta *target)
{
	static struct agent_file_meta meta;
	static char name[6];

	memset(target, 0, sizeof(*target));
	DEMO_DIAG_BEGIN();
	for (int i = 0; i < LABDEMO_CORPUS_SIZE; i++) {
		demo_corpus_name(name, 'n', i);
		write_demo_file(name, i == DEMO_BENCH_TARGET ?
				DEMO_BENCH_FAILED_BODY : DEMO_BENCH_OK_BODY);
	}
	DEMO_DIAG_END("native", "seed_file_write_close");
	DEMO_DIAG_BEGIN();
	for (int i = 0; i < LABDEMO_CORPUS_SIZE; i++) {
		demo_corpus_name(name, 'n', i);
		memset(&meta, 0, sizeof(meta));
		meta.fid = DEMO_BENCH_FID_BASE + i;
		strcpy(meta.physical_name, name);
		strcpy(meta.logical_path, name);
		strcpy(meta.project, DEMO_PROJECT);
		strcpy(meta.workflow, DEMO_WORKFLOW);
		strcpy(meta.run_id, DEMO_BENCH_RUN);
		strcpy(meta.stage, i == DEMO_BENCH_TARGET ? "align" : "other");
		strcpy(meta.kind, "artifact");
		strcpy(meta.status, i == DEMO_BENCH_TARGET ? "failed" : "ok");
		strcpy(meta.summary, i == DEMO_BENCH_TARGET ?
				"memory_limit" : "ready");
		check(agent_file_meta_set(&meta) == 0, "native corpus metadata");
		if (i == DEMO_BENCH_TARGET)
			memcpy(target, &meta, sizeof(*target));
	}
	DEMO_DIAG_END("native", "seed_meta_set");
}

static void run_native_workload(struct labdemo_workload_metrics *metrics)
{
	static struct agent_file_query query;
	static struct agent_file_query_result result;
	static struct agent_file_meta target;
	struct labdemo_lane_measurement_state *state = &native_measurement;
	static char name[6];

	memset(metrics, 0, sizeof(*metrics));
	demo_quiescence_fence("native", "E2E_START", 1, &state->e2e_start);
	metrics->end_to_end_started_us = demo_now_us();
	seed_native_workload(&target);
	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, DEMO_PROJECT);
	strcpy(query.run_id, DEMO_BENCH_RUN);
	strcpy(query.status, "failed");
	demo_quiescence_fence("native", "CORE_START", 2, &state->core_start);
	metrics->started_us = demo_now_us();
	DEMO_DIAG_BEGIN();
	check(agent_file_query(&query, &result) == 1,
	      "native failed-stage query");
	DEMO_DIAG_END("native", "failed_index_query");
	metrics->records_examined += result.scanned_records;
	check(result.returned == 1 && result.used_index == 1 &&
	      strcmp(result.hits[0].stage, "align") == 0 &&
	      strcmp(result.hits[0].status, "failed") == 0,
	      "native failed-stage result");
	metrics->discovered_us = demo_now_us();
	demo_corpus_name(name, 'n', DEMO_BENCH_TARGET);
	DEMO_DIAG_BEGIN();
	{
		int fd = open(name, O_WRONLY | O_TRUNC);

		check(fd >= 0, "native recovery open");
		check(write(fd, DEMO_BENCH_RECOVERED_BODY,
			    strlen(DEMO_BENCH_RECOVERED_BODY)) ==
			      (ssize_t)strlen(DEMO_BENCH_RECOVERED_BODY),
		      "native recovery write");
		target.update_mask = AGENT_FILE_META_UPDATE_STATUS |
				     AGENT_FILE_META_UPDATE_SUMMARY;
		strcpy(target.status, "recovered");
		strcpy(target.summary, "memory_limit recovered");
		check(agent_file_meta_set(&target) == 0,
		      "native recovery metadata stage");
		check(fsync(fd) == 0, "native recovery primary ack");
		check(close(fd) == 0, "native recovery close");
	}
	DEMO_DIAG_END("native", "grouped_recovery_primary_ack");
	metrics->committed_us = demo_now_us();
	strcpy(query.status, "recovered");
	memset(&result, 0, sizeof(result));
	DEMO_DIAG_BEGIN();
	check(agent_file_query(&query, &result) == 1,
	      "native recovered-stage query");
	DEMO_DIAG_END("native", "recovered_index_query");
	metrics->records_examined += result.scanned_records;
	check(result.returned == 1 && result.used_index == 1 &&
	      strcmp(result.hits[0].stage, "align") == 0 &&
	      strcmp(result.hits[0].status, "recovered") == 0,
	      "native recovered-stage result");
	metrics->result_items = 1;
	metrics->outcome_hash = demo_outcome_hash(result.hits[0].stage,
					    result.hits[0].status);
	metrics->finished_us = demo_now_us();
	take_performance_snapshot(&state->core_ack);
	metrics->workload_syscalls = performance_delta(
		state->core_start.performance.snapshot.observer_workload_syscalls,
		state->core_ack.snapshot.observer_workload_syscalls);
	check(metrics->workload_syscalls != 0, "native workload syscall receipt");
	demo_quiescence_fence("native", "ACK_SETTLED", 3,
			      &state->ack_settled);
	DEMO_DIAG_BEGIN();
	for (int i = 0; i < LABDEMO_CORPUS_SIZE; i++) {
		demo_corpus_name(name, 'n', i);
		check(unlink(name) == 0, "native corpus cleanup");
	}
	DEMO_DIAG_END("native", "cleanup_unlink");
	demo_quiescence_fence("native", "E2E_END", 4, &state->e2e_end);
	metrics->end_to_end_finished_us = demo_now_us();
	print_workload_lane("native", metrics);
	print_mechanism_delta("native", "core", &state->core_start.performance,
			      &state->core_ack);
	print_mechanism_delta("native", "end_to_end",
			      &state->e2e_start.performance,
			      &state->e2e_end.performance);
}

static void make_op(struct agent_op *op, int tool, uint64 id, uint64 arg0,
		    const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool;
	op->request_id = id;
	op->arg0 = arg0;
	if (payload)
		strcpy(op->payload, payload);
}

static void run_one(struct agent_op *op, struct agent_result *res, int status,
		    const char *msg)
{
	int n = agent_run(op, res, 1, 0);
	if (n != 1) {
		printf("labdemo_ucore: agent_run failed\n");
		printf("labdemo_ucore: failed check=%s\n", msg);
		printf("labdemo_ucore: failed tool=%d return=%d\n",
		       op->tool_id, n);
	}
	check(n == 1, msg);
	if (res->status != status) {
		printf("labdemo_ucore: result status mismatch\n");
		printf("labdemo_ucore: failed check=%s\n", msg);
		printf("labdemo_ucore: status=%d expected=%d result=%s\n",
		       res->status, status, res->result);
	}
	check(res->status == status, msg);
}

static void created(const char *role)
{
	struct agent_info info;

	check(agent_info(&info) == 0, "agent info");
	printf("labdemo_ucore: created role=%s pid=%d context=%p\n", role,
	       getpid(), (void *)info.context_base);
	printf("agentos:event type=AGENT_CREATED tick=%d role=%s pid=%d context=%p\n",
	       event_tick(), role, getpid(), (void *)info.context_base);
}

static void ready(char c)
{
	char start;

	if (ready_fd >= 0) {
		check(write(ready_fd, &c, 1) == 1, "ready write");
		check(close(ready_fd) == 0, "ready close");
		ready_fd = -1;
	}
	check(start_fd >= 0, "start barrier descriptor");
	check(read(start_fd, &start, 1) == 1 && start == 'G',
	      "start barrier release");
	check(close(start_fd) == 0, "start barrier close");
	start_fd = -1;
}

static void report_progress(char stage, uint records_examined,
			    uint denied_actions, uint duplicate_actions,
			    uint recovery_side_effects, uint64 milestone_us)
{
	static struct labdemo_progress_receipt receipt;
	static struct agent_info info;

	check(progress_fd >= 0, "progress descriptor");
	check(agent_info(&info) == 0, "progress agent info");
	memset(&receipt, 0, sizeof(receipt));
	receipt.magic = LABDEMO_PROGRESS_MAGIC;
	receipt.stage = (uint)(unsigned char)stage;
	receipt.records_examined = records_examined;
	receipt.denied_actions = denied_actions;
	receipt.duplicate_actions = duplicate_actions;
	receipt.recovery_side_effects = recovery_side_effects;
	receipt.milestone_us = milestone_us;
	receipt.tool_calls = info.agent_call_count;
	receipt.dispatches = info.sched_dispatch_count;
	receipt.wait_sleeps = info.wait_sleep_count;
	receipt.wait_wakeups = info.wait_wakeup_count;
	check(write(progress_fd, &receipt, sizeof(receipt)) ==
		      (ssize_t)sizeof(receipt),
	      "progress receipt");
	check(close(progress_fd) == 0, "progress close");
	progress_fd = -1;
}

static void run_sentinel(void)
{
	static struct agent_event event;
	static struct agent_op op;
	static struct agent_result res;
	static struct agent_file_prefetch_hint
		hints[AGENT_FILE_PREFETCH_MAX_HINTS];
	int hint_count;
	int matched = 0;
	int query_records;
	uint64 discovered_us;

	created("sentinel");
	check(agent_heartbeat(5) == 0, "heartbeat");
	check(agent_watch(AGENT_EVENT_FILE_STATUS, "status=failed") == 0,
	      "watch failed");
	ready('S');
	printf("agentos:event type=WATCH_REGISTERED tick=%d role=sentinel event=FILE_STATUS filter=status=failed\n",
	       event_tick());
	printf("agentos:event type=AGENT_STATE tick=%d role=sentinel state=WAITING\n",
	       event_tick());
	for (int i = 0; i < 64; i++) {
		check(agent_wait(&event, 300) == AGENT_STATUS_OK,
		      "sentinel wait");
		if (event.type == AGENT_EVENT_FILE_STATUS) {
			matched = 1;
			break;
		}
		check(event.type == AGENT_EVENT_TIMER &&
			      strcmp(event.payload, "timer=heartbeat") == 0,
		      "sentinel intrinsic heartbeat");
	}
	check(matched, "sentinel failed file event");
	check(agent_heartbeat_stop() == 0, "sentinel heartbeat stop");
	printf("labdemo_ucore: sentinel event payload=%s\n", event.payload);
	printf("agentos:event type=AGENT_STATE tick=%d role=sentinel state=RUNNING event_id=%d corr_id=%d\n",
	       event_tick(), (int)event.event_id, (int)event.corr_id);

	make_op(&op, AGENT_TOOL_QUERY_FILE, 1001, 0,
		"project=" DEMO_PROJECT ";run_id=" DEMO_RUN ";status=failed");
	run_one(&op, &res, AGENT_STATUS_OK, "query failed files");
	query_records = (int)res.value1;
	printf("agentos:event type=TOOL_CALL tick=%d role=sentinel tool=query_file project=%s run_id=%s status=failed hits=%d used_index=%d seq=%d\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, (int)res.value0,
	       (int)(res.value2 & 1), (int)res.sequence);
	hint_count = agent_file_prefetch_snapshot(hints,
						  AGENT_FILE_PREFETCH_MAX_HINTS);
	check(hint_count >= 1, "prefetch hints");
	check((hints[0].reason & AGENT_FILE_PREFETCH_REASON_DEPENDENCY) != 0,
	      "prefetch dependency");
	check(hints[0].hit.stage[0] != 0, "prefetch stage");
	printf("labdemo_ucore: sentinel prefetch_hint stage=%s source_seq=%d plan=%d candidates=%d\n",
	       hints[0].hit.stage, (int)hints[0].source_sequence,
	       hints[0].plan, hints[0].candidate_records);
	printf("agentos:event type=PREFETCH_HINT tick=%d role=sentinel project=%s run_id=%s source_stage=align next_stage=%s source_seq=%d candidates=%d reason=%d\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, hints[0].hit.stage,
	       (int)hints[0].source_sequence, hints[0].candidate_records,
	       (int)hints[0].reason);

	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 1002,
		AGENT_ROLE_SENTINEL, "action_commit");
	run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel denied");
	printf("agentos:event type=AUDIT tick=%d role=sentinel action=action_commit result=DENIED reason=capability corr_id=%s seq=%d\n",
	       event_tick(), DEMO_ALIGN_CORR, (int)res.sequence);
	discovered_us = demo_now_us();

	make_op(&op, AGENT_TOOL_SEND_MESSAGE, 1003, investigator_pid,
		"investigate " DEMO_RUN " align");
	run_one(&op, &res, AGENT_STATUS_OK, "message investigator");
	printf("agentos:event type=MESSAGE tick=%d from=sentinel to=investigator status=OK corr_id=MSG-%s-S-I prefetch_handoff=%s seq=%d\n",
	       event_tick(), DEMO_RUN, hints[0].hit.stage,
	       (int)res.sequence);
	report_progress('S', query_records, 1, 0, 0, discovered_us);
	exit(0);
}

static void run_investigator(void)
{
	static struct agent_event event;
	static struct agent_op op;
	static struct agent_result res;
	static struct agent_context_header header;
	static struct agent_context_record records[8];
	static struct agent_file_prefetch_hint
		hints[AGENT_FILE_PREFETCH_MAX_HINTS];
	int n;
	int summary_seq;
	int digest_seq;
	int dependency_seq;
	int prefetch_seq;
	int hint_count;
	int span_count;
	int span_trace_count;
	int span_found = 0;
	int span_source = 0;
	int span_target = 0;
	int span_trace_context = 0;
	int span_trace_event = 0;
	int span_trace_prefetch = 0;
	char prefetch_stage[AGENT_FILE_FIELD_SIZE];
	uint64 handoff_us;

	created("investigator");
	check(agent_watch(AGENT_EVENT_MESSAGE, "investigate") == 0,
	      "watch message");
	ready('I');
	check(agent_wait(&event, 300) == AGENT_STATUS_OK,
	      "investigator wait");
	check(event.type == AGENT_EVENT_MESSAGE,
	      "investigator message type");
	check(event.corr_id == 1003, "investigator message correlation");
	check(strncmp(event.payload, "investigate " DEMO_RUN " align",
		      strlen("investigate " DEMO_RUN " align")) == 0,
	      "investigator message payload");
	hint_count = agent_file_prefetch_snapshot(hints,
						  AGENT_FILE_PREFETCH_MAX_HINTS);
	check(hint_count >= 1, "handoff prefetch hints");
	check((hints[0].reason & AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0,
	      "handoff prefetch reason");
	check((hints[0].reason & AGENT_FILE_PREFETCH_REASON_DEPENDENCY) != 0,
	      "handoff dependency reason");
	memset(prefetch_stage, 0, sizeof(prefetch_stage));
	strcpy(prefetch_stage, hints[0].hit.stage);
	check(prefetch_stage[0] != 0, "investigator prefetch stage");
	printf("labdemo_ucore: investigator handoff_prefetch stage=%s source_seq=%d reason=%d\n",
	       prefetch_stage, (int)hints[0].source_sequence,
	       (int)hints[0].reason);
	span_count = agent_file_prefetch_span_snapshot(
		demo_span_prefetch, AGENT_FILE_PREFETCH_SPAN_MAX);
	check(span_count >= 1, "span prefetch hints");
	for (int i = 0; i < span_count; i++) {
		if (strcmp(demo_span_prefetch[i].hit.stage, prefetch_stage) == 0 &&
		    demo_span_prefetch[i].target_pid == getpid() &&
		    demo_span_prefetch[i].source_pid != getpid() &&
		    (demo_span_prefetch[i].reason &
		     AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0 &&
		    (demo_span_prefetch[i].reason &
		     AGENT_FILE_PREFETCH_REASON_SPAN_BUS) != 0) {
			span_found = 1;
			span_source = demo_span_prefetch[i].source_pid;
			span_target = demo_span_prefetch[i].target_pid;
			break;
		}
	}
	check(span_found, "span prefetch handoff");
	printf("labdemo_ucore: investigator span_prefetch stage=%s count=%d source_pid=%d target_pid=%d\n",
	       prefetch_stage, span_count, span_source, span_target);
	span_trace_count = agent_span_trace_snapshot(
		demo_audit_records, DEMO_OBSERVE_PAGE_RECORDS);
	check(span_trace_count >= 1, "investigator span trace");
	for (int i = 0; i < span_trace_count; i++) {
		check(demo_audit_records[i].span_id == event.span_id,
		      "span trace id");
		if (demo_audit_records[i].kind == AGENT_AUDIT_KIND_CONTEXT)
			span_trace_context = 1;
		if (demo_audit_records[i].kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE ||
		    demo_audit_records[i].kind == AGENT_AUDIT_KIND_EVENT_CONSUME)
			span_trace_event = 1;
		if (demo_audit_records[i].kind == AGENT_AUDIT_KIND_PREFETCH)
			span_trace_prefetch = 1;
	}
	check(span_trace_context, "span trace context");
	check(span_trace_event, "span trace event");
	check(span_trace_prefetch, "span trace prefetch");
	printf("labdemo_ucore: investigator span_trace records=%d context=%d event=%d prefetch=%d\n",
	       span_trace_count, span_trace_context, span_trace_event,
	       span_trace_prefetch);
	make_op(&op, AGENT_TOOL_READ_FILE_SUMMARY, 2001, 0, "align");
	run_one(&op, &res, AGENT_STATUS_OK, "read summary");
	summary_seq = res.sequence;
	printf("labdemo_ucore: investigator reason=%s\n", res.result);
	printf("agentos:event type=TOOL_CALL tick=%d role=investigator tool=read_file_summary stage=align status=OK seq=%d\n",
	       event_tick(), summary_seq);
	make_op(&op, AGENT_TOOL_READ_FILE_DIGEST, 2005, 0,
		"project=" DEMO_PROJECT ";run_id=" DEMO_RUN
		";stage=align;status=failed");
	run_one(&op, &res, AGENT_STATUS_OK, "read digest");
	check(res.value0 == strlen(DEMO_ALIGN_LOG_BODY), "digest size");
	check(res.value1 == strlen(DEMO_ALIGN_LOG_BODY), "digest bytes");
	check(res.value2 == digest_text(DEMO_ALIGN_LOG_BODY), "digest hash");
	check(strcmp(res.result, DEMO_ALIGN_LOG_BODY) == 0,
	      "digest preview");
	digest_seq = res.sequence;
	printf("labdemo_ucore: investigator digest bytes=%d preview=%s seq=%d\n",
	       (int)res.value1, res.result, digest_seq);
	printf("agentos:event type=TOOL_CALL tick=%d role=investigator tool=read_file_digest stage=align status=OK bytes=%d seq=%d\n",
	       event_tick(), (int)res.value1, digest_seq);
	check_investigator_digest_observation(digest_seq);
	make_op(&op, AGENT_TOOL_DEPENDENCY_QUERY, 2002, 0, "label=align");
	run_one(&op, &res, AGENT_STATUS_OK, "dependency");
	dependency_seq = res.sequence;
	printf("labdemo_ucore: affected labels=%s\n", res.result);
	printf("agentos:event type=TOOL_CALL tick=%d role=investigator tool=dependency_query label=align impact=%s seq=%d\n",
	       event_tick(), res.result, dependency_seq);
	make_op(&op, AGENT_TOOL_READ_FILE_SUMMARY, 2004, 0, prefetch_stage);
	run_one(&op, &res, AGENT_STATUS_OK, "prefetch summary");
	prefetch_seq = res.sequence;
	printf("labdemo_ucore: investigator prefetch_summary stage=%s result=%s\n",
	       prefetch_stage, res.result);
	printf("agentos:event type=PREFETCH_USED tick=%d role=investigator stage=%s summary=%s seq=%d\n",
	       event_tick(), prefetch_stage, res.result, prefetch_seq);
	printf("agentos:event type=LLM_CALL tick=%d mode=template task=explain_root_cause llm_request_id=%s project=%s run_id=%s refs=%d,%d,%d,%d status=OK\n",
	       event_tick(), DEMO_LLM_REQUEST, DEMO_PROJECT, DEMO_RUN,
	       summary_seq, digest_seq, dependency_seq, prefetch_seq);
	printf("agentos:event type=LLM_RESULT tick=%d mode=template llm_request_id=%s llm_status=OK llm_explanation=memory_limit referenced_sequences=%d,%d,%d,%d confidence=medium\n",
	       event_tick(), DEMO_LLM_REQUEST, summary_seq, digest_seq,
	       dependency_seq, prefetch_seq);
	printf("agentos:event type=PLAN_CREATED tick=%d role=investigator plan=%s project=%s run_id=%s actions=align,analyze,report skip=prepare prefetch=%s refs=%d,%d,%d,%d\n",
	       event_tick(), DEMO_PLAN, DEMO_PROJECT, DEMO_RUN,
	       prefetch_stage, summary_seq, digest_seq, dependency_seq,
	       prefetch_seq);
	n = context_snapshot(&header, records, 8);
	check(n >= 1, "investigator context");
	printf("agentos:event type=CONTEXT_SNAPSHOT tick=%d role=investigator records=%d latest=%d\n",
	       event_tick(), n, (int)header.latest_sequence);
	handoff_us = demo_now_us();
	make_op(&op, AGENT_TOOL_SEND_MESSAGE, 2003, recovery_pid,
		"recover " DEMO_RUN " align plan=" DEMO_PLAN);
	run_one(&op, &res, AGENT_STATUS_OK, "message recovery");
	printf("agentos:event type=MESSAGE tick=%d from=investigator to=recovery status=OK corr_id=MSG-%s-I-R plan=%s seq=%d\n",
	       event_tick(), DEMO_RUN, DEMO_PLAN, (int)res.sequence);
	report_progress('I', 0, 0, 0, 0, handoff_us);
	exit(0);
}

static void run_recovery(void)
{
	static struct agent_event event;
	static struct agent_op op;
	static struct agent_result res;
	static struct agent_file_query query;
	static struct agent_file_query_result result;

	created("recovery");
	check(agent_watch(AGENT_EVENT_MESSAGE, "recover") == 0, "watch recover");
	ready('R');
	check(agent_wait(&event, 300) == AGENT_STATUS_OK, "recovery wait");
	check(event.type == AGENT_EVENT_MESSAGE, "recovery message type");
	check(event.corr_id == 2003, "recovery message correlation");
	check(strncmp(event.payload, "recover " DEMO_RUN " align plan=" DEMO_PLAN,
		      strlen("recover " DEMO_RUN " align plan=" DEMO_PLAN)) == 0,
	      "recovery message payload");
	make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 3001,
		AGENT_ROLE_RECOVERY, "action_commit");
	run_one(&op, &res, AGENT_STATUS_OK, "capability");
	printf("agentos:event type=AUDIT tick=%d role=recovery action=action_commit result=ALLOW plan=%s seq=%d\n",
	       event_tick(), DEMO_PLAN, (int)res.sequence);
	printf("agentos:event type=AUDIT tick=%d role=recovery action=commit_prepare result=DENIED reason=unaffected plan=%s\n",
	       event_tick(), DEMO_PLAN);
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 4201, AGENT_ROLE_RECOVERY,
		"label=align;run_id=" DEMO_RUN ";namespace=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_OK, "commit align");
	printf("agentos:event type=ACTION tick=%d role=recovery label=align status=OK corr_id=%s plan=%s seq=%d duplicate=0\n",
	       event_tick(), DEMO_ALIGN_CORR, DEMO_PLAN, (int)res.sequence);
	make_op(&op, AGENT_TOOL_ACTION_COMMIT, 4201, AGENT_ROLE_RECOVERY,
		"label=align;run_id=" DEMO_RUN ";namespace=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_DUPLICATE, "duplicate");
	printf("agentos:event type=AUDIT tick=%d role=recovery action=commit_align result=DUPLICATE corr_id=%s plan=%s seq=%d\n",
	       event_tick(), DEMO_ALIGN_CORR, DEMO_PLAN, (int)res.sequence);
	make_op(&op, AGENT_TOOL_ARTIFACT_UPDATE, 4202, AGENT_ROLE_RECOVERY,
		"label=report;run_id=" DEMO_RUN ";namespace=" DEMO_PROJECT);
	run_one(&op, &res, AGENT_STATUS_OK, "update artifact");
	printf("agentos:event type=ARTIFACT tick=%d role=recovery namespace=%s run_id=%s file=RUN-042-recovery.md status=OK corr_id=%s plan=%s seq=%d llm_enhanced=0\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, DEMO_REPORT_CORR,
	       DEMO_PLAN, (int)res.sequence);
	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, DEMO_PROJECT);
	strcpy(query.run_id, DEMO_RUN);
	strcpy(query.status, "ok");
	strcpy(query.kind, "report");
	check(agent_file_query(&query, &result) >= 1, "final query");
	printf("labdemo_ucore: final report_query hits=%d used_index=%d scanned=%d\n",
	       result.total_hits, result.used_index, result.scanned_records);
	printf("agentos:event type=FINAL tick=%d project=%s run_id=%s status=RECOVERED plan=%s\n",
	       event_tick(), DEMO_PROJECT, DEMO_RUN, DEMO_PLAN);
	report_progress('R', result.scanned_records, 0, 1, 1, demo_now_us());
	exit(0);
}

static void set_demo_meta(int fid, const char *physical, const char *stage,
			  const char *kind, const char *status,
			  const char *summary, uint64 deps)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = fid;
	strcpy(meta.physical_name, physical);
	strcpy(meta.logical_path, physical);
	strcpy(meta.project, DEMO_PROJECT);
	strcpy(meta.workflow, DEMO_WORKFLOW);
	strcpy(meta.run_id, DEMO_RUN);
	strcpy(meta.stage, stage);
	strcpy(meta.kind, kind);
	strcpy(meta.status, status);
	strcpy(meta.summary, summary);
	meta.dependency_mask = deps;
	meta.flags = AGENT_FILE_META_F_PERSIST;
	check(agent_file_meta_set(&meta) == 0, "demo meta set");
}

static void seed_demo_metadata(void)
{
	set_demo_meta(1, "r42align", "align", "artifact", "ok",
		      "align output is ready before injected failure",
		      agent_dependency_label_bit("analyze") |
			      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive"));
	set_demo_meta(2, "r42anlz", "analyze", "status", "pending",
		      "analysis waits for align",
		      agent_dependency_label_bit("report") |
			      agent_dependency_label_bit("archive"));
	set_demo_meta(3, "r42report", "report", "report", "pending",
		      "report waits for analyze",
		      agent_dependency_label_bit("archive"));
	set_demo_meta(4, "r42archive", "archive", "artifact", "pending",
		      "archive waits for report", 0);
}

static void inject_failure(void)
{
	static struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = 5;
	write_demo_file(DEMO_ALIGN_LOG, DEMO_ALIGN_LOG_BODY);
	strcpy(meta.physical_name, DEMO_ALIGN_LOG);
	strcpy(meta.project, DEMO_PROJECT);
	strcpy(meta.workflow, DEMO_WORKFLOW);
	strcpy(meta.run_id, DEMO_RUN);
	strcpy(meta.stage, "align");
	strcpy(meta.kind, "log");
	strcpy(meta.status, "running");
	strcpy(meta.summary, "align stage running before failure");
	meta.dependency_mask = agent_dependency_label_bit("analyze") |
			       agent_dependency_label_bit("report") |
			       agent_dependency_label_bit("archive");
	check(agent_file_meta_set(&meta) == 0, "stage failure transition");
	meta.update_mask = AGENT_FILE_META_UPDATE_STATUS |
			   AGENT_FILE_META_UPDATE_SUMMARY;
	strcpy(meta.status, "failed");
	strcpy(meta.summary, "memory limit exceeded at align stage");
	check(agent_file_meta_set(&meta) == 0, "inject failure");
	printf("agentos:event type=INCIDENT_CREATED tick=%d id=%s project=%s workflow=%s run_id=%s stage=align reason=memory_limit\n",
	       event_tick(), DEMO_INCIDENT, DEMO_PROJECT, DEMO_WORKFLOW,
	       DEMO_RUN);
}

static void check_global_audit(int sentinel_pid)
{
	int n;
	int snapshot_count;
	int has_context = 0;
	int has_enqueue = 0;
	int has_consume = 0;
	int has_sched = 0;
	int has_prefetch = 0;
	int has_sentinel = 0;
	int has_investigator = 0;
	int has_recovery = 0;
	int context_query;
	int span_query;
	int event_query;
	int prefetch_query;
	int start_query;
	uint64 last_sequence = 0;
	uint64 latest_sequence;
	uint64 query_span = 0;

	n = agent_audit_snapshot(demo_audit_records,
				 DEMO_OBSERVE_PAGE_RECORDS);
	check(n > 0, "audit count");
	check(n <= DEMO_OBSERVE_PAGE_RECORDS, "audit page cap");
	snapshot_count = n;
	for (int i = 0; i < n; i++) {
		struct agent_audit_record *r = &demo_audit_records[i];
		check(r->sequence > last_sequence, "audit sequence order");
		last_sequence = r->sequence;
		if (query_span == 0 && r->span_id != 0)
			query_span = r->span_id;
		if (r->pid == sentinel_pid || r->target_pid == sentinel_pid)
			has_sentinel = 1;
		if (r->pid == investigator_pid || r->target_pid == investigator_pid)
			has_investigator = 1;
		if (r->pid == recovery_pid || r->target_pid == recovery_pid)
			has_recovery = 1;
		if (r->kind == AGENT_AUDIT_KIND_CONTEXT)
			has_context = 1;
		if (r->kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE &&
		    r->event_type == AGENT_EVENT_FILE_STATUS &&
		    r->target_pid == sentinel_pid)
			has_enqueue = 1;
		if (r->kind == AGENT_AUDIT_KIND_EVENT_CONSUME &&
		    r->event_type == AGENT_EVENT_FILE_STATUS &&
		    r->pid == sentinel_pid)
			has_consume = 1;
		if (r->kind == AGENT_AUDIT_KIND_SCHED &&
		    (r->pid == sentinel_pid || r->pid == investigator_pid ||
		     r->pid == recovery_pid))
			has_sched = 1;
		if (r->kind == AGENT_AUDIT_KIND_PREFETCH &&
		    r->source_pid == sentinel_pid &&
		    r->target_pid == investigator_pid &&
		    r->event_type == AGENT_EVENT_MESSAGE &&
		    (r->flags & AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0)
			has_prefetch = 1;
	}
	check(has_context, "audit context");
	check(has_enqueue, "audit event enqueue");
	check(has_consume, "audit event consume");
	check(has_sched, "audit sched");
	check(has_prefetch, "audit prefetch handoff");
	check(has_sentinel && has_investigator && has_recovery,
	      "audit multi agent");
	latest_sequence = last_sequence;

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags = AGENT_AUDIT_FILTER_KIND;
	demo_audit_filter.kind = AGENT_AUDIT_KIND_CONTEXT;
	context_query = agent_audit_query(&demo_audit_filter, demo_audit_records,
					  DEMO_OBSERVE_PAGE_RECORDS);
	check(context_query > 0, "audit query context");
	for (int i = 0; i < context_query; i++)
		check(demo_audit_records[i].kind == AGENT_AUDIT_KIND_CONTEXT,
		      "audit query context kind");

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags = AGENT_AUDIT_FILTER_SPAN_ID;
	demo_audit_filter.span_id = query_span;
	span_query = agent_audit_query(&demo_audit_filter, demo_audit_records,
				       DEMO_OBSERVE_PAGE_RECORDS);
	check(query_span != 0 && span_query > 0, "audit query span");
	for (int i = 0; i < span_query; i++)
		check(demo_audit_records[i].span_id == query_span,
		      "audit query span id");

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags =
		AGENT_AUDIT_FILTER_TARGET_PID | AGENT_AUDIT_FILTER_EVENT_TYPE;
	demo_audit_filter.target_pid = sentinel_pid;
	demo_audit_filter.event_type = AGENT_EVENT_FILE_STATUS;
	event_query = agent_audit_query(&demo_audit_filter, demo_audit_records,
					DEMO_OBSERVE_PAGE_RECORDS);
	check(event_query >= 2, "audit query event");
	for (int i = 0; i < event_query; i++) {
		check(demo_audit_records[i].target_pid == sentinel_pid,
		      "audit query event target");
		check(demo_audit_records[i].event_type ==
			      AGENT_EVENT_FILE_STATUS,
		      "audit query event type");
	}

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags = AGENT_AUDIT_FILTER_KIND |
				  AGENT_AUDIT_FILTER_SOURCE_PID |
				  AGENT_AUDIT_FILTER_TARGET_PID;
	demo_audit_filter.kind = AGENT_AUDIT_KIND_PREFETCH;
	demo_audit_filter.source_pid = sentinel_pid;
	demo_audit_filter.target_pid = investigator_pid;
	prefetch_query = agent_audit_query(&demo_audit_filter,
					   demo_audit_records,
					   DEMO_OBSERVE_PAGE_RECORDS);
	check(prefetch_query >= 1, "audit query prefetch");
	for (int i = 0; i < prefetch_query; i++) {
		check(demo_audit_records[i].kind == AGENT_AUDIT_KIND_PREFETCH,
		      "audit query prefetch kind");
		check(demo_audit_records[i].source_pid == sentinel_pid,
		      "audit query prefetch source");
		check(demo_audit_records[i].target_pid == investigator_pid,
		      "audit query prefetch target");
		check((demo_audit_records[i].flags &
		       AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0,
		      "audit query prefetch handoff");
	}

	memset(&demo_audit_filter, 0, sizeof(demo_audit_filter));
	demo_audit_filter.flags = AGENT_AUDIT_FILTER_START_SEQUENCE;
	demo_audit_filter.start_sequence = latest_sequence;
	start_query = agent_audit_query(&demo_audit_filter, demo_audit_records,
					DEMO_OBSERVE_PAGE_RECORDS);
	check(start_query >= 1, "audit query start");
	for (int i = 0; i < start_query; i++)
		check(demo_audit_records[i].sequence >= latest_sequence,
		      "audit query start sequence");

	printf("labdemo_ucore: global_audit=1 records=%d agents=3 context=%d event=%d sched=%d prefetch=%d\n",
	       snapshot_count, has_context, has_enqueue && has_consume,
	       has_sched, has_prefetch);
	printf("labdemo_ucore: audit_query=1 kind=%d span=%d event=%d prefetch=%d start=%d\n",
	       context_query, span_query, event_query, prefetch_query,
	       start_query);
}

static void check_unified_timeline(int sentinel_pid)
{
	int n;
	int filtered;
	int cursor_filtered;
	int has_context = 0;
	int has_event = 0;
	int has_sched = 0;
	int has_prefetch = 0;
	uint64 last_tick = 0;
	uint64 cursor_tick;
	uint64 cursor_sequence;
	int cursor_source;

	n = agent_timeline_snapshot(demo_timeline_records,
				    DEMO_OBSERVE_PAGE_RECORDS);
	check(n > 0, "timeline count");
	for (int i = 0; i < n; i++) {
		struct agent_timeline_record *r = &demo_timeline_records[i];

		check(r->tick >= last_tick, "timeline order");
		last_tick = r->tick;
		if (r->source != AGENT_TIMELINE_SOURCE_AUDIT)
			continue;
		if (r->kind == AGENT_AUDIT_KIND_CONTEXT)
			has_context = 1;
		if (r->kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE ||
		    r->kind == AGENT_AUDIT_KIND_EVENT_CONSUME)
			has_event = 1;
		if (r->kind == AGENT_AUDIT_KIND_SCHED)
			has_sched = 1;
		if (r->kind == AGENT_AUDIT_KIND_PREFETCH &&
		    (r->flags & AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0)
			has_prefetch = 1;
	}
	check(has_context, "timeline audit context");
	check(has_event, "timeline audit event");
	check(has_sched, "timeline audit sched");
	check(has_prefetch, "timeline audit prefetch");
	cursor_tick = demo_timeline_records[n / 2].tick;
	cursor_source = demo_timeline_records[n / 2].source;
	cursor_sequence = demo_timeline_records[n / 2].sequence;
	memset(&demo_timeline_filter, 0, sizeof(demo_timeline_filter));
	demo_timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
				     AGENT_TIMELINE_FILTER_KIND |
				     AGENT_TIMELINE_FILTER_SOURCE_PID |
				     AGENT_TIMELINE_FILTER_TARGET_PID |
				     AGENT_TIMELINE_FILTER_FLAGS_ALL;
	demo_timeline_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_AUDIT;
	demo_timeline_filter.kind = AGENT_AUDIT_KIND_PREFETCH;
	demo_timeline_filter.source_pid = sentinel_pid;
	demo_timeline_filter.target_pid = investigator_pid;
	demo_timeline_filter.require_flags =
		AGENT_FILE_PREFETCH_REASON_HANDOFF;
	filtered = agent_timeline_query(&demo_timeline_filter,
					demo_timeline_records,
					DEMO_OBSERVE_PAGE_RECORDS);
	check(filtered >= 1, "timeline query prefetch");
	for (int i = 0; i < filtered; i++) {
		struct agent_timeline_record *r = &demo_timeline_records[i];

		check(r->source == AGENT_TIMELINE_SOURCE_AUDIT,
		      "timeline query source");
		check(r->kind == AGENT_AUDIT_KIND_PREFETCH,
		      "timeline query kind");
		check(r->source_pid == sentinel_pid,
		      "timeline query source pid");
		check(r->target_pid == investigator_pid,
		      "timeline query target pid");
		check((r->flags & AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0,
		      "timeline query flags");
	}
	memset(&demo_timeline_filter, 0, sizeof(demo_timeline_filter));
	demo_timeline_filter.flags = AGENT_TIMELINE_FILTER_AFTER_CURSOR;
	demo_timeline_filter.after_tick = cursor_tick;
	demo_timeline_filter.after_source = cursor_source;
	demo_timeline_filter.after_sequence = cursor_sequence;
	cursor_filtered = agent_timeline_query(&demo_timeline_filter,
					       demo_timeline_records,
					       DEMO_OBSERVE_PAGE_RECORDS);
	check(cursor_filtered > 0, "timeline query cursor");
	for (int i = 0; i < cursor_filtered; i++)
		check(timeline_after_cursor(&demo_timeline_records[i],
					    cursor_tick, cursor_source,
					    cursor_sequence),
		      "timeline query cursor order");
	printf("labdemo_ucore: unified_timeline records=%d context=%d event=%d sched=%d prefetch=%d\n",
	       n, has_context, has_event, has_sched, has_prefetch);
	printf("labdemo_ucore: timeline_query prefetch=%d cursor=%d\n",
	       filtered, cursor_filtered);
}

static void check_provenance_graph(int sentinel_pid)
{
	int n;
	int has_message = 0;
	int has_prefetch = 0;

	n = agent_provenance_snapshot(demo_provenance_edges,
				      DEMO_PROVENANCE_MAX);
	check(n > 0, "provenance graph");
	for (int i = 0; i < n; i++) {
		struct agent_provenance_edge *edge =
			&demo_provenance_edges[i];

		if (edge->kind != AGENT_PROVENANCE_EDGE_AUDIT)
			continue;
		if (edge->source_pid == sentinel_pid &&
		    edge->target_pid == investigator_pid) {
			if (edge->event_type == AGENT_EVENT_MESSAGE)
				has_message = 1;
			if (edge->tool_id == AGENT_TOOL_QUERY_FILE &&
			    (edge->flags &
			     AGENT_FILE_PREFETCH_REASON_HANDOFF) != 0)
				has_prefetch = 1;
		}
	}
	check(has_message, "provenance message");
	check(has_prefetch, "provenance prefetch");
	printf("labdemo_ucore: provenance_graph edges=%d message=%d prefetch=%d\n",
	       n, has_message, has_prefetch);
}

static void run_runtime_mechanism_probe(void)
{
	static struct labdemo_fence_receipt before;
	static struct labdemo_fence_receipt after;
	int status;

	demo_quiescence_fence("runtime_probe", "PROBE_START", 1, &before);
	for (int i = 0; i < 3; i++) {
		int pid = fork();

		check(pid >= 0, "runtime probe fork");
		if (pid == 0) {
			char *argv[] = { "ldexecprobe", 0 };

			demo_cow_probe_word += (uint64)i + 1;
			if (exec("ldexecprobe", argv) < 0)
				exit(2);
			exit(3);
		}
		check(waitpid(pid, &status) == pid, "runtime probe wait");
		check(status == 0, "runtime probe child status");
	}
	demo_quiescence_fence("runtime_probe", "PROBE_END", 2, &after);
	check(after.performance.snapshot.cow_pages_shared >
		      before.performance.snapshot.cow_pages_shared,
	      "runtime probe COW shared pages");
	check(after.performance.snapshot.cow_pages_copied >
		      before.performance.snapshot.cow_pages_copied &&
	      after.performance.snapshot.cow_fault_promotions >
		      before.performance.snapshot.cow_fault_promotions,
	      "runtime probe COW promotions");
	check(after.performance.snapshot.exec_cache_misses >
		      before.performance.snapshot.exec_cache_misses,
	      "runtime probe exec miss");
	check(after.performance.snapshot.exec_cache_hits >
		      before.performance.snapshot.exec_cache_hits &&
	      after.performance.snapshot.exec_cache_shared_pages >
		      before.performance.snapshot.exec_cache_shared_pages,
	      "runtime probe exec reuse");
	print_mechanism_delta("runtime_probe", "end_to_end",
			      &before.performance, &after.performance);
}

static void run_orchestrator(void)
{
	static struct labdemo_progress_receipt sentinel_receipt;
	static struct labdemo_progress_receipt investigator_receipt;
	static struct labdemo_progress_receipt recovery_receipt;
	static struct labdemo_progress_receipt incoming_receipt;
	int sentinel_pid;
	int ready_pipe[2];
	int recovery_start[2];
	int investigator_start[2];
	int sentinel_start[2];
	int progress_pipe[2];
	int status = 0;
	int ok = 0;
	int ready_count = 0;
	int ready_mask = 0;
	char ch;
	uint64 workflow_started_us;
	uint64 workflow_discovered_us;
	uint64 workflow_handoff_us;
	uint64 workflow_committed_us;
	uint64 workflow_finished_us;
	uint64 tool_calls;
	uint64 dispatches;
	uint64 wait_sleeps;
	uint64 wait_wakeups;
	uint64 records_examined;

	created("orchestrator");
	memset(&compat_measurement, 0, sizeof(compat_measurement));
	memset(&native_measurement, 0, sizeof(native_measurement));
	memset(&workflow_perf_before, 0, sizeof(workflow_perf_before));
	memset(&workflow_perf_after, 0, sizeof(workflow_perf_after));
	memset(&measurement_warmup, 0, sizeof(measurement_warmup));
	take_performance_snapshot(&measurement_warmup);
	printf("agentos:event type=RUN_OBJECT tick=%d project=%s workflow=%s run_id=%s desired_state=RECOVERED policy=minimal_rerun\n",
	       event_tick(), DEMO_PROJECT, DEMO_WORKFLOW, DEMO_RUN);
	printf("agentos:demo schema=2 nonce=%llu kind=run sample=%d order=%s\n",
	       (uint64)LABDEMO_RUN_NONCE, LABDEMO_SAMPLE_ID,
	       LABDEMO_NATIVE_FIRST ? "native_then_compat" :
				      "compat_then_native");
	check(agent_file_meta_init() == 0, "meta init");
	if (LABDEMO_NATIVE_FIRST) {
		run_native_workload(&native_metrics);
		run_compat_workload(&compat_metrics);
	} else {
		run_compat_workload(&compat_metrics);
		run_native_workload(&native_metrics);
	}
	check(compat_metrics.outcome_hash == native_metrics.outcome_hash,
	      "showcase outcome equivalence");
	printf("agentos:demo schema=2 nonce=%llu kind=oracle project=%s workflow=%s run=%s stage=align reason=memory_limit final_status=recovered execution_order=%s corpus=%d outcome_hash=%llu compat_hash=%llu native_hash=%llu\n",
	       (uint64)LABDEMO_RUN_NONCE, DEMO_PROJECT, DEMO_WORKFLOW, DEMO_RUN,
	       LABDEMO_NATIVE_FIRST ? "native_then_compat" :
				      "compat_then_native",
	       LABDEMO_CORPUS_SIZE,
	       demo_outcome_hash("align", "recovered"),
	       compat_metrics.outcome_hash, native_metrics.outcome_hash);
	seed_demo_metadata();
	check(sync() == 0, "workflow setup boundary");
	take_performance_snapshot(&workflow_perf_before);
	check(pipe(ready_pipe) == 0, "pipe");
	check(pipe(recovery_start) == 0, "recovery start pipe");
	check(pipe(investigator_start) == 0, "investigator start pipe");
	check(pipe(sentinel_start) == 0, "sentinel start pipe");
	check(pipe(progress_pipe) == 0, "progress pipe");
	ready_fd = ready_pipe[1];
	start_fd = recovery_start[0];
	progress_fd = progress_pipe[1];
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate recovery ready pipe");
	check(agent_scope_delegate_fd(recovery_start[0]) == AGENT_STATUS_OK,
	      "delegate recovery start pipe");
	check(agent_scope_delegate_fd(progress_pipe[1]) == AGENT_STATUS_OK,
	      "delegate recovery progress pipe");
	recovery_pid = agent_create_role(AGENT_ROLE_RECOVERY);
	check(recovery_pid >= 0, "create recovery");
	if (recovery_pid == 0)
		run_recovery();
	start_fd = investigator_start[0];
	progress_fd = progress_pipe[1];
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate investigator ready pipe");
	check(agent_scope_delegate_fd(investigator_start[0]) == AGENT_STATUS_OK,
	      "delegate investigator start pipe");
	check(agent_scope_delegate_fd(progress_pipe[1]) == AGENT_STATUS_OK,
	      "delegate investigator progress pipe");
	investigator_pid = agent_create_role(AGENT_ROLE_INVESTIGATOR);
	check(investigator_pid >= 0, "create investigator");
	if (investigator_pid == 0)
		run_investigator();
	start_fd = sentinel_start[0];
	check(agent_scope_delegate_fd(ready_pipe[1]) == AGENT_STATUS_OK,
	      "delegate sentinel ready pipe");
	check(agent_scope_delegate_fd(sentinel_start[0]) == AGENT_STATUS_OK,
	      "delegate sentinel start pipe");
	check(agent_scope_delegate_fd(progress_pipe[1]) == AGENT_STATUS_OK,
	      "delegate sentinel progress pipe");
	sentinel_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(sentinel_pid >= 0, "create sentinel");
	if (sentinel_pid == 0)
		run_sentinel();
	check(close(ready_pipe[1]) == 0, "close ready send pipe");
	ready_fd = -1;
	check(close(recovery_start[0]) == 0,
	      "close recovery start receive pipe");
	check(close(investigator_start[0]) == 0,
	      "close investigator start receive pipe");
	check(close(sentinel_start[0]) == 0,
	      "close sentinel start receive pipe");
	start_fd = -1;
	check(close(progress_pipe[1]) == 0, "close progress send pipe");
	progress_fd = -1;
	while (ready_count < 3) {
		check(read(ready_pipe[0], &ch, 1) == 1, "ready read");
		if (ch == 'S')
			ready_mask |= 1;
		else if (ch == 'I')
			ready_mask |= 2;
		else if (ch == 'R')
			ready_mask |= 4;
		else
			check(0, "known ready stage");
		ready_count++;
	}
	check(ready_mask == 7, "unique ready stages");
	check(agent_route_config(sentinel_pid, investigator_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant sentinel investigator route");
	check(agent_route_config(investigator_pid, recovery_pid,
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant investigator recovery route");
	check(write(recovery_start[1], "G", 1) == 1,
	      "release recovery start barrier");
	check(close(recovery_start[1]) == 0, "close recovery start pipe");
	check(write(investigator_start[1], "G", 1) == 1,
	      "release investigator start barrier");
	check(close(investigator_start[1]) == 0,
	      "close investigator start pipe");
	check(write(sentinel_start[1], "G", 1) == 1,
	      "release sentinel start barrier");
	check(close(sentinel_start[1]) == 0, "close sentinel start pipe");
	for (int i = 0; i < 3; i++)
		check(sched_yield() == 0, "settle Agent waiters");
	workflow_started_us = demo_now_us();
	inject_failure();
	printf("agentos:demo schema=2 nonce=%llu kind=trace seq=1 tick_us=%llu role=orchestrator event=INCIDENT value0=0 value1=0\n",
	       (uint64)LABDEMO_RUN_NONCE, workflow_started_us);
	for (int received = 0; received < 3; received++) {
		check(read(progress_pipe[0], &incoming_receipt,
			   sizeof(incoming_receipt)) ==
			      (ssize_t)sizeof(incoming_receipt),
		      "progress read");
		check(incoming_receipt.magic == LABDEMO_PROGRESS_MAGIC,
		      "progress identity");
		switch ((char)incoming_receipt.stage) {
		case 'S':
			check(sentinel_receipt.magic == 0,
			      "unique sentinel receipt");
			memcpy(&sentinel_receipt, &incoming_receipt,
			       sizeof(sentinel_receipt));
			break;
		case 'I':
			check(investigator_receipt.magic == 0,
			      "unique investigator receipt");
			memcpy(&investigator_receipt, &incoming_receipt,
			       sizeof(investigator_receipt));
			break;
		case 'R':
			check(recovery_receipt.magic == 0,
			      "unique recovery receipt");
			memcpy(&recovery_receipt, &incoming_receipt,
			       sizeof(recovery_receipt));
			break;
		default:
			check(0, "known progress stage");
		}
	}
	check(sentinel_receipt.magic == LABDEMO_PROGRESS_MAGIC &&
	      investigator_receipt.magic == LABDEMO_PROGRESS_MAGIC &&
	      recovery_receipt.magic == LABDEMO_PROGRESS_MAGIC,
	      "all progress stages");
	workflow_discovered_us = sentinel_receipt.milestone_us;
	printf("agentos:demo schema=2 nonce=%llu kind=trace seq=2 tick_us=%llu role=sentinel event=DISCOVERED value0=%u value1=%llu\n",
	       (uint64)LABDEMO_RUN_NONCE, workflow_discovered_us,
	       sentinel_receipt.records_examined,
	       sentinel_receipt.wait_wakeups);
	workflow_handoff_us = investigator_receipt.milestone_us;
	printf("agentos:demo schema=2 nonce=%llu kind=trace seq=3 tick_us=%llu role=investigator event=HANDOFF value0=%llu value1=%llu\n",
	       (uint64)LABDEMO_RUN_NONCE, workflow_handoff_us,
	       investigator_receipt.tool_calls,
	       investigator_receipt.wait_wakeups);
	workflow_committed_us = recovery_receipt.milestone_us;
	printf("agentos:demo schema=2 nonce=%llu kind=trace seq=4 tick_us=%llu role=recovery event=RECOVERY_COMMITTED value0=%u value1=%u\n",
	       (uint64)LABDEMO_RUN_NONCE, workflow_committed_us,
	       recovery_receipt.duplicate_actions,
	       recovery_receipt.recovery_side_effects);
	check(close(progress_pipe[0]) == 0, "close progress receive pipe");
	check(close(ready_pipe[0]) == 0, "close ready receive pipe");
	printf("labdemo_ucore: startup_barrier ready=3 released=3 chain_receipts=3\n");
	while (wait(&status) > 0) {
		check(status == 0, "child status");
		ok++;
	}
	check(ok == 3, "three agents");
	check_global_audit(sentinel_pid);
	check_unified_timeline(sentinel_pid);
	check_provenance_graph(sentinel_pid);
	check(sync() == 0, "workflow durable boundary");
	workflow_finished_us = demo_now_us();
	take_performance_snapshot(&workflow_perf_after);
	printf("agentos:demo schema=2 nonce=%llu kind=trace seq=5 tick_us=%llu role=orchestrator event=RECOVERED value0=1 value1=0\n",
	       (uint64)LABDEMO_RUN_NONCE, workflow_finished_us);
	tool_calls = sentinel_receipt.tool_calls + investigator_receipt.tool_calls +
		     recovery_receipt.tool_calls;
	dispatches = sentinel_receipt.dispatches + investigator_receipt.dispatches +
		     recovery_receipt.dispatches;
	wait_sleeps = sentinel_receipt.wait_sleeps +
		      investigator_receipt.wait_sleeps + recovery_receipt.wait_sleeps;
	wait_wakeups = sentinel_receipt.wait_wakeups +
			investigator_receipt.wait_wakeups +
			recovery_receipt.wait_wakeups;
	records_examined = sentinel_receipt.records_examined +
			   investigator_receipt.records_examined +
			   recovery_receipt.records_examined;
	printf("agentos:demo schema=2 nonce=%llu kind=runtime mode=native agents=3 duration_us=%llu tool_calls=%llu dispatches=%llu wait_sleeps=%llu wait_wakeups=%llu records_examined=%llu denied_actions=%u duplicate_actions=%u recovery_side_effects=%u\n",
	       (uint64)LABDEMO_RUN_NONCE,
	       workflow_finished_us - workflow_started_us, tool_calls,
	       dispatches, wait_sleeps, wait_wakeups, records_examined,
	       sentinel_receipt.denied_actions +
		       investigator_receipt.denied_actions +
		       recovery_receipt.denied_actions,
	       sentinel_receipt.duplicate_actions +
		       investigator_receipt.duplicate_actions +
		       recovery_receipt.duplicate_actions,
	       sentinel_receipt.recovery_side_effects +
		       investigator_receipt.recovery_side_effects +
		       recovery_receipt.recovery_side_effects);
	print_mechanism_delta("workflow", "end_to_end", &workflow_perf_before,
			      &workflow_perf_after);
	printf("labdemo_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int orchestrator_pid;
	int status = 0;

	printf("labdemo_ucore: Agent-OS laboratory recovery demo\n");
	orchestrator_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(orchestrator_pid >= 0, "create orchestrator");
	if (orchestrator_pid == 0)
		run_orchestrator();
	check(waitpid(orchestrator_pid, &status) == orchestrator_pid,
	      "wait orchestrator");
	check(status == 0, "orchestrator status");
	run_runtime_mechanism_probe();
	printf("labdemo_ucore: parent passed\n");
	return 0;
}
