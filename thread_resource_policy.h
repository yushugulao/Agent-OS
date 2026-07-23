#ifndef THREAD_RESOURCE_POLICY_H
#define THREAD_RESOURCE_POLICY_H

/*
 * Thread slots use the same immutable admission classes as process slots.
 * Ordinary domains receive a hard per-domain ceiling, while trusted
 * bootstrap/workflow domains retain a separate global reserve.
 */
#ifndef THREAD_RESOURCE_POOL_SIZE
#define THREAD_RESOURCE_POOL_SIZE (NPROC * NTHREAD)
#endif

#ifndef THREAD_RESOURCE_ORDINARY_LIMIT
#define THREAD_RESOURCE_ORDINARY_LIMIT (PROC_ORDINARY_SLOTS * NTHREAD)
#endif

#ifndef THREAD_RESOURCE_RESERVED_LIMIT
#define THREAD_RESOURCE_RESERVED_LIMIT (PROC_RESERVED_SLOTS * NTHREAD)
#endif

#ifndef THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT
#define THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT PROC_RESOURCE_DOMAIN_LIMIT
#endif

#ifndef THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT
#define THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT \
	(PROC_RESOURCE_DOMAIN_RESERVED_LIMIT * NTHREAD)
#endif

#define THREAD_RESOURCE_DOMAIN_QUEUE_LIMIT \
	(THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT + \
	 THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT)

_Static_assert(THREAD_RESOURCE_POOL_SIZE > 0 &&
		       THREAD_RESOURCE_POOL_SIZE <= NPROC * NTHREAD,
	       "thread resource pool must fit the physical thread table");
_Static_assert(THREAD_RESOURCE_ORDINARY_LIMIT > 0 &&
		       THREAD_RESOURCE_RESERVED_LIMIT > 0 &&
		       THREAD_RESOURCE_ORDINARY_LIMIT +
				       THREAD_RESOURCE_RESERVED_LIMIT <=
			       THREAD_RESOURCE_POOL_SIZE,
	       "thread admission classes must leave a real system reserve");
_Static_assert(THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT > 0 &&
		       THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT <=
			       THREAD_RESOURCE_ORDINARY_LIMIT,
	       "ordinary thread domain limit must fit its global waterline");
_Static_assert(THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT <
		       THREAD_RESOURCE_ORDINARY_LIMIT,
	       "ordinary thread policy must preserve cross-domain capacity");
_Static_assert(THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT > 0 &&
		       THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT <=
			       THREAD_RESOURCE_RESERVED_LIMIT,
	       "reserved thread domain limit must fit its global reserve");
_Static_assert(THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT <
		       THREAD_RESOURCE_RESERVED_LIMIT,
	       "reserved thread policy must preserve cross-domain capacity");
_Static_assert(THREAD_RESOURCE_DOMAIN_QUEUE_LIMIT <=
		       THREAD_RESOURCE_POOL_SIZE,
	       "a domain run queue must fit the admitted thread pool");

#endif
