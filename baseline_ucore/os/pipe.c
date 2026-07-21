#include "defs.h"
#include "kernel_work.h"
#include "proc.h"
#include "riscv.h"

int pipealloc(struct file *f0, struct file *f1)
{
	struct pipe *pi;
	pi = 0;
	if ((pi = (struct pipe *)kalloc()) == 0)
		goto bad;
	pi->readopen = 1;
	pi->writeopen = 1;
	pi->nwrite = 0;
	pi->nread = 0;
	f0->type = FD_PIPE;
	f0->readable = 1;
	f0->writable = 0;
	f0->pipe = pi;
	f1->type = FD_PIPE;
	f1->readable = 0;
	f1->writable = 1;
	f1->pipe = pi;
	return 0;
bad:
	if (pi)
		kfree((char *)pi);
	return -1;
}

void pipeclose(struct pipe *pi, int writable)
{
	if (writable) {
		pi->writeopen = 0;
	} else {
		pi->readopen = 0;
	}
	if (pi->readopen == 0 && pi->writeopen == 0) {
		kfree((char *)pi);
	}
}

int pipewrite(struct pipe *pi, uint64 addr, uint64 n)
{
	uint64 w = 0, size, user_addr;
	struct proc *p = curr_proc();

	if (n == 0)
		return 0;
	while (w < n) {
		if (proc_thread_exit_requested())
			return w == 0 ? -1 : (int)w;
		if (pi->readopen == 0)
			return w == 0 ? -1 : (int)w;
		if (pi->nwrite == pi->nread + PIPESIZE) { // DOC: pipewrite-full
			yield();
		} else {
			size = MIN(MIN(n - w,
				       pi->nread + PIPESIZE - pi->nwrite),
				   PIPESIZE - (pi->nwrite % PIPESIZE));
			if (checked_user_offset(addr, w, 1, &user_addr) < 0 ||
			    copyin(p->pagetable,
				   &pi->data[pi->nwrite % PIPESIZE], user_addr,
				   size) < 0)
				return w == 0 ? -1 : (int)w;
			pi->nwrite += size;
			w += size;
			if (kernel_work_checkpoint((uint)size) < 0)
				return (int)w;
		}
	}
	return (int)w;
}

int piperead(struct pipe *pi, uint64 addr, uint64 n)
{
	uint64 r = 0, size, user_addr;
	struct proc *p = curr_proc();

	if (n == 0)
		return 0;
	while (pi->nread == pi->nwrite) {
		if (proc_thread_exit_requested())
			return -1;
		if (pi->writeopen)
			yield();
		else
			return -1;
	}
	if (proc_thread_exit_requested())
		return -1;
	while (r < n) { // DOC: piperead-copy
		if (pi->nread == pi->nwrite)
			break;
		size = MIN(MIN(n - r, pi->nwrite - pi->nread),
			   PIPESIZE - (pi->nread % PIPESIZE));
		if (checked_user_offset(addr, r, 1, &user_addr) < 0 ||
		    copyout(p->pagetable, user_addr,
			    &pi->data[pi->nread % PIPESIZE], size) < 0)
			return r == 0 ? -1 : (int)r;
		pi->nread += size;
		r += size;
		if (kernel_work_checkpoint((uint)size) < 0)
			return (int)r;
	}
	return (int)r;
}
