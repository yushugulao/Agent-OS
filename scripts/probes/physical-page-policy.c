#include "../../os/types.h"
#include "../../include/physical_page_policy.h"

#ifndef EXPECTED_PHYSICAL_DOMAIN_LIMIT
#error "policy probe requires an expected derived domain limit"
#endif

_Static_assert(PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT ==
	       EXPECTED_PHYSICAL_DOMAIN_LIMIT,
	       "small reserve did not derive the expected fair share");

int physical_page_policy_probe(void)
{
	return PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT;
}
