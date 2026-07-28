#ifndef KALLOC_H
#define KALLOC_H

#include "types.h"
#include "resource_controller.h"

/* Boot/kernel-infrastructure only; user-owned pages use account APIs below. */
void *kalloc_system_page(void);
void kfree_system_page(void *);
void kinit();
int kalloc_stack_reserve_init(uint);
void *kalloc_stack_page(int);
void kfree_stack_page(void *, int);
uint kalloc_free_pages(void);
uint kalloc_stack_reserved_total_pages(void);
uint kalloc_stack_reserved_free_pages(void);
int kalloc_physical_policy_init(uint, uint);
int kalloc_physical_policy_ready(void);
void *kalloc_account_page(struct resource_account_handle,
			  enum resource_charge_class);
int kfree_account_page(void *, struct resource_account_handle,
		       enum resource_charge_class);
uint kalloc_physical_reserved_free_pages(void);
uint kalloc_physical_reserved_total_pages(void);

#endif // KALLOC_H
