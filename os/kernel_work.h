#ifndef KERNEL_WORK_H
#define KERNEL_WORK_H

#include "types.h"
#include "../kernel_work_abi.h"

struct thread;

// Long kernel paths may only reschedule after committing an atomic chunk and
// releasing transient state. This granule bounds polling overhead for streams.
#define KERNEL_WORK_STREAM_GRANULE 64U
#define KERNEL_WORK_BUDGET_UNITS 1024U
#define KERNEL_WORK_OPERATION_UNITS 256U
#define KERNEL_WORK_PAGE_UNITS 64U

#define KERNEL_WORK_SYSCALL_PUBLISH 1U
#define KERNEL_WORK_SYSCALL_OBSERVER 2U

void kernel_work_reset(struct thread *);
void kernel_work_on_dispatch(struct thread *);
void kernel_work_begin(void);
void kernel_work_begin_syscall(int syscall_id, uint syscall_class);
void kernel_work_begin_background(void);
void kernel_work_end(void);
void kernel_work_end_background(void);
void kernel_work_begin_cleanup(void);
void kernel_work_end_cleanup(void);
void kernel_work_timer_advance(void);
int kernel_work_receipt_snapshot(struct thread *,
				 struct kernel_work_receipt *);
uint64 kernel_work_last_preemptions(struct thread *);
void kernel_work_request_resched(void);
int kernel_work_checkpoint(uint work_units);
int kernel_work_checkpoint_cleanup(uint work_units);

#endif // KERNEL_WORK_H
