#ifndef WORKFLOW_CREDIT_DOMAIN_H
#define WORKFLOW_CREDIT_DOMAIN_H

#include "resource_controller.h"
#include "workflow_lifecycle.h"

/*
 * U/P/F is protected by the resource-controller critical section:
 *   used    - a live object owns the credit
 *   pending - a tentative allocation owns the credit
 *   free    - the account retains an idle, globally precharged credit
 */
struct workflow_credit_counter {
	uint used;
	uint pending;
	uint free;
};
_Static_assert(sizeof(struct workflow_credit_counter) == 12,
	       "workflow credit counter budget");

struct workflow_credit_domain {
	struct workflow_credit_counter
		counter[RESOURCE_CHARGE_CLASS_COUNT][RESOURCE_KIND_COUNT];
};

enum workflow_credit_account_role {
	WORKFLOW_CREDIT_EXEC = 0,
	WORKFLOW_CREDIT_STORAGE,
	WORKFLOW_CREDIT_ACCOUNT_COUNT,
};

struct workflow_credit_account_snapshot {
	struct resource_account_handle handle;
	uint64 used[RESOURCE_KIND_COUNT];
	uint64 pending[RESOURCE_KIND_COUNT];
	uint64 free[RESOURCE_KIND_COUNT];
	uint64 held[RESOURCE_KIND_COUNT];
};

struct workflow_credit_snapshot {
	struct workflow_lifecycle_key key;
	uint64 epoch;
	struct workflow_credit_account_snapshot
		account[WORKFLOW_CREDIT_ACCOUNT_COUNT];
	uint64 used[RESOURCE_KIND_COUNT];
	uint64 pending[RESOURCE_KIND_COUNT];
	uint64 free[RESOURCE_KIND_COUNT];
	uint64 held[RESOURCE_KIND_COUNT];
};

static inline uint64
workflow_credit_counter_held(const struct workflow_credit_counter *counter)
{
	return (uint64)counter->used + counter->pending + counter->free;
}

static inline int
workflow_credit_domain_empty(const struct workflow_credit_domain *domain)
{
	for (uint charge_class = 0;
	     charge_class < RESOURCE_CHARGE_CLASS_COUNT; charge_class++)
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
			if (workflow_credit_counter_held(
				    &domain->counter[charge_class][kind]) != 0)
				return 0;
	return 1;
}

int workflow_credit_domain_fence(
	struct workflow_lifecycle_key key,
	struct resource_account_handle exec_account,
	struct resource_account_handle storage_account,
	struct workflow_credit_snapshot *out);
int workflow_credit_domain_switch(
	struct workflow_lifecycle_key previous_key,
	struct resource_account_handle previous_exec,
	struct workflow_lifecycle_key next_key,
	struct resource_account_handle next_exec);

#endif
