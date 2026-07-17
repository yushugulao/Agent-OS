#include "loader.h"
#include "agent.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "trap.h"
#include "vfs_security.h"

extern char INIT_PROC[];

static struct inode *init_image_lookup(char *path)
{
	struct inode *ip;
	int status;

	ip = namei_policy_status(path, VFS_POLICY_WORKFLOW, &status);
	if (status == FS_LOOKUP_FOUND || status == FS_LOOKUP_ERROR)
		return ip;
	return namei_policy_status(path, VFS_POLICY_PUBLIC, &status);
}

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
	uint64 va_end;
	int perm;
	struct vfs_cred kernel_cred;

	if (ip == 0 || image == 0 || trapframe_pa == 0)
		return -1;
	memset(image, 0, sizeof(*image));
	ivalid(ip);
	if (!vfs_inode_label_valid(ip) ||
	    !exec_policy_inode_layout_valid(ip) ||
	    !vfs_exec_profile_valid(ip->vfs_exec_profile))
		return -1;
	vfs_cred_kernel(&kernel_cred);
	image->exec_dev = ip->dev;
	image->exec_inum = ip->inum;
	image->exec_flags = ip->exec_flags;
	image->exec_generation = ip->exec_generation;
	image->exec_role_mask = ip->exec_role_mask;
	image->exec_layout_version = ip->exec_layout_version;
	image->exec_rw_offset = ip->exec_rw_offset;
	image->vfs_exec_profile = ip->vfs_exec_profile;
	image->vfs_exec_incarnation = ip->vfs_incarnation;
	length = ip->size;
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
		if (readi(ip, &kernel_cred, 0, (uint64)page, off, want) !=
		    (int)want) {
			kfree(page);
			goto fail;
		}
		perm = PTE_U | PTE_R;
		perm |= off < image->exec_rw_offset ? PTE_X : PTE_W;
		if (mappages(image->pagetable, va, PAGE_SIZE, (uint64)page,
			     perm) < 0) {
			kfree(page);
			goto fail;
		}
		image->max_page = (va + PAGE_SIZE) / PAGE_SIZE;
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
	if ((ip = init_image_lookup(INIT_PROC)) == 0) {
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
	if (exec_policy_process_bootstrap(p))
		agent_authority_bootstrap(p);
	struct thread *t = &p->threads[0];
	t->trapframe->a0 = argc;
	t->state = RUNNABLE;
	add_task(t);
	return 0;
}
