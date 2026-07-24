#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define TEST_DOMAIN_LIMIT 6
#define TEST_ORDINARY_LIMIT 12
#define TEST_RESERVED_DOMAIN_LIMIT 4
#define TEST_RESERVED_LIMIT 6
#define FAIR_WORKERS (TEST_DOMAIN_LIMIT - 1)
#define FAIR_VICTIM_ROUNDS 512
#define FAIR_SCHED_SLACK 64
#define RESERVED_DOMAIN_WORKERS (TEST_RESERVED_DOMAIN_LIMIT - 1)
#define RESERVED_GLOBAL_ROOT_WORKERS 2
#define RESERVED_GLOBAL_CHILD_WORKERS 2

static volatile int quota_stop;
static volatile int reserved_probe_ran;
static volatile int reserved_stop;
static volatile uint64 reserved_counts[RESERVED_DOMAIN_WORKERS];
static volatile int fair_start;
static volatile int fair_stop;
static volatile uint64 fair_counts[FAIR_WORKERS];

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("threadresource_ucore: check failed: %s\n", message);
	exit(1);
}

static void wait_success(int pid, const char *message)
{
	int status = -1;

	check(pid > 0, message);
	check(waitpid(pid, &status) == pid, "wait direct child");
	check(status == 0, "direct child exit status");
}

static void read_exact(int fd, void *buffer, int size)
{
	char *bytes = buffer;
	int offset = 0;

	while (offset < size) {
		int n = read(fd, bytes + offset, size - offset);

		check(n > 0, "read exact");
		offset += n;
	}
}

static void delegate_fd(int fd, const char *message)
{
	check(agent_scope_delegate_fd(fd) == AGENT_STATUS_OK, message);
}

static void quota_worker(void *arg)
{
	int code = (int)(uint64)arg;

	while (!quota_stop)
		sched_yield();
	exit(code);
}

static void exit_worker(void *arg)
{
	(void)arg;
	for (;;)
		sched_yield();
}

static void domain_limit_child(void)
{
	int tids[TEST_DOMAIN_LIMIT - 1];
	int replacement;

	quota_stop = 0;
	for (int i = 0; i < TEST_DOMAIN_LIMIT - 1; i++) {
		tids[i] = thread_create(quota_worker, (void *)(uint64)(20 + i));
		if (tids[i] < 0)
			exit(10);
	}
	for (int i = 0; i < 8; i++)
		if (thread_create(quota_worker, (void *)(uint64)40) != -1)
			exit(11);
	quota_stop = 1;
	if (waittid(tids[0]) != 20)
		exit(12);
	replacement = thread_create(quota_worker, (void *)(uint64)30);
	if (replacement < 0 || waittid(replacement) != 30)
		exit(13);
	for (int i = 1; i < TEST_DOMAIN_LIMIT - 1; i++)
		if (waittid(tids[i]) != 20 + i)
			exit(14);
	exit(0);
}

static void domain_limit_phase(void)
{
	int child = fork();

	check(child >= 0, "fork domain-limit child");
	if (child == 0)
		domain_limit_child();
	wait_success(child, "domain-limit child created");
	printf("threadresource_ucore: domain_limit=1\n");
	printf("threadresource_ucore: capacity_reject_stable=1\n");
}

static void exit_reuse_holder(void)
{
	int worker = fork();
	int tids[TEST_DOMAIN_LIMIT - 1];

	if (worker < 0)
		exit(30);
	if (worker == 0) {
		for (int i = 0; i < TEST_DOMAIN_LIMIT - 2; i++)
			if (thread_create(exit_worker, 0) < 0)
				exit(31);
		/*
		 * Process teardown must interrupt and refund all sibling slots; the
		 * holder deliberately never joins these threads.
		 */
		exit(0);
	}
	wait_success(worker, "wait exit-reuse worker");
	quota_stop = 0;
	for (int i = 0; i < TEST_DOMAIN_LIMIT - 1; i++) {
		tids[i] = thread_create(quota_worker, (void *)(uint64)(40 + i));
		if (tids[i] < 0)
			exit(32);
	}
	quota_stop = 1;
	for (int i = 0; i < TEST_DOMAIN_LIMIT - 1; i++)
		if (waittid(tids[i]) != 40 + i)
			exit(33);
	exit(0);
}

static void exit_reuse_phase(void)
{
	int holder = fork();

	check(holder >= 0, "fork exit-reuse holder");
	if (holder == 0)
		exit_reuse_holder();
	wait_success(holder, "exit-reuse holder created");
	printf("threadresource_ucore: exit_reuse=1\n");
}

