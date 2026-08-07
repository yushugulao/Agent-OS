#include "defs.h"
#include "bio.h"
#include "kernel_work.h"
_Static_assert(KERNEL_WORK_BYTES_PER_UNIT > 0,
	       "kernel byte normalization granule must be positive");
_Static_assert(KERNEL_WORK_IO_BATCH_BYTES / KERNEL_WORK_BYTES_PER_UNIT <
	       KERNEL_WORK_BUDGET_UNITS,
	       "one I/O batch must leave room in the work budget");

static int kernel_work_running(struct thread *t)
{
	return t != 0 && t->trapframe != 0 && t->process != 0 &&
	       t->state == RUNNING &&
	       t->tid >= 0;
}

void kernel_work_reset(struct thread *t)
{
	struct thread_trap_cold *cold;

	if (t == 0 || t->trapframe == 0)
		return;
	bio_request_abort_thread(t);
	cold = thread_trap_cold(t);
	memset(cold, 0, __builtin_offsetof(struct thread_trap_cold, context));
	cold->kernel_work_target_syscall_id = -1;
}

// 时间片在调度时开始，而不是在系统调用入口刷新，避免短调用流无限续期。
void kernel_work_on_dispatch(struct thread *t)
{
	struct thread_trap_cold *cold;

	if (t == 0 || t->trapframe == 0)
		return;
	cold = thread_trap_cold(t);
	cold->kernel_work_resumed = cold->kernel_work_depth != 0;
	if (cold->kernel_work_resumed)
		cold->kernel_work_redispatches++;
	cold->kernel_resched_pending = 0;
	cold->kernel_work_units = 0;
}

static void kernel_work_begin_scope(int syscall_id, int measure_preemptions)
{
	struct thread *t = curr_thread();
	struct thread_trap_cold *cold;

	if (!kernel_work_running(t))
		return;
	cold = thread_trap_cold(t);
	if (cold->kernel_work_depth == (uint)-1)
		panic("kernel work depth overflow");
	if (cold->kernel_work_depth == 0) {
		if (cold->kernel_work_generation == (uint64)-1)
			panic("kernel work generation exhausted");
		cold->kernel_work_generation++;
		cold->kernel_syscall_preemptions_start =
			cold->kernel_work_redispatches;
		cold->kernel_work_target_syscall_id = syscall_id;
		cold->kernel_work_measure_preemptions = measure_preemptions;
		if (measure_preemptions)
			cold->kernel_last_syscall_preemptions = 0;
	}
	cold->kernel_work_depth++;
}

void kernel_work_begin_syscall(int syscall_id, uint syscall_class)
{
	if (syscall_class != KERNEL_WORK_SYSCALL_PUBLISH &&
	    syscall_class != KERNEL_WORK_SYSCALL_OBSERVER)
		panic("invalid kernel work syscall class");
	kernel_work_begin_scope(
		syscall_id, syscall_class == KERNEL_WORK_SYSCALL_PUBLISH);
}

void kernel_work_begin_background(void)
{
	kernel_work_begin_scope(-1, 0);
}

void kernel_work_begin_cleanup(void)
{
	struct thread *t = curr_thread();

	/* 终止退出优先复用外层系统调用的内核工作时间片。 */
	if (!kernel_work_running(t) ||
	    thread_trap_cold(t)->kernel_work_depth != 0)
		return;
	kernel_work_begin_scope(-1, 0);
}

void kernel_work_request_resched(void)
{
	struct thread *t = curr_thread();

	if (kernel_work_running(t) &&
	    thread_trap_cold(t)->kernel_work_depth != 0)
		thread_trap_cold(t)->kernel_resched_pending = 1;
}

/*
 * 系统调用入口会关闭 SIE。若唯一竞争者正等待设备完成，运行队列查询看不到它。
 * 仅在已提交原子批次的慢速安全点短暂开放中断，让设备和时钟发布唤醒及待调度位；
 * 短系统调用不经过这里，也不重复写 CSR。
 */
static void kernel_work_irq_window(struct thread *t)
{
	if (thread_trap_cold(t)->kernel_work_target_syscall_id < 0)
		return;
	/* SPP=1 表示正在处理内核陷阱，禁止在中断上下文中再次开放 SIE。 */
	if ((r_sstatus() & (SSTATUS_SPP | SSTATUS_SIE)) != 0)
		return;
	intr_delivery_window();
}

