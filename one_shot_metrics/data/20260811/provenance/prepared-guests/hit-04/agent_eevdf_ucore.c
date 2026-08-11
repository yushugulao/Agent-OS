#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TEST_NAME "agent_eevdf_ucore"
#define MAX_CONCURRENT_WORKFLOWS 4
#define MAX_FRESH_WORKFLOWS (MAX_CONCURRENT_WORKFLOWS - 1)
#define MAX_SCENARIO_WORKFLOWS 16
#define INITIAL_FRESH_ATTEMPTS (MAX_SCENARIO_WORKFLOWS - 1)
#define MAX_BUSY_THREADS 4
#define WAKE_PROBES 4
#define SCHEDULER_TICK_MILLISECONDS 10
#define MEASUREMENT_TICKS 12
#define MEASUREMENT_MILLISECONDS \
	(MEASUREMENT_TICKS * SCHEDULER_TICK_MILLISECONDS)
#define TIMING_CHECK_ITERATIONS 64U
#define RESULT_MAGIC 0x45455644U

struct workflow_result {
	uint magic;
	uint scenario;
	uint index;
	uint busy_threads;
	int wait_status;
	uint scheduler_mode;
	uint scheduler_flags;
	uint latency_class;
	uint weight;
	uint request_ticks;
	uint lifecycle_id;
	uint fresh_agent;
	uint wake_probes;
	uint reserved;
	uint64 lifecycle_generation;
	uint64 work_iterations;
	uint64 service_cycles;
	uint64 dispatches;
	uint64 sleep_decays;
	uint64 eligibility_misses;
	uint64 fallbacks;
	uint64 max_wakeup_ticks;
	uint64 deadline_misses;
	uint64 wakeup_samples;
	uint64 wakeup_buckets[AGENT_WORKFLOW_WAKE_BUCKET_COUNT];
};

struct spawn_config {
	uint scenario;
	uint index;
	uint busy_threads;
	int ready_fd;
	int start_fd;
	int measure_fd;
	int go_fd;
	int stop_fd;
	int result_fd;
};

struct cohort_summary {
	uint scenario;
	uint requested;
	uint admitted;
	uint rejected;
	uint rejected_no_space;
	uint rejected_retry;
	uint rejected_other;
	uint waves;
	uint concurrency_cap;
	uint bootstrap_samples;
	uint fresh_samples;
	uint initial_fresh_attempts;
	uint64 ordinary_progress;
	uint64 jain_sum;
	uint64 jain_sum_sq;
	uint64 buckets[AGENT_WORKFLOW_WAKE_BUCKET_COUNT];
	uint64 deadline_misses;
	uint64 dispatches;
	uint64 fallbacks;
	uint64 amplified_service;
	uint64 peer_service_sum;
	uint peer_service_count;
};

struct bootstrap_run {
	struct agent_workflow_lifecycle_info before;
	uint scenario;
	uint index;
	uint busy_threads;
	int tids[MAX_BUSY_THREADS];
};

static struct spawn_config next_spawn;
static volatile int workload_start;
static volatile int workload_measure_start;
static volatile int workload_stop;
static volatile uint64 worker_counts[MAX_BUSY_THREADS];
static volatile uint64 worker_mix[MAX_BUSY_THREADS];
static volatile int worker_started[MAX_BUSY_THREADS];
static volatile int worker_paused[MAX_BUSY_THREADS];
static volatile int workload_self_timed;
static int workload_stop_fd;
static uint workload_stop_peer_count;
static volatile int ordinary_enabled;
static volatile int ordinary_exit;
static volatile uint64 ordinary_count;

_Static_assert(sizeof(struct agent_workflow_lifecycle_info) == 216,
	       "EEVDF Guest requires frozen lifecycle info v3");
_Static_assert(AGENT_WORKFLOW_WAKE_BUCKET_COUNT == 4,
	       "EEVDF wake approximation uses the frozen four buckets");
_Static_assert(MAX_CONCURRENT_WORKFLOWS == 4,
	       "Guest documents the current production concurrency cap");
_Static_assert(MAX_FRESH_WORKFLOWS == 3,
	       "bootstrap lifecycle leaves exactly three fresh slots");
_Static_assert(INITIAL_FRESH_ATTEMPTS == 15,
	       "sixteen logical samples probe fifteen fresh arrivals");
_Static_assert(MEASUREMENT_MILLISECONDS == 120,
	       "twelve scheduler ticks require a 120ms blocking interval");
