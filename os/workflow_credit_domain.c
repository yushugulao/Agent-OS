#include "workflow_credit_domain.h"

int
workflow_credit_domain_fence(
	struct workflow_lifecycle_key key,
	struct resource_account_handle exec_account,
	struct resource_account_handle storage_account,
	struct workflow_credit_snapshot *out)
{
	if (!workflow_lifecycle_key_valid(key) || out == 0)
		return -1;
	if (resource_credit_snapshot_pair_trim(exec_account, storage_account,
					       out) < 0)
		return -1;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		if (out->pending[kind] != 0)
			return -1;
	out->key = key;
	return 0;
}

int
workflow_credit_domain_switch(
	struct workflow_lifecycle_key previous_key,
	struct resource_account_handle previous_exec,
	struct workflow_lifecycle_key next_key,
	struct resource_account_handle next_exec)
{
	int previous_workflow = workflow_lifecycle_key_valid(previous_key);
	int next_workflow = workflow_lifecycle_key_valid(next_key);

	if ((previous_workflow && next_workflow &&
	     workflow_lifecycle_key_equal(previous_key, next_key)) ||
	    (!previous_workflow && !next_workflow &&
	     resource_account_handle_equal(previous_exec, next_exec)))
		return 0;
	if (previous_exec.slot >= RESOURCE_ACCOUNT_CAP ||
	    previous_exec.generation == 0)
		return 0;
	return resource_account_trim(previous_exec);
}
