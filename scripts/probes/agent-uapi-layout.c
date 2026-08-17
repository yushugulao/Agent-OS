#include <agent.h>
#include "agent_file_publish_abi.h"
#include "agent_task_channel_abi.h"

#define ABI_SIZE(type, name) \
	unsigned char agent_uapi_layout_size_##name[sizeof(type)]
#define ABI_OFFSET(type, field, name) \
	unsigned char agent_uapi_layout_offset_##name##_##field \
		[__builtin_offsetof(type, field) + 1]
#define ABI_VALUE(value, name) \
	unsigned char agent_uapi_layout_value_##name[(value)]

#define ABI_RECORD(type, name, first, last) \
	ABI_SIZE(type, name); \
	ABI_OFFSET(type, first, name); \
	ABI_OFFSET(type, last, name)

ABI_RECORD(struct agent_workflow_lifecycle_key, lifecycle_key, id, generation);
ABI_RECORD(struct agent_workflow_lifecycle_info, lifecycle_info, version,
	   metadata_txn_waiters);
ABI_OFFSET(struct agent_workflow_lifecycle_info, struct_size, lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, key, lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, context_lane_depth,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, metadata_txn_owned,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, resource_account_valid,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, resource_account_slot,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, resource_account_generation,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_mode,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_flags,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_latency_class,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_weight,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_runnable,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_request_ticks,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_remaining_cycles,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_lag_cycles,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_vruntime,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_virtual_deadline,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_dispatches,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_service_cycles,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_sleep_decays,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_eligibility_misses,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_fallbacks,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_max_wakeup_ticks,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_deadline_misses,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info, scheduler_wakeup_samples,
	   lifecycle_info);
ABI_OFFSET(struct agent_workflow_lifecycle_info,
	   scheduler_wakeup_latency_buckets, lifecycle_info);
ABI_VALUE(AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION,
	  workflow_lifecycle_info_version);
ABI_VALUE(AGENT_WORKFLOW_LIFECYCLE_INFO_V2_VERSION,
	  workflow_lifecycle_info_v2_version);
ABI_VALUE(AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE,
	  workflow_lifecycle_info_v2_size);
ABI_VALUE(AGENT_WORKFLOW_SCHED_MODE_EEVDF, workflow_sched_mode_eevdf);
ABI_VALUE(AGENT_WORKFLOW_SCHED_MODE_FALLBACK, workflow_sched_mode_fallback);
ABI_VALUE(AGENT_WORKFLOW_LATENCY_INTERACTIVE,
	  workflow_latency_interactive);
ABI_VALUE(AGENT_WORKFLOW_LATENCY_NORMAL, workflow_latency_normal);
ABI_VALUE(AGENT_WORKFLOW_LATENCY_BATCH, workflow_latency_batch);
ABI_VALUE(AGENT_WORKFLOW_WAKE_BUCKET_COUNT, workflow_wake_bucket_count);

ABI_VALUE(AGENT_EXECUTION_CONTRACT_VERSION, execution_contract_version);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_NODE_VERSION,
	  execution_contract_node_version);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_MAX_NODES,
	  execution_contract_max_nodes);
ABI_VALUE(AGENT_EXECUTION_DIGEST_SIZE, execution_digest_size);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_CREATE, execution_contract_create);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_QUERY, execution_contract_query);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_RETIRE, execution_contract_retire);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_F_ENFORCE,
	  execution_contract_f_enforce);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_FROZEN, execution_contract_frozen);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_RETIRING, execution_contract_retiring);
ABI_VALUE(AGENT_EXECUTION_CONTRACT_RECLAIMED,
	  execution_contract_reclaimed);
ABI_VALUE(AGENT_EXECUTION_NODE_BLOCKED, execution_node_blocked);
ABI_VALUE(AGENT_EXECUTION_NODE_READY, execution_node_ready);
ABI_VALUE(AGENT_EXECUTION_NODE_RUNNING, execution_node_running);
ABI_VALUE(AGENT_EXECUTION_NODE_SUCCEEDED, execution_node_succeeded);
ABI_VALUE(AGENT_EXECUTION_NODE_FAILED, execution_node_failed);
ABI_VALUE(AGENT_EXECUTION_NODE_CANCELLED, execution_node_cancelled);
ABI_VALUE(AGENT_EXECUTION_RETRY_ALL, execution_retry_all);
ABI_VALUE(AGENT_EXECUTION_CANCEL_ALLOW, execution_cancel_allow);
ABI_VALUE(AGENT_ARTIFACT_BYTES, artifact_bytes);
ABI_VALUE(AGENT_ARTIFACT_UTF8, artifact_utf8);
ABI_VALUE(AGENT_ARTIFACT_JSON, artifact_json);
ABI_VALUE(AGENT_ARTIFACT_FILE, artifact_file);
ABI_VALUE(AGENT_ARTIFACT_MESSAGE, artifact_message);
ABI_VALUE(AGENT_ARTIFACT_TASK, artifact_task);
ABI_VALUE(AGENT_ARTIFACT_OPAQUE_HANDLE, artifact_opaque_handle);
ABI_VALUE(AGENT_ARTIFACT_WORKSPACE_MUTATION,
	  artifact_workspace_mutation);
ABI_VALUE(AGENT_ARTIFACT_TYPE_COUNT, artifact_type_count);
ABI_VALUE(AGENT_EXECUTION_REASON_DEPENDENCY_FAILED,
	  execution_reason_dependency_failed);
ABI_VALUE(AGENT_CALL_VERSION_V3, call_version_v3);
ABI_VALUE(AGENT_RESPONSE_V3_F_CACHED, response_v3_f_cached);

