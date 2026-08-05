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

static struct {
	struct linklist *freelist;
	char *begin;
	char *end;
	uint total_pages;
	uint free_pages;
	int initialized;
} physical_reserve;

#define ACCOUNT_PAGE_CAP ((PHYSTOP - KERNBASE) / PGSIZE)
#define ACCOUNT_PAGE_OWNER_MASK 0x1ffU
#define ACCOUNT_PAGE_CLASS_SHIFT 9
#define ACCOUNT_PAGE_REF_MAX 0xffU

/*
 * A physical page is charged once, even while several COW mappings refer to
 * it.  The compact owner tag keeps the original account alive until the last
 * mapping disappears, including forks that enter a different resource domain.
 */
static ushort account_page_owner_class[ACCOUNT_PAGE_CAP];
static uchar account_page_refs[ACCOUNT_PAGE_CAP];
static uint64 account_page_generation[RESOURCE_ACCOUNT_CAP];
static uint account_page_count[RESOURCE_ACCOUNT_CAP];

_Static_assert(RESOURCE_ACCOUNT_CAP <= ACCOUNT_PAGE_OWNER_MASK,
	       "account page owner tag is too small");
_Static_assert(NPROC < ACCOUNT_PAGE_REF_MAX,
	       "account page reference tag is too small");

static int page_address_valid(void *pa)
{
	return ((uint64)pa % PGSIZE) == 0 && (char *)pa >= ekernel &&
	       (uint64)pa < PHYSTOP;
}

static uint account_page_index(void *pa)
{
	if (!page_address_valid(pa) || (uint64)pa < KERNBASE)
		panic("account page address");
	return ((uint64)pa - KERNBASE) / PGSIZE;
}

static int account_page_tracked(void *pa)
{
	uint index = account_page_index(pa);

	return account_page_owner_class[index] != 0 ||
	       account_page_refs[index] != 0;
}

static uint account_page_owner(ushort owner_class)
{
	uint encoded = owner_class & ACCOUNT_PAGE_OWNER_MASK;

	if (encoded == 0 || encoded > RESOURCE_ACCOUNT_CAP)
		panic("account page owner");
	return encoded - 1;
}

static enum resource_charge_class account_page_class(ushort owner_class)
{
	return (enum resource_charge_class)
		((owner_class >> ACCOUNT_PAGE_CLASS_SHIFT) & 1U);
}

static void account_page_track(
	void *pa, struct resource_account_handle account,
	enum resource_charge_class charge_class)
{
	uint index = account_page_index(pa);
	int enabled = intr_save();

	if (account.slot >= RESOURCE_ACCOUNT_CAP || account.generation == 0 ||
	    charge_class < RESOURCE_CHARGE_ORDINARY ||
	    charge_class >= RESOURCE_CHARGE_CLASS_COUNT ||
	    account_page_owner_class[index] != 0 ||
	    account_page_refs[index] != 0 ||
	    account_page_count[account.slot] == (uint)-1)
		panic("account page track");
	if (account_page_count[account.slot] == 0)
		account_page_generation[account.slot] = account.generation;
	else if (account_page_generation[account.slot] != account.generation)
		panic("account page generation");
	account_page_count[account.slot]++;
	account_page_owner_class[index] =
		(ushort)((account.slot + 1) |
			 ((uint)charge_class << ACCOUNT_PAGE_CLASS_SHIFT));
	account_page_refs[index] = 1;
	intr_restore(enabled);
}

static int account_page_matches(
	ushort owner_class, struct resource_account_handle account,
	enum resource_charge_class charge_class)
{
	uint owner = account_page_owner(owner_class);

	return owner == account.slot &&
	       account_page_generation[owner] == account.generation &&
	       account_page_class(owner_class) == charge_class;
}

static int physical_reserved_page_owned(void *pa)
{
	return page_address_valid(pa) && physical_reserve.begin != 0 &&
	       (char *)pa >= physical_reserve.begin &&
	       (char *)pa < physical_reserve.end;
}

static int physical_reserved_page_is_free(void *pa)
{
	struct linklist *page = physical_reserve.freelist;
	uint seen = 0;
	int found = 0;

	while (page != 0) {
		if (!physical_reserved_page_owned(page) ||
		    ++seen > physical_reserve.total_pages)
			panic("physical reserve list");
		found |= page == (struct linklist *)pa;
		page = page->next;
	}
	if (seen != physical_reserve.free_pages)
		panic("physical reserve count");
	return found;
}

