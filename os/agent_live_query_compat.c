#include "agent.h"

/* Retired disk-catalog hooks kept only for unchanged generic callers. */
int
agent_file_is_meta_store_name(char *path)
{
	(void)path;
	return 0;
}

void
agent_file_request_scan(void)
{
}