static int kernel_work_checkpoint_mode(uint work_units, int cleanup)
{
	struct thread *t = curr_thread();
	struct thread_trap_cold *cold;
	int exit_requested;

	if (!kernel_work_running(t))
		return 0;
	cold = thread_trap_cold(t);
	if (cold->kernel_work_depth == 0)
		return 0;
	if (work_units >= KERNEL_WORK_BUDGET_UNITS - cold->kernel_work_units)
		cold->kernel_work_units = KERNEL_WORK_BUDGET_UNITS;
	else
		cold->kernel_work_units += work_units;
	exit_requested = !cleanup && proc_thread_exit_requested();
	// 缓冲区持有者须先完成短临界区；bget 只提供互斥，并非可睡眠锁。
	if (cold->bio_buffer_holds != 0 || cold->bio_fs_atomic_depth != 0)
		return exit_requested ? -1 : 0;
	if (!cleanup && cold->kernel_work_resumed) {
		cold->kernel_work_resumed = 0;
		return exit_requested ? -1 : 1;
	}
	if (!cold->kernel_resched_pending &&
	    cold->kernel_work_units < KERNEL_WORK_BUDGET_UNITS)
		return exit_requested ? -1 : 0;
	/* 先投递中断，设备等待者才有机会进入运行队列。 */
	kernel_work_irq_window(t);
	/* 没有竞争者时只开启下一批预算，避免把长调用切换给自己。 */
	if (!scheduler_has_runnable_peer()) {
		cold->kernel_resched_pending = 0;
		cold->kernel_work_units = 0;
		return exit_requested ? -1 : 0;
	}
	cold->kernel_resched_pending = 0;
	yield();
	if (!cleanup)
		cold->kernel_work_resumed = 0;
	return !cleanup && proc_thread_exit_requested() ? -1 : 1;
}

uint kernel_work_units_from_bytes(uint64 bytes)
{
	uint64 units;

	if (bytes == 0)
		return 0;
	units = bytes / KERNEL_WORK_BYTES_PER_UNIT;
	if (bytes % KERNEL_WORK_BYTES_PER_UNIT != 0)
		units++;
	if (units > KERNEL_WORK_BUDGET_UNITS)
		return KERNEL_WORK_BUDGET_UNITS;
	return (uint)units;
}

// 完成一轮调度返回 1；时间片仍有效返回 0；协同拆除要求线程退栈返回 -1。
int kernel_work_checkpoint(uint work_units)
{
	return kernel_work_checkpoint_mode(work_units, 0);
}

int kernel_work_checkpoint_bytes(uint64 bytes)
{
	return kernel_work_checkpoint(
		kernel_work_units_from_bytes(bytes));
}

// 拆除阶段已分离对象，即使进程要求线程退栈，也必须完成资源回收。
int kernel_work_checkpoint_cleanup(uint work_units)
{
	return kernel_work_checkpoint_mode(work_units, 1);
}

uint64 kernel_work_last_preemptions(struct thread *t)
{
	return t != 0 && t->trapframe != 0 ?
		       thread_trap_cold(t)->kernel_last_syscall_preemptions : 0;
}

static void kernel_work_end_mode(int cleanup, int terminal)
{
	struct thread *t = curr_thread();
	struct thread_trap_cold *cold;
	int outer;

	if (!kernel_work_running(t))
		return;
	cold = thread_trap_cold(t);
	if (cold->kernel_work_depth == 0)
		panic("kernel work depth underflow");
	outer = cold->kernel_work_depth == 1;
	if (terminal || outer) {
		/* 时钟中断只发布待调度位；短系统调用不反复读取周期计数，
		 * 仅积累足够工作或收到调度请求时进入慢路径。 */
		if (cold->kernel_resched_pending || cold->kernel_work_resumed ||
		    cold->kernel_work_units >= KERNEL_WORK_BUDGET_UNITS)
			(void)kernel_work_checkpoint_mode(0, cleanup);
		if (cold->kernel_work_measure_preemptions)
			cold->kernel_last_syscall_preemptions =
				cold->kernel_work_redispatches -
				cold->kernel_syscall_preemptions_start;
	}
	if (terminal)
		cold->kernel_work_depth = 0;
	else
		cold->kernel_work_depth--;
	if (terminal || outer) {
		cold->kernel_work_target_syscall_id = -1;
		cold->kernel_work_measure_preemptions = 0;
	}
}

void kernel_work_end(void)
{
	kernel_work_end_mode(0, 0);
}

void kernel_work_end_background(void)
{
	kernel_work_end_mode(0, 0);
}

void kernel_work_end_cleanup(void)
{
	kernel_work_end_mode(1, 1);
}
