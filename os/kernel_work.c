#include "defs.h"
#include "bio.h"
#include "kernel_work.h"
#include "timer.h"

#define KERNEL_WORK_QUANTUM_CYCLES (CPU_FREQ / TICKS_PER_SEC)

_Static_assert(KERNEL_WORK_QUANTUM_CYCLES > 0,
	       "kernel work quantum must be positive");

static uint64 kernel_work_receipt_next_generation;
static uint64 kernel_work_timer_epoch;

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
	t->kernel_slice_deadline = 0;
	t->kernel_work_redispatches = 0;
	t->kernel_syscall_preemptions_start = 0;
	t->kernel_last_syscall_preemptions = 0;
	t->kernel_receipt_generation = 0;
	t->kernel_receipt_completion_timer_epoch = 0;
	t->kernel_receipt_syscall_id = -1;
	t->kernel_work_target_syscall_id = -1;
	t->kernel_work_publish_receipt = 0;
	t->io_request_depth = 0;
	t->io_request_owner = 0;
	t->io_request_class = 0;
	t->io_request_reservation = 0;
	t->io_request_device_reservation = 0;
	t->io_request_transfers = 0;
	t->bio_buffer_holds = 0;
	t->bio_fs_atomic_depth = 0;
}

// A dispatch, rather than a syscall entry, starts a new slice. Otherwise a
// process could refresh its budget forever with a stream of short syscalls.
void kernel_work_on_dispatch(struct thread *t)
{
	if (t == 0)
		return;
	t->kernel_work_resumed = t->kernel_work_depth != 0;
	if (t->kernel_work_resumed)
		t->kernel_work_redispatches++;
	t->kernel_slice_deadline = get_cycle() + KERNEL_WORK_QUANTUM_CYCLES;
	t->kernel_resched_pending = 0;
	t->kernel_work_units = 0;
}

static void kernel_work_begin_scope(int syscall_id, int publish_receipt)
{
	struct thread *t = curr_thread();

	if (!kernel_work_running(t))
		return;
	if (t->kernel_work_depth == (uint)-1)
		panic("kernel work depth overflow");
	if (t->kernel_slice_deadline == 0)
		kernel_work_on_dispatch(t);
	if (t->kernel_work_depth == 0) {
		t->kernel_syscall_preemptions_start =
			t->kernel_work_redispatches;
		t->kernel_work_target_syscall_id = syscall_id;
		t->kernel_work_publish_receipt = publish_receipt;
	}
	t->kernel_work_depth++;
}

void kernel_work_begin(void)
{
	kernel_work_begin_scope(-1, 0);
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

	/* A terminal exit reuses an enclosing syscall slice when one exists. */
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

static int kernel_work_checkpoint_mode(uint work_units, int cleanup)
{
	struct thread *t = curr_thread();
	uint64 now;
	int exit_requested;

	if (!kernel_work_running(t) || t->kernel_work_depth == 0)
		return 0;
	if (work_units >= KERNEL_WORK_BUDGET_UNITS - t->kernel_work_units)
		t->kernel_work_units = KERNEL_WORK_BUDGET_UNITS;
	else
		t->kernel_work_units += work_units;
	exit_requested = !cleanup && proc_thread_exit_requested();
	// Buffer holders must finish their small critical section before a
	// scheduler hand-off; bget provides exclusion, not a sleepable lock.
	if (t->bio_buffer_holds != 0 || t->bio_fs_atomic_depth != 0)
		return exit_requested ? -1 : 0;
	if (!cleanup && t->kernel_work_resumed) {
		t->kernel_work_resumed = 0;
		return exit_requested ? -1 : 1;
	}
	now = get_cycle();
	if (!t->kernel_resched_pending &&
	    t->kernel_work_units < KERNEL_WORK_BUDGET_UNITS &&
	    now < t->kernel_slice_deadline)
		return exit_requested ? -1 : 0;
	t->kernel_resched_pending = 0;
	yield();
	if (!cleanup)
		t->kernel_work_resumed = 0;
	return !cleanup && proc_thread_exit_requested() ? -1 : 1;
}

// Returns one after a scheduling round, zero if the slice remains valid, and
// minus one when cooperative process teardown asks this thread to unwind.
int kernel_work_checkpoint(uint work_units)
{
	return kernel_work_checkpoint_mode(work_units, 0);
}

// Teardown has already detached its objects, so it must finish reclaiming
// them even after the process has requested that this thread unwind.
int kernel_work_checkpoint_cleanup(uint work_units)
{
	return kernel_work_checkpoint_mode(work_units, 1);
}

static void kernel_work_publish_receipt(struct thread *t)
{
	int enabled = intr_save();

	if (kernel_work_receipt_next_generation == (uint64)-1)
		panic("kernel work receipt generation exhausted");
	kernel_work_receipt_next_generation++;
	t->kernel_last_syscall_preemptions =
		t->kernel_work_redispatches -
		t->kernel_syscall_preemptions_start;
	t->kernel_receipt_generation = kernel_work_receipt_next_generation;
	t->kernel_receipt_completion_timer_epoch = kernel_work_timer_epoch;
	t->kernel_receipt_syscall_id = t->kernel_work_target_syscall_id;
	intr_restore(enabled);
}

void kernel_work_timer_advance(void)
{
	int enabled = intr_save();

	if (kernel_work_timer_epoch == (uint64)-1)
		panic("kernel work timer epoch exhausted");
	kernel_work_timer_epoch++;
	intr_restore(enabled);
}

int kernel_work_receipt_snapshot(struct thread *t,
				 struct kernel_work_receipt *receipt)
{
	int enabled;

	if (t == 0 || receipt == 0)
		return -1;
	enabled = intr_save();
	if (t->kernel_receipt_generation == 0) {
		intr_restore(enabled);
		return -1;
	}
	memset(receipt, 0, sizeof(*receipt));
	receipt->version = KERNEL_WORK_RECEIPT_ABI_VERSION;
	receipt->struct_size = sizeof(*receipt);
	receipt->generation = t->kernel_receipt_generation;
	receipt->owner_generation = t->identity_generation;
	receipt->preemptions = t->kernel_last_syscall_preemptions;
	receipt->completion_timer_epoch =
		t->kernel_receipt_completion_timer_epoch;
	receipt->observed_timer_epoch = kernel_work_timer_epoch;
	receipt->owner_tid = t->tid;
	receipt->owner_pid = t->process != 0 ? t->process->pid : -1;
	receipt->owner_kind = KERNEL_WORK_RECEIPT_OWNER_THREAD;
	receipt->kind = KERNEL_WORK_RECEIPT_KIND_SYSCALL;
	receipt->syscall_id = t->kernel_receipt_syscall_id;
	intr_restore(enabled);
	return 0;
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
		(void)kernel_work_checkpoint_mode(0, cleanup);
		if (t->kernel_work_publish_receipt)
			kernel_work_publish_receipt(t);
	}
	if (terminal)
		t->kernel_work_depth = 0;
	else
		t->kernel_work_depth--;
	if (terminal || outer) {
		t->kernel_work_target_syscall_id = -1;
		t->kernel_work_publish_receipt = 0;
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
