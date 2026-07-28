#ifndef USER_STACK_LAYOUT_H
#define USER_STACK_LAYOUT_H

#include "types.h"
#include "../user_stack_policy.h"

struct user_stack_argv_layout {
	uint64 used;
	uint64 argc;
};

_Static_assert(USER_STACK_POINTER_BYTES == sizeof(uint64),
	       "argv pointer width must match the RISC-V user ABI");

static inline uint64 user_stack_layout_align(uint64 value)
{
	return (value + USER_STACK_ALIGNMENT_BYTES - 1) &
	       ~(USER_STACK_ALIGNMENT_BYTES - 1);
}

static inline void
user_stack_argv_layout_init(struct user_stack_argv_layout *layout)
{
	layout->used = 0;
	layout->argc = 0;
}

static inline int
user_stack_argv_layout_add_string(struct user_stack_argv_layout *layout,
				  uint64 bytes)
{
	uint64 next;

	if (layout == 0 || bytes == 0 ||
	    layout->used > USER_STACK_ARGV_LAYOUT_BYTES ||
	    bytes > USER_STACK_ARGV_LAYOUT_BYTES - layout->used)
		return -1;
	next = user_stack_layout_align(layout->used + bytes);
	if (next > USER_STACK_ARGV_LAYOUT_BYTES)
		return -1;
	layout->used = next;
	layout->argc++;
	return 0;
}

static inline int
user_stack_argv_layout_finish(const struct user_stack_argv_layout *layout,
			      uint64 *layout_bytes)
{
	uint64 pointer_bytes;
	uint64 next;

	if (layout == 0 || layout_bytes == 0 ||
	    layout->used > USER_STACK_ARGV_LAYOUT_BYTES ||
	    layout->argc >=
		USER_STACK_ARGV_LAYOUT_BYTES / USER_STACK_POINTER_BYTES)
		return -1;
	pointer_bytes = (layout->argc + 1) * USER_STACK_POINTER_BYTES;
	if (pointer_bytes > USER_STACK_ARGV_LAYOUT_BYTES - layout->used)
		return -1;
	next = user_stack_layout_align(layout->used + pointer_bytes);
	if (next > USER_STACK_ARGV_LAYOUT_BYTES)
		return -1;
	*layout_bytes = next;
	return 0;
}

#endif
