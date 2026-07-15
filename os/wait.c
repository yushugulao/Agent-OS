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

int wait_queue_sleep(struct wait_queue *q)
{
	struct thread *t = curr_thread();
	int enabled = intr_save();

	if (q == 0 || q->reason == WAIT_REASON_NONE || t == 0 ||
	    t->state != RUNNING || t->wait_channel != 0 || t->on_run_queue) {
		intr_restore(enabled);
		return -1;
	}
	t->wait_channel = q;
	t->wait_reason = q->reason;
	t->wait_next = 0;
	if (q->tail)
		q->tail->wait_next = t;
	else
		q->head = t;
	q->tail = t;
	t->state = SLEEPING;
	sched();
	intr_restore(enabled);
	return 0;
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
	intr_restore(enabled);
}