ABI_RECORD(struct agent_execution_contract_key, execution_contract_key,
	   lifecycle, generation);
ABI_RECORD(struct agent_execution_contract_node, execution_contract_node,
	   version, reserved_tail);
ABI_OFFSET(struct agent_execution_contract_node, size,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, node_id,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, tool_id,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, predecessor_mask,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, required_capabilities,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, accepted_input_labels,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, output_add_labels,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, side_effect_mask,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, schema_digest,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, deadline_tick,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, input_artifact_type,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, output_artifact_type,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, max_attempts,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, retry_policy,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, cancel_policy,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, charge_class,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, flags,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, exec_envelope,
	   execution_contract_node);
ABI_OFFSET(struct agent_execution_contract_node, storage_envelope,
	   execution_contract_node);
ABI_RECORD(struct agent_execution_contract_control,
	   execution_contract_control, version, reserved);
ABI_OFFSET(struct agent_execution_contract_control, size,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, operation,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, flags,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, key,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, request_id,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, contract_fingerprint,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, deadline_tick,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, nodes,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, node_count,
	   execution_contract_control);
ABI_OFFSET(struct agent_execution_contract_control, node_size,
	   execution_contract_control);
ABI_RECORD(struct agent_execution_contract_result, execution_contract_result,
	   version, reserved);
ABI_OFFSET(struct agent_execution_contract_result, size,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, status,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, state,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, key,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, request_id,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, contract_fingerprint,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, deadline_tick,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, created_tick,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, completed_mask,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, failed_mask,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, running_mask,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, node_count,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, flags,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, denial_count,
	   execution_contract_result);
ABI_OFFSET(struct agent_execution_contract_result, replay_count,
	   execution_contract_result);
ABI_RECORD(struct agent_request_v3, request_v3, version, reserved);
ABI_OFFSET(struct agent_request_v3, contract, request_v3);
ABI_OFFSET(struct agent_request_v3, node_id, request_v3);
ABI_OFFSET(struct agent_request_v3, attempt_id, request_v3);
ABI_OFFSET(struct agent_request_v3, input_fingerprint, request_v3);
ABI_OFFSET(struct agent_request_v3, source_context_sequence, request_v3);
ABI_OFFSET(struct agent_request_v3, schema_digest, request_v3);
ABI_OFFSET(struct agent_request_v3, input_artifact_type, request_v3);
ABI_OFFSET(struct agent_request_v3, source_node_id, request_v3);
ABI_OFFSET(struct agent_request_v3, source_control_id, request_v3);
ABI_OFFSET(struct agent_request_v3, source_pid, request_v3);
ABI_OFFSET(struct agent_request_v3, source_reserved, request_v3);
ABI_RECORD(struct agent_response_v3, response_v3, version,
	   output_provenance_labels);
ABI_OFFSET(struct agent_response_v3, contract, response_v3);
ABI_OFFSET(struct agent_response_v3, input_fingerprint, response_v3);
ABI_OFFSET(struct agent_response_v3, source_context_sequence, response_v3);
ABI_OFFSET(struct agent_response_v3, evidence_ticket, response_v3);
ABI_OFFSET(struct agent_response_v3, node_id, response_v3);
ABI_OFFSET(struct agent_response_v3, attempt_id, response_v3);
ABI_OFFSET(struct agent_response_v3, output_artifact_type, response_v3);
ABI_OFFSET(struct agent_response_v3, decision_reason, response_v3);
ABI_OFFSET(struct agent_response_v3, completion_flags, response_v3);

ABI_VALUE(AGENT_PROVENANCE_FINGERPRINT_SIZE, provenance_fingerprint_size);
ABI_VALUE(AGENT_PROVENANCE_KERNEL_FACT, provenance_kernel_fact);
ABI_VALUE(AGENT_PROVENANCE_TRUSTED_USER_CONTROL,
	  provenance_trusted_user_control);
ABI_VALUE(AGENT_PROVENANCE_AGENT_DERIVED, provenance_agent_derived);
ABI_VALUE(AGENT_PROVENANCE_UNTRUSTED_FILE_DATA,
	  provenance_untrusted_file_data);
ABI_VALUE(AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT,
	  provenance_untrusted_tool_output);
ABI_VALUE(AGENT_PROVENANCE_CROSS_AGENT_DATA, provenance_cross_agent_data);
ABI_VALUE(AGENT_PROVENANCE_ALL, provenance_all);
ABI_VALUE(AGENT_SIDE_EFFECT_FILE, side_effect_file);
ABI_VALUE(AGENT_SIDE_EFFECT_METADATA, side_effect_metadata);
ABI_VALUE(AGENT_SIDE_EFFECT_IPC, side_effect_ipc);
ABI_VALUE(AGENT_SIDE_EFFECT_PROCESS, side_effect_process);
ABI_VALUE(AGENT_SIDE_EFFECT_PERMISSION, side_effect_permission);
ABI_VALUE(AGENT_SIDE_EFFECT_ARTIFACT, side_effect_artifact);
ABI_VALUE(AGENT_SIDE_EFFECT_WATCH, side_effect_watch);
ABI_VALUE(AGENT_SIDE_EFFECT_ALL, side_effect_all);
ABI_VALUE(AGENT_CONTEXT_PROVENANCE_SHIFT, context_provenance_shift);
ABI_VALUE(AGENT_PROVENANCE_AUTH_F_ALL, provenance_auth_f_all);
ABI_VALUE(AGENT_PROVENANCE_DENY_EVIDENCE_UNAVAILABLE,
	  provenance_deny_evidence_unavailable);
