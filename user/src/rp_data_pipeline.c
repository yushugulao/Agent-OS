#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_input_fastq", "@RUN-042-read-1");
	ok = ok && rp_file_contains("rp_artifact", "normalized_read=RUN-042-read-2;sequence=ACGTTCGTACGA");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_align_table");
	ok = ok && rp_file_contains("rp_artifact", "\"variants\":2");
	ok = ok && rp_file_contains("rp_artifact", "section=rp_gene_counts_csv;geneA=18");
	ok = ok && rp_file_contains("rp_datadic", "schema_fields=17");
	ok = ok && rp_file_contains("rp_dataprof", "profiles=4");
	ok = ok && rp_file_contains("rp_quality", "passed=7");
	if (!ok) return 1;

	if (!rp_write_file("rp_ingest_files",
			   "run_id=RUN-042\n"
			   "files=2\n"
			   "file=1;path=rp_input_fastq;kind=fastq;records=2;bytes=72;status=ready\n"
			   "file=2;path=rp_samples;kind=sample_sheet;records=4;bytes=128;status=ready\n"
			   "derived_items=5\n"
			   "derived=rp_artifact:rp_normalized_fastq,rp_artifact:rp_align_table,rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv,rp_artifact:rp_archive_manifest\n"
			   "scan_status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_dataset_snapshot",
			   "dataset=lab-gene-x-input\n"
			   "snapshots=2\n"
			   "snapshot=raw;files=2;records=6;status=ready\n"
			   "snapshot=normalized;files=2;records=6;transform=normalize_fastq;normalized_fastq=rp_artifact:rp_normalized_fastq;status=ready\n"
			   "total_bytes=200\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_data_preview",
			   "previews=2\n"
			   "preview=fastq;rows=2;columns=4;source=rp_artifact:rp_normalized_fastq;status=ready\n"
			   "preview=samples;rows=4;columns=4;source=rp_samples;status=ready\n"
			   "derived_preview=alignment;rows=2;columns=3;source=rp_artifact:rp_align_table;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_data_quality",
			   "dataset=lab-gene-x-input\n"
			   "rules=7\n"
			   "passed=7\n"
			   "failed=0\n"
			   "min_reads=2\n"
			   "derived_variants=2\n"
			   "metrics_section=rp_artifact:rp_metrics_json\n"
			   "sample_sheet_valid=1\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_data_transform",
			   "transforms=2\n"
			   "transform=normalize_fastq;input=rp_input_fastq;output=rp_dataset_snapshot;status=ready\n"
			   "transform=join_sample_sheet;input=rp_samples;output=rp_dataset_collection;status=ready\n"
			   "derived=alignment;input=rp_artifact:rp_normalized_fastq;output=rp_artifact:rp_align_table;status=ready\n"
			   "derived=metrics;input=rp_artifact:rp_align_table;output=rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv;status=ready\n"
			   "derived_transform_steps=2\n"
			   "validations=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_dataset_collection",
			   "collection=lab-gene-x-run042-analysis\n"
			   "items=4\n"
			   "item=raw_fastq;source=rp_input_fastq;status=ready\n"
			   "item=samples;source=rp_samples;status=ready\n"
			   "item=counts;source=rp_artifact:rp_gene_counts_csv;status=ready\n"
			   "item=artifact;source=rp_artifact;status=ready\n"
			   "derived_artifacts=5\n"
			   "export=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has("kind=research_run")) {
		char value[96];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "dataset_rows=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "4");
		}
		if (!rp_append_host_action_line("rp_ingest_files", "host_input_dataset_rows=", value)) return 1;
		if (!rp_append_host_action_line("rp_dataset_snapshot", "host_input_dataset_rows=", value)) return 1;
		if (!rp_append_host_action_line("rp_data_preview", "host_input_dataset_rows=", value)) return 1;
		if (!rp_append_host_action_line("rp_data_quality", "host_input_dataset_rows=", value)) return 1;
		if (!rp_append_host_action_line("rp_dataset_collection", "host_input_dataset_rows=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "reference_entries=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "2");
		}
		if (!rp_append_host_action_line("rp_ingest_files", "host_input_reference_entries=", value)) return 1;
		if (!rp_append_host_action_line("rp_dataset_collection", "host_input_reference_entries=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "workspace_files=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "4");
		}
		if (!rp_append_host_action_line("rp_ingest_files", "host_input_workspace_files=", value)) return 1;
		if (!rp_append_host_action_line("rp_dataset_snapshot", "host_input_workspace_files=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "csv_file=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "expr.csv");
		}
		if (!rp_append_host_action_line("rp_ingest_files", "host_input_csv_file=", value)) return 1;
		if (!rp_append_host_action_line("rp_data_preview", "host_input_csv_file=", value)) return 1;
	}
	if (rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify")) {
		char value[96];
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "manifest=", value, sizeof(value)) &&
		    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "manifest=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "delivery-manifest.json");
		}
		if (!rp_append_host_action_line("rp_ingest_files", "host_file_manifest=", value)) return 1;
		if (!rp_append_host_action_line("rp_dataset_collection", "host_file_manifest=", value)) return 1;
		if (!rp_append_host_action_line("rp_data_quality", "host_file_manifest=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "files=", value, sizeof(value)) &&
		    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "files=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "9");
		}
		if (!rp_append_host_action_line("rp_ingest_files", "host_file_manifest_files=", value)) return 1;
		if (!rp_append_host_action_line("rp_dataset_collection", "host_file_manifest_files=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "sha_records=", value, sizeof(value)) &&
		    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "sha_records=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "9");
		}
		if (!rp_append_host_action_line("rp_ingest_files", "host_file_manifest_sha_records=", value)) return 1;
		if (!rp_append_host_action_line("rp_data_quality", "host_file_manifest_sha_records=", value)) return 1;
		if (rp_host_seed_has("kind=workbench_file_verify")) {
			if (!rp_append_file("rp_data_quality", "host_file_verify=passed")) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "verified=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "9");
			}
			if (!rp_append_host_action_line("rp_data_quality", "host_file_verify_verified=", value)) return 1;
			if (!rp_append_host_action_line("rp_dataset_collection", "host_file_verified_items=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "missing=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "0");
			}
			if (!rp_append_host_action_line("rp_data_quality", "host_file_verify_missing=", value)) return 1;
		}
	}
	if (!rp_append_file("rp_ack", "ack=data_pipeline;msg=data;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=data_pipeline.scan_files;target=rp_ingest_files;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=data_pipeline.snapshot;target=rp_dataset_snapshot;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=data_pipeline.preview;target=rp_data_preview;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=data_pipeline.quality;target=rp_data_quality;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=data_pipeline.transform;target=rp_data_transform;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=data_pipeline.collection;target=rp_dataset_collection;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=data_pipeline.export;target=rp_dataset_collection;status=ok")) return 1;
	if (!rp_append_status("data_pipeline=ready")) return 1;
	if (!rp_append_status("ingest_files=ready")) return 1;
	if (!rp_append_status("dataset_snapshot=ready")) return 1;
	if (!rp_append_status("data_preview=ready")) return 1;
	if (!rp_append_status("data_quality=ready")) return 1;
	if (!rp_append_status("data_transform=ready")) return 1;
	if (!rp_append_status("dataset_collection=ready")) return 1;
	printf("rp_data_pipeline: files=2 snapshots=2 previews=2 quality=passed transforms=2 status=ready\n");
	return 0;
}
