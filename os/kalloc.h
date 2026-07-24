#ifndef KALLOC_H
#define KALLOC_H

#include "types.h"

void *kalloc();
void kfree(void *);
void kinit();
int kalloc_stack_reserve_init(uint);
void *kalloc_stack_page(int);
void kfree_stack_page(void *, int);
uint kalloc_free_pages(void);
uint kalloc_stack_reserved_total_pages(void);
uint kalloc_stack_reserved_free_pages(void);

#endif // KALLOC_H