static void physical_reserved_page_validate_allocated(void *pa)
{
	if (!physical_reserve.initialized ||
	    !physical_reserved_page_owned(pa) ||
	    physical_reserve.free_pages >= physical_reserve.total_pages ||
	    physical_reserved_page_is_free(pa))
		panic("physical reserve free");
}

void freerange(void *pa_start, void *pa_end)
{
	char *p;
	p = (char *)PGROUNDUP((uint64)pa_start);
	for (; p + PGSIZE <= (char *)pa_end; p += PGSIZE)
		kfree_system_page(p);
}

void kinit()
{
	freerange(ekernel, (void *)PHYSTOP);
}

// Free the page of physical memory pointed at by v,
// which normally should have been returned by a
// call to kalloc_system_page().  (The exception is when
// initializing the allocator; see kinit above.)
void kfree_system_page(void *pa)
{
	struct linklist *l;
	if (!page_address_valid(pa))
		panic("kfree");
	if (account_page_tracked(pa))
		panic("kfree account page");
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
void *kalloc_system_page(void)
{
	struct linklist *l;
	l = kmem.freelist;
	if (l) {
		if (account_page_tracked(l))
			panic("kalloc account page");
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
		struct linklist *page =
			(struct linklist *)kalloc_system_page();

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
		kfree_system_page(page);
	}
	return -1;
}

void *kalloc_stack_page(int reserved)
{
	struct linklist *page;

	if (!reserved)
		return kalloc_system_page();
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
		kfree_system_page(pa);
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

int kalloc_physical_policy_init(uint pages, uint ordinary_limit)
{
	uint capacity;

	if (pages == 0 || physical_reserve.initialized ||
	    kmem.free_pages <= pages)
		return -1;
	while (physical_reserve.total_pages < pages) {
		struct linklist *page =
			(struct linklist *)kalloc_system_page();

		if (page == 0)
			goto fail;
		if (physical_reserve.total_pages == 0) {
			physical_reserve.begin = (char *)page;
			physical_reserve.end = (char *)page + PGSIZE;
		} else if ((char *)page + PGSIZE == physical_reserve.begin) {
			physical_reserve.begin = (char *)page;
		} else if ((char *)page == physical_reserve.end) {
			physical_reserve.end += PGSIZE;
		} else {
			kfree_system_page(page);
			goto fail;
		}
		page->next = physical_reserve.freelist;
		physical_reserve.freelist = page;
		physical_reserve.total_pages++;
		physical_reserve.free_pages++;
	}
	/* Reserved kernel stacks are a disjoint THREAD-backed hard pool. */
	capacity = kmem.free_pages + physical_reserve.total_pages;
	if (ordinary_limit == 0)
		ordinary_limit = kmem.free_pages;
	if (ordinary_limit > kmem.free_pages)
		goto fail;
	if (resource_policy_configure(
		    RESOURCE_PHYSICAL_PAGE, capacity, ordinary_limit,
		    physical_reserve.total_pages) < 0 ||
	    resource_policy_guarantee_reserved(
		    RESOURCE_PHYSICAL_PAGE) < 0)
		goto fail;
	physical_reserve.initialized = 1;
	return 0;

fail:
	while (physical_reserve.freelist != 0) {
		struct linklist *page = physical_reserve.freelist;

		physical_reserve.freelist = page->next;
		physical_reserve.total_pages--;
		physical_reserve.free_pages--;
		kfree_system_page(page);
	}
	physical_reserve.begin = 0;
	physical_reserve.end = 0;
	return -1;
}

int kalloc_physical_policy_ready(void)
{
	return physical_reserve.initialized;
}

static void *kalloc_reserved_page(void)
{
	struct linklist *page;

	if (!physical_reserve.initialized ||
	    (page = physical_reserve.freelist) == 0)
		return 0;
	if (physical_reserve.free_pages == 0 ||
	    !physical_reserved_page_owned(page))
		panic("physical reserve alloc");
	physical_reserve.freelist = page->next;
	physical_reserve.free_pages--;
	memset(page, 5, PGSIZE);
	return page;
}

static void kfree_reserved_page_validated(void *pa)
{
	struct linklist *page;

	if (account_page_tracked(pa))
		panic("reserved account page");
	memset(pa, 1, PGSIZE);
	page = pa;
	page->next = physical_reserve.freelist;
	physical_reserve.freelist = page;
	physical_reserve.free_pages++;
}

void *kalloc_account_page(struct resource_account_handle account,
			  enum resource_charge_class charge_class)
{
	struct resource_request request = {
		.kind = RESOURCE_PHYSICAL_PAGE,
		.amount = 1,
	};
	struct resource_reservation reservation;
	void *page;

	if (!physical_reserve.initialized ||
	    resource_reserve_many(account, charge_class, &request, 1,
				  &reservation) < 0)
		return 0;
	page = charge_class == RESOURCE_CHARGE_RESERVED ?
		       kalloc_reserved_page() : kalloc_system_page();
	if (page == 0) {
		resource_reservation_cancel(&reservation);
		return 0;
	}
	if (resource_reservation_commit(&reservation) < 0) {
		if (charge_class == RESOURCE_CHARGE_RESERVED) {
			physical_reserved_page_validate_allocated(page);
			kfree_reserved_page_validated(page);
		} else {
			kfree_system_page(page);
		}
		return 0;
	}
	account_page_track(page, account, charge_class);
	return page;
}

int kretain_account_page(void *pa)
{
	uint index = account_page_index(pa);
	int enabled = intr_save();
	uint refs = account_page_refs[index];

	if (account_page_owner_class[index] == 0 || refs == 0 ||
	    refs == ACCOUNT_PAGE_REF_MAX) {
		intr_restore(enabled);
		return -1;
	}
	account_page_refs[index] = refs + 1;
	intr_restore(enabled);
	return 0;
}

int kaccount_page_exclusive(
	void *pa, struct resource_account_handle account,
	enum resource_charge_class charge_class)
{
	uint index = account_page_index(pa);
	int enabled = intr_save();
	ushort owner_class = account_page_owner_class[index];
	int exclusive = owner_class != 0 && account_page_refs[index] == 1 &&
			account_page_matches(owner_class, account,
					     charge_class);

	intr_restore(enabled);
	return exclusive;
}

static int account_page_release(
	void *pa, const struct resource_account_handle *expected_account,
	int expected_class)
{
	struct resource_request request = {
		.kind = RESOURCE_PHYSICAL_PAGE,
		.amount = 1,
	};
	struct resource_account_handle owner;
	enum resource_charge_class charge_class;
	uint index = account_page_index(pa);
	int enabled = intr_save();
	ushort owner_class = account_page_owner_class[index];
	uint refs = account_page_refs[index];
	uint owner_slot;

	if (owner_class == 0 || refs == 0) {
		intr_restore(enabled);
		return -1;
	}
	charge_class = account_page_class(owner_class);
	if (expected_account != 0 &&
	    !account_page_matches(owner_class, *expected_account,
				  (enum resource_charge_class)expected_class)) {
		intr_restore(enabled);
		return -1;
	}
	if (refs > 1) {
		account_page_refs[index] = refs - 1;
		intr_restore(enabled);
		return 0;
	}
	owner_slot = account_page_owner(owner_class);
	owner.slot = owner_slot;
	owner.generation = account_page_generation[owner_slot];
	if (owner.generation == 0 || account_page_count[owner_slot] == 0)
		panic("account page release owner");
	account_page_owner_class[index] = 0;
	account_page_refs[index] = 0;
	account_page_count[owner_slot]--;
	if (resource_release_many(owner, charge_class, &request, 1) < 0)
		panic("physical page accounting");
	proc_resource_account_reap(owner);
	if (account_page_count[owner_slot] == 0)
		account_page_generation[owner_slot] = 0;
	if (charge_class == RESOURCE_CHARGE_RESERVED) {
		physical_reserved_page_validate_allocated(pa);
		kfree_reserved_page_validated(pa);
	} else {
		if (!page_address_valid(pa) || physical_reserved_page_owned(pa))
			panic("physical page pool");
		kfree_system_page(pa);
	}
	intr_restore(enabled);
	return 0;
}

int krelease_account_page(void *pa)
{
	return account_page_release(pa, 0, 0);
}

int kfree_account_page(void *pa, struct resource_account_handle account,
		       enum resource_charge_class charge_class)
{
	return account_page_release(pa, &account, charge_class);
}

uint kalloc_physical_reserved_free_pages(void)
{
	return physical_reserve.free_pages;
}

uint kalloc_physical_reserved_total_pages(void)
{
	return physical_reserve.total_pages;
}
