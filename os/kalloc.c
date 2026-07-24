#include "kalloc.h"
#include "defs.h"
#include "riscv.h"

extern char ekernel[];

struct linklist {
	struct linklist *next;
};

struct {
	struct linklist *freelist;
	uint free_pages;
} kmem;

/*
 * Trusted threads must retain a real stack guarantee even when ordinary user
 * memory exhausts the general allocator. Empty reserved pages carry their own
 * links, so the guarantee costs no pointer array in BSS.
 */
static struct {
	struct linklist *freelist;
	uint total_pages;
	uint free_pages;
	int initialized;
} stack_reserve;

static int page_address_valid(void *pa)
{
	return ((uint64)pa % PGSIZE) == 0 && (char *)pa >= ekernel &&
	       (uint64)pa < PHYSTOP;
}

void freerange(void *pa_start, void *pa_end)
{
	char *p;
	p = (char *)PGROUNDUP((uint64)pa_start);
	for (; p + PGSIZE <= (char *)pa_end; p += PGSIZE)
		kfree(p);
}

void kinit()
{
	freerange(ekernel, (void *)PHYSTOP);
}

// Free the page of physical memory pointed at by v,
// which normally should have been returned by a
// call to kalloc().  (The exception is when
// initializing the allocator; see kinit above.)
void kfree(void *pa)
{
	struct linklist *l;
	if (!page_address_valid(pa))
		panic("kfree");
	// Fill with junk to catch dangling refs.
	memset(pa, 1, PGSIZE);
	l = (struct linklist *)pa;
	l->next = kmem.freelist;
	kmem.freelist = l;
	kmem.free_pages++;
}

// Allocate one 4096-byte page of physical memory.
// Returns a pointer that the kernel can use.
// Returns 0 if the memory cannot be allocated.
void *kalloc()
{
	struct linklist *l;
	l = kmem.freelist;
	if (l) {
		kmem.freelist = l->next;
		if (kmem.free_pages == 0)
			panic("kalloc count");
		kmem.free_pages--;
		memset((char *)l, 5, PGSIZE); // fill with junk
	}
	return (void *)l;
}

int kalloc_stack_reserve_init(uint pages)
{
	if (pages == 0 || stack_reserve.initialized)
		return -1;
	while (stack_reserve.total_pages < pages) {
		struct linklist *page = (struct linklist *)kalloc();

		if (page == 0)
			goto fail;
		memset(page, 1, PGSIZE);
		page->next = stack_reserve.freelist;
		stack_reserve.freelist = page;
		stack_reserve.total_pages++;
		stack_reserve.free_pages++;
	}
	stack_reserve.initialized = 1;
	return 0;

fail:
	while (stack_reserve.freelist != 0) {
		struct linklist *page = stack_reserve.freelist;

		stack_reserve.freelist = page->next;
		stack_reserve.total_pages--;
		stack_reserve.free_pages--;
		kfree(page);
	}
	return -1;
}

void *kalloc_stack_page(int reserved)
{
	struct linklist *page;

	if (!reserved)
		return kalloc();
	if (!stack_reserve.initialized ||
	    (page = stack_reserve.freelist) == 0)
		return 0;
	if (stack_reserve.free_pages == 0)
		panic("stack reserve count");
	stack_reserve.freelist = page->next;
	stack_reserve.free_pages--;
	memset(page, 5, PGSIZE);
	return page;
}

void kfree_stack_page(void *pa, int reserved)
{
	struct linklist *page;

	if (!reserved) {
		kfree(pa);
		return;
	}
	if (!stack_reserve.initialized || !page_address_valid(pa) ||
	    stack_reserve.free_pages >= stack_reserve.total_pages)
		panic("stack reserve free");
	memset(pa, 1, PGSIZE);
	page = (struct linklist *)pa;
	page->next = stack_reserve.freelist;
	stack_reserve.freelist = page;
	stack_reserve.free_pages++;
}

uint kalloc_free_pages(void)
{
	return kmem.free_pages;
}

uint kalloc_stack_reserved_total_pages(void)
{
	return stack_reserve.total_pages;
}

uint kalloc_stack_reserved_free_pages(void)
{
	return stack_reserve.free_pages;
}
