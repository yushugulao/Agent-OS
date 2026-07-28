#include "agent_observe_recovery.h"
#include "agent_observe_recovery_store.h"
#include "agent_observe_test.h"
#include "agent_internal.h"
#include "agent_metadata_internal.h"
#include "defs.h"
#include "exec_policy.h"
#include "proc.h"
#include "vfs_security.h"

struct agent_observe_recovery_binding {
	uint64 control_id;
	struct workflow_lifecycle_key lifecycle;
};

struct agent_observe_recovery_tail {
	uint64 receipt_id;
	uint64 bank_generation;
	uint durability;
	uint reserved;
};

static struct agent_observe_recovery_binding agent_observe_recovery_binding;
static struct agent_obsstore_record_view agent_observe_recovery_entry;

void
agent_observe_recovery_init(void)
{
	memset(&agent_observe_recovery_binding, 0,
	       sizeof(agent_observe_recovery_binding));
}

int
agent_observe_recovery_bind(struct proc *p, const struct proc *factory)
{
	if (p == 0 || factory == 0 || p->agent_role != AGENT_ROLE_RECOVERY ||
	    factory->is_agent || !factory->resource_domain_admin ||
	    !exec_policy_process_bootstrap(factory) ||
	    !exec_policy_process_bootstrap(p) || p->agent_control_id == 0 ||
	    !vfs_proc_lifecycle_active(p))
		return 0;
	if (agent_observe_recovery_binding.control_id != 0)
		return -1;
	agent_observe_recovery_binding.control_id = p->agent_control_id;
	agent_observe_recovery_binding.lifecycle = vfs_proc_lifecycle(p);
	return 1;
}

void
agent_observe_recovery_unbind_proc(const struct proc *p)
{
	if (p != 0 && agent_observe_recovery_binding.control_id != 0 &&
	    p->agent_control_id == agent_observe_recovery_binding.control_id &&
	    workflow_lifecycle_key_equal(vfs_proc_lifecycle(p),
				 agent_observe_recovery_binding.lifecycle))
		memset(&agent_observe_recovery_binding, 0,
		       sizeof(agent_observe_recovery_binding));
}

static int
agent_observe_recovery_authorized(struct proc *p)
{
	return p != 0 && proc_teardown_live(p) && p->is_agent &&
	       p->agent_role == AGENT_ROLE_RECOVERY &&
	       exec_policy_process_bootstrap(p) &&
	       p->agent_control_id == agent_observe_recovery_binding.control_id &&
	       workflow_lifecycle_key_equal(vfs_proc_lifecycle(p),
				    agent_observe_recovery_binding.lifecycle);
}

static int
agent_observe_recovery_find_scope(
	struct workflow_lifecycle_key key, uint *slot_out,
	struct agent_obsstore_scope_view *scope_out, uint64 *bank_generation)
{
	uint64 snapshot_generation = 0;
	uint scope_capacity = agent_obsstore_snapshot_scope_capacity();
	int found = 1;

	if (slot_out == 0 || scope_out == 0 || bank_generation == 0 ||
	    scope_capacity == 0 ||
	    agent_obsstore_snapshot_begin(&snapshot_generation) < 0)
		return -1;
	for (uint slot = 0; slot < scope_capacity; slot++) {
		struct agent_obsstore_scope_view scope;
		int result = agent_obsstore_snapshot_scope(
			snapshot_generation, slot, &scope);

		if (result < 0)
			return -1;
		if (result == AGENT_OBSSTORE_SNAPSHOT_RETRY)
			return 2;
		if (result != AGENT_OBSSTORE_SNAPSHOT_READY ||
		    !workflow_lifecycle_key_equal(scope.lifecycle, key))
			continue;
		*slot_out = slot;
		*scope_out = scope;
		found = 0;
		break;
	}
	{
		int confirmed = agent_obsstore_snapshot_confirm(snapshot_generation);

		if (confirmed < 0)
			return -1;
		if (confirmed == AGENT_OBSSTORE_SNAPSHOT_RETRY)
			return 2;
	}
	*bank_generation = snapshot_generation;
	return found;
}

