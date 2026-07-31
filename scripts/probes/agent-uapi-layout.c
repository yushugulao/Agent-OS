#include <agent.h>

#define ABI_SIZE(type, name) \
	unsigned char agent_uapi_layout_size_##name[sizeof(type)]
#define ABI_OFFSET(type, field, name) \
	unsigned char agent_uapi_layout_offset_##name##_##field \
		[__builtin_offsetof(type, field) + 1]

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
ABI_RECORD(struct agent_op, op, version, payload);
ABI_RECORD(struct agent_result, result, version, result);
ABI_RECORD(struct agent_request, request, version, payload);
ABI_RECORD(struct agent_response, response, version, result);
ABI_RECORD(struct agent_tool_desc, tool_desc, tool_id, params);
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

ABI_RECORD(struct agent_info, info, is_agent, file_scan_failures);
ABI_OFFSET(struct agent_info, filesystem_capability_mask, info);
ABI_OFFSET(struct agent_info, legacy_mailbox_allocated, info);
ABI_OFFSET(struct agent_info, legacy_mailbox_pages, info);
ABI_OFFSET(struct agent_info, file_scan_deferred, info);
ABI_RECORD(struct agent_sched_record, sched_record, tick, priority);
ABI_RECORD(struct agent_sched_config, sched_config, update_mask, budget);
ABI_RECORD(struct agent_trace_record, trace_record, tick, text);
ABI_RECORD(struct agent_audit_record, audit_record, sequence, text);
ABI_RECORD(struct agent_ledger_summary, ledger_summary, version, observe_epoch);
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
ABI_RECORD(struct agent_file_hit, file_hit, fid, fs_generation);
ABI_RECORD(struct agent_file_prefetch_hint, file_prefetch_hint, sequence, hit);
ABI_RECORD(struct agent_file_query, file_query, flags, summary_contains);
ABI_RECORD(struct agent_file_query_result, file_query_result, total_hits, hits);
ABI_RECORD(struct agent_file_edit_state, file_edit_state, active, path);
ABI_RECORD(struct agent_observe_recovery_scope, observe_recovery_scope,
	   scope_id, ledger_hash);
ABI_RECORD(struct agent_observe_recovery_request, observe_recovery_request,
	   version, reserved);
ABI_OFFSET(struct agent_observe_recovery_request, evidence,
	   observe_recovery_request);
ABI_OFFSET(struct agent_observe_recovery_request, bank_generation,
	   observe_recovery_request);

ABI_OFFSET(struct agent_context_header, records_offset, context_header);
ABI_OFFSET(struct agent_context_header, active_path_count, context_header);
ABI_OFFSET(struct agent_context_header, active_path_oldest_sequence,
	   context_header);
ABI_OFFSET(struct agent_context_record, record_hash, context_record);
ABI_OFFSET(struct agent_context_record, path_parent_sequence,
	   context_record);
ABI_OFFSET(struct agent_file_meta, dependency_mask, file_meta);
ABI_OFFSET(struct agent_file_query_result, fs_generation, file_query_result);
ABI_OFFSET(struct agent_file_query_result, index_rebuild_records, file_query_result);
