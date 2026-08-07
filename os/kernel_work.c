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
	return t != 0 && t->process != 0 && t->state == RUNNING &&
	       t->tid >= 0;
}

void kernel_work_reset(struct thread *t)
{
	if (t == 0)
		return;
	bio_request_abort_thread(t);
	t->kernel_work_depth = 0;
	t->kernel_work_resumed = 0;
	t->kernel_resched_pending = 0;
	t->kernel_work_units = 0;
	t->kernel_work_redispatches = 0;
	t->kernel_syscall_preemptions_start = 0;
	t->kernel_last_syscall_preemptions = 0;
	t->kernel_work_generation = 0;
	t->kernel_work_target_syscall_id = -1;
	t->kernel_work_measure_preemptions = 0;
	t->io_request_flags = 0;
	t->io_request_depth = 0;
	t->io_request_owner = 0;
	t->io_request_class = 0;
	t->io_request_reservation = 0;
	t->io_request_device_reservation = 0;
	t->io_request_transfers = 0;
	t->bio_buffer_holds = 0;
	t->bio_fs_atomic_depth = 0;
}

// 时间片在调度时开始，而不是在 syscall 入口刷新，避免短调用流无限续期。
void kernel_work_on_dispatch(struct thread *t)
{
	if (t == 0)
		return;
	t->kernel_work_resumed = t->kernel_work_depth != 0;
	if (t->kernel_work_resumed)
		t->kernel_work_redispatches++;
	t->kernel_resched_pending = 0;
	t->kernel_work_units = 0;
}

static void kernel_work_begin_scope(int syscall_id, int measure_preemptions)
{
	struct thread *t = curr_thread();

	if (!kernel_work_running(t))
		return;
	if (t->kernel_work_depth == (uint)-1)
		panic("kernel work depth overflow");
	if (t->kernel_work_depth == 0) {
		if (t->kernel_work_generation == (uint64)-1)
			panic("kernel work generation exhausted");
		t->kernel_work_generation++;
		t->kernel_syscall_preemptions_start =
			t->kernel_work_redispatches;
		t->kernel_work_target_syscall_id = syscall_id;
		t->kernel_work_measure_preemptions = measure_preemptions;
		if (measure_preemptions)
			t->kernel_last_syscall_preemptions = 0;
	}
	t->kernel_work_depth++;
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

	/* 终止退出优先复用外层 syscall 的内核工作时间片。 */
	if (!kernel_work_running(t) || t->kernel_work_depth != 0)
		return;
	kernel_work_begin_scope(-1, 0);
}

void kernel_work_request_resched(void)
{
	struct thread *t = curr_thread();

	if (kernel_work_running(t) && t->kernel_work_depth != 0)
		t->kernel_resched_pending = 1;
}

/*
 * syscall 入口会关闭 SIE。若唯一竞争者正等待设备完成，单纯查询运行队列
 * 永远看不到它。只在已提交原子批次的慢速安全点短暂开放中断，让设备和
 * 时钟处理程序发布唤醒/need_resched；短 syscall 不经过这里，也不多写 CSR。
 */
static void kernel_work_irq_window(struct thread *t)
{
	if (t->kernel_work_target_syscall_id < 0)
		return;
	/* SPP=1 表示正在处理内核陷阱，禁止在中断上下文中再次开放 SIE。 */
	if ((r_sstatus() & (SSTATUS_SPP | SSTATUS_SIE)) != 0)
		return;
	intr_delivery_window();
}

static int kernel_work_checkpoint_mode(uint work_units, int cleanup)
{
	struct thread *t = curr_thread();
	int exit_requested;

	if (!kernel_work_running(t) || t->kernel_work_depth == 0)
		return 0;
	if (work_units >= KERNEL_WORK_BUDGET_UNITS - t->kernel_work_units)
		t->kernel_work_units = KERNEL_WORK_BUDGET_UNITS;
	else
		t->kernel_work_units += work_units;
	exit_requested = !cleanup && proc_thread_exit_requested();
	// 缓冲区持有者须先完成短临界区；bget 只提供互斥，并非可睡眠锁。
	if (t->bio_buffer_holds != 0 || t->bio_fs_atomic_depth != 0)
		return exit_requested ? -1 : 0;
	if (!cleanup && t->kernel_work_resumed) {
		t->kernel_work_resumed = 0;
		return exit_requested ? -1 : 1;
	}
	if (!t->kernel_resched_pending &&
	    t->kernel_work_units < KERNEL_WORK_BUDGET_UNITS)
		return exit_requested ? -1 : 0;
	/* 先投递 IRQ，设备等待者才有机会进入运行队列。 */
	kernel_work_irq_window(t);
	/* 没有竞争者时只开启下一批预算，避免把长调用切换给自己。 */
	if (!scheduler_has_runnable_peer()) {
		t->kernel_resched_pending = 0;
		t->kernel_work_units = 0;
		return exit_requested ? -1 : 0;
	}
	t->kernel_resched_pending = 0;
	yield();
	if (!cleanup)
		t->kernel_work_resumed = 0;
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
	return t != 0 ? t->kernel_last_syscall_preemptions : 0;
}

static void kernel_work_end_mode(int cleanup, int terminal)
{
	struct thread *t = curr_thread();
	int outer;

	if (!kernel_work_running(t))
		return;
	if (t->kernel_work_depth == 0)
		panic("kernel work depth underflow");
	outer = t->kernel_work_depth == 1;
	if (terminal || outer) {
		/*
		 * 仿照 Linux 的 need_resched：时钟中断只发布待调度位，普通
		 * 短 syscall 不再反复读取 cycle。只有真的积累了工作或收到
		 * 调度请求时才进入慢路径。
		 */
		if (t->kernel_resched_pending || t->kernel_work_resumed ||
		    t->kernel_work_units >= KERNEL_WORK_BUDGET_UNITS)
			(void)kernel_work_checkpoint_mode(0, cleanup);
		if (t->kernel_work_measure_preemptions)
			t->kernel_last_syscall_preemptions =
				t->kernel_work_redispatches -
				t->kernel_syscall_preemptions_start;
	}
	if (terminal)
		t->kernel_work_depth = 0;
	else
		t->kernel_work_depth--;
	if (terminal || outer) {
		t->kernel_work_target_syscall_id = -1;
		t->kernel_work_measure_preemptions = 0;
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
