#include "../../os/proc.h"

int physical_page_capacity_probe(void)
{
	return PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT;
}