ABI_RECORD(struct agent_provenance_manifest, provenance_manifest,
	   accepted_input_labels, side_effect_mask);
ABI_OFFSET(struct agent_provenance_manifest, output_add_labels,
	   provenance_manifest);
ABI_OFFSET(struct agent_provenance_manifest, required_capabilities,
	   provenance_manifest);

ABI_VALUE(AGENT_FILE_PUBLISH_SYSCALL, file_publish_syscall);
ABI_VALUE(AGENT_FILE_PUBLISH_VERSION, file_publish_version);
ABI_VALUE(AGENT_FILE_PUBLISH_MAX_BYTES, file_publish_max_bytes);
ABI_RECORD(struct agent_file_publish_request, file_publish_request,
	   version, reserved_tail);
ABI_OFFSET(struct agent_file_publish_request, size, file_publish_request);
ABI_OFFSET(struct agent_file_publish_request, flags, file_publish_request);
ABI_OFFSET(struct agent_file_publish_request, reserved,
	   file_publish_request);
ABI_OFFSET(struct agent_file_publish_request, path, file_publish_request);
ABI_OFFSET(struct agent_file_publish_request, header, file_publish_request);
ABI_OFFSET(struct agent_file_publish_request, payload, file_publish_request);
ABI_OFFSET(struct agent_file_publish_request, header_size,
	   file_publish_request);
ABI_OFFSET(struct agent_file_publish_request, payload_size,
	   file_publish_request);

ABI_VALUE(AGENT_TASK_CHANNEL_SETUP_SYSCALL, task_channel_setup_syscall);
ABI_VALUE(AGENT_TASK_CHANNEL_ENTER_SYSCALL, task_channel_enter_syscall);
ABI_VALUE(AGENT_TASK_CHANNEL_RESOURCE_SYSCALL, task_channel_resource_syscall);
ABI_VALUE(AGENT_TASK_DELEGATE_CLAIM_SYSCALL, task_delegate_claim_syscall);
ABI_VALUE(AGENT_TASK_DELEGATE_COMPLETE_SYSCALL,
	  task_delegate_complete_syscall);
ABI_VALUE(AGENT_RUNTIME_CONTROL_SYSCALL, runtime_control_syscall);
ABI_VALUE(AGENT_CONTEXT_ARTIFACT_SYSCALL, context_artifact_syscall);
ABI_VALUE(AGENT_CONTEXT_PREFETCH_SYSCALL, context_prefetch_syscall);
ABI_VALUE(AGENT_TASK_CHANNEL_VERSION, task_channel_version);
ABI_VALUE(AGENT_TASK_CHANNEL_ENTRY_VERSION, task_channel_entry_version);
ABI_VALUE(AGENT_TASK_CHANNEL_CAPACITY, task_channel_capacity);
ABI_VALUE(AGENT_TASK_CHANNEL_SCHEMA_SIZE, task_channel_schema_size);
ABI_VALUE(AGENT_TASK_RESOURCE_UTF8_MAX, task_resource_utf8_max);
ABI_VALUE(AGENT_TASK_DELEGATE_VERSION, task_delegate_version);
ABI_VALUE(AGENT_TASK_DELEGATE_DESCRIPTOR_VERSION,
	  task_delegate_descriptor_version);
ABI_VALUE(AGENT_TASK_DELEGATE_CLAIM_F_WAIT,
	  task_delegate_claim_f_wait);
ABI_VALUE(AGENT_TASK_DELEGATE_CLAIM_F_ALL,
	  task_delegate_claim_f_all);
ABI_VALUE(AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL,
	  task_delegate_complete_f_ack_terminal);
ABI_VALUE(AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL,
	  task_delegate_complete_f_request_cancel);
ABI_VALUE(AGENT_TASK_DELEGATE_COMPLETE_F_QUERY_TERMINAL,
	  task_delegate_complete_f_query_terminal);
ABI_VALUE(AGENT_TASK_DELEGATE_COMPLETE_F_ALL,
	  task_delegate_complete_f_all);
ABI_VALUE(((AGENT_IPC_ROUTE_MASK >> 0) & 0xffU) + 1U,
	  agent_ipc_route_mask_byte0_plus_one);
ABI_VALUE(((AGENT_IPC_ROUTE_MASK >> 8) & 0xffU) + 1U,
	  agent_ipc_route_mask_byte1_plus_one);
ABI_VALUE(((AGENT_IPC_ROUTE_MASK >> 16) & 0xffU) + 1U,
	  agent_ipc_route_mask_byte2_plus_one);
ABI_VALUE(((AGENT_IPC_ROUTE_MASK >> 24) & 0xffU) + 1U,
	  agent_ipc_route_mask_byte3_plus_one);
ABI_VALUE(((AGENT_IPC_ROUTE_MASK >> 32) & 0xffU) + 1U,
	  agent_ipc_route_mask_byte4_plus_one);
ABI_VALUE(((AGENT_IPC_ROUTE_MASK >> 40) & 0xffU) + 1U,
	  agent_ipc_route_mask_byte5_plus_one);
ABI_VALUE(((AGENT_IPC_ROUTE_MASK >> 48) & 0xffU) + 1U,
	  agent_ipc_route_mask_byte6_plus_one);
ABI_VALUE(((AGENT_IPC_ROUTE_MASK >> 56) & 0xffU) + 1U,
	  agent_ipc_route_mask_byte7_plus_one);
ABI_VALUE(AGENT_TASK_DELEGATE_STATE_QUEUED,
	  task_delegate_state_queued);
