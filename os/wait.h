#ifndef WAIT_H
#define WAIT_H

#include "types.h"

struct thread;

enum wait_reason {
	WAIT_REASON_NONE,
	WAIT_REASON_CHILD,
	WAIT_REASON_THREAD_EXIT,
	WAIT_REASON_MUTEX,
	WAIT_REASON_SEMAPHORE,
	WAIT_REASON_CONDVAR,
	WAIT_REASON_EVENT,
	WAIT_REASON_TIMELINE,
	WAIT_REASON_AGENT_CONTEXT,
	WAIT_REASON_AGENT_META,
	WAIT_REASON_FS_CLAIM,
	WAIT_REASON_IO_BUDGET,
	WAIT_REASON_BUFFER_CACHE,
	WAIT_REASON_PIPE_READ,
	WAIT_REASON_PIPE_WRITE,
	WAIT_REASON_CONSOLE_INPUT,
	WAIT_REASON_VIRTIO_DESCRIPTOR,
	WAIT_REASON_VIRTIO_COMPLETION,
};

#define WAIT_QUEUE_OK          0
#define WAIT_QUEUE_ERROR      -1
#define WAIT_QUEUE_INTERRUPTED -2
/* A normal wake won publication, but teardown was visible before resume. */
#define WAIT_QUEUE_WOKEN_INTERRUPTED -3

// Queue identity is the wait channel; a sleeping thread belongs to one queue.
struct wait_queue {
	struct thread *head;
	struct thread *tail;
	enum wait_reason reason;
};

	/* 选中等待者时仍处于关中断区间，在此捕获其稳定身份。 */
void wait_queue_init(struct wait_queue *, enum wait_reason);
/*
 * wait_queue_sleep() owns its interrupt transition and is only suitable when
 * no shared predicate must stay atomic with queue publication.  The *_irq
 * entry points require interrupts to be disabled by the caller; they preserve
 * that outer state across sleep so predicate-check-to-enqueue is indivisible.
 */
int wait_queue_sleep(struct wait_queue *);
int wait_queue_sleep_irq(struct wait_queue *);
int wait_queue_sleep_key_irq(struct wait_queue *, uint64);
int wait_queue_sleep_irq_uninterruptible(struct wait_queue *);
struct thread *wait_queue_wake_one_thread(struct wait_queue *);
int wait_queue_wake_one(struct wait_queue *);
int wait_queue_wake_all(struct wait_queue *);
int wait_queue_wake_key_all(struct wait_queue *, uint64);
int wait_queue_interrupt(struct thread *);
void wait_queue_cancel(struct thread *);

#endif // WAIT_H