_Static_assert((TIMING_CHECK_ITERATIONS & (TIMING_CHECK_ITERATIONS - 1U)) == 0,
	       "timing checks use a power-of-two low-frequency cadence");

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agent_eevdf_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void read_exact(int fd, void *buffer, uint size, const char *message)
{
	char *bytes = buffer;
	uint offset = 0;

	while (offset < size) {
		int count = read(fd, bytes + offset, size - offset);

		check(count > 0, message);
		offset += count;
	}
}

static void write_exact(int fd, const void *buffer, uint size,
			const char *message)
{
	const char *bytes = buffer;
	uint offset = 0;

	while (offset < size) {
		int count = write(fd, bytes + offset, size - offset);

		check(count > 0, message);
		offset += count;
	}
}

static void busy_worker(void *argument)
{
	uint slot = (uint)(uint64)argument;
	uint64 count = 0;
	uint64 mix = 0x9e3779b97f4a7c15ULL ^ slot;
	int64 deadline = 0;

	while (!workload_start)
		sched_yield();
	worker_started[slot] = 1;
	while (!workload_measure_start)
		sched_yield();
	if (workload_self_timed && slot == 0)
		deadline = get_mtime() + MEASUREMENT_MILLISECONDS;
	while (!workload_stop) {
		for (int i = 0; i < 64; i++)
			mix = mix * 6364136223846793005ULL +
			      1442695040888963407ULL;
		count++;
		if (deadline != 0 &&
		    (count & (TIMING_CHECK_ITERATIONS - 1U)) == 0 &&
		    get_mtime() >= deadline) {
			workload_stop = 1;
			for (uint i = 0; i < workload_stop_peer_count; i++)
				write_exact(workload_stop_fd, "X", 1,
					    "release fresh workflow at deadline");
		}
		if ((count & 7) == 0)
			sched_yield();
	}
	worker_paused[slot] = 1;
	worker_counts[slot] = count;
	worker_mix[slot] = mix;
	exit(0);
}

static void ordinary_worker(void *unused)
{
	uint64 local = 0;
	int64 deadline = get_mtime() + MEASUREMENT_MILLISECONDS;
	(void)unused;

	while (!ordinary_exit) {
		if (ordinary_enabled) {
			local++;
			ordinary_count = local;
		}
		if ((local & (TIMING_CHECK_ITERATIONS - 1U)) == 0 &&
		    get_mtime() >= deadline)
			break;
		sched_yield();
	}
	ordinary_exit = 1;
	exit(0);
}

static uint64 delta_u64(uint64 after, uint64 before)
{
	return after >= before ? after - before : 0;
}

static int workers_reached(volatile int states[MAX_BUSY_THREADS], uint count)
{
	for (uint i = 0; i < count; i++)
		if (!states[i])
			return 0;
	return 1;
}

static void reset_workers(void)
{
	memset((void *)worker_counts, 0, sizeof(worker_counts));
	memset((void *)worker_mix, 0, sizeof(worker_mix));
	memset((void *)worker_started, 0, sizeof(worker_started));
	memset((void *)worker_paused, 0, sizeof(worker_paused));
	workload_start = 0;
	workload_measure_start = 0;
	workload_stop = 0;
	workload_self_timed = 0;
	workload_stop_fd = -1;
	workload_stop_peer_count = 0;
}

