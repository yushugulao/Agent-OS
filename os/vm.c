#include "vm.h"
#include "defs.h"
#include "kernel_work.h"
#include "plic.h"
#include "riscv.h"

pagetable_t kernel_pagetable;

extern char e_text[]; // kernel.ld sets this to end of kernel code.
extern char trampoline[];

#define VM_ACCOUNT_BINDING_CAP (2 * NPROC + 1)

struct vm_account_binding {
	pagetable_t root;
	struct resource_account_handle account;
	enum resource_charge_class charge_class;
};

static struct vm_account_binding vm_accounts[VM_ACCOUNT_BINDING_CAP];

static int vm_account_get(pagetable_t root,
			  struct resource_account_handle *account,
			  enum resource_charge_class *charge_class)
{
	int enabled = intr_save();

	for (uint i = 0; i < VM_ACCOUNT_BINDING_CAP; i++) {
		if (vm_accounts[i].root != root)
			continue;
		*account = vm_accounts[i].account;
		*charge_class = vm_accounts[i].charge_class;
		intr_restore(enabled);
		return 0;
	}
	intr_restore(enabled);
	return -1;
}

static int vm_account_bind(pagetable_t root,
			   struct resource_account_handle account,
			   enum resource_charge_class charge_class)
{
	int enabled = intr_save();
	int free_slot = -1;

	for (uint i = 0; i < VM_ACCOUNT_BINDING_CAP; i++) {
		if (vm_accounts[i].root == root)
			goto fail;
		if (vm_accounts[i].root == 0 && free_slot < 0)
			free_slot = (int)i;
	}
	if (free_slot < 0)
		goto fail;
	vm_accounts[free_slot].root = root;
	vm_accounts[free_slot].account = account;
	vm_accounts[free_slot].charge_class = charge_class;
	intr_restore(enabled);
	return 0;
fail:
	intr_restore(enabled);
	return -1;
}

static int vm_account_unbind(pagetable_t root)
{
	int enabled = intr_save();

	for (uint i = 0; i < VM_ACCOUNT_BINDING_CAP; i++) {
		if (vm_accounts[i].root != root)
			continue;
		memset(&vm_accounts[i], 0, sizeof(vm_accounts[i]));
		intr_restore(enabled);
		return 0;
	}
	intr_restore(enabled);
	return -1;
}

void *uvm_page_alloc(pagetable_t root)
{
	struct resource_account_handle account;
	enum resource_charge_class charge_class;

	if (vm_account_get(root, &account, &charge_class) < 0)
		return 0;
	return kalloc_account_page(account, charge_class);
}

static void *vm_page_alloc_or_raw(pagetable_t root)
{
	struct resource_account_handle account;
	enum resource_charge_class charge_class;

	if (vm_account_get(root, &account, &charge_class) < 0)
		return !kalloc_physical_policy_ready() ||
			       root == kernel_pagetable ?
			       kalloc_system_page() : 0;
	return kalloc_account_page(account, charge_class);
}

int uvm_page_free(pagetable_t root, void *page)
{
	struct resource_account_handle account;
	enum resource_charge_class charge_class;

	if (vm_account_get(root, &account, &charge_class) < 0)
		return -1;
	return kfree_account_page(page, account, charge_class);
}

// Make a direct-map page table for the kernel.
pagetable_t kvmmake()
{
	pagetable_t kpgtbl;
	kpgtbl = (pagetable_t)kalloc_system_page();
	if (kpgtbl == 0)
		panic("kernel page table allocation");
	memset(kpgtbl, 0, PGSIZE);
	// virtio mmio disk interface
	kvmmap(kpgtbl, VIRTIO0, VIRTIO0, PGSIZE, PTE_R | PTE_W);
	// PLIC
	kvmmap(kpgtbl, PLIC, PLIC, 0x400000, PTE_R | PTE_W);
	// map kernel text executable and read-only.
	kvmmap(kpgtbl, KERNBASE, KERNBASE, (uint64)e_text - KERNBASE,
	       PTE_R | PTE_X);
	// map kernel data and the physical RAM we'll make use of.
	kvmmap(kpgtbl, (uint64)e_text, (uint64)e_text, PHYSTOP - (uint64)e_text,
	       PTE_R | PTE_W);
	// Each kernel stack has its own virtual mapping and an unmapped guard page.
	proc_mapstacks(kpgtbl);
	kvmmap(kpgtbl, TRAMPOLINE, (uint64)trampoline, PGSIZE, PTE_R | PTE_X);
	return kpgtbl;
}

