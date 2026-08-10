#include "agent_internal.h"
#include "defs.h"
#include "kernel_work.h"

#define AGENT_META_TXN_WORK_GRANULE 128U

static void *txn_owner;
static int txn_depth;
static uint64 txn_next_ticket;
static uint64 txn_serving_ticket;
static uint txn_work_records;
static int txn_reserved_turn;
static struct wait_queue txn_waiters;
static char scheduler_token;

#define TXN_NOWAIT 0
#define TXN_WAIT 1
#define TXN_EXTERNAL 2

void
agent_metadata_init(void)
{
	txn_owner = 0;
	txn_depth = 0;
	txn_next_ticket = 1;
	txn_serving_ticket = 1;
	txn_work_records = 0;
	txn_reserved_turn = 0;
	wait_queue_init(&txn_waiters, WAIT_REASON_AGENT_META);
}

void *
agent_metadata_txn_token(void)
{
	struct thread *t = curr_thread();

	if (t != 0 && t->state == RUNNING)
		return t;
	return &scheduler_token;
}

static int
agent_metadata_txn_acquire(int mode)
{
	struct proc *p = curr_proc();
	void *token = agent_metadata_txn_token();
	int scheduler_context = token == &scheduler_token;
	uint64 ticket = 0;
	int queued = 0;

	int enabled = intr_save();
	if (mode == TXN_EXTERNAL) {
		if (txn_owner != 0 ||
		    (!scheduler_context &&
		     txn_next_ticket != txn_serving_ticket))
			goto unavailable;
		txn_reserved_turn =
			scheduler_context &&
			txn_next_ticket != txn_serving_ticket;
		goto take;
	}
	for (;;) {
		if (txn_owner == token) {
			txn_depth++;
			goto acquired;
		}
		if (txn_owner == 0 &&
		    (queued ? ticket : txn_next_ticket) ==
			    txn_serving_ticket) {
			txn_reserved_turn = 0;
			if (queued)
				txn_serving_ticket++;
			goto take;
		}
		if (mode == TXN_NOWAIT || scheduler_context)
			goto unavailable;
		if (!queued) {
			if (mode == TXN_WAIT && proc_thread_exit_requested())
				goto unavailable;
			ticket = txn_next_ticket++;
			queued = 1;
		}
		if (p != 0)
			p->agent_meta_txn_wait_count++;
		if (wait_queue_sleep_irq_uninterruptible(&txn_waiters) !=
		    WAIT_QUEUE_OK)
			panic("metadata transaction wait failed");
	}
take:
	txn_owner = token;
	txn_depth = 1;
	txn_work_records = 0;
acquired:
	if (mode == TXN_WAIT && queued && proc_thread_exit_requested()) {
		agent_metadata_txn_unlock();
		goto unavailable;
	}
	intr_restore(enabled);
	return 1;
unavailable:
	intr_restore(enabled);
	return 0;
}

/*
 * 进程线程按票号顺序进入 metadata 门。调度器空闲时可执行一次有界维护，
 * 进程回调不得越过已排队票号。
 */
int
agent_metadata_txn_lock(int wait)
{
	return agent_metadata_txn_acquire(wait ? TXN_WAIT : TXN_NOWAIT);
}

int
agent_metadata_txn_try_external(void)
{
	return agent_metadata_txn_acquire(TXN_EXTERNAL);
}

void
agent_metadata_txn_unlock(void)
{
	int enabled = intr_save();

	if (!agent_metadata_txn_owned(0))
		panic("Agent metadata transaction invariant");
	txn_depth--;
	if (txn_depth == 0) {
		int reserved_turn = txn_reserved_turn;

		txn_owner = 0;
		txn_work_records = 0;
		txn_reserved_turn = 0;
		if (!reserved_turn)
			wait_queue_wake_one(&txn_waiters);
	}
	intr_restore(enabled);
}

void
agent_metadata_txn_work_charge(uint records)
{
	if (!agent_metadata_txn_owned(0))
		panic("metadata work outside transaction");
	if (agent_metadata_txn_token() == &scheduler_token)
		return;
	while (records > 0) {
		uint room = AGENT_META_TXN_WORK_GRANULE - txn_work_records;
		uint add = records < room ? records : room;

		txn_work_records += add;
		records -= add;
		if (txn_work_records < AGENT_META_TXN_WORK_GRANULE)
			continue;
		txn_work_records = 0;
		(void)kernel_work_checkpoint_cleanup(
			KERNEL_WORK_OPERATION_UNITS);
	}
}

int
agent_metadata_txn_owned(int exact_depth)
{
	return txn_owner == agent_metadata_txn_token() && txn_depth > 0 &&
	       (exact_depth <= 0 || txn_depth == exact_depth);
}

void __attribute__((noinline))
agent_metadata_txn_require_owned(int exact_depth, const char *reason)
{
	if (!agent_metadata_txn_owned(exact_depth))
		panic("%s", reason);
}

void
agent_metadata_proc_runtime_snapshot(
	struct proc *p, struct agent_metadata_runtime_snapshot *snapshot)
{
	if (snapshot == 0)
		return;
	memset(snapshot, 0, sizeof(*snapshot));
	int enabled = intr_save();
	for (int tid = 0; p != 0 && tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (txn_owner == t)
			snapshot->metadata_txn_owned = 1;
		if (t->state != SLEEPING)
			continue;
		if (t->wait_channel == &txn_waiters &&
		    t->wait_reason == WAIT_REASON_AGENT_META)
			snapshot->metadata_txn_waiters++;
	}
	intr_restore(enabled);
}
