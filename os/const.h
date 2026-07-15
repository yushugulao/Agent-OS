#ifndef CONST_H
#define CONST_H

#define PAGE_SIZE (0x1000)

#ifndef KSTACK_SIZE
#define KSTACK_SIZE (4 * PAGE_SIZE)
#endif
#ifndef KSTACK_GUARD_SIZE
#define KSTACK_GUARD_SIZE PAGE_SIZE
#endif
#define KSTACK_SLOT_SIZE (KSTACK_GUARD_SIZE + KSTACK_SIZE)

// memory layout

// the kernel expects there to be RAM
// for use by the kernel and user pages
// from physical address 0x80000000 to PHYSTOP.
#define KERNBASE 0x80200000L
#define PHYSTOP (0x80000000 + 128 * 1024 * 1024) // we have 128M memroy

// one beyond the highest possible virtual address.
// MAXVA is actually one bit less than the max allowed by
// Sv39, to avoid having to sign-extend virtual addresses
// that have the high bit set.
#define MAXVA (1L << (9 + 9 + 9 + 12 - 1))

// map the trampoline page to the highest address,
// in both user and kernel space.
#define USER_TOP (MAXVA)
#define TRAMPOLINE (USER_TOP - PGSIZE)
#define TRAPFRAME (TRAMPOLINE - PGSIZE)

#define MAX_APP_NUM (32)
#define MAX_STR_LEN (300)
#define IDLE_PID (0)
#define MAX_ARG_NUM (32) // max exec arguments
#define MAX_RW_COUNT (0x7fffffffULL)

#endif // CONST_H