// Initialize the one kernel_pagetable
// Switch h/w page table register to the kernel's page table,
// and enable paging.
void kvm_init()
{
	kernel_pagetable = kvmmake();
	w_satp(MAKE_SATP(kernel_pagetable));
	sfence_vma();
	infof("enable pageing at %p", r_satp());
}

// Return the address of the PTE in page table pagetable
// that corresponds to virtual address va.  If alloc!=0,
// create any required page-table pages.
//
// The risc-v Sv39 scheme has three levels of page-table
// pages. A page-table page contains 512 64-bit PTEs.
// A 64-bit virtual address is split into five fields:
//   39..63 -- must be zero.
//   30..38 -- 9 bits of level-2 index.
//   21..29 -- 9 bits of level-1 index.
//   12..20 -- 9 bits of level-0 index.
//    0..11 -- 12 bits of byte offset within the page.
pte_t *walk(pagetable_t pagetable, uint64 va, int alloc)
{
	pagetable_t root = pagetable;
	if (va >= MAXVA)
		panic("walk");

	for (int level = 2; level > 0; level--) {
		pte_t *pte = &pagetable[PX(level, va)];
		if (*pte & PTE_V) {
			pagetable = (pagetable_t)PTE2PA(*pte);
		} else {
			if (!alloc ||
			    (pagetable = (pde_t *)vm_page_alloc_or_raw(root)) == 0)
				return 0;
			memset(pagetable, 0, PGSIZE);
			*pte = PA2PTE(pagetable) | PTE_V;
		}
	}
	return &pagetable[PX(0, va)];
}

static pte_t *walk_user_leaf(pagetable_t pagetable, uint64 va, int perm)
{
	pte_t *pte;
	uint64 flags;

	if (pagetable == 0 || va >= MAXVA ||
	    (perm & ~(PTE_R | PTE_W | PTE_X)) != 0)
		return 0;
	pte = walk(pagetable, va, 0);
	if (pte == 0)
		return 0;
	flags = PTE_FLAGS(*pte);
	if ((flags & (PTE_V | PTE_U)) != (PTE_V | PTE_U))
		return 0;
	if ((flags & (PTE_W | PTE_X)) == (PTE_W | PTE_X))
		return 0;
	if ((flags & (PTE_R | PTE_W | PTE_X)) == 0)
		return 0;
	if ((flags & PTE_W) != 0 && (flags & PTE_R) == 0)
		return 0;
	if ((flags & perm) != (uint64)perm)
		return 0;
	return pte;
}

// Look up a user leaf and return its page-aligned physical address.
uint64 walkaddr(pagetable_t pagetable, uint64 va)
{
	pte_t *pte = walk_user_leaf(pagetable, va, 0);

	return pte == 0 ? 0 : PTE2PA(*pte);
}

// Validate every page touched by a user range before accessing it.
int user_range_check(pagetable_t pagetable, uint64 addr, uint64 len, int perm)
{
	uint64 page, last;

	if (len == 0)
		return 0;
	if (addr >= MAXVA || len > MAXVA - addr)
		return -1;
	if ((perm & ~(PTE_R | PTE_W | PTE_X)) != 0)
		return -1;

	page = PGROUNDDOWN(addr);
	last = PGROUNDDOWN(addr + len - 1);
	for (;;) {
		if (walk_user_leaf(pagetable, page, perm) == 0)
			return -1;
		if (page == last)
			break;
		page += PGSIZE;
	}
	return 0;
}

// Compute base + index * size without wrapping out of user address space.
int checked_user_offset(uint64 base, uint64 index, uint64 size, uint64 *addr)
{
	uint64 offset, result;
	uint64 max = ~(uint64)0;

	if (addr == 0 || base >= MAXVA)
		return -1;
	if (size != 0 && index > max / size)
		return -1;
	offset = index * size;
	if (offset > max - base)
		return -1;
	result = base + offset;
	if (result >= MAXVA)
		return -1;
	*addr = result;
	return 0;
}

// Add a mapping to the kernel page table.
// only used when booting.
// does not flush TLB or enable paging.
void kvmmap(pagetable_t kpgtbl, uint64 va, uint64 pa, uint64 sz, int perm)
{
	if (mappages(kpgtbl, va, sz, pa, perm) != 0)
		panic("kvmmap");
}

