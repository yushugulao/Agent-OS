#include "defs.h"
#include "wait_atomic_test.h"

#ifdef WAIT_ATOMIC_TEST_PROFILE

struct wait_atomic_test_state {
	struct proc *process;
	int pid;
	uint operation;
	uint complete;
	uint phase;
	uint64 sequence;
	uint64 flags;
	uint64 timeout_baseline;
	uint64 long_deadline;
	int long_tid;
	uint64 prior_generation[NTHREAD];
};

static struct wait_atomic_test_state wait_atomic_test_state;
static uint64 wait_atomic_test_next_sequence = 1;

static int
wait_atomic_test_live_siblings(struct proc *p, struct thread *self)
{
	int count = 0;

	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *candidate = &p->threads[tid];

		if (candidate != self && candidate->state != T_UNUSED &&
		    candidate->state != EXITED)
			count++;
	}
	return count;
}

static int
wait_atomic_test_arm(uint operation)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	int enabled;
	int allowed;

	if (operation != WAIT_ATOMIC_TEST_AGENT_WAIT &&
	    operation != WAIT_ATOMIC_TEST_TEARDOWN)
		return -1;
	enabled = intr_save();
	allowed = proc_teardown_live(p) && t != 0 &&
		(operation == WAIT_ATOMIC_TEST_AGENT_WAIT ?
			 p->is_agent : wait_atomic_test_live_siblings(p, t) == 1);
	if (!allowed || wait_atomic_test_state.process != 0) {
		intr_restore(enabled);
		return -1;
	}
	wait_atomic_test_state.process = p;
	wait_atomic_test_state.pid = p->pid;
	wait_atomic_test_state.operation = operation;
	wait_atomic_test_state.sequence = wait_atomic_test_next_sequence++;
	if (wait_atomic_test_next_sequence == 0)
		wait_atomic_test_next_sequence = 1;
	intr_restore(enabled);
	return 0;
}

int
wait_atomic_test_begin(struct proc *p, uint operation, uint64 flags)
{
	int enabled = intr_save();
	int armed = wait_atomic_test_state.process == p &&
		wait_atomic_test_state.pid == p->pid &&
		wait_atomic_test_state.operation == operation &&
		!wait_atomic_test_state.complete &&
		(wait_atomic_test_state.flags &
		 WAIT_ATOMIC_TEST_F_HOOK_FIRED) == 0;

	if (armed)
		wait_atomic_test_state.flags =
			WAIT_ATOMIC_TEST_F_HOOK_FIRED | flags;
	intr_restore(enabled);
	return armed;
}

void
wait_atomic_test_complete(struct proc *p, uint operation, uint64 flags)
{
	int enabled = intr_save();

	if (wait_atomic_test_state.process != p ||
	    wait_atomic_test_state.pid != p->pid ||
	    wait_atomic_test_state.operation != operation ||
	    (wait_atomic_test_state.flags & WAIT_ATOMIC_TEST_F_HOOK_FIRED) == 0 ||
	    wait_atomic_test_state.complete)
		panic("wait atomic test completion state");
	wait_atomic_test_state.flags |= flags | WAIT_ATOMIC_TEST_F_COMPLETE;
	wait_atomic_test_state.complete = 1;
	intr_restore(enabled);
}

int
wait_atomic_test_agent_wait(struct proc *p)
{
	int enabled = intr_save(), empty = p->agent_event_waiters.head == 0, armed = 0, queued = -1;
	if ((armed = wait_atomic_test_begin(p, WAIT_ATOMIC_TEST_AGENT_WAIT,
		     empty ? WAIT_ATOMIC_TEST_F_WAITER_EMPTY : 0))) {
		if (!empty) {
			intr_restore(enabled);
			panic("agent wait injection queue not empty");
		}
		queued = agent_ipc_wait_test_publish(p);
		if (queued == 1)
			wait_atomic_test_complete(p, WAIT_ATOMIC_TEST_AGENT_WAIT, WAIT_ATOMIC_TEST_F_EVENT_PUBLISHED);
	}
	intr_restore(enabled);
	return !armed ? 0 : queued == 1 ? 1 : -1;
}