ABI_VALUE(AGENT_TASK_DELEGATE_STATE_CLAIMED,
	  task_delegate_state_claimed);
ABI_VALUE(AGENT_TASK_DELEGATE_STATE_READY,
	  task_delegate_state_ready);
ABI_VALUE(AGENT_TASK_DELEGATE_EXECUTOR_AGENT_SHIFT,
	  task_delegate_executor_agent_shift);
ABI_VALUE(AGENT_TASK_CHANNEL_RING_F_ALL, task_channel_ring_f_all);
ABI_VALUE(AGENT_TASK_CHANNEL_SETUP_F_SINGLE_ISSUER,
	  task_channel_setup_f_single_issuer);
ABI_VALUE(AGENT_TASK_CHANNEL_ENTER_F_ALL, task_channel_enter_f_all);
ABI_VALUE(AGENT_TASK_CHANNEL_OP_SUBMIT, task_channel_op_submit);
ABI_VALUE(AGENT_TASK_CHANNEL_OP_CANCEL, task_channel_op_cancel);
ABI_VALUE(AGENT_TASK_SQE_F_ALL, task_sqe_f_all);
ABI_VALUE(AGENT_TASK_CQE_F_ALL, task_cqe_f_all);
ABI_VALUE(AGENT_TASK_HANDLE_F_OWNED, task_handle_f_owned);
ABI_VALUE(AGENT_TASK_HANDLE_F_BORROWED, task_handle_f_borrowed);
ABI_VALUE(AGENT_TASK_HANDLE_F_ALL, task_handle_f_all);
ABI_VALUE(AGENT_TASK_RESOURCE_IMPORT, task_resource_import);
ABI_VALUE(AGENT_TASK_RESOURCE_RELEASE, task_resource_release);
ABI_VALUE(AGENT_TASK_RESOURCE_QUERY, task_resource_query);
ABI_VALUE(AGENT_TASK_RESOURCE_STATE_LIVE, task_resource_state_live);
ABI_VALUE(AGENT_TASK_RESOURCE_STATE_IN_FLIGHT,
	  task_resource_state_in_flight);

ABI_RECORD(struct agent_task_resource_handle, task_resource_handle,
	   slot, generation);
ABI_OFFSET(struct agent_task_resource_handle, type, task_resource_handle);
ABI_OFFSET(struct agent_task_resource_handle, flags, task_resource_handle);
ABI_RECORD(struct agent_task_ring_header, task_ring_header, magic,
	   reserved_tail);
ABI_OFFSET(struct agent_task_ring_header, version, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, struct_size, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, entry_size, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, capacity, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, generation, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, head, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, tail, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, flags, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, submitted, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, completed, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, backpressure, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, protocol_faults, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, resync_count, task_ring_header);
ABI_OFFSET(struct agent_task_ring_header, last_accepted_request_id,
	   task_ring_header);
ABI_RECORD(struct agent_task_sqe, task_sqe, version, schema_digest);
ABI_OFFSET(struct agent_task_sqe, size, task_sqe);
ABI_OFFSET(struct agent_task_sqe, opcode, task_sqe);
ABI_OFFSET(struct agent_task_sqe, flags, task_sqe);
ABI_OFFSET(struct agent_task_sqe, request_id, task_sqe);
ABI_OFFSET(struct agent_task_sqe, ring_generation, task_sqe);
ABI_OFFSET(struct agent_task_sqe, slot_generation, task_sqe);
ABI_OFFSET(struct agent_task_sqe, contract, task_sqe);
ABI_OFFSET(struct agent_task_sqe, node_id, task_sqe);
ABI_OFFSET(struct agent_task_sqe, attempt_id, task_sqe);
ABI_OFFSET(struct agent_task_sqe, tool_id, task_sqe);
ABI_OFFSET(struct agent_task_sqe, deadline_tick, task_sqe);
ABI_OFFSET(struct agent_task_sqe, link_request_id, task_sqe);
ABI_OFFSET(struct agent_task_sqe, input, task_sqe);
ABI_RECORD(struct agent_task_cqe, task_cqe, version, reserved);
ABI_OFFSET(struct agent_task_cqe, size, task_cqe);
ABI_OFFSET(struct agent_task_cqe, flags, task_cqe);
ABI_OFFSET(struct agent_task_cqe, status, task_cqe);
ABI_OFFSET(struct agent_task_cqe, decision_reason, task_cqe);
ABI_OFFSET(struct agent_task_cqe, request_id, task_cqe);
ABI_OFFSET(struct agent_task_cqe, ring_generation, task_cqe);
ABI_OFFSET(struct agent_task_cqe, slot_generation, task_cqe);
ABI_OFFSET(struct agent_task_cqe, contract, task_cqe);
ABI_OFFSET(struct agent_task_cqe, node_id, task_cqe);
ABI_OFFSET(struct agent_task_cqe, attempt_id, task_cqe);
ABI_OFFSET(struct agent_task_cqe, tool_id, task_cqe);
ABI_OFFSET(struct agent_task_cqe, result, task_cqe);
ABI_OFFSET(struct agent_task_cqe, context_sequence, task_cqe);
ABI_OFFSET(struct agent_task_cqe, evidence_ticket, task_cqe);
ABI_OFFSET(struct agent_task_cqe, provenance_labels, task_cqe);
ABI_OFFSET(struct agent_task_cqe, completion_tick, task_cqe);
ABI_RECORD(struct agent_task_channel_setup, task_channel_setup, version,
	   reserved_tail);
