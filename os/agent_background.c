#include "agent_internal.h"

/*
 * Edge-triggered wake state for schedulable Agent maintenance.  Producers
 * only publish work here; the core coordinator remains the sole consumer.
 */
static int agent_background_pending;

void
agent_background_request(void)
{
	__atomic_store_n(&agent_background_pending, 1, __ATOMIC_RELEASE);
}

int
agent_background_work_pending(void)
{
	return __atomic_load_n(&agent_background_pending, __ATOMIC_ACQUIRE);
}

int
agent_background_take(void)
{
	return __atomic_exchange_n(&agent_background_pending, 0,
				   __ATOMIC_ACQ_REL);
}
