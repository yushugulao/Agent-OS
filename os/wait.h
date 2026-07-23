#ifndef WAIT_H
#define WAIT_H

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
	WAIT_REASON_AGENT_META,
	WAIT_REASON_FS_CLAIM,
	WAIT_REASON_IO_BUDGET,
	WAIT_REASON_BUFFER_CACHE,
};

#define WAIT_QUEUE_OK          0
#define WAIT_QUEUE_ERROR      -1
#define WAIT_QUEUE_INTERRUPTED -2

// Queue identity is the wait channel; a sleeping thread belongs to one queue.
struct wait_queue {
	struct thread *head;
	struct thread *tail;
	enum wait_reason reason;
};

void wait_queue_init(struct wait_queue *, enum wait_reason);
int wait_queue_sleep(struct wait_queue *);
int wait_queue_sleep_irq(struct wait_queue *);
int wait_queue_sleep_irq_uninterruptible(struct wait_queue *);
int wait_queue_wake_one(struct wait_queue *);
int wait_queue_wake_all(struct wait_queue *);
int wait_queue_interrupt(struct thread *);
void wait_queue_cancel(struct thread *);

#endif // WAIT_H