static void global_holder(int ready_fd, int release_fd, char marker,
			  int worker_count)
{
	int tids[TEST_DOMAIN_LIMIT - 1];
	char token = 0;

	quota_stop = 0;
	for (int i = 0; i < worker_count; i++) {
		tids[i] = thread_create(quota_worker, (void *)(uint64)(60 + i));
		if (tids[i] < 0)
			exit(50);
	}
	if (write(ready_fd, &marker, 1) != 1)
		exit(51);
	if (read(release_fd, &token, 1) != 1 || token != 'X')
		exit(52);
	quota_stop = 1;
	for (int i = 0; i < worker_count; i++)
		if (waittid(tids[i]) != 60 + i)
			exit(53);
	exit(0);
}

static void reserved_probe(void *arg)
{
	(void)arg;
	reserved_probe_ran = 1;
	exit(70);
}

static void reserved_worker(void *arg)
{
	int index = (int)(uint64)arg;

	reserved_counts[index]++;
	while (!reserved_stop)
		sched_yield();
	exit(0);
}

static void reset_reserved_workers(int count)
{
	reserved_stop = 0;
	for (int i = 0; i < count; i++)
		reserved_counts[i] = 0;
}

static void wait_reserved_workers(int count)
{
	for (int round = 0; round < 4096; round++) {
		int ready = 1;

		for (int i = 0; i < count; i++)
			if (reserved_counts[i] == 0)
				ready = 0;
		if (ready)
			return;
		sched_yield();
	}
	check(0, "reserved workers made progress");
}

static void reserved_domain_limit_phase(void)
{
	int tids[RESERVED_DOMAIN_WORKERS];
	int replacement;

	reset_reserved_workers(RESERVED_DOMAIN_WORKERS);
	for (int i = 0; i < RESERVED_DOMAIN_WORKERS; i++) {
		tids[i] = thread_create(reserved_worker, (void *)(uint64)i);
		check(tids[i] >= 0, "fill reserved domain");
	}
	wait_reserved_workers(RESERVED_DOMAIN_WORKERS);
	check(thread_create(reserved_probe, 0) == -1,
	      "reserved domain limit enforced");
	reserved_stop = 1;
	for (int i = 0; i < RESERVED_DOMAIN_WORKERS; i++)
		check(waittid(tids[i]) == 0, "join reserved domain worker");
	reserved_probe_ran = 0;
	replacement = thread_create(reserved_probe, 0);
	check(replacement >= 0 && waittid(replacement) == 70 &&
		      reserved_probe_ran,
	      "reserved domain slot refunded");
	printf("threadresource_ucore: reserved_domain_limit=1\n");
	printf("threadresource_ucore: reserved_domain_reuse=1\n");
}

static void reserved_global_child(void)
{
	int tids[RESERVED_GLOBAL_CHILD_WORKERS];
	int replacement;

	reset_reserved_workers(RESERVED_GLOBAL_CHILD_WORKERS);
	for (int i = 0; i < RESERVED_GLOBAL_CHILD_WORKERS; i++) {
		tids[i] = thread_create(reserved_worker, (void *)(uint64)i);
		if (tids[i] < 0)
			exit(71);
	}
	wait_reserved_workers(RESERVED_GLOBAL_CHILD_WORKERS);
	/*
	 * The child domain owns only three of its four allowed slots here.
	 * Ordinary pressure consumes 12 slots and the root reserved domain owns
	 * three more, so this denial can only come from the reserved global
	 * waterline; the physical pool deliberately retains one spare slot.
	 */
	if (thread_create(reserved_probe, 0) != -1)
		exit(72);
	reserved_stop = 1;
	for (int i = 0; i < RESERVED_GLOBAL_CHILD_WORKERS; i++)
		if (waittid(tids[i]) != 0)
			exit(73);
	reserved_probe_ran = 0;
	replacement = thread_create(reserved_probe, 0);
	if (replacement < 0 || waittid(replacement) != 70 ||
	    !reserved_probe_ran)
		exit(74);
	exit(0);
}

static void global_limit_probe(int ready_fd, int release_fd)
{
	char token = 0;

	/*
	 * This third ordinary domain owns only its precharged main thread. The
	 * other holders consume 11 slots, so thread_create() reaches the global
	 * ordinary waterline while remaining well below this domain's ceiling.
	 */
	if (thread_create(quota_worker, 0) != -1)
		exit(75);
	if (write(ready_fd, "G", 1) != 1)
		exit(76);
	if (read(release_fd, &token, 1) != 1 || token != 'X')
		exit(77);
	exit(0);
}

