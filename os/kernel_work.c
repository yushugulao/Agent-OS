#include "defs.h"
#include "kernel_work.h"
#include "timer.h"

#define KERNEL_WORK_QUANTUM_CYCLES (CPU_FREQ / TICKS_PER_SEC)

_Static_assert(KERNEL_WORK_QUANTUM_CYCLES > 0,
	       "kernel work quantum must be positive");

static int kernel_work_running(struct thread *t)
{
	return t != 0 && t->process != 0 && t->state == RUNNING &&
	       t->tid >= 0;
}

void kernel_work_reset(struct thread *t)
{
	if (t == 0)
		return;
	t->kernel_work_depth = 0;
	t->kernel_work_resumed = 0;
	t->kernel_resched_pending = 0;
	t->kernel_work_units = 0;
	t->kernel_slice_deadline = 0;
	t->kernel_work_redispatches = 0;
	t->kernel_syscall_preemptions_start = 0;
	t->kernel_last_syscall_preemptions = 0;
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

void kernel_work_begin(void)
{
	struct thread *t = curr_thread();

	if (!kernel_work_running(t))
		return;
	if (t->kernel_work_depth == (uint)-1)
		panic("kernel work depth overflow");
	if (t->kernel_slice_deadline == 0)
		kernel_work_on_dispatch(t);
	if (t->kernel_work_depth == 0)
		t->kernel_syscall_preemptions_start =
			t->kernel_work_redispatches;
	t->kernel_work_depth++;
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

void kernel_work_end(void)
{
	struct thread *t = curr_thread();

	if (!kernel_work_running(t))
		return;
	if (t->kernel_work_depth == 0)
		panic("kernel work depth underflow");
	if (t->kernel_work_depth == 1) {
		(void)kernel_work_checkpoint(0);
		t->kernel_last_syscall_preemptions =
			t->kernel_work_redispatches -
			t->kernel_syscall_preemptions_start;
	}
	t->kernel_work_depth--;
}