static void fill_result(
	struct workflow_result *result,
	const struct agent_workflow_lifecycle_info *before,
	const struct agent_workflow_lifecycle_info *workload_after,
	const struct agent_workflow_lifecycle_info *probe_after, uint scenario,
	uint index, uint busy_threads, uint fresh_agent, uint wake_probes,
	int wait_status, uint64 total)
{
	memset(result, 0, sizeof(*result));
	result->magic = RESULT_MAGIC;
	result->scenario = scenario;
	result->index = index;
	result->busy_threads = busy_threads;
	result->wait_status = wait_status;
	result->scheduler_mode = workload_after->scheduler_mode;
	result->scheduler_flags = workload_after->scheduler_flags;
	result->latency_class = workload_after->scheduler_latency_class;
	result->weight = workload_after->scheduler_weight;
	result->request_ticks = workload_after->scheduler_request_ticks;
	result->lifecycle_id = workload_after->key.id;
	result->fresh_agent = fresh_agent;
	result->wake_probes = wake_probes;
	result->lifecycle_generation = workload_after->key.generation;
	result->work_iterations = total;
	result->service_cycles = delta_u64(workload_after->scheduler_service_cycles,
					  before->scheduler_service_cycles);
	result->dispatches = delta_u64(workload_after->scheduler_dispatches,
				      before->scheduler_dispatches);
	result->sleep_decays = delta_u64(workload_after->scheduler_sleep_decays,
				       before->scheduler_sleep_decays);
	result->eligibility_misses = delta_u64(
		workload_after->scheduler_eligibility_misses,
		before->scheduler_eligibility_misses);
	result->fallbacks = delta_u64(workload_after->scheduler_fallbacks,
				    before->scheduler_fallbacks);
	result->max_wakeup_ticks = probe_after->scheduler_max_wakeup_ticks;
	result->deadline_misses = delta_u64(
		probe_after->scheduler_deadline_misses,
		workload_after->scheduler_deadline_misses);
	result->wakeup_samples = delta_u64(
		probe_after->scheduler_wakeup_samples,
		workload_after->scheduler_wakeup_samples);
	for (uint i = 0; i < AGENT_WORKFLOW_WAKE_BUCKET_COUNT; i++)
		result->wakeup_buckets[i] = delta_u64(
			probe_after->scheduler_wakeup_latency_buckets[i],
			workload_after->scheduler_wakeup_latency_buckets[i]);
}

static void workflow_child(void)
{
	struct agent_workflow_lifecycle_info before;
	struct agent_workflow_lifecycle_info workload_after;
	struct agent_workflow_lifecycle_info probe_after;
	struct agent_workflow_lifecycle_key lifecycle;
	struct workflow_result result;
	int tids[MAX_BUSY_THREADS];
	char token = 0;
	uint64 total = 0;
	uint64 last_wakeup_dispatch = 0;
	int wait_status = AGENT_STATUS_OK;

	check(next_spawn.busy_threads > 0 &&
	      next_spawn.busy_threads <= MAX_BUSY_THREADS,
	      "workflow thread count");
	reset_workers();
	memset(&before, 0, sizeof(before));
	check(agent_workflow_lifecycle_info(&before, 0) == AGENT_STATUS_OK &&
	      before.version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      before.struct_size == sizeof(before) && before.charged,
	      "lifecycle v3 before workload");
	lifecycle = before.key;
	write_exact(next_spawn.ready_fd, "R", 1, "publish workflow ready");
	read_exact(next_spawn.start_fd, &token, 1, "receive workflow start");
	check(token == 'S', "workflow start token");
	for (uint i = 0; i < next_spawn.busy_threads; i++) {
		tids[i] = thread_create(busy_worker, (void *)(uint64)i);
		check(tids[i] > 0, "create workflow busy thread");
	}
	workload_start = 1;
	while (!workers_reached(worker_started, next_spawn.busy_threads))
		sched_yield();
	write_exact(next_spawn.ready_fd, "B", 1,
		    "publish workflow started");
	read_exact(next_spawn.measure_fd, &token, 1,
		   "receive measurement prepare");
	check(token == 'M', "workflow measurement prepare token");
	memset(&before, 0, sizeof(before));
	check(agent_workflow_lifecycle_info(&before, &lifecycle) ==
		      AGENT_STATUS_OK &&
	      before.version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      before.struct_size == sizeof(before) && before.charged,
	      "lifecycle v3 at workload start boundary");
	write_exact(next_spawn.ready_fd, "A", 1,
		    "publish workflow measurement armed");
	read_exact(next_spawn.go_fd, &token, 1,
		   "receive measurement go");
	check(token == 'G', "workflow measurement go token");
	workload_measure_start = 1;
	read_exact(next_spawn.stop_fd, &token, 1, "receive workflow stop");
	check(token == 'X', "workflow stop token");
	workload_stop = 1;
	while (!workers_reached(worker_paused, next_spawn.busy_threads))
		sched_yield();
	for (uint i = 0; i < next_spawn.busy_threads; i++) {
		check(waittid(tids[i]) == 0, "join workflow busy thread");
		check(worker_started[i] && worker_paused[i],
		      "busy thread completed started/paused handshake");
		check(worker_counts[i] != 0, "busy thread made progress");
		total += worker_counts[i];
	}
	memset(&workload_after, 0, sizeof(workload_after));
	check(agent_workflow_lifecycle_info(&workload_after, &before.key) ==
		      AGENT_STATUS_OK &&
	      workload_after.version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      workload_after.struct_size == sizeof(workload_after),
	      "lifecycle v3 at workload boundary");
	write_exact(next_spawn.ready_fd, "P", 1,
		    "publish workflow paused");
	for (int i = 0; i < WAKE_PROBES; i++) {
		struct agent_sched_record records[8];
		struct agent_event event;
		uint histogram_bucket;
		int record_count;
		int selected = -1;

		memset(&event, 0, sizeof(event));
		wait_status = agent_wait(&event, 1);
		check(wait_status == AGENT_STATUS_TIMEOUT,
		      "deadline wake probe times out");
		memset(records, 0, sizeof(records));
		record_count = agent_sched_snapshot(records, 8);
		check(record_count > 0 && record_count <= 8,
		      "deadline wake probe scheduler snapshot");
		for (int record = record_count - 1; record >= 0; record--) {
			if (records[record].dispatch_count > last_wakeup_dispatch &&
			    (records[record].reason_flags &
			     AGENT_SCHED_REASON_DEADLINE_NOW) != 0) {
				selected = record;
				break;
			}
		}
		check(selected >= 0,
		      "deadline wake probe has a new deadline-now dispatch");
		last_wakeup_dispatch = records[selected].dispatch_count;
		if (records[selected].ready_age <= 1)
			histogram_bucket = 0;
		else if (records[selected].ready_age <= 2)
			histogram_bucket = 1;
		else if (records[selected].ready_age <= 8)
			histogram_bucket = 2;
		else
			histogram_bucket = 3;
		printf("agent_eevdf_ucore: one_shot_wakeup schema=1 "
		       "scenario=%u index=%u probe=%u wakeup_latency_ticks=%llu "
		       "dispatch_tick=%llu reason_flags=%llu histogram_bucket=%u "
		       "status=measured\n",
		       next_spawn.scenario, next_spawn.index, i,
		       records[selected].ready_age, records[selected].tick,
		       records[selected].reason_flags, histogram_bucket);
	}
	memset(&probe_after, 0, sizeof(probe_after));
	check(agent_workflow_lifecycle_info(&probe_after, &before.key) ==
		      AGENT_STATUS_OK &&
	      probe_after.version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      probe_after.struct_size == sizeof(probe_after),
	      "lifecycle v3 after wake probes");

	fill_result(&result, &before, &workload_after, &probe_after,
		    next_spawn.scenario,
		    next_spawn.index, next_spawn.busy_threads, 1, WAKE_PROBES,
		    wait_status, total);
	write_exact(next_spawn.result_fd, &result, sizeof(result),
		    "publish workflow result");
	exit(0);
}

