#ifndef FS_ALLOCATOR_TEST_H
#define FS_ALLOCATOR_TEST_H

#include "types.h"
#include "../fs_allocator_test_abi.h"

struct proc;

void fs_allocator_test_bind_boot_init(struct proc *, const char *);
int fs_allocator_test_authorized(const struct proc *);
int fs_allocator_test_arm(uint, uint, uint, uint);
void fs_allocator_test_disarm(void);
void fs_allocator_test_snapshot(struct fsalloc_test_snapshot *);
int fs_allocator_test_before(uint, uint);
void fs_allocator_test_after(uint, uint);
void fs_allocator_test_storage_snapshot(struct fsalloc_test_snapshot *);

#endif