int
sys_agent_observe_recovery(uint64 requestaddr, uint64 recordsaddr)
{
	struct proc *p = curr_proc();
	struct agent_observe_recovery_request request;
	struct agent_observe_reap_cookie reap_cookie;
	struct agent_obsstore_scope_view scope;
	struct workflow_lifecycle_key evidence;
	uint64 bank_generation = 0;
	uint64 resumed_token = 0;
	uint slot = 0;
	uint returned = 0;
	int operation_valid;
	int reap_delivery = 0;
	int reap_resume = 0;
	int status = AGENT_STATUS_OK;

	if (p == 0 ||
	    user_range_check(p->pagetable, requestaddr, sizeof(request), PTE_W) < 0 ||
	    copyin(p->pagetable, (char *)&request, requestaddr,
		   sizeof(request)) < 0)
		return -1;
	if (request.version != AGENT_OBSERVE_RECOVERY_VERSION_V1 &&
	    request.version != AGENT_OBSERVE_RECOVERY_VERSION)
		return AGENT_STATUS_BAD_VERSION;
	if (request.size != sizeof(request))
		return AGENT_STATUS_BAD_SIZE;
	operation_valid = request.operation >= AGENT_OBSERVE_RECOVERY_LIST &&
			  request.operation <= AGENT_OBSERVE_RECOVERY_STATUS;
#ifdef AGENT_OBSERVE_TEST_PROFILE
	operation_valid |= agent_observe_test_operation(request.operation);
#endif
	if (request.flags != AGENT_OBSERVE_RECOVERY_F_NONE ||
	    request.reserved != 0 || request.bank_generation != 0 ||
	    request.returned != 0 || request.status != 0 || !operation_valid)
		return AGENT_STATUS_BAD_PARAM;
	if (!agent_observe_recovery_authorized(p))
		return AGENT_STATUS_DENIED;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_RETRY;
	evidence.id = request.evidence.id;
	evidence.generation = request.evidence.generation;
#ifdef AGENT_OBSERVE_TEST_PROFILE
	if (agent_observe_test_execute(&request, recordsaddr,
				       &bank_generation, &returned, &status))
		goto complete;
#endif
	if (request.operation == AGENT_OBSERVE_RECOVERY_LIST) {
		uint64 snapshot_generation = 0;
		uint scope_capacity = agent_obsstore_snapshot_scope_capacity();

		if (request.evidence.id != 0 || request.evidence.generation != 0 ||
		    request.evidence.reserved != 0 || request.after_sequence != 0 ||
		    request.completion_token != 0 ||
		    request.max_records > AGENT_OBSERVE_RECOVERY_MAX_SCOPES)
			status = AGENT_STATUS_BAD_PARAM;
		else if (request.max_records != 0 &&
			 (recordsaddr == 0 ||
			  user_range_check(
				  p->pagetable, recordsaddr,
				  request.max_records *
					  sizeof(struct agent_observe_recovery_scope),
				  PTE_W) < 0))
			status = AGENT_STATUS_BAD_PARAM;
		else if (agent_obsstore_snapshot_begin(&snapshot_generation) < 0)
			status = AGENT_STATUS_IO_ERROR;
		for (uint i = 0; status == AGENT_STATUS_OK &&
			     i < scope_capacity; i++) {
			struct agent_observe_recovery_scope out;
			int result = agent_obsstore_snapshot_scope(
				snapshot_generation, i, &scope);

			if (result < 0) {
				status = AGENT_STATUS_IO_ERROR;
				break;
			}
			if (result == AGENT_OBSSTORE_SNAPSHOT_RETRY) {
				status = AGENT_STATUS_RETRY;
				returned = 0;
				break;
			}
			if (result != AGENT_OBSSTORE_SNAPSHOT_READY)
				continue;
			if (returned < request.max_records) {
				memset(&out, 0, sizeof(out));
				out.scope_id = scope.scope_id;
				out.record_count = scope.record_count;
				out.lifecycle.id = scope.lifecycle.id;
				out.lifecycle.generation = scope.lifecycle.generation;
				out.total_records = scope.total_records;
				out.dropped_records = scope.dropped_records;
				out.ledger_hash = scope.ledger_hash;
				if (copyout(
					    p->pagetable,
					    recordsaddr + returned * sizeof(out),
					    (char *)&out, sizeof(out)) < 0) {
					status = AGENT_STATUS_BAD_PARAM;
					break;
				}
			}
			returned++;
		}
		if (status == AGENT_STATUS_OK) {
			int confirmed = agent_obsstore_snapshot_confirm(
				snapshot_generation);

			if (confirmed < 0)
				status = AGENT_STATUS_IO_ERROR;
			else if (confirmed == AGENT_OBSSTORE_SNAPSHOT_RETRY) {
				status = AGENT_STATUS_RETRY;
				returned = 0;
			} else
				bank_generation = snapshot_generation;
		}
	} else if (!workflow_lifecycle_key_valid(evidence) ||
		   request.evidence.reserved != 0) {
		status = AGENT_STATUS_BAD_PARAM;
	} else if (request.operation == AGENT_OBSERVE_RECOVERY_STATUS) {
		int replicated;

		if (request.after_sequence != 0 || request.max_records != 0 ||
		    recordsaddr != 0 || request.completion_token == 0 ||
		    agent_obsstore_reap_query(
			    evidence, request.completion_token, &replicated,
			    &bank_generation, &reap_cookie) < 0)
			status = AGENT_STATUS_BAD_PARAM;
		else {
			status = replicated > 0 ? AGENT_STATUS_OK :
				 replicated < 0 ? replicated : AGENT_STATUS_RETRY;
			reap_delivery = status == AGENT_STATUS_OK;
		}
	} else if (request.operation == AGENT_OBSERVE_RECOVERY_REAP &&
		   request.after_sequence == 0 && request.max_records == 0 &&
		   recordsaddr == 0 && request.completion_token == 0 &&
		   (reap_resume = agent_obsstore_recovery_reap_resume(
			    evidence, &resumed_token, &bank_generation)) != 0) {
		status = reap_resume > 0 ? AGENT_STATUS_OK : AGENT_STATUS_IO_ERROR;
		request.completion_token = resumed_token;
	} else {
		int found = agent_observe_recovery_find_scope(
			evidence, &slot, &scope, &bank_generation);

		if (found < 0)
			status = AGENT_STATUS_IO_ERROR;
		else if (found == 2)
			status = AGENT_STATUS_RETRY;
		else if (found > 0)
			status = AGENT_STATUS_NOT_FOUND;
		else if (request.operation == AGENT_OBSERVE_RECOVERY_READ) {
			uint record_bytes = request.version ==
				AGENT_OBSERVE_RECOVERY_VERSION_V1 ?
					sizeof(struct agent_audit_record) :
					sizeof(struct agent_observe_recovery_record);

			if (request.completion_token != 0 ||
			    request.max_records >
				    agent_obsstore_snapshot_record_capacity() ||
			    (request.max_records != 0 &&
			     (recordsaddr == 0 ||
			      user_range_check(
				      p->pagetable, recordsaddr,
				      request.max_records * record_bytes, PTE_W) < 0)))
				status = AGENT_STATUS_BAD_PARAM;
			for (uint i = 0; status == AGENT_STATUS_OK &&
				     i < scope.record_count; i++) {
				int result = agent_obsstore_snapshot_record(
					bank_generation, slot, i, scope.scope_id,
					evidence, &agent_observe_recovery_entry);

				if (result < 0) {
					status = AGENT_STATUS_IO_ERROR;
					break;
				}
				if (result == AGENT_OBSSTORE_SNAPSHOT_RETRY) {
					status = AGENT_STATUS_RETRY;
					returned = 0;
					break;
				}
				if (result != AGENT_OBSSTORE_SNAPSHOT_READY) {
					status = AGENT_STATUS_IO_ERROR;
					break;
				}
				if (agent_observe_recovery_entry.record.sequence <=
				    request.after_sequence)
					continue;
				if (returned < request.max_records) {
					uint64 output = recordsaddr +
						returned * record_bytes;

					if (copyout(
						    p->pagetable, output,
						    (char *)&agent_observe_recovery_entry.record,
						    sizeof(struct agent_audit_record)) < 0) {
						status = AGENT_STATUS_BAD_PARAM;
						break;
					}
					if (request.version ==
					    AGENT_OBSERVE_RECOVERY_VERSION) {
						struct agent_observe_recovery_tail tail;

						tail.receipt_id =
							agent_observe_recovery_entry.receipt_id;
						tail.bank_generation = bank_generation;
						tail.durability =
							AGENT_AUDIT_DURABILITY_DURABLE;
						tail.reserved = 0;
						if (copyout(
							    p->pagetable,
							    output + sizeof(
								     struct agent_audit_record),
							    (char *)&tail,
							    sizeof(tail)) < 0) {
							status = AGENT_STATUS_BAD_PARAM;
							break;
						}
					}
				}
				returned++;
			}
			if (status == AGENT_STATUS_OK) {
				int confirmed = agent_obsstore_snapshot_confirm(
					bank_generation);

				if (confirmed < 0)
					status = AGENT_STATUS_IO_ERROR;
				else if (confirmed ==
					 AGENT_OBSSTORE_SNAPSHOT_RETRY) {
					status = AGENT_STATUS_RETRY;
					returned = 0;
					bank_generation = 0;
				}
			}
		} else if (request.operation == AGENT_OBSERVE_RECOVERY_REAP) {
			uint64 completion_token = 0;

			if (request.after_sequence != 0 || request.max_records != 0 ||
			    recordsaddr != 0 || request.completion_token != 0)
				status = AGENT_STATUS_BAD_PARAM;
			else if (agent_obsstore_recovery_reap(
					 scope.scope_id, evidence,
					 &completion_token,
					 &bank_generation) < 0)
				status = AGENT_STATUS_RETRY;
			else
				request.completion_token = completion_token;
		}
	}
#ifdef AGENT_OBSERVE_TEST_PROFILE
complete:
#endif
	request.returned = returned;
	request.bank_generation = bank_generation;
	request.status = status;
	if (copyout(p->pagetable, requestaddr, (char *)&request,
		    sizeof(request)) < 0) {
		agent_metadata_txn_unlock();
		return -1;
	}
	if (reap_delivery && agent_obsstore_reap_consume(&reap_cookie) < 0) {
		status = request.status = AGENT_STATUS_RETRY;
		if (copyout(p->pagetable, requestaddr, (char *)&request,
			    sizeof(request)) < 0) {
			agent_metadata_txn_unlock();
			return -1;
		}
	}
	agent_metadata_txn_unlock();
	return status;
}
