#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SYS_SCHED_YIELD 124
#define SYS_WAITTID 462
#define FAIR_BATCH 8
#define FAIR_ROUNDS 8
#define OWNER_REUSE_ROUNDS 16
#define SYNC_RACE_ROUNDS 512

static int nonowner_mutex;
static volatile int nonowner_result;

static int owner_mutex;
static volatile int owner_ready;
static volatile int owner_may_exit;
static volatile int owner_waiter_started;
static volatile int owner_waiter_acquired;
static int process_exit_mutex[2];
static volatile int process_exit_waiter_ready[2];
static int process_exit_semaphore;
static int process_exit_condvar;
static int process_exit_cond_mutex;
static volatile int process_exit_sem_ready;
static volatile int process_exit_cond_ready;

static int cond_race_mutex;
static int cond_race_condvar;
static volatile int cond_race_seen;
static volatile int cond_race_epoch;
static int sem_race;
static volatile int sem_race_entered;
static volatile int sem_race_consumed;

static int fair_mutex;
static volatile int fair_ready_count;
static volatile int fair_acquire_count;
static volatile int fair_ready_order[FAIR_BATCH];
static volatile int fair_acquire_order[FAIR_BATCH];

static int blocking_pipe[2];
static volatile int pipe_reader_started;
static volatile int pipe_reader_result;
static volatile char pipe_reader_byte;
static volatile int pipe_close_started[FAIR_BATCH];
static volatile int pipe_close_result[FAIR_BATCH];

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("blocking_semantics_ucore: FAIL %s\n", message);
	exit(1);
}

static void immediate_exit(void *arg)
{
	(void)arg;
	exit(37);
}

static void nonowner_unlock(void *arg)
{
	(void)arg;
	nonowner_result = mutex_unlock(nonowner_mutex);
	exit(0);
}

static void exiting_owner(void *arg)
{
	(void)arg;
	if (mutex_lock(owner_mutex) != 0)
		exit(41);
	owner_ready = 1;
	while (!owner_may_exit)
		sched_yield();
	// 故意保持互斥锁占用；线程拆除必须完成移交。
	exit(0);
}

static void owner_waiter(void *arg)
{
	(void)arg;
	owner_waiter_started = 1;
	if (mutex_lock(owner_mutex) != 0)
		exit(42);
	owner_waiter_acquired = 1;
	if (mutex_unlock(owner_mutex) != 0)
		exit(43);
	exit(0);
}

static void process_exit_waiter(void *arg)
{
	int slot = (int)(long)arg;

	process_exit_waiter_ready[slot] = 1;
	(void)mutex_lock(process_exit_mutex[slot]);
	exit(0);
}

static void process_exit_sem_waiter(void *arg)
{
	(void)arg;
	process_exit_sem_ready = 1;
	(void)semaphore_down(process_exit_semaphore);
	exit(0);
}

static void process_exit_cond_waiter(void *arg)
{
	(void)arg;
	if (mutex_lock(process_exit_cond_mutex) != 0)
		exit(61);
	process_exit_cond_ready = 1;
	(void)condvar_wait(process_exit_condvar, process_exit_cond_mutex);
	exit(0);
}

static void cond_race_waiter(void *arg)
{
	(void)arg;
	for (int round = 1; round <= SYNC_RACE_ROUNDS; round++) {
		if (mutex_lock(cond_race_mutex) != 0)
			exit(62);
		cond_race_seen = round;
		while (cond_race_epoch < round)
			if (condvar_wait(cond_race_condvar, cond_race_mutex) != 0)
				exit(63);
		if (mutex_unlock(cond_race_mutex) != 0)
			exit(64);
	}
	exit(0);
}

static void sem_race_waiter(void *arg)
{
	(void)arg;
	for (int round = 1; round <= SYNC_RACE_ROUNDS; round++) {
		sem_race_entered = round;
		if (semaphore_down(sem_race) != 0)
			exit(65);
		sem_race_consumed = round;
	}
	exit(0);
}

static void fair_waiter(void *arg)
{
	int id = (int)(long)arg;
	int slot = fair_ready_count++;

	if (slot < 0 || slot >= FAIR_BATCH)
		exit(50);
	fair_ready_order[slot] = id;
	if (mutex_lock(fair_mutex) != 0)
		exit(51);
	slot = fair_acquire_count++;
	if (slot < 0 || slot >= FAIR_BATCH)
		exit(52);
	fair_acquire_order[slot] = id;
	if (mutex_unlock(fair_mutex) != 0)
		exit(53);
	exit(0);
}