// Create PTEs for virtual addresses starting at va that refer to
// physical addresses starting at pa. va and size might not
// be page-aligned. Returns 0 on success, -1 if walk() couldn't
// allocate a needed page-table page.
int mappages(pagetable_t pagetable, uint64 va, uint64 size, uint64 pa, int perm)
{
	uint64 a, last;
	pte_t *pte;

	if ((perm & (PTE_W | PTE_X)) == (PTE_W | PTE_X))
		return -1;
	a = PGROUNDDOWN(va);
	last = PGROUNDDOWN(va + size - 1);
	for (;;) {
		if ((pte = walk(pagetable, a, 1)) == 0) {
			errorf("pte invalid, va = %p", a);
			return -1;
		}
		if (*pte & PTE_V) {
			errorf("remap");
			return -1;
		}
		*pte = PA2PTE(pa) | perm | PTE_V;
		if (a == last)
			break;
		a += PGSIZE;
		pa += PGSIZE;
	}
	return 0;
}

int uvmmap(pagetable_t pagetable, uint64 va, uint64 npages, int perm)
{
	uint64 mapped;
	char *mem;

	if (npages == 0)
		return 0;
	if (pagetable == 0 || (va % PGSIZE) != 0 || va >= MAXVA ||
	    npages > (MAXVA - va) / PGSIZE)
		return -1;

	for (mapped = 0; mapped < npages; ++mapped) {
		mem = uvm_page_alloc(pagetable);
		if (mem == 0)
			goto fail;
		memset(mem, 0, PGSIZE);
		if (mappages(pagetable, va + mapped * PGSIZE, PGSIZE,
			     (uint64)mem, perm) < 0) {
			(void)uvm_page_free(pagetable, mem);
			goto fail;
		}
	}
	return 0;

fail:
	if (mapped != 0)
		uvmunmap(pagetable, va, mapped, 1);
	return -1;
}

// Remove npages of mappings starting from va. va must be
// page-aligned. The mappings must exist.
// Optionally free the physical memory.
void uvmunmap(pagetable_t pagetable, uint64 va, uint64 npages, int do_free)
{
	uint64 a;
	pte_t *pte;

	if ((va % PGSIZE) != 0)
		panic("uvmunmap: not aligned");

	for (a = va; a < va + npages * PGSIZE; a += PGSIZE) {
		if ((pte = walk(pagetable, a, 0)) == 0)
			continue;
		if ((*pte & PTE_V) != 0) {
			if (PTE_FLAGS(*pte) == PTE_V)
				panic("uvmunmap: not a leaf");
			if (do_free) {
				uint64 pa = PTE2PA(*pte);
				if (uvm_page_free(pagetable, (void *)pa) < 0)
					panic("uvmunmap account");
			}
		}
		*pte = 0;
	}
}

/*
 * Rollback and heap shrink must refund intermediate page-table pages as well
 * as leaves. Generic uvmunmap leaves them for freewalk(), which would let a
 * grow/shrink loop pin physical-account quota until process exit.
 */
static int uvm_prune_empty_walk(
	pagetable_t pagetable, int level,
	struct resource_account_handle account,
	enum resource_charge_class charge_class)
{
	int empty = 1;

	for (int i = 0; i < 512; i++) {
		pte_t pte = pagetable[i];

		if ((pte & PTE_V) == 0)
			continue;
		if ((pte & (PTE_R | PTE_W | PTE_X)) != 0) {
			empty = 0;
			continue;
		}
		if (level <= 0)
			panic("uvm prune level");
		pagetable_t child = (pagetable_t)PTE2PA(pte);
		if (uvm_prune_empty_walk(child, level - 1, account,
					 charge_class)) {
			pagetable[i] = 0;
			if (kfree_account_page(child, account, charge_class) < 0)
				panic("uvm prune account");
		} else {
			empty = 0;
		}
		(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_PAGE_UNITS);
	}
	return empty;
}

