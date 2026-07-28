#ifndef USER_STACK_POLICY_H
#define USER_STACK_POLICY_H

/*
 * One immutable contract covers the loader, exec argument staging, user-space
 * call-path analysis, and acceptance tests.  The initial argv image grows
 * down from the top of the page; compiled C call paths grow below it.
 */
#define USER_STACK_SIZE_BYTES 4096ULL
#define USER_STACK_ARGV_LAYOUT_BYTES 1024ULL
#define USER_STACK_CALL_PATH_BYTES 3072ULL
#define USER_STACK_ALIGNMENT_BYTES 16ULL
#define USER_STACK_POINTER_BYTES 8ULL

#if USER_STACK_ARGV_LAYOUT_BYTES + USER_STACK_CALL_PATH_BYTES != \
	USER_STACK_SIZE_BYTES
#error "user stack partitions must cover exactly one stack"
#endif
#if USER_STACK_ALIGNMENT_BYTES == 0 || \
	(USER_STACK_ALIGNMENT_BYTES & (USER_STACK_ALIGNMENT_BYTES - 1)) != 0
#error "user stack alignment must be a power of two"
#endif
#if USER_STACK_SIZE_BYTES % USER_STACK_ALIGNMENT_BYTES != 0 || \
	USER_STACK_ARGV_LAYOUT_BYTES % USER_STACK_ALIGNMENT_BYTES != 0
#error "user stack partitions must preserve ABI alignment"
#endif
#if USER_STACK_POINTER_BYTES > USER_STACK_ALIGNMENT_BYTES || \
	USER_STACK_ALIGNMENT_BYTES % USER_STACK_POINTER_BYTES != 0
#error "user pointer layout must fit the ABI alignment"
#endif

#endif
