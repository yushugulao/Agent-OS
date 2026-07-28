#include "agent_internal.h"
#include "bio.h"
#include "defs.h"
#include "kernel_work.h"

#define AGENT_META_TXN_WORK_GRANULE 128U

static void *txn_owner;
static int txn_depth;
static uint64 txn_next_ticket;
static uint64 txn_serving_ticket;
static uint txn_work_records;
static int txn_reserved_turn;
static int txn_projection_pending;
static struct wait_queue txn_waiters;
static char scheduler_token;
static void *reload_owner;

#define TXN_NOWAIT 0
#define TXN_WAIT 1
#define TXN_RELOCK 2
#define TXN_EXTERNAL 3

void
agent_metadata_init(void)
{
	txn_owner = 0;
	txn_depth = 0;
	txn_next_ticket = 1;
	txn_serving_ticket = 1;
	txn_work_records = 0;
	txn_reserved_turn = 0;
	txn_projection_pending = 0;
	reload_owner = 0;
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

	if (mode == TXN_RELOCK && scheduler_context)
		panic("scheduler metadata relock");
	int enabled = intr_save();
	if (mode == TXN_EXTERNAL) {
		if (txn_owner != 0 || reload_owner != 0 ||
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
			panic("%s", mode == TXN_RELOCK ? "metadata relock failed" :
			      "metadata transaction wait failed");
		if (mode == TXN_RELOCK) {
			intr_restore(enabled);
			enabled = intr_save();
		}
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
 * Process threads enter the metadata gate in ticket order.  The scheduler may
 * take one bounded maintenance turn while idle, but process callbacks may not
 * barge ahead of a queued ticket.
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

	if (!agent_metadata_txn_owned(0) ||
	    (txn_depth == 1 && txn_projection_pending))
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

void agent_metadata_txn_projection_transition(int pending) {
	pending = !!pending;
	if (!agent_metadata_txn_owned(0) ||
	    pending == txn_projection_pending)
		panic("Agent metadata transaction invariant");
	txn_projection_pending = pending;
}

void agent_metadata_txn_projection_require_idle(void) {
	if (!agent_metadata_txn_owned(0) || txn_projection_pending)
		panic("Agent metadata transaction invariant");
}

void
agent_metadata_txn_relock_uninterruptible(void)
{
	(void)agent_metadata_txn_acquire(TXN_RELOCK);
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

static struct bio_checkpoint_result
agent_metadata_txn_checkpoint_mode(int cleanup)
{
	if (!agent_metadata_txn_owned(0) || txn_projection_pending)
		panic("metadata checkpoint invariant");
	if (agent_metadata_txn_token() == &scheduler_token)
		return bio_request_checkpoint();
	int depth = txn_depth;
	for (int i = 0; i < depth; i++)
		agent_metadata_txn_unlock();
	struct bio_checkpoint_result checkpoint =
		cleanup ? bio_request_checkpoint_cleanup() :
			  bio_request_checkpoint();
	for (int i = 0; i < depth; i++)
		agent_metadata_txn_relock_uninterruptible();
	return checkpoint;
}

struct bio_checkpoint_result agent_metadata_txn_checkpoint_unlocked(void)
{
	return agent_metadata_txn_checkpoint_mode(0);
}

struct bio_checkpoint_result
agent_metadata_txn_checkpoint_cleanup_unlocked(void)
{
	return agent_metadata_txn_checkpoint_mode(1);
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

int
agent_metadata_txn_depth(void)
{
	return agent_metadata_txn_owned(0) ? txn_depth : 0;
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

int
agent_metadata_reload_available(void)
{
	return reload_owner == 0 || agent_metadata_reload_is_current();
}

int
agent_metadata_reload_is_current(void)
{
	return reload_owner == agent_metadata_txn_token();
}

int
agent_metadata_reload_claim(void)
{
	if (!agent_metadata_reload_available())
		return 0;
	reload_owner = agent_metadata_txn_token();
	return 1;
}

void
agent_metadata_reload_release(void)
{
	if (!agent_metadata_reload_is_current())
		panic("metadata reload owner");
	reload_owner = 0;
}