void uvm_unmap_reclaim(pagetable_t pagetable, uint64 va, uint64 npages)
{
	struct resource_account_handle account;
	enum resource_charge_class charge_class;

	if (pagetable == 0 || (va % PGSIZE) != 0 || va >= MAXVA ||
	    npages > (MAXVA - va) / PGSIZE)
		panic("uvm reclaim range");
	for (uint64 page = 0; page < npages; page++) {
		uvmunmap(pagetable, va + page * PGSIZE, 1, 1);
		(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_PAGE_UNITS);
	}
	if (vm_account_get(pagetable, &account, &charge_class) < 0)
		panic("uvm reclaim account");
	(void)uvm_prune_empty_walk(pagetable, 2, account, charge_class);
}

// create an empty user page table.
// returns 0 if out of memory.
pagetable_t uvmcreate_account(struct resource_account_handle account,
			      enum resource_charge_class charge_class)
{
	pagetable_t pagetable;
	pagetable = (pagetable_t)kalloc_account_page(account, charge_class);
	if (pagetable == 0) {
		errorf("uvmcreate: kalloc error");
		return 0;
	}
	memset(pagetable, 0, PGSIZE);
	if (vm_account_bind(pagetable, account, charge_class) < 0) {
		(void)kfree_account_page(pagetable, account, charge_class);
		return 0;
	}
	if (mappages(pagetable, TRAMPOLINE, PAGE_SIZE, (uint64)trampoline,
		     PTE_R | PTE_X) < 0) {
		uvmfree(pagetable, 0);
		return 0;
	}
	return pagetable;
}

// Recursively free page-table pages.
// All leaf mappings must already have been removed.
static int freewalk(
	pagetable_t pagetable, struct resource_account_handle account,
	enum resource_charge_class charge_class)
{
	// there are 2^9 = 512 PTEs in a page table.
	for (int i = 0; i < 512; i++) {
		pte_t pte = pagetable[i];
		if ((pte & PTE_V) && (pte & (PTE_R | PTE_W | PTE_X)) == 0) {
			// this PTE points to a lower-level page table.
			uint64 child = PTE2PA(pte);
			if (freewalk((pagetable_t)child, account,
				     charge_class) < 0)
				return -1;
			pagetable[i] = 0;
		} else if (pte & PTE_V) {
			panic("freewalk: leaf");
		}
	}
	return kfree_account_page((void *)pagetable, account, charge_class);
}

/**
 * @brief Free user memory pages, then free page-table pages.
 *
 * @param max_page The max vaddr of user-space.
 */
void uvmfree(pagetable_t pagetable, uint64 max_page)
{
	struct resource_account_handle account;
	enum resource_charge_class charge_class;

	if (max_page > 0)
		uvmunmap(pagetable, 0, max_page, 1);
	if (vm_account_get(pagetable, &account, &charge_class) < 0)
		panic("uvmfree account");
	if (freewalk(pagetable, account, charge_class) < 0 ||
	    vm_account_unbind(pagetable) < 0)
		panic("uvmfree release");
}

// Terminal and rollback teardown may own a large sparse address space. Release
// one leaf at a time so the cleanup owner remains subject to kernel fairness;
// all other process threads are quiescent before this function is entered.
void uvmfree_cleanup(pagetable_t pagetable, uint64 max_page)
{
	struct resource_account_handle account;
	enum resource_charge_class charge_class;

	for (uint64 page = 0; page < max_page; page++) {
		uvmunmap(pagetable, page * PGSIZE, 1, 1);
		(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_PAGE_UNITS);
	}
	if (vm_account_get(pagetable, &account, &charge_class) < 0)
		panic("uvmfree cleanup account");
	if (freewalk(pagetable, account, charge_class) < 0 ||
	    vm_account_unbind(pagetable) < 0)
		panic("uvmfree cleanup release");
}

// Used in fork.
// Copy the pagetable page and all the user pages.
// Return 0 on success, -1 on error.
int uvmcopy(pagetable_t old, pagetable_t new, uint64 max_page)
{
	pte_t *pte;
	uint64 pa, page;
	uint64 scanned_pages = 0;
	uint flags;
	char *mem;

	for (page = 0; page < max_page; page++) {
		uint64 va = page * PGSIZE;

		pte = walk(old, va, 0);
		if (pte != 0 && (*pte & PTE_V) != 0) {
			pa = PTE2PA(*pte);
			flags = PTE_FLAGS(*pte);
			if ((mem = uvm_page_alloc(new)) == 0)
				goto err;
			memmove(mem, (char *)pa, PGSIZE);
			if (mappages(new, va, PGSIZE, (uint64)mem,
				     flags) != 0) {
				(void)uvm_page_free(new, mem);
				goto err;
			}
		}
		scanned_pages = page + 1;
		if (kernel_work_checkpoint(KERNEL_WORK_PAGE_UNITS) < 0)
			goto err;
	}
	return 0;

err:
	while (scanned_pages != 0) {
		scanned_pages--;
		uvmunmap(new, scanned_pages * PGSIZE, 1, 1);
		(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_PAGE_UNITS);
	}
	return -1;
}

