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
static struct wait_queue txn_waiters;
static char scheduler_token;
static void *reload_owner;

void
agent_metadata_init(void)
{
	txn_owner = 0;
	txn_depth = 0;
	txn_next_ticket = 1;
	txn_serving_ticket = 1;
	txn_work_records = 0;
	txn_reserved_turn = 0;
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

/*
 * Process threads enter the metadata gate in ticket order.  The scheduler may
 * take one bounded maintenance turn while idle, but process callbacks may not
 * barge ahead of a queued ticket.
 */
int
agent_metadata_txn_lock(int wait)
{
	struct proc *p = curr_proc();
	void *token = agent_metadata_txn_token();
	int scheduler_context = token == &scheduler_token;
	int enabled = intr_save();
	uint64 ticket = 0;
	int queued = 0;

	for (;;) {
		if (txn_owner == token) {
			txn_depth++;
			intr_restore(enabled);
			return 1;
		}
		if (txn_owner == 0 &&
		    ((!queued && txn_next_ticket == txn_serving_ticket) ||
		     (queued && ticket == txn_serving_ticket))) {
			txn_owner = token;
			txn_depth = 1;
			txn_work_records = 0;
			txn_reserved_turn = 0;
			if (queued)
				txn_serving_ticket++;
			if (queued && proc_thread_exit_requested()) {
				agent_metadata_txn_unlock();
				intr_restore(enabled);
				return 0;
			}
			intr_restore(enabled);
			return 1;
		}
		if (!wait || scheduler_context) {
			intr_restore(enabled);
			return 0;
		}
		if (!queued) {
			if (proc_thread_exit_requested()) {
				intr_restore(enabled);
				return 0;
			}
			ticket = txn_next_ticket++;
			queued = 1;
		}
		if (p != 0)
			p->agent_meta_txn_wait_count++;
		if (wait_queue_sleep_irq_uninterruptible(&txn_waiters) !=
		    WAIT_QUEUE_OK)
			panic("metadata transaction wait failed");
	}
}

int
agent_metadata_txn_try_external(void)
{
	void *token = agent_metadata_txn_token();
	int scheduler_context = token == &scheduler_token;
	int enabled = intr_save();

	if (txn_owner != 0 || reload_owner != 0 ||
	    (!scheduler_context && txn_next_ticket != txn_serving_ticket)) {
		intr_restore(enabled);
		return 0;
	}
	txn_owner = token;
	txn_depth = 1;
	txn_work_records = 0;
	txn_reserved_turn =
		scheduler_context && txn_next_ticket != txn_serving_ticket;
	intr_restore(enabled);
	return 1;
}

void
agent_metadata_txn_unlock(void)
{
	void *token = agent_metadata_txn_token();
	int enabled = intr_save();

	if (txn_owner != token || txn_depth <= 0)
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
agent_metadata_txn_relock_uninterruptible(void)
{
	struct proc *p = curr_proc();
	void *token = agent_metadata_txn_token();
	uint64 ticket = 0;
	int queued = 0;

	if (token == &scheduler_token)
		panic("scheduler metadata relock");
	for (;;) {
		int enabled = intr_save();

		if (txn_owner == token) {
			txn_depth++;
			intr_restore(enabled);
			return;
		}
		if (txn_owner == 0 &&
		    ((!queued && txn_next_ticket == txn_serving_ticket) ||
		     (queued && ticket == txn_serving_ticket))) {
			txn_owner = token;
			txn_depth = 1;
			txn_work_records = 0;
			txn_reserved_turn = 0;
			if (queued)
				txn_serving_ticket++;
			intr_restore(enabled);
			return;
		}
		if (!queued) {
			ticket = txn_next_ticket++;
			queued = 1;
		}
		if (p != 0)
			p->agent_meta_txn_wait_count++;
		if (wait_queue_sleep_irq_uninterruptible(&txn_waiters) !=
		    WAIT_QUEUE_OK)
			panic("metadata relock failed");
		intr_restore(enabled);
	}
}

void
agent_metadata_txn_work_charge(uint records)
{
	void *token = agent_metadata_txn_token();

	if (txn_owner != token || txn_depth <= 0)
		panic("metadata work outside transaction");
	if (token == &scheduler_token)
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
agent_metadata_txn_checkpoint_unlocked(void)
{
	void *token = agent_metadata_txn_token();
	int depth;
	int checkpoint;

	if (txn_owner != token || txn_depth <= 0)
		panic("metadata checkpoint invariant");
	if (token == &scheduler_token)
		return bio_request_checkpoint();
	depth = txn_depth;
	for (int i = 0; i < depth; i++)
		agent_metadata_txn_unlock();
	checkpoint = bio_request_checkpoint();
	for (int i = 0; i < depth; i++)
		agent_metadata_txn_relock_uninterruptible();
	return checkpoint;
}

int
agent_metadata_txn_owned(int exact_depth)
{
	void *token = agent_metadata_txn_token();

	return txn_owner == token && txn_depth > 0 &&
	       (exact_depth <= 0 || txn_depth == exact_depth);
}

int
agent_metadata_txn_depth(void)
{
	if (!agent_metadata_txn_owned(0))
		return 0;
	return txn_depth;
}

int
agent_metadata_reload_available(void)
{
	void *token = agent_metadata_txn_token();
	int enabled = intr_save();
	int available = reload_owner == 0 || reload_owner == token;

	intr_restore(enabled);
	return available;
}

int
agent_metadata_reload_is_current(void)
{
	void *token = agent_metadata_txn_token();
	int enabled = intr_save();
	int current = reload_owner == token;

	intr_restore(enabled);
	return current;
}

int
agent_metadata_reload_claim(void)
{
	void *token = agent_metadata_txn_token();
	int enabled = intr_save();

	if (reload_owner != 0 && reload_owner != token) {
		intr_restore(enabled);
		return 0;
	}
	reload_owner = token;
	intr_restore(enabled);
	return 1;
}

void
agent_metadata_reload_release(void)
{
	void *token = agent_metadata_txn_token();
	int enabled = intr_save();

	if (reload_owner != token)
		panic("metadata reload owner");
	reload_owner = 0;
	intr_restore(enabled);
}