ABI_OFFSET(struct agent_task_channel_setup, size, task_channel_setup);
ABI_OFFSET(struct agent_task_channel_setup, flags, task_channel_setup);
ABI_OFFSET(struct agent_task_channel_setup, lifecycle, task_channel_setup);
ABI_RECORD(struct agent_task_channel_setup_result, task_channel_setup_result,
	   version, reserved_tail);
ABI_OFFSET(struct agent_task_channel_setup_result, size,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, status,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, flags,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, lifecycle,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, generation,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, sq_base,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, cq_base,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, sq_capacity,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, cq_capacity,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, sqe_size,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, cqe_size,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, mapped_page_count,
	   task_channel_setup_result);
ABI_OFFSET(struct agent_task_channel_setup_result, private_page_count,
	   task_channel_setup_result);
ABI_RECORD(struct agent_task_channel_enter, task_channel_enter, version,
	   reserved_tail);
ABI_OFFSET(struct agent_task_channel_enter, size, task_channel_enter);
ABI_OFFSET(struct agent_task_channel_enter, flags, task_channel_enter);
ABI_OFFSET(struct agent_task_channel_enter, max_submit, task_channel_enter);
ABI_OFFSET(struct agent_task_channel_enter, generation, task_channel_enter);
ABI_OFFSET(struct agent_task_channel_enter, sq_tail, task_channel_enter);
ABI_OFFSET(struct agent_task_channel_enter, cq_head, task_channel_enter);
ABI_OFFSET(struct agent_task_channel_enter, min_complete, task_channel_enter);
ABI_RECORD(struct agent_task_channel_enter_result,
	   task_channel_enter_result, version, last_accepted_request_id);
ABI_OFFSET(struct agent_task_channel_enter_result, size,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, status,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, flags,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, generation,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, sq_head,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, cq_head,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, cq_tail,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, submitted,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, completed,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, in_flight,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, terminal_pending,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, resource_count,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, protocol_faults,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, resync_count,
	   task_channel_enter_result);
ABI_OFFSET(struct agent_task_channel_enter_result, backpressure,
	   task_channel_enter_result);
ABI_RECORD(struct agent_task_channel_resource, task_channel_resource,
	   version, reserved_tail);
ABI_OFFSET(struct agent_task_channel_resource, size, task_channel_resource);
ABI_OFFSET(struct agent_task_channel_resource, operation,
	   task_channel_resource);
ABI_OFFSET(struct agent_task_channel_resource, flags, task_channel_resource);
ABI_OFFSET(struct agent_task_channel_resource, handle, task_channel_resource);
ABI_OFFSET(struct agent_task_channel_resource, resource_type,
	   task_channel_resource);
ABI_OFFSET(struct agent_task_channel_resource, resource_flags,
	   task_channel_resource);
ABI_OFFSET(struct agent_task_channel_resource, source_handle,
	   task_channel_resource);
ABI_OFFSET(struct agent_task_channel_resource, length, task_channel_resource);
ABI_OFFSET(struct agent_task_channel_resource, channel_generation,
	   task_channel_resource);
ABI_RECORD(struct agent_task_channel_resource_result,
	   task_channel_resource_result, version, reserved_tail);
ABI_OFFSET(struct agent_task_channel_resource_result, size,
	   task_channel_resource_result);
ABI_OFFSET(struct agent_task_channel_resource_result, status,
	   task_channel_resource_result);
ABI_OFFSET(struct agent_task_channel_resource_result, state,
	   task_channel_resource_result);
ABI_OFFSET(struct agent_task_channel_resource_result, handle,
	   task_channel_resource_result);
ABI_OFFSET(struct agent_task_channel_resource_result, source_handle,
	   task_channel_resource_result);
ABI_OFFSET(struct agent_task_channel_resource_result, length,
	   task_channel_resource_result);
ABI_OFFSET(struct agent_task_channel_resource_result, generation,
	   task_channel_resource_result);
ABI_OFFSET(struct agent_task_channel_resource_result, references,
	   task_channel_resource_result);
ABI_RECORD(struct agent_task_delegate_descriptor, task_delegate_descriptor,
	   version, deadline_tick);
ABI_OFFSET(struct agent_task_delegate_descriptor, size,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, target_pid,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, target_agent_id,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, task_type,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, target_control_id,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, task_id,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, correlation_id,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, parent_task_id,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, capsule_handle,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, input_artifact_handle,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, result_artifact_handle,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, expected_result_type,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, required_capabilities,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, allowed_tools,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, workspace_revision_sha256,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, resource_budget,
	   task_delegate_descriptor);
ABI_OFFSET(struct agent_task_delegate_descriptor, read_budget,
	   task_delegate_descriptor);

ABI_RECORD(struct agent_runtime_config, runtime_config, version, reserved_tail);
ABI_OFFSET(struct agent_runtime_config, operation, runtime_config);
ABI_OFFSET(struct agent_runtime_config, capabilities, runtime_config);
ABI_OFFSET(struct agent_runtime_config, allowed_tools, runtime_config);
ABI_OFFSET(struct agent_runtime_config, prompt_artifact_handle, runtime_config);
ABI_RECORD(struct agent_runtime_config_result, runtime_config_result,
	   version, reserved_final);
ABI_OFFSET(struct agent_runtime_config_result, status, runtime_config_result);
ABI_OFFSET(struct agent_runtime_config_result, capabilities,
	   runtime_config_result);
ABI_OFFSET(struct agent_runtime_config_result, allowed_tools,
	   runtime_config_result);

ABI_RECORD(struct agent_context_artifact_control, context_artifact_control,
	   version, reserved_tail);
