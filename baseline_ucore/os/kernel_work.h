#ifndef KERNEL_WORK_H
#define KERNEL_WORK_H

#include "types.h"

struct thread;

// Long kernel paths may only reschedule after committing an atomic chunk and
// releasing transient state. This granule bounds polling overhead for streams.
#define KERNEL_WORK_STREAM_GRANULE 64U
#define KERNEL_WORK_BUDGET_UNITS 1024U
#define KERNEL_WORK_OPERATION_UNITS 256U
#define KERNEL_WORK_PAGE_UNITS 64U

void kernel_work_reset(struct thread *);
void kernel_work_on_dispatch(struct thread *);
void kernel_work_begin(void);
void kernel_work_end(void);
void kernel_work_request_resched(void);
int kernel_work_checkpoint(uint work_units);
int kernel_work_checkpoint_cleanup(uint work_units);

#endif // KERNEL_WORK_H
