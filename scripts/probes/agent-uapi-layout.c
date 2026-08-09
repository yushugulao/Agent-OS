#include <agent.h>

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
ABI_OFFSET(struct agent_info, metadata_journal_txns, info);
ABI_OFFSET(struct agent_info, metadata_journal_blocks, info);
ABI_OFFSET(struct agent_info, metadata_compactions, info);
ABI_OFFSET(struct agent_info, metadata_full_cow_blocks, info);
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
ABI_VALUE(AGENT_OBSERVE_RECOVERY_COMPAT_TOMBSTONE,
	  observe_recovery_compat_tombstone);
ABI_VALUE(AGENT_OBSERVE_RECOVERY_VERSION_V1, observe_recovery_version_v1);
ABI_VALUE(AGENT_OBSERVE_RECOVERY_VERSION, observe_recovery_version);
ABI_VALUE(AGENT_OBSERVE_RECOVERY_LIST, observe_recovery_list);
ABI_VALUE(AGENT_OBSERVE_RECOVERY_READ, observe_recovery_read);
ABI_VALUE(AGENT_OBSERVE_RECOVERY_REAP, observe_recovery_reap);
ABI_VALUE(AGENT_OBSERVE_RECOVERY_STATUS, observe_recovery_status);
ABI_RECORD(struct agent_observe_recovery_scope, observe_recovery_scope,
	   scope_id, ledger_hash);
ABI_RECORD(struct agent_observe_recovery_request, observe_recovery_request,
	   version, reserved);
ABI_OFFSET(struct agent_observe_recovery_request, evidence,
	   observe_recovery_request);
ABI_OFFSET(struct agent_observe_recovery_request, bank_generation,
	   observe_recovery_request);

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
ABI_OFFSET(struct agent_file_query_result, fs_generation, file_query_result);
ABI_OFFSET(struct agent_file_query_result, index_rebuild_records, file_query_result);