static void pipe_reader(void *arg)
{
	char byte = 0;

	(void)arg;
	pipe_reader_started = 1;
	pipe_reader_result = read(blocking_pipe[0], &byte, 1);
	pipe_reader_byte = byte;
	exit(0);
}

static void pipe_close_reader(void *arg)
{
	int slot = (int)(long)arg;
	char byte;

	pipe_close_started[slot] = 1;
	pipe_close_result[slot] = read(blocking_pipe[0], &byte, 1);
	exit(0);
}

static void test_waittid_sleep(void)
{
	int tid = thread_create(immediate_exit, 0);
	int waits_before;
	int yields_before;

	check(tid > 0, "create waittid target");
	waits_before = count_syscall(SYS_WAITTID);
	yields_before = count_syscall(SYS_SCHED_YIELD);
	check(waittid(tid) == 37, "waittid returns target status");
	check(count_syscall(SYS_WAITTID) == waits_before + 1,
	      "waittid uses one blocking syscall");
	check(count_syscall(SYS_SCHED_YIELD) == yields_before,
	      "waittid does not poll with yield");
}

static void test_mutex_ownership(void)
{
	int recursive = mutex_blocking_create();
	int tid;

	check(recursive >= 0 && mutex_lock(recursive) == 0,
	      "acquire recursive probe");
	check(mutex_lock(recursive) == -0xdead,
	      "recursive acquisition has explicit error");
	check(mutex_unlock(recursive) == 0, "release recursive probe");

	nonowner_mutex = mutex_blocking_create();
	check(nonowner_mutex >= 0 && mutex_lock(nonowner_mutex) == 0,
	      "acquire non-owner probe");
	nonowner_result = 0;
	tid = thread_create(nonowner_unlock, 0);
	check(tid > 0 && waittid(tid) == 0, "join non-owner unlocker");
	check(nonowner_result == -1, "reject non-owner unlock");
	check(mutex_unlock(nonowner_mutex) == 0,
	      "owner retains mutex after rejected unlock");
}

static void test_owner_exit_handoff(void)
{
	int owner_tid;
	int waiter_tid;

	owner_mutex = mutex_blocking_create();
	check(owner_mutex >= 0, "create owner-exit mutex");
	owner_ready = 0;
	owner_may_exit = 0;
	owner_waiter_started = 0;
	owner_waiter_acquired = 0;
	owner_tid = thread_create(exiting_owner, 0);
	check(owner_tid > 0, "create exiting mutex owner");
	while (!owner_ready)
		sched_yield();
	waiter_tid = thread_create(owner_waiter, 0);
	check(waiter_tid > 0, "create owner-exit waiter");
	while (!owner_waiter_started)
		sched_yield();
	for (int i = 0; i < 4; i++)
		sched_yield();
	check(owner_waiter_acquired == 0, "waiter sleeps behind live owner");
	owner_may_exit = 1;
	check(waittid(owner_tid) == 0, "join exiting mutex owner");
	check(waittid(waiter_tid) == 0, "join handed-off mutex waiter");
	check(owner_waiter_acquired == 1, "owner exit hands mutex to waiter");
}

static void test_owner_slot_reuse(void)
{
	for (int round = 0; round < OWNER_REUSE_ROUNDS; round++) {
		int owner_tid;
		int waiter_tid;

		owner_ready = 0;
		owner_may_exit = 0;
		owner_waiter_started = 0;
		owner_waiter_acquired = 0;
		owner_tid = thread_create(exiting_owner, 0);
		check(owner_tid > 0, "create reused owner slot");
		while (!owner_ready)
			sched_yield();
		waiter_tid = thread_create(owner_waiter, 0);
		check(waiter_tid > 0, "create reused waiter slot");
		while (!owner_waiter_started)
			sched_yield();
		sched_yield();
		owner_may_exit = 1;
		check(waittid(owner_tid) == 0 && waittid(waiter_tid) == 0 &&
		      owner_waiter_acquired,
		      "generation-safe owner handoff after slot reuse");
	}
	printf("blocking_semantics_ucore: owner_slot_reuse=%d generation_safe=1\n",
	       OWNER_REUSE_ROUNDS);
}