ABI_OFFSET(struct agent_context_artifact_control, operation,
	   context_artifact_control);
ABI_OFFSET(struct agent_context_artifact_control, handle,
	   context_artifact_control);
ABI_OFFSET(struct agent_context_artifact_control, content_sha256,
	   context_artifact_control);
ABI_RECORD(struct agent_context_artifact_result, context_artifact_result,
	   version, content_sha256);
ABI_OFFSET(struct agent_context_artifact_result, status,
	   context_artifact_result);
ABI_OFFSET(struct agent_context_artifact_result, lifecycle,
	   context_artifact_result);

ABI_RECORD(struct agent_context_prefetch_control, context_prefetch_control,
	   version, reserved_tail);
ABI_OFFSET(struct agent_context_prefetch_control, operation,
	   context_prefetch_control);
ABI_OFFSET(struct agent_context_prefetch_control, context_sequence,
	   context_prefetch_control);
ABI_OFFSET(struct agent_context_prefetch_control, query_fingerprint,
	   context_prefetch_control);
ABI_RECORD(struct agent_context_prefetch_result, context_prefetch_result,
	   version, reserved_tail);
ABI_OFFSET(struct agent_context_prefetch_result, status,
	   context_prefetch_result);
ABI_OFFSET(struct agent_context_prefetch_result, target_query_fingerprint,
	   context_prefetch_result);
ABI_RECORD(struct agent_task_delegate_claim, task_delegate_claim,
	   version, reserved_tail);
ABI_OFFSET(struct agent_task_delegate_claim, size, task_delegate_claim);
ABI_OFFSET(struct agent_task_delegate_claim, flags, task_delegate_claim);
ABI_OFFSET(struct agent_task_delegate_claim, lifecycle,
	   task_delegate_claim);
ABI_RECORD(struct agent_task_delegate_claim_result,
	   task_delegate_claim_result, version, slot_generation);
ABI_OFFSET(struct agent_task_delegate_claim_result, size,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, status,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, state,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, lifecycle,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, descriptor,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, owner_pid,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, owner_agent_id,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, owner_control_id,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, channel_generation,
	   task_delegate_claim_result);
ABI_OFFSET(struct agent_task_delegate_claim_result, request_id,
	   task_delegate_claim_result);
ABI_RECORD(struct agent_task_delegate_complete, task_delegate_complete,
	   version, terminal_generation);
ABI_OFFSET(struct agent_task_delegate_complete, size,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, flags,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, lifecycle,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, owner_pid,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, terminal_status,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, owner_control_id,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, channel_generation,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, request_id,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, slot_generation,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, task_id,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, correlation_id,
	   task_delegate_complete);
ABI_OFFSET(struct agent_task_delegate_complete, ack_terminal_status,
	   task_delegate_complete);
ABI_RECORD(struct agent_task_delegate_complete_result,
	   task_delegate_complete_result, version, terminal_generation);
ABI_OFFSET(struct agent_task_delegate_complete_result, terminal_status,
	   task_delegate_complete_result);
ABI_OFFSET(struct agent_task_delegate_complete_result, size,
	   task_delegate_complete_result);
ABI_OFFSET(struct agent_task_delegate_complete_result, status,
	   task_delegate_complete_result);
ABI_OFFSET(struct agent_task_delegate_complete_result, state,
	   task_delegate_complete_result);
ABI_OFFSET(struct agent_task_delegate_complete_result, channel_generation,
	   task_delegate_complete_result);
ABI_OFFSET(struct agent_task_delegate_complete_result, request_id,
	   task_delegate_complete_result);
ABI_OFFSET(struct agent_task_delegate_complete_result, slot_generation,
	   task_delegate_complete_result);
ABI_OFFSET(struct agent_task_delegate_complete_result, task_id,
	   task_delegate_complete_result);
ABI_OFFSET(struct agent_task_delegate_complete_result, correlation_id,
	   task_delegate_complete_result);
ABI_VALUE(AGENT_RUN_F_FENCE, agent_run_f_fence);
ABI_VALUE(AGENT_WORKFLOW_FENCE_VERSION, workflow_fence_version);
ABI_VALUE(AGENT_WORKFLOW_FENCE_RECEIPT_F_PARTIAL_COVERAGE,
	  workflow_fence_receipt_f_partial_coverage);
ABI_VALUE(AGENT_WORKFLOW_FENCE_RECEIPT_F_CREDIT_EXACT,
	  workflow_fence_receipt_f_credit_exact);
ABI_VALUE(AGENT_WORKFLOW_FENCE_RECEIPT_F_EVIDENCE_SEALED,
	  workflow_fence_receipt_f_evidence_sealed);
ABI_VALUE(AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE,
	  workflow_fence_receipt_f_metadata_volatile);
ABI_VALUE(AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE,
	  workflow_fence_challenge_size);
ABI_VALUE(AGENT_WORKFLOW_FENCE_ROOT_SIZE, workflow_fence_root_size);
ABI_VALUE(AGENT_WORKFLOW_FENCE_RESOURCE_KINDS,
	  workflow_fence_resource_kinds);
ABI_RECORD(struct agent_workflow_fence_request, workflow_fence_request,
	   version, request_id);
ABI_OFFSET(struct agent_workflow_fence_request, struct_size,
	   workflow_fence_request);
ABI_OFFSET(struct agent_workflow_fence_request, flags,
	   workflow_fence_request);
ABI_OFFSET(struct agent_workflow_fence_request, reserved,
	   workflow_fence_request);