static void bootstrap_prepare(struct bootstrap_run *run, uint scenario,
			      uint index, int stop_fd, uint fresh_count)
{
	memset(run, 0, sizeof(*run));
	run->scenario = scenario;
	run->index = index;
	run->busy_threads = 1;
	reset_workers();
	workload_self_timed = 1;
	workload_stop_fd = stop_fd;
	workload_stop_peer_count = fresh_count;
	memset(&run->before, 0, sizeof(run->before));
	check(agent_workflow_lifecycle_info(&run->before, 0) ==
		      AGENT_STATUS_OK &&
	      run->before.version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      run->before.struct_size == sizeof(run->before) &&
	      run->before.charged,
	      "bootstrap lifecycle v3 before workload");
	run->tids[0] = thread_create(busy_worker, 0);
	check(run->tids[0] > 0, "create bootstrap local worker");
}

static void bootstrap_start(struct bootstrap_run *run)
{
	workload_start = 1;
	while (!workers_reached(worker_started, run->busy_threads))
		sched_yield();
}

static void bootstrap_measure_prepare(struct bootstrap_run *run)
{
	struct agent_workflow_lifecycle_key lifecycle = run->before.key;

	memset(&run->before, 0, sizeof(run->before));
	check(agent_workflow_lifecycle_info(&run->before, &lifecycle) ==
		      AGENT_STATUS_OK &&
	      run->before.version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      run->before.struct_size == sizeof(run->before) &&
	      run->before.charged,
	      "bootstrap lifecycle v3 at workload start boundary");
}

static void bootstrap_pause(struct bootstrap_run *run,
			    struct workflow_result *result)
{
	struct agent_workflow_lifecycle_info after;
	uint64 total = 0;