static void test_process_exit_handoff(void)
{
	int status = -1;
	int child = fork();

	check(child >= 0, "fork process-exit mutex probe");
	if (child == 0) {
		for (int i = 0; i < 2; i++) {
			process_exit_mutex[i] = mutex_blocking_create();
			process_exit_waiter_ready[i] = 0;
			check(process_exit_mutex[i] >= 0 &&
			      mutex_lock(process_exit_mutex[i]) == 0,
			      "hold process-exit mutex");
			check(thread_create(process_exit_waiter,
					    (void *)(long)i) > 0,
			      "create process-exit waiter");
		}
		process_exit_semaphore = semaphore_create(0);
		process_exit_cond_mutex = mutex_blocking_create();
		process_exit_condvar = condvar_create();
		process_exit_sem_ready = 0;
		process_exit_cond_ready = 0;
		check(process_exit_semaphore >= 0 &&
		      process_exit_cond_mutex >= 0 && process_exit_condvar >= 0 &&
		      thread_create(process_exit_sem_waiter, 0) > 0 &&
		      thread_create(process_exit_cond_waiter, 0) > 0,
		      "create interrupted semaphore and condvar waiters");
		for (int i = 0; i < 2; i++)
			while (!process_exit_waiter_ready[i])
				sched_yield();
		while (!process_exit_sem_ready || !process_exit_cond_ready)
			sched_yield();
		sleep(2);
	/* 拆除把两把锁移交给同时被撤销的线程。 */
		exit(0);
	}
	check(waitpid(child, &status) == child && status == 0,
	      "process teardown drains multiple mutex owners and batons");
	puts("blocking_semantics_ucore: process_exit_multilock=1 baton_revoke=1 cond_sem_interrupt_refund=1");
}

static void test_exec_sync_reset(void)
{
	int status = -1;
	int child = fork();

	check(child >= 0, "fork exec synchronization reset probe");
	if (child == 0) {
		char *argv[] = { "blocking_semantics_ucore", "exec-reset", 0 };
		int mutex = mutex_blocking_create();

		check(mutex == 0 && mutex_lock(mutex) == 0 &&
		      semaphore_create(1) == 0 && condvar_create() == 0,
		      "construct pre-exec synchronization namespace");
		exec("blocking_semantics_ucore", argv);
		exit(91);
	}
	check(waitpid(child, &status) == child && status == 0,
	      "exec replaces synchronization namespace");
}

static int exec_reset_image(void)
{
	int mutex;

	check(mutex_lock(0) == -1 && semaphore_down(0) == -1 &&
	      condvar_signal(0) == -1,
	      "old synchronization IDs are invalid after exec");
	mutex = mutex_blocking_create();
	check(mutex == 0 && semaphore_create(1) == 0 && condvar_create() == 0,
	      "new synchronization namespaces restart at zero");
	check(mutex_lock(mutex) == 0 && mutex_unlock(mutex) == 0,
	      "new mutex is usable after exec reset");
	puts("blocking_semantics_ucore: exec_sync_reset=1 stale_ids_rejected=1");
	return 0;
}

static void test_atomic_wait_publication(void)
{
	int cond_tid;
	int sem_tid;

	cond_race_mutex = mutex_blocking_create();
	cond_race_condvar = condvar_create();
	cond_race_seen = 0;
	cond_race_epoch = 0;
	check(cond_race_mutex >= 0 && cond_race_condvar >= 0,
	      "create condition publication race");
	cond_tid = thread_create(cond_race_waiter, 0);
	check(cond_tid > 0, "create condition publication waiter");
	for (int round = 1; round <= SYNC_RACE_ROUNDS; round++) {
		while (cond_race_seen < round)
			sched_yield();
		check(mutex_lock(cond_race_mutex) == 0,
		      "serialize signal against condition unlock");
		cond_race_epoch = round;
		check(condvar_signal(cond_race_condvar) == 0 &&
		      mutex_unlock(cond_race_mutex) == 0,
		      "publish condition signal");
	}
	check(waittid(cond_tid) == 0, "join condition publication waiter");

	sem_race = semaphore_create(0);
	sem_race_entered = 0;
	sem_race_consumed = 0;
	check(sem_race >= 0, "create semaphore publication race");
	sem_tid = thread_create(sem_race_waiter, 0);
	check(sem_tid > 0, "create semaphore publication waiter");
	for (int round = 1; round <= SYNC_RACE_ROUNDS; round++) {
		while (sem_race_entered < round)
			sched_yield();
		check(semaphore_up(sem_race) == 0,
		      "publish semaphore permit");
		while (sem_race_consumed < round)
			sched_yield();
	}
	check(waittid(sem_tid) == 0 && semaphore_up(sem_race) == 0 &&
	      semaphore_down(sem_race) == 0,
	      "semaphore count remains exact after race");
	printf("blocking_semantics_ucore: atomic_wait_publication=%d cond=1 semaphore=1 count_stable=1\n",
	       SYNC_RACE_ROUNDS);
}