static int
wait_atomic_test_query(uint operation, int target_pid, uint64 receiptaddr,
		       uint user_size)
{
	struct wait_atomic_test_receipt receipt;
	struct proc *p = curr_proc();
	uint64 sequence;
	int enabled;

	if (receiptaddr == 0 || user_size != sizeof(receipt) || target_pid <= 0 ||
	    user_range_check(p->pagetable, receiptaddr, sizeof(receipt), PTE_W) < 0)
		return -1;
	enabled = intr_save();
	if (!wait_atomic_test_state.complete ||
	    wait_atomic_test_state.operation != operation ||
	    wait_atomic_test_state.pid != target_pid) {
		intr_restore(enabled);
		return -1;
	}
	memset(&receipt, 0, sizeof(receipt));
	receipt.version = WAIT_ATOMIC_TEST_ABI_VERSION;
	receipt.size = sizeof(receipt);
	receipt.operation = operation;
	receipt.target_pid = target_pid;
	receipt.sequence = wait_atomic_test_state.sequence;
	receipt.flags = wait_atomic_test_state.flags;
	sequence = receipt.sequence;
	intr_restore(enabled);
	if (copyout(p->pagetable, receiptaddr, (char *)&receipt,
		    sizeof(receipt)) < 0)
		return -1;
	enabled = intr_save();
	if (!wait_atomic_test_state.complete ||
	    wait_atomic_test_state.sequence != sequence ||
	    wait_atomic_test_state.operation != operation ||
	    wait_atomic_test_state.pid != target_pid) {
		intr_restore(enabled);
		return -1;
	}
	memset(&wait_atomic_test_state, 0, sizeof(wait_atomic_test_state));
	intr_restore(enabled);
	return 0;
}

static void
wait_atomic_deadline_snapshot_locked(
	struct proc *p, uint phase, struct wait_atomic_deadline_snapshot *snapshot,
	uint *deadline_slots)
{
	memset(snapshot, 0, sizeof(*snapshot));
	snapshot->version = WAIT_ATOMIC_TEST_ABI_VERSION;
	snapshot->size = sizeof(*snapshot);
	snapshot->phase = phase;
	snapshot->target_pid = p->pid;
	snapshot->loop_state = p->loop_state;
	snapshot->earliest_tid = -1;
	snapshot->latest_tid = -1;
	snapshot->timeout_count = p->agent_timeout_count;
	*deadline_slots = 0;
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (t->agent_wait_deadline_valid)
			(*deadline_slots)++;
		if (t->state != SLEEPING ||
		    t->wait_channel != &p->agent_event_waiters ||
		    t->wait_reason != WAIT_REASON_EVENT)
			continue;
		snapshot->waiting_threads++;
		if (t->wait_key == t->identity_generation &&
		    t->identity_generation != 0)
			snapshot->keyed_threads++;
		if (!t->agent_wait_deadline_valid) {
			snapshot->infinite_threads++;
			continue;
		}
		snapshot->finite_threads++;
		if (snapshot->earliest_tid < 0 ||
		    t->agent_wait_deadline < snapshot->earliest_deadline) {
			snapshot->earliest_tid = tid;
			snapshot->earliest_deadline = t->agent_wait_deadline;
		}
		if (snapshot->latest_tid < 0 ||
		    t->agent_wait_deadline > snapshot->latest_deadline) {
			snapshot->latest_tid = tid;
			snapshot->latest_deadline = t->agent_wait_deadline;
		}
	}
}