	for (uint i = 0; i < run->busy_threads; i++) {
		check(waittid(run->tids[i]) == 0,
		      "join bootstrap local worker");
		check(worker_started[i] && worker_paused[i] &&
		      worker_counts[i] != 0,
		      "bootstrap worker completed started/paused handshake");
		total += worker_counts[i];
	}
	check(workload_stop, "bootstrap worker reached its measurement deadline");
	memset(&after, 0, sizeof(after));
	check(agent_workflow_lifecycle_info(&after, &run->before.key) ==
		      AGENT_STATUS_OK &&
	      after.version == AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
	      after.struct_size == sizeof(after),
	      "bootstrap lifecycle v3 after workload");
	fill_result(result, &run->before, &after, &after, run->scenario,
		    run->index,
		    run->busy_threads, 0, 0, AGENT_STATUS_OK, total);
}

static void delegate_workflow_fds(int ready_write, int start_read,
				  int measure_read, int go_read, int stop_read,
				  int result_write)
{
	check(agent_scope_delegate_fd(ready_write) == AGENT_STATUS_OK,
	      "delegate ready fd");
	check(agent_scope_delegate_fd(start_read) == AGENT_STATUS_OK,
	      "delegate start fd");
	check(agent_scope_delegate_fd(measure_read) == AGENT_STATUS_OK,
	      "delegate measurement-prepare fd");
	check(agent_scope_delegate_fd(go_read) == AGENT_STATUS_OK,
	      "delegate measurement-go fd");
	check(agent_scope_delegate_fd(stop_read) == AGENT_STATUS_OK,
	      "delegate stop fd");
	check(agent_scope_delegate_fd(result_write) == AGENT_STATUS_OK,
	      "delegate result fd");
}

static int spawn_workflow(uint scenario, uint index, uint threads,
			  int ready_write, int start_read, int measure_read,
			  int go_read, int stop_read, int result_write)
{
	int pid;

	next_spawn.scenario = scenario;
	next_spawn.index = index;
	next_spawn.busy_threads = threads;
	next_spawn.ready_fd = ready_write;
	next_spawn.start_fd = start_read;
	next_spawn.measure_fd = measure_read;
	next_spawn.go_fd = go_read;
	next_spawn.stop_fd = stop_read;
	next_spawn.result_fd = result_write;
	delegate_workflow_fds(ready_write, start_read, measure_read, go_read,
			      stop_read, result_write);
	pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	if (pid == 0)
		workflow_child();
	return pid;
}

static int spawn_workflow_retry(uint scenario, uint index, uint threads,
				int ready_write, int start_read, int measure_read,
				int go_read, int stop_read, int result_write)
{
	for (int attempt = 0; attempt < 2000; attempt++) {
		int pid = spawn_workflow(scenario, index, threads, ready_write,
					 start_read, measure_read, go_read, stop_read,
					 result_write);

		if (pid != AGENT_STATUS_RETRY)
			return pid;
		sleep(1);
	}
	return AGENT_STATUS_RETRY;
}

static uint latency_percentile_bucket(const uint64 buckets[4], uint percent)
{
	uint64 total = 0;
	uint64 cumulative = 0;
	uint64 threshold;

	for (uint i = 0; i < 4; i++)
		total += buckets[i];
	if (total == 0)
		return 4;
	threshold = (total * percent + 99) / 100;
	for (uint i = 0; i < 4; i++) {
		cumulative += buckets[i];
		if (cumulative >= threshold)
			return i;
	}
	return 3;
}

static void summary_add(struct cohort_summary *summary,
			const struct workflow_result *result)
{
	uint64 scaled = result->service_cycles >> 10;

	if (scaled == 0)
		scaled = 1;
	check(scaled <= 0xffffffffULL, "Jain input remains square-safe");
	summary->jain_sum += scaled;
	summary->jain_sum_sq += scaled * scaled;
	if (result->fresh_agent) {
		summary->fresh_samples++;
		summary->deadline_misses += result->deadline_misses;
		for (uint i = 0; i < AGENT_WORKFLOW_WAKE_BUCKET_COUNT; i++)
			summary->buckets[i] += result->wakeup_buckets[i];
	} else {
		summary->bootstrap_samples++;
	}
	summary->dispatches += result->dispatches;
	summary->fallbacks += result->fallbacks;
	if (summary->scenario == 44) {
		if (result->busy_threads == MAX_BUSY_THREADS)
			summary->amplified_service = result->service_cycles;
		else {
			summary->peer_service_sum += result->service_cycles;
			summary->peer_service_count++;
		}
	}
	printf("agent_eevdf_ucore: sample scenario=%u index=%u source=%s threads=%u wake_probes=%u mode=%u flags=%u "
	       "latency_class=%u weight=%u request_ticks=%u lifecycle=%u:%llu "
	       "work=%llu service=%llu dispatch=%llu fallback=%llu deadline_miss=%llu "
	       "wake_samples=%llu wake_max=%llu "
	       "wake_bucket_0=%llu wake_bucket_1=%llu "
	       "wake_bucket_2=%llu wake_bucket_3=%llu\n",
	       result->scenario, result->index,
	       result->fresh_agent ? "fresh" : "bootstrap",
	       result->busy_threads, result->wake_probes,
	       result->scheduler_mode, result->scheduler_flags,
	       result->latency_class, result->weight, result->request_ticks,
	       result->lifecycle_id, result->lifecycle_generation,
	       result->work_iterations, result->service_cycles,
	       result->dispatches, result->fallbacks,
	       result->deadline_misses, result->wakeup_samples,
	       result->max_wakeup_ticks, result->wakeup_buckets[0],
	       result->wakeup_buckets[1], result->wakeup_buckets[2],
	       result->wakeup_buckets[3]);
}