static void test_fifo_fairness(void)
{
	int tids[FAIR_BATCH];

	fair_mutex = mutex_blocking_create();
	check(fair_mutex >= 0, "create FIFO mutex");
	for (int round = 0; round < FAIR_ROUNDS; round++) {
		fair_ready_count = 0;
		fair_acquire_count = 0;
		check(mutex_lock(fair_mutex) == 0, "hold FIFO mutex");
		for (int i = 0; i < FAIR_BATCH; i++) {
			int id = round * FAIR_BATCH + i;

			tids[i] = thread_create(fair_waiter, (void *)(long)id);
			check(tids[i] > 0, "create FIFO waiter");
			while (fair_ready_count <= i)
				sched_yield();
	// 让已就绪工作线程有机会进入内核等待队列。
			sched_yield();
		}
		for (int i = 0; i < 16; i++)
			sched_yield();
		check(fair_acquire_count == 0,
		      "blocked mutex waiters do not redispatch to user");
		check(mutex_unlock(fair_mutex) == 0, "release FIFO mutex");
		for (int i = 0; i < FAIR_BATCH; i++)
			check(waittid(tids[i]) == 0, "join FIFO waiter");
		check(fair_acquire_count == FAIR_BATCH,
		      "all FIFO waiters acquired");
		for (int i = 0; i < FAIR_BATCH; i++)
			check(fair_acquire_order[i] == fair_ready_order[i],
			      "mutex handoff preserves FIFO order");
	}
	printf("blocking_semantics_ucore: mutex_fifo_waiters=%d dispatch_stable=1\n",
	       FAIR_BATCH * FAIR_ROUNDS);
}

static void test_pipe_sleep(void)
{
	int tid;
	int yields_before;
	char byte = 'Q';

	check(pipe(blocking_pipe) == 0, "create blocking pipe");
	pipe_reader_started = 0;
	pipe_reader_result = -99;
	pipe_reader_byte = 0;
	tid = thread_create(pipe_reader, 0);
	check(tid > 0, "create pipe reader");
	while (!pipe_reader_started)
		sched_yield();
	for (int i = 0; i < 4; i++)
		sched_yield();
	check(pipe_reader_result == -99, "empty pipe reader remains asleep");
	yields_before = count_syscall(SYS_SCHED_YIELD);
	check(write(blocking_pipe[1], &byte, 1) == 1, "wake pipe reader");
	check(waittid(tid) == 0, "join pipe reader");
	check(count_syscall(SYS_SCHED_YIELD) == yields_before,
	      "pipe and waittid completion do not poll");
	check(pipe_reader_result == 1 && pipe_reader_byte == byte,
	      "pipe reader receives exact byte");
	check(close(blocking_pipe[0]) == 0 && close(blocking_pipe[1]) == 0,
	      "close blocking pipe");
}

static void test_pipe_close_wakes_all(void)
{
	int tids[FAIR_BATCH];

	check(pipe(blocking_pipe) == 0, "create close-wakeup pipe");
	for (int i = 0; i < FAIR_BATCH; i++) {
		pipe_close_started[i] = 0;
		pipe_close_result[i] = -99;
		tids[i] = thread_create(pipe_close_reader, (void *)(long)i);
		check(tids[i] > 0, "create close-wakeup reader");
	}
	for (int i = 0; i < FAIR_BATCH; i++)
		while (!pipe_close_started[i])
			sched_yield();
	for (int i = 0; i < 8; i++)
		sched_yield();
	check(close(blocking_pipe[1]) == 0, "close pipe writer");
	for (int i = 0; i < FAIR_BATCH; i++) {
		check(waittid(tids[i]) == 0, "join close-wakeup reader");
		check(pipe_close_result[i] == -1,
		      "writer close wakes every blocked reader");
	}
	check(close(blocking_pipe[0]) == 0, "close final pipe reader");
}

int main(int argc, char **argv)
{
	if (argc == 2 && argv != 0 && argv[1] != 0 &&
	    strcmp(argv[1], "exec-reset") == 0)
		return exec_reset_image();
	test_waittid_sleep();
	test_mutex_ownership();
	test_owner_exit_handoff();
	test_owner_slot_reuse();
	test_process_exit_handoff();
	test_exec_sync_reset();
	test_atomic_wait_publication();
	test_fifo_fairness();
	test_pipe_sleep();
	test_pipe_close_wakes_all();
	puts("blocking_semantics_ucore: mutex_owner=1 nonowner_rejected=1 recursive_rejected=1 owner_exit_handoff=1");
	puts("blocking_semantics_ucore: waittid_sleep=1 pipe_wait_queue=1 close_wake_all=1");
	puts("blocking_semantics_ucore: parent passed");
	return 0;
}
