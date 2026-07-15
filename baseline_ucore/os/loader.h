#ifndef LOADER_H
#define LOADER_H

#include "const.h"
#include "file.h"
#include "proc.h"
#include "types.h"

struct user_image {
	pagetable_t pagetable;
	uint64 max_page;
	uint64 ustack_base;
	uint64 entry;
	uint64 shared_base;
	uint64 shared_pages;
};

int load_init_app();
int user_image_build(struct inode *, uint64, struct user_image *);
void user_image_discard(struct user_image *);

#define BASE_ADDRESS (0x1000)
#define USTACK_SIZE (PAGE_SIZE)
#define TRAP_PAGE_SIZE (PAGE_SIZE)

#define USER_IMAGE_LIMIT \
	(TRAPFRAME - (NTHREAD - 1) * TRAP_PAGE_SIZE)

#endif // LOADER_H