static void print_summary(const struct cohort_summary *summary)
{
	uint p50 = latency_percentile_bucket(summary->buckets, 50);
	uint p99 = latency_percentile_bucket(summary->buckets, 99);

	printf("agent_eevdf_ucore: cohort scenario=%u requested=%u admitted=%u rejected=%u "
	       "no_space=%u retry=%u other=%u waves=%u concurrency_cap=%u bootstrap_samples=%u fresh_samples=%u initial_fresh_attempts=%u ordinary_progress=%llu\n",
	       summary->scenario, summary->requested, summary->admitted,
	       summary->rejected, summary->rejected_no_space,
	       summary->rejected_retry, summary->rejected_other,
	       summary->waves, summary->concurrency_cap,
	       summary->bootstrap_samples, summary->fresh_samples,
	       summary->initial_fresh_attempts,
	       summary->ordinary_progress);
	printf("agent_eevdf_ucore: jain_inputs scenario=%u n=%u sum=%llu sum_sq=%llu "
	       "basis=service_cycles_div_1024\n",
	       summary->scenario, summary->admitted, summary->jain_sum,
	       summary->jain_sum_sq);
	printf("agent_eevdf_ucore: wake scenario=%u scope=fresh_agents_only fresh_samples=%u buckets=%llu,%llu,%llu,%llu "
	       "p50_bucket=%u p99_bucket=%u deadline_miss=%llu dispatch=%llu fallback=%llu\n",
	       summary->scenario, summary->fresh_samples,
	       summary->buckets[0], summary->buckets[1],
	       summary->buckets[2], summary->buckets[3], p50, p99,
	       summary->deadline_misses, summary->dispatches,
	       summary->fallbacks);
	if (summary->scenario == 44)
		printf("agent_eevdf_ucore: amplification_inputs amplified_threads=4 amplified_service=%llu "
		       "peer_threads=1 fresh_peer_count=2 bootstrap_peer_count=1 peer_count=%u peer_service_sum=%llu accounting=workflow\n",
		       summary->amplified_service, summary->peer_service_count,
		       summary->peer_service_sum);
}

static void execute_wave(uint scenario, uint bootstrap_index, uint fresh_count,
			 int pids[MAX_FRESH_WORKFLOWS], int ready[2],
			 int start[2], int measure[2], int go[2],
			 int stop[2], int result_pipe[2],
			 struct cohort_summary *summary)
{
	struct bootstrap_run bootstrap;
	struct workflow_result result;
	char token;