ABI_OFFSET(struct agent_workflow_fence_request, challenge,
	   workflow_fence_request);
ABI_RECORD(struct agent_workflow_credit_account_key,
	   workflow_credit_account_key, slot, generation);
ABI_OFFSET(struct agent_workflow_credit_account_key, reserved,
	   workflow_credit_account_key);
ABI_RECORD(struct agent_workflow_fence_receipt, workflow_fence_receipt,
	   version, evidence_root);
ABI_OFFSET(struct agent_workflow_fence_receipt, struct_size,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, status,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, flags,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, key,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, request_id,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, fence_sequence,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, metadata_generation,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, credit_epoch,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, evidence_first_sequence,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, evidence_last_sequence,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, evidence_event_count,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, evidence_dropped_success,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, resource_used,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, credit_exec_account,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, credit_storage_account,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, credit_digest,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, challenge,
	   workflow_fence_receipt);
ABI_OFFSET(struct agent_workflow_fence_receipt, previous_root,
	   workflow_fence_receipt);
ABI_RECORD(struct agent_op, op, version, payload);
ABI_RECORD(struct agent_result, result, version, result);
ABI_VALUE(AGENT_UAPI_ABI_VERSION, agent_uapi_abi_version);
ABI_VALUE(AGENT_OP_VERSION, agent_op_version);
ABI_SIZE(union agent_param_value_v2, param_value_v2);
ABI_RECORD(struct agent_param_v2, param_v2, version, value);
ABI_OFFSET(struct agent_param_v2, size, param_v2);
ABI_OFFSET(struct agent_param_v2, type, param_v2);
ABI_OFFSET(struct agent_param_v2, value_size, param_v2);
ABI_OFFSET(struct agent_param_v2, key, param_v2);
ABI_RECORD(struct agent_request_v2, request_v2, version, tool_name);
ABI_OFFSET(struct agent_request_v2, size, request_v2);
ABI_OFFSET(struct agent_request_v2, tool_id, request_v2);
ABI_OFFSET(struct agent_request_v2, param_count, request_v2);
ABI_OFFSET(struct agent_request_v2, request_id, request_v2);
ABI_OFFSET(struct agent_request_v2, params, request_v2);
ABI_RECORD(struct agent_response_v2, response_v2, version, result);
ABI_OFFSET(struct agent_response_v2, size, response_v2);
ABI_OFFSET(struct agent_response_v2, status, response_v2);
ABI_OFFSET(struct agent_response_v2, tool_name, response_v2);
ABI_RECORD(struct agent_tool_desc_v2, tool_desc_v2, version, description);
ABI_OFFSET(struct agent_tool_desc_v2, size, tool_desc_v2);
ABI_OFFSET(struct agent_tool_desc_v2, flags, tool_desc_v2);
ABI_OFFSET(struct agent_tool_desc_v2, name, tool_desc_v2);
ABI_OFFSET(struct agent_tool_desc_v2, params, tool_desc_v2);

ABI_VALUE(AGENT_WORKSPACE_MUTATION_VERSION,
	  workspace_mutation_version);
ABI_VALUE(AGENT_WORKSPACE_MUTATION_APPLY_PATCH,
	  workspace_mutation_apply_patch);
ABI_VALUE(AGENT_WORKSPACE_MUTATION_WRITE_FILE,
	  workspace_mutation_write_file);
ABI_VALUE(AGENT_WORKSPACE_PATH_SIZE, workspace_path_size);
ABI_VALUE(AGENT_WORKSPACE_SHA256_SIZE, workspace_sha256_size);
ABI_RECORD(struct agent_workspace_mutation_request,
	   workspace_mutation_request, version, path);
ABI_OFFSET(struct agent_workspace_mutation_request, operation,
	   workspace_mutation_request);
ABI_OFFSET(struct agent_workspace_mutation_request, request_id,
	   workspace_mutation_request);
ABI_OFFSET(struct agent_workspace_mutation_request, lifecycle,
	   workspace_mutation_request);
ABI_OFFSET(struct agent_workspace_mutation_request, object_id,
	   workspace_mutation_request);
ABI_OFFSET(struct agent_workspace_mutation_request, expected_revision,
	   workspace_mutation_request);
ABI_OFFSET(struct agent_workspace_mutation_request, content_artifact_handle,
	   workspace_mutation_request);
ABI_OFFSET(struct agent_workspace_mutation_request, content_sha256,
	   workspace_mutation_request);
ABI_RECORD(struct agent_workspace_mutation_receipt,
	   workspace_mutation_receipt, version, content_sha256);
ABI_OFFSET(struct agent_workspace_mutation_receipt, status,
	   workspace_mutation_receipt);
ABI_OFFSET(struct agent_workspace_mutation_receipt, request_id,
	   workspace_mutation_receipt);
ABI_OFFSET(struct agent_workspace_mutation_receipt, previous_revision,
	   workspace_mutation_receipt);
ABI_OFFSET(struct agent_workspace_mutation_receipt, new_revision,
	   workspace_mutation_receipt);
ABI_OFFSET(struct agent_workspace_mutation_receipt, written_size,
	   workspace_mutation_receipt);

ABI_RECORD(struct agent_info, info, is_agent, file_scan_failures);
ABI_OFFSET(struct agent_info, filesystem_capability_mask, info);
ABI_OFFSET(struct agent_info, file_scan_deferred, info);
ABI_RECORD(struct agent_sched_record, sched_record, tick, priority);
ABI_RECORD(struct agent_sched_config, sched_config, update_mask, budget);
ABI_RECORD(struct agent_trace_record, trace_record, tick, text);
ABI_RECORD(struct agent_audit_record, audit_record, sequence, text);
ABI_RECORD(struct agent_ledger_summary, ledger_summary, version, observe_epoch);
ABI_VALUE(AGENT_LEDGER_VERSION, ledger_version);
ABI_RECORD(struct agent_audit_filter, audit_filter, flags, status);
ABI_RECORD(struct agent_audit_receipt_request, audit_receipt_request,
	   version, reserved);
