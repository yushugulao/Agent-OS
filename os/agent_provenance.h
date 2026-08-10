#ifndef AGENT_PROVENANCE_H
#define AGENT_PROVENANCE_H

#include "../agent_provenance_abi.h"
#include "types.h"
#include "workflow_lifecycle.h"

struct agent_context_record;
struct proc;

struct agent_provenance_request {
	struct workflow_lifecycle_key lifecycle;
	uint64 contract_generation;
	uint64 request_id;
	uint64 source_context_sequence;
	uint64 source_node_id;
	uint64 target_node_id;
	uint64 attempt_id;
	uchar input_fingerprint[AGENT_PROVENANCE_FINGERPRINT_SIZE];
	uint64 declared_side_effect_mask;
	int tool_id;
	uint flags;
};

struct agent_provenance_decision {
	uint64 input_labels;
	uint64 output_labels;
	uint64 side_effect_mask;
	uint64 request_id;
	uint64 source_node_id;
	uint64 target_node_id;
	int tool_id;
	uint reason;
	int status;
};

int agent_provenance_authorize_tool(
	struct proc *, const struct agent_provenance_request *,
	const struct agent_provenance_manifest *,
	struct agent_provenance_decision *);
int agent_provenance_prepare_denial(
	const struct agent_provenance_request *, int, uint, uint64,
	struct agent_provenance_decision *);
int agent_provenance_commit_tool_output(
	struct proc *, const struct agent_provenance_decision *, int);
int agent_provenance_append_security_denial(
	struct proc *, const struct agent_provenance_request *,
	const struct agent_provenance_decision *, uint64 *);

uint64 agent_provenance_current_labels(struct proc *);
int agent_provenance_merge_current(struct proc *, uint64);
uint64 agent_provenance_ipc_output_labels(struct proc *, int);
void agent_provenance_mailbox_publish(struct proc *, uint64);
uint64 agent_provenance_mailbox_take(struct proc *);

uint64 agent_provenance_context_flags(struct proc *, uint64, int, uint64);
void agent_provenance_context_committed(struct proc *, uint64, int, uint64);
void agent_provenance_context_restore(struct proc *, uint64);
void agent_provenance_proc_reset(struct proc *);

#endif
