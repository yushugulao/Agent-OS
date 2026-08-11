#ifndef PHYSICAL_PAGE_POLICY_H
#define PHYSICAL_PAGE_POLICY_H

/* 物理页按不可变执行资源账户准入；分配器从普通空闲链表切出真实保留水位。 */
#ifndef PHYSICAL_PAGE_SYSTEM_RESERVE
/* 为每个保留域中的并发控制器与端点映像提供资源。 */
#define PHYSICAL_PAGE_SYSTEM_RESERVE 2048U
#endif

/* 每个计入准入的 workflow 都有保留执行账户；其基数绑定进程和 VFS 准入上限。 */
#ifndef PHYSICAL_PAGE_RESERVED_DOMAIN_CAP
#define PHYSICAL_PAGE_RESERVED_DOMAIN_CAP 4U
#endif

/* 当前 Sv39 内核映射范围：0x80200000 至 0x88000000。 */
#ifndef PHYSICAL_PAGE_ADDRESSABLE_LIMIT
#define PHYSICAL_PAGE_ADDRESSABLE_LIMIT 32256U
#endif

/* 保留存储账户也会分配短命回收工作区。 */
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

/* 零表示根据启动后的空闲链表推导普通水位线。 */
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

/* 向每个保留域承诺的非 Agent VM 最小工作集。 */
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