static void global_waterline_phase(void)
{
	int ready[2];
	int release[2];
	int reserved_start[2];
	int holders[2];
	int root_reserved[RESERVED_GLOBAL_ROOT_WORKERS];
	int global_probe;
	int workflow;
	int reserved_tid;
	int probe;
	char markers[2];
	char marker;

	check(pipe(ready) == 0, "create global ready pipe");
	check(pipe(release) == 0, "create global release pipe");
	for (int i = 0; i < 2; i++) {
		delegate_fd(ready[1], "delegate holder ready pipe");
		delegate_fd(release[0], "delegate holder release pipe");
		holders[i] = fork();
		check(holders[i] >= 0, "fork global holder");
		if (holders[i] == 0)
			global_holder(ready[1], release[0], 'A' + i,
				      TEST_DOMAIN_LIMIT - 1 - i);
	}
	/*
	 * Establish the ordinary waterline before admitting the probe domain.
	 * Domain-fair scheduling may otherwise run the probe between the two
	 * holders: its speculative worker can steal the last slot from a holder,
	 * or be admitted before the waterline exists.
	 */
	read_exact(ready[0], markers, sizeof(markers));
	check((markers[0] == 'A' || markers[0] == 'B') &&
		      (markers[1] == 'A' || markers[1] == 'B') &&
		      markers[0] != markers[1],
	      "ordinary holders reached pre-probe waterline");
	delegate_fd(ready[1], "delegate global probe ready pipe");
	delegate_fd(release[0], "delegate global probe release pipe");
	global_probe = fork();
	check(global_probe >= 0, "fork global limit probe");
	if (global_probe == 0)
		global_limit_probe(ready[1], release[0]);
	close(ready[1]);
	close(release[0]);
	read_exact(ready[0], &marker, 1);
	check(marker == 'G', "ordinary probe reached waterline");
	check(fork() == -1, "ordinary global waterline enforced");
	printf("threadresource_ucore: ordinary_waterline=1\n");
	printf("threadresource_ucore: global_thread_limit=1\n");

	check(pipe(reserved_start) == 0, "create reserved start pipe");
	delegate_fd(reserved_start[0], "delegate reserved start pipe");
	workflow = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(workflow >= 0, "reserved workflow admitted at waterline");
	if (workflow == 0) {
		char token = 0;

		read_exact(reserved_start[0], &token, 1);
		if (token != 'S')
			exit(78);
		reserved_global_child();
	}
	close(reserved_start[0]);
	reset_reserved_workers(RESERVED_GLOBAL_ROOT_WORKERS);
	for (int i = 0; i < RESERVED_GLOBAL_ROOT_WORKERS; i++) {
		root_reserved[i] =
			thread_create(reserved_worker, (void *)(uint64)i);
		check(root_reserved[i] >= 0,
		      "root reserved worker admitted at ordinary waterline");
	}
	wait_reserved_workers(RESERVED_GLOBAL_ROOT_WORKERS);
	check(write(reserved_start[1], "S", 1) == 1,
	      "start reserved global-limit workflow");
	close(reserved_start[1]);
	wait_success(workflow, "reserved global-limit workflow");
	printf("threadresource_ucore: reserved_global_limit=1\n");
	printf("threadresource_ucore: reserved_progress=1\n");

	reserved_stop = 1;
	for (int i = 0; i < RESERVED_GLOBAL_ROOT_WORKERS; i++)
		check(waittid(root_reserved[i]) == 0,
		      "join root reserved worker");
	reserved_probe_ran = 0;
	reserved_tid = thread_create(reserved_probe, 0);
	check(reserved_tid >= 0 && waittid(reserved_tid) == 70 &&
		      reserved_probe_ran,
	      "reserved global slot refunded");
	printf("threadresource_ucore: reserved_global_reuse=1\n");

	check(write(release[1], "XXX", 3) == 3,
	      "release ordinary holders");
	close(release[1]);
	close(ready[0]);
	for (int i = 0; i < 2; i++)
		wait_success(holders[i], "global holder completed");
	wait_success(global_probe, "global limit probe completed");
	probe = fork();
	check(probe >= 0, "ordinary thread waterline refunded");
	if (probe == 0)
		exit(0);
	wait_success(probe, "global reuse probe");
	printf("threadresource_ucore: global_reuse=1\n");
}

static void fair_worker(void *arg)
{
	int index = (int)(uint64)arg;

	while (!fair_start)
		sched_yield();
	while (!fair_stop) {
		fair_counts[index]++;
		sched_yield();
	}
	exit(0);
}

