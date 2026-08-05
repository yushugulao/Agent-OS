#ifndef VM_H
#define VM_H

#include "riscv.h"
#include "resource_controller.h"
#include "types.h"

#define UVM_COW_STATS_VERSION 1ULL

struct uvm_cow_stats {
	uint64 version;
	uint64 size;
	uint64 cow_shared_mappings;
	uint64 cow_fault_copies;
	uint64 cow_fault_promotions;
};

#define VM_COPY_SEGMENT_MAX 4U
#define VM_COPYOUTV_MAX_BYTES PGSIZE
#define VM_COPYOUTV_MAX_USER_PAGES 2U

struct vm_copy_segment {
	const char *source;
	uint64 length;
};

extern pagetable_t kernel_pagetable;

void kvm_init();
void kvmmap(pagetable_t, uint64, uint64, uint64, int);
pte_t *walk(pagetable_t, uint64, int);
int mappages(pagetable_t, uint64, uint64, uint64, int);
pagetable_t uvmcreate_account(struct resource_account_handle,
			      enum resource_charge_class);
void *uvm_page_alloc(pagetable_t);
int uvm_page_free(pagetable_t, void *);
int uvmcopy(pagetable_t, pagetable_t, uint64);
int uvm_cow_fault(pagetable_t, uint64);
void uvm_cow_stats_snapshot(struct uvm_cow_stats *);
void uvmfree(pagetable_t, uint64);
void uvmfree_cleanup(pagetable_t, uint64);
int uvmmap(pagetable_t pagetable, uint64 va, uint64 npages, int perm);
void uvmunmap(pagetable_t, uint64, uint64, int);
void uvm_unmap_reclaim(pagetable_t, uint64, uint64);
uint64 walkaddr(pagetable_t, uint64);
int user_range_check(pagetable_t, uint64, uint64, int);
int fetch_user_u64(pagetable_t, uint64, uint64 *);
int checked_user_offset(uint64, uint64, uint64, uint64 *);
int copyout(pagetable_t, uint64, char *, uint64);
int copyoutv(pagetable_t, uint64, const struct vm_copy_segment *, uint);
int copyin(pagetable_t, char *, uint64, uint64);
int copyinstr(pagetable_t, char *, uint64, uint64);
int either_copyout(int, uint64, char *, uint64);
int either_copyoutv(int, uint64, const struct vm_copy_segment *, uint);
int either_copyin(int, uint64, char *, uint64);

#endif // VM_H
