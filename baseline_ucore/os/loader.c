#include "loader.h"
#include "defs.h"
#include "file.h"
#include "kernel_work.h"
#include "trap.h"

extern char INIT_PROC[];

void user_image_discard(struct user_image *image)
{
	if (image == 0 || image->pagetable == 0)
		return;
	if (image->shared_pages != 0)
		uvmunmap(image->pagetable, image->shared_base,
			 image->shared_pages, 0);
	uvmunmap(image->pagetable, TRAPFRAME, 1, 0);
	uvmunmap(image->pagetable, TRAMPOLINE, 1, 0);
	uvmfree(image->pagetable, image->max_page);
	memset(image, 0, sizeof(*image));
}

int user_image_build(struct inode *ip, uint64 trapframe_pa,
		     struct user_image *image)
{
	char *page;
	uint64 length;
	uint64 content_epoch;
	uint64 va_end;

	if (ip == 0 || image == 0 || trapframe_pa == 0)
		return -1;
	memset(image, 0, sizeof(*image));
	ivalid(ip);
	if (ip->type != T_FILE)
		return -1;
	length = ip->size;
	content_epoch = ip->content_epoch;
	if (length == 0 || length > MAXVA - BASE_ADDRESS)
		return -1;
	va_end = PGROUNDUP(BASE_ADDRESS + length);
	if (va_end < BASE_ADDRESS ||
	    va_end > USER_IMAGE_LIMIT - PAGE_SIZE - NTHREAD * USTACK_SIZE)
		return -1;

	image->pagetable = uvmcreate();
	if (image->pagetable == 0)
		return -1;
	image->entry = BASE_ADDRESS;
	image->ustack_base = va_end + PAGE_SIZE;

	for (uint64 va = BASE_ADDRESS, off = 0; va < va_end;
	     va += PAGE_SIZE, off += PAGE_SIZE) {
		uint want = MIN(PAGE_SIZE, length - off);

		page = kalloc();
		if (page == 0)
			goto fail;
		memset(page, 0, PAGE_SIZE);
		if (readi(ip, 0, (uint64)page, off, want) != (int)want) {
			kfree(page);
			goto fail;
		}
		if (mappages(image->pagetable, va, PAGE_SIZE, (uint64)page,
			     PTE_U | PTE_R | PTE_W | PTE_X) < 0) {
			kfree(page);
			goto fail;
		}
		image->max_page = (va + PAGE_SIZE) / PAGE_SIZE;
		if (kernel_work_checkpoint(KERNEL_WORK_PAGE_UNITS) < 0 ||
		    ip->content_epoch != content_epoch)
			goto fail;
	}

	if (uvmmap(image->pagetable, image->ustack_base,
		   USTACK_SIZE / PAGE_SIZE, PTE_U | PTE_R | PTE_W) < 0)
		goto fail;
	image->max_page =
		(image->ustack_base + USTACK_SIZE) / PAGE_SIZE;
	if (mappages(image->pagetable, TRAPFRAME, TRAP_PAGE_SIZE,
		     trapframe_pa, PTE_R | PTE_W) < 0)
		goto fail;
	return 0;

fail:
	user_image_discard(image);
	return -1;
}

// load all apps and init the corresponding `proc` structure.
int load_init_app()
{
	struct inode *ip = 0;
	struct user_image image;
	struct trapframe staged;
	char *argv[2];
	int argc;
	struct proc *p = allocproc();

	if (p == 0)
		return -1;
	if ((ip = namei(INIT_PROC)) == 0) {
		errorf("invalid init proc name\n");
		freeproc(p);
		return -1;
	}
	debugf("load init app %s", INIT_PROC);
	if (user_image_build(ip, (uint64)proc_trapframe(p, 0), &image) < 0) {
		iput(ip);
		freeproc(p);
		return -1;
	}
	iput(ip);
	argv[0] = INIT_PROC;
	argv[1] = NULL;
	memset(&staged, 0, sizeof(staged));
	staged.epc = image.entry;
	argc = push_argv_image(image.pagetable, image.ustack_base, &staged,
			       argv);
	if (argc < 0 || init_stdio(p) < 0) {
		user_image_discard(&image);
		freeproc(p);
		return -1;
	}
	proc_install_user_image(p, &image, &staged, 0);
	struct thread *t = &p->threads[0];
	t->trapframe->a0 = argc;
	t->state = RUNNABLE;
	add_task(t);
	return 0;
}