static void fairness_attacker(int ready_fd, int start_fd, int stop_fd,
			      int result_fd)
{
	int tids[FAIR_WORKERS];
	uint64 total = 0;
	char token = 0;

	fair_start = 0;
	fair_stop = 0;
	for (int i = 0; i < FAIR_WORKERS; i++) {
		fair_counts[i] = 0;
		tids[i] = thread_create(fair_worker, (void *)(uint64)i);
		if (tids[i] < 0)
			exit(80);
	}
	if (write(ready_fd, "A", 1) != 1)
		exit(81);
	if (read(start_fd, &token, 1) != 1 || token != 'S')
		exit(82);
	fair_start = 1;
	if (read(stop_fd, &token, 1) != 1 || token != 'X')
		exit(83);
	fair_stop = 1;
	for (int i = 0; i < FAIR_WORKERS; i++) {
		if (waittid(tids[i]) != 0 || fair_counts[i] == 0)
			exit(84);
		total += fair_counts[i];
	}
	if (write(result_fd, &total, sizeof(total)) != sizeof(total))
		exit(85);
	exit(0);
}

static void fairness_victim(int ready_fd, int start_fd, int done_fd)
{
	char token = 0;

	if (write(ready_fd, "V", 1) != 1)
		exit(90);
	if (read(start_fd, &token, 1) != 1 || token != 'S')
		exit(91);
	for (int i = 0; i < FAIR_VICTIM_ROUNDS; i++)
		sched_yield();
	if (write(done_fd, "D", 1) != 1)
		exit(92);
	exit(0);
}

static void domain_fairness_phase(void)
{
	int ready[2];
	int attacker_start[2];
	int victim_start[2];
	int stop[2];
	int done[2];
	int result[2];
	int attacker;
	int victim;
	char markers[2];
	char token = 0;
	uint64 total = 0;

	check(pipe(ready) == 0, "create fairness ready pipe");
	check(pipe(attacker_start) == 0, "create attacker start pipe");
	check(pipe(victim_start) == 0, "create victim start pipe");
	check(pipe(stop) == 0, "create fairness stop pipe");
	check(pipe(done) == 0, "create fairness done pipe");
	check(pipe(result) == 0, "create fairness result pipe");
	delegate_fd(ready[1], "delegate attacker ready pipe");
	delegate_fd(attacker_start[0], "delegate attacker start pipe");
	delegate_fd(stop[0], "delegate attacker stop pipe");
	delegate_fd(result[1], "delegate attacker result pipe");
	attacker = fork();
	check(attacker >= 0, "fork fairness attacker");
	if (attacker == 0)
		fairness_attacker(ready[1], attacker_start[0], stop[0],
				  result[1]);
	delegate_fd(ready[1], "delegate victim ready pipe");
	delegate_fd(victim_start[0], "delegate victim start pipe");
	delegate_fd(done[1], "delegate victim done pipe");
	victim = fork();
	check(victim >= 0, "fork fairness victim");
	if (victim == 0)
		fairness_victim(ready[1], victim_start[0], done[1]);
	close(ready[1]);
	close(attacker_start[0]);
	close(victim_start[0]);
	close(stop[0]);
	close(done[1]);
	close(result[1]);
	read_exact(ready[0], markers, sizeof(markers));
	check((markers[0] == 'A' || markers[0] == 'V') &&
		      (markers[1] == 'A' || markers[1] == 'V') &&
		      markers[0] != markers[1],
	      "fairness peers ready");
	check(write(attacker_start[1], "S", 1) == 1,
	      "start fairness attacker");
	check(write(victim_start[1], "S", 1) == 1,
	      "start fairness victim");
	close(attacker_start[1]);
	close(victim_start[1]);
	read_exact(done[0], &token, 1);
	check(token == 'D', "victim completed bounded work");
	check(write(stop[1], "X", 1) == 1, "stop fairness attacker");
	close(stop[1]);
	read_exact(result[0], &total, sizeof(total));
	close(result[0]);
	close(done[0]);
	close(ready[0]);
	wait_success(victim, "fairness victim completed");
	wait_success(attacker, "fairness attacker completed");
	check(total <= FAIR_VICTIM_ROUNDS + FAIR_SCHED_SLACK,
	      "thread count did not amplify domain CPU share");
	printf("threadresource_ucore: domain_fairness=1 hog=%d victim=%d bound=%d\n",
	       (int)total, FAIR_VICTIM_ROUNDS,
	       FAIR_VICTIM_ROUNDS + FAIR_SCHED_SLACK);
}

int main(void)
{
	check(TEST_ORDINARY_LIMIT == 2 * TEST_DOMAIN_LIMIT,
	      "test policy constants");
	check(TEST_RESERVED_LIMIT > TEST_RESERVED_DOMAIN_LIMIT,
	      "reserved policy preserves cross-domain capacity");
	printf("threadresource_ucore: thread resource-domain verification\n");
	domain_limit_phase();
	reserved_domain_limit_phase();
	exit_reuse_phase();
	global_waterline_phase();
	domain_fairness_phase();
	printf("threadresource_ucore: parent passed\n");
	return 0;
}
