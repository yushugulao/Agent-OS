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
/* 正常唤醒已赢得发布，但恢复执行前已观察到拆除。 */
#define WAIT_QUEUE_WOKEN_INTERRUPTED -3

// 队列身份即等待通道；休眠线程只属于一个队列。
struct wait_queue {
	struct thread *head;
	struct thread *tail;
	enum wait_reason reason;
};

	/* 选中等待者时仍处于关中断区间，在此捕获其稳定身份。 */
void wait_queue_init(struct wait_queue *, enum wait_reason);
/*
 * wait_queue_sleep() 自行管理中断转换，仅适用于共享谓词无需与队列发布保持
 * 原子的场景。*_irq 入口要求调用方已关中断，并跨休眠保持该外层状态，
 * 使谓词检查到入队不可分割。
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