// Copy from kernel to user.
// Copy len bytes from src to virtual address dstva in a given page table.
// Return 0 on success, -1 on error.
int copyout(pagetable_t pagetable, uint64 dstva, char *src, uint64 len)
{
	uint64 n, va0, pa0;
	pte_t *pte;

	if (user_range_check(pagetable, dstva, len, PTE_W) < 0)
		return -1;

	while (len > 0) {
		va0 = PGROUNDDOWN(dstva);
		pte = walk_user_leaf(pagetable, va0, PTE_W);
		if (pte == 0)
			return -1;
		pa0 = PTE2PA(*pte);
		n = PGSIZE - (dstva - va0);
		if (n > len)
			n = len;
		memmove((void *)(pa0 + (dstva - va0)), src, n);

		len -= n;
		src += n;
		dstva = va0 + PGSIZE;
	}
	return 0;
}

// Copy from user to kernel.
// Copy len bytes to dst from virtual address srcva in a given page table.
// Return 0 on success, -1 on error.
int copyin(pagetable_t pagetable, char *dst, uint64 srcva, uint64 len)
{
	uint64 n, va0, pa0;
	pte_t *pte;

	if (user_range_check(pagetable, srcva, len, PTE_R) < 0)
		return -1;

	while (len > 0) {
		va0 = PGROUNDDOWN(srcva);
		pte = walk_user_leaf(pagetable, va0, PTE_R);
		if (pte == 0)
			return -1;
		pa0 = PTE2PA(*pte);
		n = PGSIZE - (srcva - va0);
		if (n > len)
			n = len;
		memmove(dst, (void *)(pa0 + (srcva - va0)), n);

		len -= n;
		dst += n;
		srcva = va0 + PGSIZE;
	}
	return 0;
}

int fetch_user_u64(pagetable_t pagetable, uint64 addr, uint64 *value)
{
	if (value == 0)
		return -1;
	return copyin(pagetable, (char *)value, addr, sizeof(*value));
}

// Copy a null-terminated string from user to kernel.
// Copy bytes to dst from virtual address srcva in a given page table,
// until a '\0', within max bytes (including the terminator).
// Return the length excluding '\0' on success, -1 on error or truncation.
int copyinstr(pagetable_t pagetable, char *dst, uint64 srcva, uint64 max)
{
	uint64 n, va0, pa0;
	uint64 len = 0;
	pte_t *pte;

	if (max == 0 || srcva >= MAXVA)
		return -1;
	while (max > 0) {
		va0 = PGROUNDDOWN(srcva);
		pte = walk_user_leaf(pagetable, va0, PTE_R);
		if (pte == 0)
			return -1;
		pa0 = PTE2PA(*pte);
		n = PGSIZE - (srcva - va0);
		if (n > max)
			n = max;

		char *p = (char *)(pa0 + (srcva - va0));
		while (n > 0) {
			if (*p == '\0') {
				*dst = '\0';
				return (int)len;
			}
			*dst = *p;
			--n;
			--max;
			p++;
			dst++;
			len++;
		}

		srcva = va0 + PGSIZE;
	}
	return -1;
}

// Copy to either a user address, or kernel address,
// depending on usr_dst.
// Returns 0 on success, -1 on error.
int either_copyout(int user_dst, uint64 dst, char *src, uint64 len)
{
	struct proc *p = curr_proc();
	if (user_dst) {
		return copyout(p->pagetable, dst, src, len);
	} else {
		memmove((void *)dst, src, len);
		return 0;
	}
}

// Copy from either a user address, or kernel address,
// depending on usr_src.
// Returns 0 on success, -1 on error.
int either_copyin(int user_src, uint64 src, char *dst, uint64 len)
{
	struct proc *p = curr_proc();
	if (user_src) {
		return copyin(p->pagetable, dst, src, len);
	} else {
		memmove(dst, (char *)src, len);
		return 0;
	}
}
