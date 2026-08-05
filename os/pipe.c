#include "defs.h"
#include "kernel_work.h"
#include "proc.h"
#include "riscv.h"

static void pipe_wake_units(struct wait_queue *waiters, uint64 units)
{
	while (units-- != 0)
		if (!wait_queue_wake_one(waiters))
			break;
}

int pipealloc(struct file *f0, struct file *f1)
{
	struct pipe *pi;
	struct resource_account_handle account;
	enum resource_charge_class charge_class;

	pi = 0;
	if (f0 == 0 || f1 == 0 ||
	    !resource_account_handle_equal(f0->resource_account,
					   f1->resource_account) ||
	    f0->resource_reserved != f1->resource_reserved)
		goto bad;
	account = f0->resource_account;
	charge_class = f0->resource_reserved ? RESOURCE_CHARGE_RESERVED :
					       RESOURCE_CHARGE_ORDINARY;
	if ((pi = (struct pipe *)kalloc_account_page(account, charge_class)) == 0)
		goto bad;
	pi->page_account = account;
	pi->page_charge_class = charge_class;
	pi->readopen = 1;
	pi->writeopen = 1;
	pi->nwrite = 0;
	pi->nread = 0;
	wait_queue_init(&pi->read_waiters, WAIT_REASON_PIPE_READ);
	wait_queue_init(&pi->write_waiters, WAIT_REASON_PIPE_WRITE);
	f0->type = FD_PIPE;
	f0->inherit_class = FD_INHERIT_DELEGATE;
	f0->readable = 1;
	f0->writable = 0;
	f0->pipe = pi;
	f1->type = FD_PIPE;
	f1->inherit_class = FD_INHERIT_DELEGATE;
	f1->readable = 0;
	f1->writable = 1;
	f1->pipe = pi;
	return 0;
bad:
	if (pi)
		(void)kfree_account_page((char *)pi, account, charge_class);
	return -1;
}

void pipeclose(struct pipe *pi, int writable)
{
	int enabled = intr_save();
	int release;

	if (writable) {
		pi->writeopen = 0;
		wait_queue_wake_all(&pi->read_waiters);
	} else {
		pi->readopen = 0;
		wait_queue_wake_all(&pi->write_waiters);
	}
	release = pi->readopen == 0 && pi->writeopen == 0;
	intr_restore(enabled);
	if (release) {
		(void)kfree_account_page((char *)pi, pi->page_account,
					 pi->page_charge_class);
	}
}

int pipewrite(struct pipe *pi, uint64 addr, uint64 n)
{
	uint64 w = 0, size, user_addr;
	struct proc *p = curr_proc();

	if (n == 0)
		return 0;
	while (w < n) {
		int enabled = intr_save();

		if (proc_thread_exit_requested())
			goto interrupted;
		if (pi->readopen == 0)
			goto interrupted;
		if (pi->nwrite == pi->nread + PIPESIZE) { // DOC: pipewrite-full
			if (wait_queue_sleep_irq(&pi->write_waiters) !=
			    WAIT_QUEUE_OK) {
				intr_restore(enabled);
				return w == 0 ? -1 : (int)w;
			}
			intr_restore(enabled);
			continue;
		}
		size = MIN(MIN(n - w,
			       pi->nread + PIPESIZE - pi->nwrite),
			   PIPESIZE - (pi->nwrite % PIPESIZE));
		if (checked_user_offset(addr, w, 1, &user_addr) < 0 ||
		    copyin(p->pagetable,
			   &pi->data[pi->nwrite % PIPESIZE], user_addr,
			   size) < 0) {
			intr_restore(enabled);
			return w == 0 ? -1 : (int)w;
		}
		pi->nwrite += size;
		w += size;
		pipe_wake_units(&pi->read_waiters, size);
		intr_restore(enabled);
		if (kernel_work_checkpoint_bytes(size) < 0)
			return (int)w;
		continue;
interrupted:
		intr_restore(enabled);
		return w == 0 ? -1 : (int)w;
	}
	return (int)w;
}

int piperead(struct pipe *pi, uint64 addr, uint64 n)
{
	uint64 r = 0, size, user_addr;
	struct proc *p = curr_proc();

	if (n == 0)
		return 0;
	int enabled = intr_save();
	while (pi->nread == pi->nwrite) {
		if (proc_thread_exit_requested())
			goto interrupted;
		if (!pi->writeopen)
			goto interrupted;
		if (wait_queue_sleep_irq(&pi->read_waiters) != WAIT_QUEUE_OK)
			goto interrupted;
	}
	if (proc_thread_exit_requested())
		goto interrupted;
	while (r < n) { // DOC: piperead-copy
		if (pi->nread == pi->nwrite)
			break;
		size = MIN(MIN(n - r, pi->nwrite - pi->nread),
			   PIPESIZE - (pi->nread % PIPESIZE));
		if (checked_user_offset(addr, r, 1, &user_addr) < 0 ||
		    copyout(p->pagetable, user_addr,
			    &pi->data[pi->nread % PIPESIZE], size) < 0)
			goto interrupted;
		pi->nread += size;
		r += size;
		pipe_wake_units(&pi->write_waiters, size);
		intr_restore(enabled);
		if (kernel_work_checkpoint_bytes(size) < 0)
			return (int)r;
		enabled = intr_save();
	}
	intr_restore(enabled);
	return (int)r;

interrupted:
	intr_restore(enabled);
	return r == 0 ? -1 : (int)r;
}
