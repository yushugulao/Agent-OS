#ifndef PHYSICAL_PAGE_TEST_H
#define PHYSICAL_PAGE_TEST_H

#include "types.h"

struct proc;

void physical_page_test_bind_boot_init(struct proc *, const char *);
int sys_physical_page_test(uint, uint64, uint64);

#endif