	check(fresh_count <= MAX_FRESH_WORKFLOWS, "fresh wave capacity");
	for (uint i = 0; i < fresh_count; i++) {
		read_exact(ready[0], &token, 1, "wait fresh workflow ready");
		check(token == 'R', "fresh workflow ready token");
	}
	bootstrap_prepare(&bootstrap, scenario, bootstrap_index, stop[1],
			  fresh_count);
	for (uint i = 0; i < fresh_count; i++)
		write_exact(start[1], "S", 1, "start fresh workflow");
	bootstrap_start(&bootstrap);
	for (uint i = 0; i < fresh_count; i++) {
		read_exact(ready[0], &token, 1, "wait fresh workflow started");
		check(token == 'B', "fresh workflow started token");
	}
	for (uint i = 0; i < fresh_count; i++)
		write_exact(measure[1], "M", 1,
			    "prepare fresh workflow measurement");
	for (uint i = 0; i < fresh_count; i++) {
		read_exact(ready[0], &token, 1,
			   "wait fresh workflow measurement armed");
		check(token == 'A', "fresh workflow measurement armed token");
	}
	bootstrap_measure_prepare(&bootstrap);
	for (uint i = 0; i < fresh_count; i++)
		write_exact(go[1], "G", 1,
			    "start fresh workflow measurement");
	workload_measure_start = 1;
	bootstrap_pause(&bootstrap, &result);
	for (uint i = 0; i < fresh_count; i++) {
		read_exact(ready[0], &token, 1, "wait fresh workflow paused");
		check(token == 'P', "fresh workflow paused token");
	}
	check(result.scheduler_mode == AGENT_WORKFLOW_SCHED_MODE_EEVDF ||
	      result.scheduler_mode == AGENT_WORKFLOW_SCHED_MODE_FALLBACK,
	      "bootstrap reports frozen scheduler mode");
	summary_add(summary, &result);
	for (uint i = 0; i < fresh_count; i++) {
		int status = -1;

		read_exact(result_pipe[0], &result, sizeof(result),
			   "read fresh workflow result");
		check(result.magic == RESULT_MAGIC && result.scenario == scenario &&
		      result.fresh_agent && result.wake_probes == WAKE_PROBES,
		      "fresh workflow result identity");
		check(result.scheduler_mode == AGENT_WORKFLOW_SCHED_MODE_EEVDF ||
		      result.scheduler_mode == AGENT_WORKFLOW_SCHED_MODE_FALLBACK,
		      "fresh workflow reports frozen scheduler mode");
		summary_add(summary, &result);
		check(waitpid(pids[i], &status) == pids[i] && status == 0,
		      "reap fresh workflow result");
	}
	summary->admitted += fresh_count + 1U;
	summary->waves++;
}

static void close_wave_pipes(int ready[2], int start[2], int measure[2],
			     int go[2], int stop[2], int result_pipe[2])
{
	close(ready[0]); close(ready[1]);
	close(start[0]); close(start[1]);
	close(measure[0]); close(measure[1]);
	close(go[0]); close(go[1]);
	close(stop[0]); close(stop[1]);
	close(result_pipe[0]); close(result_pipe[1]);
}

static void run_wave(uint scenario, uint bootstrap_index,
		     uint first_fresh_index, uint fresh_count, int amplified,
		     struct cohort_summary *summary)
{
	int ready[2], start[2], measure[2], go[2], stop[2], result_pipe[2];
	int pids[MAX_FRESH_WORKFLOWS];

	check(fresh_count <= MAX_FRESH_WORKFLOWS, "wave fresh concurrency");
	check(pipe(ready) == 0 && pipe(start) == 0 && pipe(measure) == 0 &&
	      pipe(go) == 0 && pipe(stop) == 0 &&
	      pipe(result_pipe) == 0, "create cohort pipes");
	for (uint i = 0; i < fresh_count; i++) {
		uint threads = amplified && i == 0 ? MAX_BUSY_THREADS : 1;

		pids[i] = spawn_workflow_retry(
			scenario, first_fresh_index + i, threads, ready[1],
			start[0], measure[0], go[0], stop[0], result_pipe[1]);
		check(pids[i] > 0, "spawn fresh workflow wave");
	}
	execute_wave(scenario, bootstrap_index, fresh_count, pids, ready,
		     start, measure, go, stop, result_pipe, summary);
	close_wave_pipes(ready, start, measure, go, stop, result_pipe);
}

static void run_simple_scenario(uint scenario, uint fresh_count, int amplified)
{
	struct cohort_summary summary;

	memset(&summary, 0, sizeof(summary));
	summary.scenario = scenario;
	summary.requested = fresh_count + 1U;
	summary.concurrency_cap = MAX_CONCURRENT_WORKFLOWS;
	run_wave(scenario, 0, 1, fresh_count, amplified, &summary);
	check(summary.bootstrap_samples == 1 &&
	      summary.fresh_samples == fresh_count,
	      "simple scenario topology");
	print_summary(&summary);
}

