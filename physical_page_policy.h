#ifndef PHYSICAL_PAGE_POLICY_H
#define PHYSICAL_PAGE_POLICY_H

/*
 * Physical pages are admitted by the immutable execution resource account.
 * The allocator carves this reserve out of the ordinary freelist, so the
 * waterline is physical rather than merely an accounting convention.
 */
#ifndef PHYSICAL_PAGE_SYSTEM_RESERVE
/* Fund concurrent controller and endpoint images in every reserved domain. */
#define PHYSICAL_PAGE_SYSTEM_RESERVE 2048U
#endif

/*
 * A reserved execution account exists for each admission-counted workflow.
 * Keep this policy-level cardinality tied to the process and VFS admission
 * limits (proc.h supplies the compile-time bridge) instead of letting a
 * caller silently size one account to the whole pool.
 */
#ifndef PHYSICAL_PAGE_RESERVED_DOMAIN_CAP
#define PHYSICAL_PAGE_RESERVED_DOMAIN_CAP 4U
#endif

/* Current Sv39 kernel mapping: 0x80200000 through 0x88000000. */
#ifndef PHYSICAL_PAGE_ADDRESSABLE_LIMIT
#define PHYSICAL_PAGE_ADDRESSABLE_LIMIT 32256U
#endif

/* Reserved storage accounts also allocate short-lived reclaim workspaces. */
#ifndef PHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT
#define PHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT 16U
#endif

#ifndef PHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT
#define PHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT 2U
#endif

#define PHYSICAL_PAGE_STORAGE_RESERVED_BUDGET \
	(1ULL * PHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT + \
	 1ULL * PHYSICAL_PAGE_RESERVED_DOMAIN_CAP * \
		 PHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT)

#define PHYSICAL_PAGE_EXEC_RESERVED_BUDGET \
	(1ULL * PHYSICAL_PAGE_SYSTEM_RESERVE > \
		 PHYSICAL_PAGE_STORAGE_RESERVED_BUDGET ? \
	 1ULL * PHYSICAL_PAGE_SYSTEM_RESERVE - \
		 PHYSICAL_PAGE_STORAGE_RESERVED_BUDGET : 0ULL)

/* Zero derives the ordinary waterline from the post-boot freelist. */
#ifndef PHYSICAL_PAGE_ORDINARY_LIMIT
#define PHYSICAL_PAGE_ORDINARY_LIMIT 0U
#endif

#ifndef PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT
#define PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT 2048U
#endif

#ifndef PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT
#define PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT \
	(PHYSICAL_PAGE_EXEC_RESERVED_BUDGET / \
	 PHYSICAL_PAGE_RESERVED_DOMAIN_CAP)
#define PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT_DERIVED 1
#else
#define PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT_DERIVED 0
#endif

/* Minimum non-Agent VM working set promised to each reserved domain. */
#ifndef PHYSICAL_PAGE_DOMAIN_RESERVED_VM_FLOOR
#define PHYSICAL_PAGE_DOMAIN_RESERVED_VM_FLOOR 250U
#endif

_Static_assert(PHYSICAL_PAGE_SYSTEM_RESERVE > 0,
	       "physical page policy needs a real system reserve");
_Static_assert(1ULL * PHYSICAL_PAGE_SYSTEM_RESERVE <= 0xffffffffULL,
	       "physical reserve must fit the allocator page-count type");
_Static_assert(1ULL * PHYSICAL_PAGE_SYSTEM_RESERVE <=
	       1ULL * PHYSICAL_PAGE_ADDRESSABLE_LIMIT,
	       "physical reserve exceeds addressable kernel RAM");
_Static_assert(PHYSICAL_PAGE_RESERVED_DOMAIN_CAP > 0,
	       "physical page policy needs reserved domains");
_Static_assert(PHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT > 0 &&
	       PHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT > 0,
	       "storage workspaces need finite physical guarantees");
_Static_assert(PHYSICAL_PAGE_SYSTEM_RESERVE >
	       PHYSICAL_PAGE_STORAGE_RESERVED_BUDGET,
	       "physical reserve must fund storage before execution domains");
_Static_assert(PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT > 0 &&
	       PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT > 0,
	       "physical page domains need finite non-zero quotas");
_Static_assert(1ULL * PHYSICAL_PAGE_ORDINARY_LIMIT <=
	       1ULL * PHYSICAL_PAGE_ADDRESSABLE_LIMIT &&
	       1ULL * PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT <=
	       1ULL * PHYSICAL_PAGE_ADDRESSABLE_LIMIT &&
	       1ULL * PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT <=
	       1ULL * PHYSICAL_PAGE_ADDRESSABLE_LIMIT,
	       "physical page limits exceed addressable kernel RAM");
_Static_assert(1ULL * PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT <= 0xffffffffULL,
	       "reserved domain limit must fit allocator page counts");
_Static_assert(1ULL * PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT *
		       PHYSICAL_PAGE_RESERVED_DOMAIN_CAP +
		       PHYSICAL_PAGE_STORAGE_RESERVED_BUDGET <=
	       PHYSICAL_PAGE_SYSTEM_RESERVE,
	       "reserved physical promises must fit the carved pool");

#endif