ABI_OFFSET(struct agent_audit_receipt_request, operation,
	   audit_receipt_request);
ABI_OFFSET(struct agent_audit_receipt_request, lifecycle,
	   audit_receipt_request);
ABI_OFFSET(struct agent_audit_receipt_request, sequence,
	   audit_receipt_request);
ABI_OFFSET(struct agent_audit_receipt_request, record_hash,
	   audit_receipt_request);
ABI_OFFSET(struct agent_audit_receipt_request, receipt_id,
	   audit_receipt_request);
ABI_OFFSET(struct agent_audit_receipt_request, timeout_ticks,
	   audit_receipt_request);
ABI_OFFSET(struct agent_audit_receipt_request, durability,
	   audit_receipt_request);
ABI_OFFSET(struct agent_audit_receipt_request, status,
	   audit_receipt_request);
ABI_RECORD(struct agent_timeline_record, timeline_record, tick, text);
ABI_RECORD(struct agent_timeline_filter, timeline_filter, flags, after_source);
ABI_RECORD(struct agent_provenance_edge, provenance_edge, span_id, text);
ABI_RECORD(struct agent_context_header, context_header, magic, eviction_policy);
ABI_RECORD(struct agent_context_record, context_record, sequence, result);
ABI_RECORD(struct agent_context_detail, context_detail, sequence, result);
ABI_RECORD(struct agent_event, event, type, payload);
ABI_RECORD(struct agent_file_meta, file_meta, used, update_mask);
ABI_VALUE(AGENT_FILE_META_F_PERSIST, file_meta_f_persist);
ABI_VALUE(AGENT_FILE_META_F_AUTOSCAN, file_meta_f_autoscan);
ABI_VALUE(AGENT_FILE_META_F_UNSUPPORTED_MASK, file_meta_f_unsupported_mask);
ABI_RECORD(struct agent_file_hit, file_hit, fid, fs_generation);
ABI_RECORD(struct agent_file_query, file_query, flags, summary_contains);
ABI_VALUE(AGENT_EVENT_FILE_QUERY, event_file_query);
ABI_VALUE(AGENT_FILE_LIVE_WATCH_VERSION, file_live_watch_version);
ABI_VALUE(AGENT_FILE_LIVE_WATCH_F_RESYNC_REQUIRED,
	  file_live_watch_f_resync_required);
ABI_VALUE(AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC,
	  file_live_watch_f_ack_resync);
ABI_RECORD(struct agent_file_live_watch, file_live_watch, version, query);
ABI_OFFSET(struct agent_file_live_watch, flags, file_live_watch);
ABI_OFFSET(struct agent_file_live_watch, watch_id, file_live_watch);
ABI_OFFSET(struct agent_file_live_watch, initial_generation, file_live_watch);
ABI_OFFSET(struct agent_file_live_watch, catalog_generation, file_live_watch);
ABI_OFFSET(struct agent_file_live_watch, resync_generation, file_live_watch);
ABI_RECORD(struct agent_file_query_result, file_query_result, total_hits, hits);
ABI_RECORD(struct agent_file_edit_state, file_edit_state, active, path);
ABI_RECORD(struct agent_performance_snapshot, performance_snapshot, version,
	   overwrite_prereads_skipped);
ABI_OFFSET(struct agent_performance_snapshot, struct_size,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, counter_scope,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, reserved, performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, sample_tick,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, observer_lifecycle_id,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, observer_lifecycle_generation,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, fs_epoch_commits,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, fs_epoch_buffers_staged,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, block_physical_writes,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, block_durable_flushes,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, fs_epoch_deduplicated_stages,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, cow_pages_shared,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, cow_pages_copied,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, cow_fault_promotions,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, exec_cache_hits,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, exec_cache_misses,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, exec_cache_shared_pages,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, exec_cache_evictions,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, observer_workload_syscalls,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, directory_block_probes,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, directory_entries_examined,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, virtio_notifications,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, virtio_submitted_requests,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, virtio_write_batch_calls,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, virtio_batched_write_requests,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot,
	   virtio_indirect_write_batch_calls, performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, virtio_read_batch_calls,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, virtio_batched_read_requests,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, block_physical_reads,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, file_auth_full,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, file_auth_lease_hits,
	   performance_snapshot);
ABI_OFFSET(struct agent_performance_snapshot, file_auth_revalidations,
	   performance_snapshot);

ABI_OFFSET(struct agent_context_header, records_offset, context_header);
ABI_OFFSET(struct agent_context_header, active_path_count, context_header);
ABI_OFFSET(struct agent_context_header, active_path_oldest_sequence,
	   context_header);
ABI_OFFSET(struct agent_context_record, record_hash, context_record);
ABI_OFFSET(struct agent_context_record, path_parent_sequence,
	   context_record);
ABI_OFFSET(struct agent_file_meta, dependency_mask, file_meta);
ABI_VALUE(AGENT_FILE_META_BATCH_MAX, file_meta_batch_max);
ABI_OFFSET(struct agent_file_query_result, fs_generation, file_query_result);
ABI_OFFSET(struct agent_file_query_result, index_rebuild_records, file_query_result);
