#ifndef KERNEL_WORK_H
#define KERNEL_WORK_H

#include "types.h"

struct thread;

// 长内核路径只能在提交原子批次并释放临时状态后让出处理器。
// 搬运数据的路径按 64 字节折算工作量，避免把每个字节都当成一个调度单位。
#define KERNEL_WORK_STREAM_GRANULE 64U
#define KERNEL_WORK_BYTES_PER_UNIT 64U
#define KERNEL_WORK_IO_BATCH_BYTES (16U * 1024U)
#define KERNEL_WORK_BUDGET_UNITS 512U
#define KERNEL_WORK_OPERATION_UNITS 256U
#define KERNEL_WORK_PAGE_UNITS 64U

#define KERNEL_WORK_SYSCALL_PUBLISH 1U
#define KERNEL_WORK_SYSCALL_OBSERVER 2U

void kernel_work_reset(struct thread *);
void kernel_work_on_dispatch(struct thread *);
void kernel_work_begin_syscall(int syscall_id, uint syscall_class);
void kernel_work_begin_background(void);
void kernel_work_end(void);
void kernel_work_end_background(void);
void kernel_work_begin_cleanup(void);
void kernel_work_end_cleanup(void);
uint64 kernel_work_last_preemptions(struct thread *);
void kernel_work_request_resched(void);
uint kernel_work_units_from_bytes(uint64);
int kernel_work_checkpoint(uint work_units);
int kernel_work_checkpoint_bytes(uint64 bytes);
int kernel_work_checkpoint_cleanup(uint work_units);

#endif // KERNEL_WORK_H
