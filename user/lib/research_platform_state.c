#include <research_platform_state.h>

char rp_state_buf[RP_STATE_BUFFER_SIZE];
char rp_host_seed_buf[RP_HOST_SEED_BUFFER_SIZE];
int rp_host_seed_loaded;

_Static_assert(sizeof(rp_state_buf) == RP_STATE_BUFFER_SIZE,
	       "research platform state scratch definition mismatch");
_Static_assert(sizeof(rp_host_seed_buf) == RP_HOST_SEED_BUFFER_SIZE,
	       "research platform host seed scratch definition mismatch");