static void run_sixteen_arrivals(void)
{
	struct cohort_summary summary;
	int ready[2], start[2], measure[2], go[2], stop[2], result_pipe[2];
	int pids[MAX_FRESH_WORKFLOWS];
	uint admitted = 0;

	memset(&summary, 0, sizeof(summary));
	summary.scenario = 16;
	summary.requested = MAX_SCENARIO_WORKFLOWS;
	summary.concurrency_cap = MAX_CONCURRENT_WORKFLOWS;
	summary.initial_fresh_attempts = INITIAL_FRESH_ATTEMPTS;
	check(pipe(ready) == 0 && pipe(start) == 0 && pipe(measure) == 0 &&
	      pipe(go) == 0 && pipe(stop) == 0 &&
	      pipe(result_pipe) == 0, "create sixteen-arrival pipes");
	for (uint i = 0; i < INITIAL_FRESH_ATTEMPTS; i++) {
		uint index = i < MAX_FRESH_WORKFLOWS ? i + 1U : 100U + i;
		int pid = spawn_workflow_retry(16, index, 1, ready[1], start[0],
					       measure[0], go[0], stop[0],
					       result_pipe[1]);

		if (pid > 0) {
			check(i < MAX_FRESH_WORKFLOWS &&
			      admitted < MAX_FRESH_WORKFLOWS,
			      "only first three fresh attempts are admitted");
			pids[admitted++] = pid;
		} else {
			summary.rejected++;
			if (pid == AGENT_STATUS_NO_SPACE)
				summary.rejected_no_space++;
			else if (pid == AGENT_STATUS_RETRY)
				summary.rejected_retry++;
			else
				summary.rejected_other++;
		}
	}
	check(admitted == MAX_FRESH_WORKFLOWS && summary.rejected == 12,
	      "bootstrap plus three fresh saturates the frozen cap");
	check(summary.rejected_no_space == 12 &&
	      summary.rejected_retry == 0 && summary.rejected_other == 0,
	      "twelve excess fresh attempts are stable no-space");
	execute_wave(16, 0, admitted, pids, ready, start, measure, go, stop,
		     result_pipe, &summary);
	close_wave_pipes(ready, start, measure, go, stop, result_pipe);
	for (uint wave = 1; wave < 4; wave++)
		run_wave(16, wave * 4U, wave * 4U + 1U,
			 MAX_FRESH_WORKFLOWS, 0, &summary);
	check(summary.admitted == MAX_SCENARIO_WORKFLOWS &&
	      summary.waves == 4 && summary.bootstrap_samples == 4 &&
	      summary.fresh_samples == 12,
	      "sixteen logical samples use bootstrap plus twelve fresh");
	print_summary(&summary);
}

int main(void)
{
	int ordinary_tid;
	uint64 ordinary_before;
	uint64 ordinary_baseline;

	printf("agent_eevdf_ucore: workflow EEVDF measurement Guest\n");
	printf("agent_eevdf_ucore: topology one_way=bootstrap four_way=bootstrap+3fresh amplification=bootstrap_peer+fresh4thread+2fresh_peers\n");
	printf("agent_eevdf_ucore: wake_bucket_map=0:le1,1:le2,2:le8,3:gt8 p50_p99=histogram_approx probes=fresh_agents_only\n");
	ordinary_enabled = 0;
	ordinary_exit = 0;
	ordinary_count = 0;
	ordinary_tid = thread_create(ordinary_worker, 0);
	check(ordinary_tid > 0, "create ordinary-process control thread");
	ordinary_before = ordinary_count;
	ordinary_enabled = 1;
	check(waittid(ordinary_tid) == 0,
	      "ordinary worker reached its measurement deadline");
	ordinary_enabled = 0;
	ordinary_baseline = delta_u64(ordinary_count, ordinary_before);
	check(ordinary_baseline != 0, "ordinary baseline makes progress");
	printf("agent_eevdf_ucore: ordinary_baseline_ticks=12 progress=%llu\n",
	       ordinary_baseline);
	run_simple_scenario(1, 0, 0);
	run_simple_scenario(2, 1, 0);
	run_simple_scenario(3, 2, 0);
	run_sixteen_arrivals();
	run_simple_scenario(4, MAX_FRESH_WORKFLOWS, 0);
	run_simple_scenario(44, MAX_FRESH_WORKFLOWS, 1);
	printf("agent_eevdf_ucore: thread_amplification scenario=44 amplified_threads=4 fresh_peers=2 bootstrap_peers=1 accounting=workflow\n");
	printf("agent_eevdf_ucore: sixteen_arrivals=1 logical_samples=16 concurrency_cap=4 bootstrap_samples=4 fresh_samples=12 initial_fresh_attempts=15 initial_admitted=3 stable_no_space=12 waves=4 retry_policy=retry_only\n");
	printf("agent_eevdf_ucore: parent passed\n");
	return 0;
}
