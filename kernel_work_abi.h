#ifndef KERNEL_WORK_ABI_H
#define KERNEL_WORK_ABI_H

#define KERNEL_WORK_RECEIPT_ABI_VERSION 1U

#define KERNEL_WORK_RECEIPT_OWNER_THREAD 1U
#define KERNEL_WORK_RECEIPT_KIND_SYSCALL 1U

/*
 * The published fields identify one completed syscall. observed_timer_epoch
 * is sampled by the non-publishing snapshot syscall and is not part of the
 * immutable receipt identity.
 */
struct kernel_work_receipt {
	unsigned int version;
	unsigned int struct_size;
	unsigned long long generation;
	unsigned long long owner_generation;
	unsigned long long preemptions;
	unsigned long long completion_timer_epoch;
	unsigned long long observed_timer_epoch;
	int owner_tid;
	int owner_pid;
	unsigned int owner_kind;
	unsigned int kind;
	int syscall_id;
	unsigned int reserved;
};

_Static_assert(sizeof(unsigned int) == 4,
	       "kernel work ABI requires 32-bit unsigned int");
_Static_assert(sizeof(unsigned long long) == 8,
	       "kernel work ABI requires 64-bit unsigned long long");
_Static_assert(__builtin_offsetof(struct kernel_work_receipt, generation) == 8,
	       "kernel work receipt generation ABI offset");
_Static_assert(__builtin_offsetof(struct kernel_work_receipt,
				  completion_timer_epoch) == 32,
	       "kernel work receipt completion epoch ABI offset");
_Static_assert(__builtin_offsetof(struct kernel_work_receipt,
				  observed_timer_epoch) == 40,
	       "kernel work receipt observation epoch ABI offset");
_Static_assert(__builtin_offsetof(struct kernel_work_receipt, owner_tid) == 48,
	       "kernel work receipt owner ABI offset");
_Static_assert(__builtin_offsetof(struct kernel_work_receipt, syscall_id) == 64,
	       "kernel work receipt syscall ABI offset");
_Static_assert(sizeof(struct kernel_work_receipt) == 72,
	       "kernel work receipt ABI layout");

#endif // KERNEL_WORK_ABI_H
