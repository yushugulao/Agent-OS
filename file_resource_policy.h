#ifndef FILE_RESOURCE_POLICY_H
#define FILE_RESOURCE_POLICY_H

// 打开文件表与进程表使用相同准入类别。普通准入只使用公共水位线，
// 其余容量保留给内核授权的启动和 workflow。
#ifndef FILE_RESOURCE_POOL_SIZE
#define FILE_RESOURCE_POOL_SIZE (NPROC * FD_BUFFER_SIZE)
#endif

#ifndef FILE_RESOURCE_ORDINARY_LIMIT
#define FILE_RESOURCE_ORDINARY_LIMIT \
	(PROC_ORDINARY_SLOTS * FD_BUFFER_SIZE)
#endif

#ifndef FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT
#define FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT \
	(PROC_RESOURCE_DOMAIN_LIMIT * FD_BUFFER_SIZE)
#endif

#ifndef FILE_RESOURCE_DOMAIN_RESERVED_LIMIT
#define FILE_RESOURCE_DOMAIN_RESERVED_LIMIT \
	(PROC_RESOURCE_DOMAIN_RESERVED_LIMIT * FD_BUFFER_SIZE)
#endif

_Static_assert(FILE_RESOURCE_POOL_SIZE > 0,
	       "file resource pool must not be empty");
_Static_assert(FILE_RESOURCE_ORDINARY_LIMIT > 0 &&
	       FILE_RESOURCE_ORDINARY_LIMIT < FILE_RESOURCE_POOL_SIZE,
	       "ordinary file waterline must leave a system reserve");
_Static_assert(FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT > 0 &&
	       FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT <=
		       FILE_RESOURCE_ORDINARY_LIMIT,
	       "ordinary file domain limit must fit the public waterline");
_Static_assert(FILE_RESOURCE_DOMAIN_RESERVED_LIMIT > 0 &&
	       FILE_RESOURCE_DOMAIN_RESERVED_LIMIT <= FILE_RESOURCE_POOL_SIZE,
	       "reserved file domain limit must fit the file pool");

#endif