static int
wait_atomic_deadline_observe(uint phase, uint64 snapshotaddr, uint user_size)
{
	struct wait_atomic_deadline_snapshot snapshot;
	struct proc *p = curr_proc();
	uint deadline_slots;
	int enabled;
	int valid = 0;

	if (snapshotaddr == 0 || user_size != sizeof(snapshot) || !p->is_agent ||
	    user_range_check(p->pagetable, snapshotaddr, sizeof(snapshot),
			     PTE_W) < 0)
		return -1;
	enabled = intr_save();
	wait_atomic_deadline_snapshot_locked(p, phase, &snapshot,
					     &deadline_slots);
	if (phase == WAIT_ATOMIC_TEST_DEADLINE_FINITE_INFINITE) {
		if (wait_atomic_test_state.process != 0) {
			intr_restore(enabled);
			return -1;
		}
		if (snapshot.waiting_threads != 2 ||
		    snapshot.finite_threads != 1 ||
		    snapshot.infinite_threads != 1) {
			intr_restore(enabled);
			return WAIT_ATOMIC_TEST_RETRY;
		}
		valid = snapshot.keyed_threads == 2 && deadline_slots == 1 &&
			snapshot.loop_state == AGENT_LOOP_WAITING;
		if (valid) {
			wait_atomic_test_state.process = p;
			wait_atomic_test_state.pid = p->pid;
			wait_atomic_test_state.operation =
				WAIT_ATOMIC_TEST_THREAD_DEADLINE;
			wait_atomic_test_state.sequence =
				wait_atomic_test_next_sequence++;
			if (wait_atomic_test_next_sequence == 0)
				wait_atomic_test_next_sequence = 1;
			wait_atomic_test_state.timeout_baseline =
				p->agent_timeout_count;
			for (int tid = 0; tid < NTHREAD; tid++)
				if (p->threads[tid].state == SLEEPING &&
				    p->threads[tid].wait_channel ==
					    &p->agent_event_waiters)
					wait_atomic_test_state
						.prior_generation[tid] =
						p->threads[tid]
							.identity_generation;
			wait_atomic_test_state.flags =
				WAIT_ATOMIC_TEST_F_FINITE_INFINITE |
				WAIT_ATOMIC_TEST_F_DISTINCT_KEYS |
				WAIT_ATOMIC_TEST_F_LOOP_AGGREGATE;
		}
	} else if (wait_atomic_test_state.process != p ||
		   wait_atomic_test_state.operation !=
			   WAIT_ATOMIC_TEST_THREAD_DEADLINE ||
		   phase != wait_atomic_test_state.phase + 1) {
		intr_restore(enabled);
		return -1;
	} else if (phase == WAIT_ATOMIC_TEST_DEADLINE_INFINITE_ONLY) {
		if (snapshot.waiting_threads != 1 ||
		    snapshot.infinite_threads != 1) {
			intr_restore(enabled);
			return WAIT_ATOMIC_TEST_RETRY;
		}
		valid = snapshot.finite_threads == 0 && deadline_slots == 0 &&
			snapshot.keyed_threads == 1 &&
			snapshot.loop_state == AGENT_LOOP_WAITING &&
			snapshot.timeout_count >=
				wait_atomic_test_state.timeout_baseline + 1;
		if (valid)
			wait_atomic_test_state.flags |=
				WAIT_ATOMIC_TEST_F_INFINITE_KEPT |
				WAIT_ATOMIC_TEST_F_TIMEOUTS;
	} else if (phase == WAIT_ATOMIC_TEST_DEADLINE_IDLE_FIRST) {
		if (snapshot.waiting_threads != 0) {
			intr_restore(enabled);
			return WAIT_ATOMIC_TEST_RETRY;
		}
		valid = deadline_slots == 0 &&
			snapshot.loop_state == AGENT_LOOP_IDLE;
		if (valid)
			wait_atomic_test_state.flags |=
				WAIT_ATOMIC_TEST_F_RUNTIME_CLEARED;
	} else if (phase == WAIT_ATOMIC_TEST_DEADLINE_DISTINCT) {
		if (snapshot.waiting_threads != 2 ||
		    snapshot.finite_threads != 2) {
			intr_restore(enabled);
			return WAIT_ATOMIC_TEST_RETRY;
		}
		for (int tid = 0; tid < NTHREAD; tid++) {
			struct thread *t = &p->threads[tid];

			if (t->state == SLEEPING &&
			    t->wait_channel == &p->agent_event_waiters &&
			    wait_atomic_test_state.prior_generation[tid] != 0 &&
			    wait_atomic_test_state.prior_generation[tid] !=
				    t->identity_generation)
				snapshot.reused_threads++;
		}
		valid = snapshot.infinite_threads == 0 && deadline_slots == 2 &&
			snapshot.keyed_threads == 2 &&
			snapshot.reused_threads == 2 &&
			snapshot.earliest_tid != snapshot.latest_tid &&
			snapshot.earliest_deadline < snapshot.latest_deadline &&
			snapshot.loop_state == AGENT_LOOP_WAITING;
		if (valid) {
			wait_atomic_test_state.long_deadline =
				snapshot.latest_deadline;
			wait_atomic_test_state.long_tid = snapshot.latest_tid;
			wait_atomic_test_state.flags |=
				WAIT_ATOMIC_TEST_F_DISTINCT_KEYS |
				WAIT_ATOMIC_TEST_F_SLOT_REUSED |
				WAIT_ATOMIC_TEST_F_LOOP_AGGREGATE;
		}
	} else if (phase == WAIT_ATOMIC_TEST_DEADLINE_LONG_ONLY) {
		if (snapshot.waiting_threads != 1 ||
		    snapshot.finite_threads != 1) {
			intr_restore(enabled);
			return WAIT_ATOMIC_TEST_RETRY;
		}
		valid = snapshot.infinite_threads == 0 && deadline_slots == 1 &&
			snapshot.keyed_threads == 1 &&
			snapshot.earliest_tid == wait_atomic_test_state.long_tid &&
			snapshot.earliest_deadline ==
				wait_atomic_test_state.long_deadline &&
			snapshot.loop_state == AGENT_LOOP_WAITING &&
			snapshot.timeout_count >=
				wait_atomic_test_state.timeout_baseline + 2;
		if (valid)
			wait_atomic_test_state.flags |=
				WAIT_ATOMIC_TEST_F_LONG_KEPT |
				WAIT_ATOMIC_TEST_F_TIMEOUTS;
	} else if (phase == WAIT_ATOMIC_TEST_DEADLINE_COMPLETE) {
		if (snapshot.waiting_threads != 0) {
			intr_restore(enabled);
			return WAIT_ATOMIC_TEST_RETRY;
		}
		valid = deadline_slots == 0 &&
			snapshot.loop_state == AGENT_LOOP_IDLE &&
			snapshot.timeout_count >=
				wait_atomic_test_state.timeout_baseline + 3;
		if (valid) {
			wait_atomic_test_state.flags |=
				WAIT_ATOMIC_TEST_F_TIMEOUTS |
				WAIT_ATOMIC_TEST_F_RUNTIME_CLEARED |
				WAIT_ATOMIC_TEST_F_COMPLETE;
			wait_atomic_test_state.complete = 1;
		}
	}
	if (!valid) {
		intr_restore(enabled);
		return -1;
	}
	wait_atomic_test_state.phase = phase;
	intr_restore(enabled);
	return copyout(p->pagetable, snapshotaddr, (char *)&snapshot,
		       sizeof(snapshot));
}

int
sys_wait_atomic_test(uint version, uint command, uint operation,
		     int target_pid, uint64 receiptaddr, uint user_size)
{
	if (version != WAIT_ATOMIC_TEST_ABI_VERSION)
		return -1;
	if (command == WAIT_ATOMIC_TEST_COMMAND_ARM && target_pid == 0 &&
	    receiptaddr == 0 && user_size == 0)
		return wait_atomic_test_arm(operation);
	if (command == WAIT_ATOMIC_TEST_COMMAND_QUERY)
		return wait_atomic_test_query(operation, target_pid, receiptaddr,
					      user_size);
	if (command == WAIT_ATOMIC_TEST_COMMAND_DEADLINE_OBSERVE &&
	    operation >= WAIT_ATOMIC_TEST_DEADLINE_FINITE_INFINITE &&
	    operation <= WAIT_ATOMIC_TEST_DEADLINE_COMPLETE && target_pid == 0)
		return wait_atomic_deadline_observe(operation, receiptaddr,
						     user_size);
	return -1;
}

#endif
