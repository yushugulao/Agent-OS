#include "defs.h"
#include "wait.h"

void wait_queue_init(struct wait_queue *q, enum wait_reason reason)
{
	int enabled = intr_save();

	q->head = 0;
	q->tail = 0;
	q->reason = reason;
	intr_restore(enabled);
}

static int wait_queue_sleep_mode(struct wait_queue *q, int interruptible)
{
	struct thread *t = curr_thread();
	int enabled = intr_save();
	int interrupted;

	if (q == 0 || q->reason == WAIT_REASON_NONE || t == 0 ||
	    t->state != RUNNING || t->wait_channel != 0 || t->on_run_queue) {
		intr_restore(enabled);
		return WAIT_QUEUE_ERROR;
	}
	if (interruptible && proc_thread_exit_requested()) {
		intr_restore(enabled);
		return WAIT_QUEUE_INTERRUPTED;
	}
	t->wait_interrupted = 0;
	t->wait_interruptible = interruptible;
	t->wait_channel = q;
	t->wait_reason = q->reason;
	t->wait_next = 0;
	if (q->tail)
		q->tail->wait_next = t;
	else
		q->head = t;
	q->tail = t;
	t->state = SLEEPING;
	/*
	 * Switch with interrupts disabled. The scheduler publishes idle as the
	 * current thread before opening its interrupt window, so an interrupt can
	 * never be attributed to this sleeping thread on the scheduler stack.
	 */
	sched();
	interrupted = t->wait_interrupted ||
		(interruptible && proc_thread_exit_requested());
	t->wait_interrupted = 0;
	t->wait_interruptible = 0;
	intr_restore(enabled);
	return interrupted ? WAIT_QUEUE_INTERRUPTED : WAIT_QUEUE_OK;
}

int wait_queue_sleep(struct wait_queue *q)
{
	return wait_queue_sleep_mode(q, 1);
}

int wait_queue_sleep_irq(struct wait_queue *q)
{
	return wait_queue_sleep_mode(q, 1);
}

int wait_queue_sleep_irq_uninterruptible(struct wait_queue *q)
{
	return wait_queue_sleep_mode(q, 0);
}

int wait_queue_wake_one(struct wait_queue *q)
{
	struct thread *t;
	int enabled = intr_save();
	int woken = 0;

	while (q != 0 && (t = q->head) != 0) {
		q->head = t->wait_next;
		if (q->head == 0)
			q->tail = 0;
		t->wait_next = 0;
		if (t->wait_channel != q)
			continue;
		t->wait_channel = 0;
		t->wait_reason = WAIT_REASON_NONE;
		t->wait_interruptible = 0;
		if (t->state != SLEEPING)
			continue;
		t->state = RUNNABLE;
		add_task(t);
		woken = 1;
		break;
	}
	intr_restore(enabled);
	return woken;
}

int wait_queue_wake_all(struct wait_queue *q)
{
	int woken = 0;
	int enabled = intr_save();

	while (wait_queue_wake_one(q))
		woken++;
	intr_restore(enabled);
	return woken;
}

void wait_queue_cancel(struct thread *t)
{
	struct wait_queue *q;
	struct thread *previous = 0;
	struct thread *current;
	int enabled = intr_save();

	if (t == 0 || t->wait_channel == 0) {
		intr_restore(enabled);
		return;
	}
	q = t->wait_channel;
	for (current = q->head; current; current = current->wait_next) {
		if (current != t) {
			previous = current;
			continue;
		}
		if (previous)
			previous->wait_next = current->wait_next;
		else
			q->head = current->wait_next;
		if (q->tail == current)
			q->tail = previous;
		break;
	}
	t->wait_channel = 0;
	t->wait_next = 0;
	t->wait_reason = WAIT_REASON_NONE;
	t->wait_interrupted = 0;
	t->wait_interruptible = 0;
	intr_restore(enabled);
}

int wait_queue_interrupt(struct thread *t)
{
	int enabled = intr_save();

	if (t == 0 || t->state != SLEEPING || t->wait_channel == 0 ||
	    !t->wait_interruptible) {
		intr_restore(enabled);
		return 0;
	}
	wait_queue_cancel(t);
	t->wait_interrupted = 1;
	t->state = RUNNABLE;
	add_task(t);
	intr_restore(enabled);
	return 1;
}
