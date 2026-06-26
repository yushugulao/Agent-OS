#include <stdio.h>
#include <research_platform_state.h>

static int fastq_profile(int *reads, int *bases, int *diffs)
{
	char *buf = rp_state_buf;
	int n = rp_read_file("rp_input_fastq", buf, RP_STATE_BUFFER_SIZE);
	if (n < 0) return 0;
	char seq1[64];
	char seq2[64];
	int len1 = 0;
	int len2 = 0;
	int line = 0;
	int col = 0;
	*reads = 0;
	*bases = 0;
	*diffs = 0;
	for (int i = 0; i < n; i++) {
		char c = buf[i];
		if (c == '\n') {
			if (line % 4 == 1) {
				(*reads)++;
			}
			line++;
			col = 0;
			continue;
		}
		if (line % 4 == 0 && col == 0 && c != '@') {
			return 0;
		}
		if (line % 4 == 1) {
			int read_index = line / 4;
			if (read_index == 0 && len1 < (int)sizeof(seq1) - 1) {
				seq1[len1++] = c;
			} else if (read_index == 1 && len2 < (int)sizeof(seq2) - 1) {
				seq2[len2++] = c;
			}
			(*bases)++;
		}
		col++;
	}
	seq1[len1] = 0;
	seq2[len2] = 0;
	if (len1 <= 0 || len1 != len2) return 0;
	for (int i = 0; i < len1; i++) {
		if (seq1[i] != seq2[i]) {
			(*diffs)++;
		}
	}
	return 1;
}

static int append_artifact_input_action(void)
{
	char file[64];
	char kind[32];
	char sha[64];
	char bytes[32];
	char source[48];
	char line[240];
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "file=", file, sizeof(file))) {
		rp_copy_text(file, sizeof(file), "reads_R1.fastq");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "artifact_kind=", kind, sizeof(kind))) {
		rp_copy_text(kind, sizeof(kind), "fastq");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "sha256=", sha, sizeof(sha))) {
		rp_copy_text(sha, sizeof(sha), "sha-host-input");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "bytes=", bytes, sizeof(bytes))) {
		rp_copy_text(bytes, sizeof(bytes), "2048");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_input", "source=", source, sizeof(source))) {
		rp_copy_text(source, sizeof(source), "upload");
	}
	rp_copy_text(line, sizeof(line), "host_artifact_input=");
	rp_append_text(line, sizeof(line), file);
	rp_append_text(line, sizeof(line), ";kind=");
	rp_append_text(line, sizeof(line), kind);
	rp_append_text(line, sizeof(line), ";sha256=");
	rp_append_text(line, sizeof(line), sha);
	rp_append_text(line, sizeof(line), ";bytes=");
	rp_append_text(line, sizeof(line), bytes);
	rp_append_text(line, sizeof(line), ";source=");
	rp_append_text(line, sizeof(line), source);
	return rp_append_file("rp_artifact", line) &&
	       rp_append_file("rp_input", line);
}

static int append_artifact_derive_action(void)
{
	char input[64];
	char output[64];
	char operation[48];
	char stage[48];
	char sha[64];
	char line[240];
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "input=", input, sizeof(input))) {
		rp_copy_text(input, sizeof(input), "reads_R1.fastq");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "output=", output, sizeof(output))) {
		rp_copy_text(output, sizeof(output), "clean_reads.fastq");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "operation=", operation, sizeof(operation))) {
		rp_copy_text(operation, sizeof(operation), "trim");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "stage=", stage, sizeof(stage))) {
		rp_copy_text(stage, sizeof(stage), "clean");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_derive", "sha256=", sha, sizeof(sha))) {
		rp_copy_text(sha, sizeof(sha), "sha-host-derived");
	}
	rp_copy_text(line, sizeof(line), "host_artifact_derive=");
	rp_append_text(line, sizeof(line), input);
	rp_append_text(line, sizeof(line), ";output=");
	rp_append_text(line, sizeof(line), output);
	rp_append_text(line, sizeof(line), ";operation=");
	rp_append_text(line, sizeof(line), operation);
	rp_append_text(line, sizeof(line), ";stage=");
	rp_append_text(line, sizeof(line), stage);
	rp_append_text(line, sizeof(line), ";sha256=");
	rp_append_text(line, sizeof(line), sha);
	return rp_append_file("rp_artifact", line);
}

static int append_artifact_log_action(void)
{
	char log[64];
	char stage[48];
	char level[32];
	char message[80];
	char line[240];
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_log", "log=", log, sizeof(log))) {
		rp_copy_text(log, sizeof(log), "clean.log");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_log", "stage=", stage, sizeof(stage))) {
		rp_copy_text(stage, sizeof(stage), "clean");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_log", "level=", level, sizeof(level))) {
		rp_copy_text(level, sizeof(level), "warn");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_log", "message=", message, sizeof(message))) {
		rp_copy_text(message, sizeof(message), "adapter_trimmed");
	}
	rp_copy_text(line, sizeof(line), "host_artifact_log=");
	rp_append_text(line, sizeof(line), log);
	rp_append_text(line, sizeof(line), ";stage=");
	rp_append_text(line, sizeof(line), stage);
	rp_append_text(line, sizeof(line), ";level=");
	rp_append_text(line, sizeof(line), level);
	rp_append_text(line, sizeof(line), ";message=");
	rp_append_text(line, sizeof(line), message);
	return rp_append_file("rp_stage_log", line);
}

static int append_artifact_chart_action(void)
{
	char chart[64];
	char chart_type[32];
	char data_file[64];
	char points[32];
	char line[220];
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_chart", "chart=", chart, sizeof(chart))) {
		rp_copy_text(chart, sizeof(chart), "qc-chart.json");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_chart", "chart_type=", chart_type, sizeof(chart_type))) {
		rp_copy_text(chart_type, sizeof(chart_type), "line");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_chart", "data_file=", data_file, sizeof(data_file))) {
		rp_copy_text(data_file, sizeof(data_file), "clean.metrics.json");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_chart", "points=", points, sizeof(points))) {
		rp_copy_text(points, sizeof(points), "12");
	}
	rp_copy_text(line, sizeof(line), "host_artifact_chart=");
	rp_append_text(line, sizeof(line), chart);
	rp_append_text(line, sizeof(line), ";type=");
	rp_append_text(line, sizeof(line), chart_type);
	rp_append_text(line, sizeof(line), ";data_file=");
	rp_append_text(line, sizeof(line), data_file);
	rp_append_text(line, sizeof(line), ";points=");
	rp_append_text(line, sizeof(line), points);
	return rp_append_file("rp_chart_data", line);
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_plan", "run=RUN-042");
	ok = ok && rp_file_contains("rp_taskrec", "stage=align");
	ok = ok && rp_file_contains("rp_data", "failed_stage=align");
	ok = ok && rp_file_contains("rp_fix", "status=recovered");
	ok = ok && rp_file_contains("rp_retrylog", "final_result=recovered");
	ok = ok && rp_file_contains("rp_completion", "status=ready");
	if (!ok) return 1;
	if (!rp_write_file("rp_input",
			   "input=RUN-042:sample-fastq\n"
			   "source=ordinary_ucore_file\n"
			   "records=2\n"
			   "bytes=96\n"
			   "checksum=input-demo-042\n"
			   "custom_requests=3\n"
			   "custom_run=usable-run:RUN-900\n"
			   "custom_run_2=usable-run:RUN-901\n"
			   "custom_run_3=usable-run:RUN-902\n"
			   "custom_title=Browser started study\n"
			   "custom_question=Can this platform run a custom research task?\n"
			   "custom_provider=template\n"
			   "custom_dataset_rows=3\n"
			   "custom_dataset_rows_total=9\n"
			   "custom_row=S1,control,12\n"
			   "custom_row=S2,treatment,19\n"
			   "custom_row=S3,treatment,21\n"
			   "custom_row_2=S4,control,8\n"
			   "custom_row_2=S5,treatment,13\n"
			   "custom_row_3=S6,control,30\n"
			   "custom_row_3=S7,treatment,28\n"
			   "custom_outputs=stage_dag,analysis,report,review,export\n"
			   "request_form=form_fields=8;request_count=3;source_mode=pasted_or_uploaded;provider_options=template,host-relay;delivery_audience=reviewer;reviewer=Wang\n"
			   "upload_files=uploads=2;csv_rows_total=9;reference_entries=2;dataset_target=rp_input\n"
			   "library_sources=1;library_tag=reusable;library_source_id=usable-source:library2026:1;citation_key=library2026\n"
			   "library_backed_run=usable-run:RUN-900;source_tag=reusable;selected_library_sources=1\n"
			   "workspace_import=workspace:RUN-900:folder;files=4;csv=1;refs=2;notes=1;manifest=workspace-manifest.json\n"
			   "workspace_file=expr.csv;kind=dataset;rows=3;target=usable-dataset:workspace-900:expr\n"
			   "workspace_file=refs.bib;kind=references;entries=2;target=usable-source:workspace-900:refs\n"
			   "workspace_file=notes.md;kind=notes;target=usable-template:workspace-900\n"
			   "workspace_template=usable-template:workspace-900;status=ready\n"
			   "workspace_run=usable-run:RUN-903;template=usable-template:workspace-900;status=ready\n"
			   "dynamic_submissions=4\n"
			   "dynamic_submission=1;source=form;run=RUN-900;state=accepted;rows=3\n"
			   "dynamic_submission=2;source=upload;run=RUN-901;state=accepted;rows=2\n"
			   "dynamic_submission=3;source=workspace;run=RUN-903;state=accepted;files=4\n"
			   "dynamic_submission=4;source=api;run=RUN-904;state=queued;rows=4\n"
			   "dynamic_validation=passed;dedupe=passed;schema=sample,group,value\n"
			   "dynamic_queue=plain_ucore_file_backed;accepted=3;pending=1\n"
			   "host_ui_feed=rp_web_bundle;events=10;source=rp_input\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_run_id=", seed_run)) return 1;
		if (!rp_append_host_action_line("rp_input", "host_action_research_run=usable-run:", seed_run)) return 1;
		if (!rp_append_file("rp_input", "host_action_source=rp_host_action_seed")) return 1;
		if (!rp_append_file("rp_input", "host_action_state=accepted")) return 1;
		if (!rp_append_file("rp_input", "host_action_dataset_rows=4")) return 1;
		if (!rp_append_file("rp_input", "host_action_validation=passed")) return 1;
		char value[96];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "title=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Browser started study");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_title=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "question=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Can this platform run a custom research task?");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_question=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "provider=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "template");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_provider=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "dataset_rows=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "4");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_dataset_rows_value=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "reference_entries=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "2");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_reference_entries=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "workspace_files=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "4");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_workspace_files=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "csv_file=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "expr.csv");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_csv_file=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "reference_file=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "refs.bib");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_reference_file=", value)) return 1;
	}
	if (rp_host_seed_has("kind=dataset")) {
		char value[96];
		if (!rp_append_file("rp_input", "host_action_dataset=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=dataset", "title=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Reusable response table");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_dataset_title=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=dataset", "dataset_rows=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "3");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_dataset_rows=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=dataset", "columns=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "sample,group,value");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_dataset_columns=", value)) return 1;
	}
	if (rp_host_seed_has("kind=library_source")) {
		char value[96];
		if (!rp_append_file("rp_input", "host_action_library_source=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=library_source", "citation_key=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agentlibrary2026");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_library_citation=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=library_source", "tags=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "agent reusable");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_library_tags=", value)) return 1;
	}
	if (rp_host_seed_has("kind=template")) {
		char value[96];
		if (!rp_append_file("rp_input", "host_action_template=registered")) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=template", "name=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Reusable response comparison");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_template_name=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=template", "question=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "Which group is stronger?");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_template_question=", value)) return 1;
		if (!rp_host_seed_copy_value_for_kind("kind=template", "provider_id=", value, sizeof(value))) {
			rp_copy_text(value, sizeof(value), "template");
		}
		if (!rp_append_host_action_line("rp_input", "host_action_template_provider=", value)) return 1;
	}
	if (rp_host_seed_has("kind=workspace_inspect") ||
	    rp_host_seed_has("kind=workspace_import") ||
	    rp_host_seed_has("kind=workspace_import_run")) {
		char value[96];
		if (!rp_append_file("rp_input", "host_action_workspace=observed")) return 1;
		if (rp_host_seed_copy_value_for_kind("kind=workspace_inspect", "root=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import", "root=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "root=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_root=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_inspect", "max_files=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import", "max_files=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "max_files=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_max_files=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_import", "manifest=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "manifest=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_manifest=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_import", "title=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "title=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_title=", value)) return 1;
		}
		if (rp_host_seed_copy_value_for_kind("kind=workspace_import", "question=", value, sizeof(value)) ||
		    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "question=", value, sizeof(value))) {
			if (!rp_append_host_action_line("rp_input", "host_action_workspace_question=", value)) return 1;
		}
	}
	if (!rp_write_file("rp_input_fastq",
			   "@RUN-042-read-1\n"
			   "ACGTACGTACGT\n"
			   "+\n"
			   "FFFFFFFFFFFF\n"
			   "@RUN-042-read-2\n"
			   "ACGTTCGTACGA\n"
			   "+\n"
			   "FFFFFFFFFFFF\n")) {
		return 1;
	}
	if (!rp_file_contains("rp_input_fastq", "@RUN-042-read-1")) return 1;
	int reads = 0;
	int bases = 0;
	int diffs = 0;
	if (!fastq_profile(&reads, &bases, &diffs)) return 1;
	if (reads != 2 || bases != 24 || diffs != 2) return 1;
	if (!rp_write_file("rp_stage_dag",
			   "dag=lab-gene-x-nightly\n"
			   "stage=ingest;deps=none;cache=miss;status=done\n"
			   "stage=align;deps=ingest;cache=miss;status=recovered\n"
			   "stage=profile;deps=align;cache=hit;status=done\n"
			   "stage=review;deps=profile;cache=miss;status=done\n"
			   "stage=package;deps=review;cache=miss;status=ready\n"
			   "edges=4\n"
			   "failed_stage=align\n"
			   "retry_stage=align\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_stage_log",
			   "run_id=RUN-042\n"
			   "log=ingest read rp_input_fastq records=2 status=ok\n"
			   "log=align first_attempt status=failed reason=tool_output_missing\n"
			   "log=align retry attempt=2 status=recovered artifact=rp_artifact\n"
			   "log=profile cache=hit source=rp_compute status=ok\n"
			   "log=review claims=8 evidence_links=5 status=accepted\n"
			   "lines=5\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_artifact",
			   "artifact=artifact:RUN-042:align-recovered\n"
			   "input=rp_input_fastq\n"
			   "derived_sections=5\n"
			   "section=rp_normalized_fastq;reads=2;bases=24;status=ready\n"
			   "normalized_read=RUN-042-read-1;sequence=ACGTACGTACGT\n"
			   "normalized_read=RUN-042-read-2;sequence=ACGTTCGTACGA\n"
			   "section=rp_align_table;reference=RUN-042-read-1;variant_count=2;status=ready\n"
			   "align_row=RUN-042-read-1;diffs=0;status=reference\n"
			   "align_row=RUN-042-read-2;diffs=2;status=variant\n"
			   "section=rp_metrics_json;\"reads\":2;\"bases\":24;\"variants\":2;status=ready\n"
			   "section=rp_gene_counts_csv;geneA=18;geneB=11;geneC=7;status=ready\n"
			   "section=rp_archive_manifest;files=5;status=ready\n"
			   "archive_file=rp_normalized_fastq;kind=prepared_input;status=ready\n"
			   "archive_file=rp_align_table;kind=alignment;status=ready\n"
			   "archive_file=rp_metrics_json;kind=metrics;status=ready\n"
			   "archive_file=rp_gene_counts_csv;kind=counts;status=ready\n"
			   "archive_file=rp_report_text;kind=report;status=ready\n"
			   "stage=align\n"
			   "attempt=2\n"
			   "records=2\n"
			   "derived_variants=2\n"
			   "normalized_fastq=section:rp_normalized_fastq\n"
			   "align_table=section:rp_align_table\n"
			   "metrics=section:rp_metrics_json\n"
			   "counts=section:rp_gene_counts_csv\n"
			   "archive_manifest=section:rp_archive_manifest\n"
			   "artifact_dossier=rp_input_fastq,rp_normalized_fastq,rp_align_table,rp_metrics_json,rp_gene_counts_csv,rp_chart_data,rp_stage_log\n"
			   "artifact_review_link=rp_artifact_manifest->rp_review_pack->rp_package\n"
			   "provenance=rp_align_table;stage=align;event=4;retry=rp_retry_plan;review_gate=artifact_manifest;llm_quality=rp_llmeval;status=recovered\n"
			   "provenance=rp_metrics_json;stage=profile;event=5;cache=hit;review_gate=artifact_manifest;status=ready\n"
			   "provenance=rp_report_text;stage=package;event=7;review_pack=rp_review_pack;status=ready\n"
			   "status=recovered\n")) {
		return 1;
	}
	if (!rp_write_file("rp_report_text",
			   "# RUN-042 Recovery Report\n"
			   "The align stage failed because the first tool output was missing.\n"
			   "Recovery reran only the align stage and reused cached profile data.\n"
			   "Evidence links: rp_evidence, rp_claimrec, rp_provpath, rp_stage_log.\n"
			   "Release state: ready.\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_chart_data",
			   "chart=stage_attempts\n"
			   "stage,attempts,status\n"
			   "ingest,1,done\n"
			   "align,2,recovered\n"
			   "profile,1,cached\n"
			   "review,1,accepted\n"
			   "package,1,ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_has_artifact_action()) {
		if (!rp_append_file("rp_artifact", "host_artifact_actions=applied")) return 1;
		if (rp_host_seed_has("kind=artifact_input") && !append_artifact_input_action()) return 1;
		if (rp_host_seed_has("kind=artifact_derive") && !append_artifact_derive_action()) return 1;
		if (rp_host_seed_has("kind=artifact_log") && !append_artifact_log_action()) return 1;
		if (rp_host_seed_has("kind=artifact_chart") && !append_artifact_chart_action()) return 1;
		if (!rp_append_file("rp_tool", "tool=artifact_ops.host_artifact_actions")) return 1;
	}
	if (rp_host_seed_count() > 0) {
		if (rp_host_seed_has("kind=research_run")) {
			char seed_run[48];
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
				rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_run_id=", seed_run)) return 1;
			char value[96];
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "title=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Browser started study");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_title=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "question=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Can this platform run a custom research task?");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_question=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "provider=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "template");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_provider=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "dataset_rows=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "4");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_dataset_rows=", value)) return 1;
		}
		if (rp_host_seed_has("kind=human_review")) {
			char reviewer[48];
			char decision[48];
			if (!rp_host_seed_copy_value_for_kind("kind=human_review", "reviewer=", reviewer, sizeof(reviewer))) {
				rp_copy_text(reviewer, sizeof(reviewer), "HOST");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=human_review", "decision=", decision, sizeof(decision))) {
				rp_copy_text(decision, sizeof(decision), "needs_revision");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_reviewer=", reviewer)) return 1;
			if (!rp_append_host_action_line("rp_report_text", "host_report_review_decision=", decision)) return 1;
		}
		if (rp_host_seed_has("kind=revision_task")) {
			char targets[80];
			if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "targets=", targets, sizeof(targets))) {
				rp_copy_text(targets, sizeof(targets), "methods,chart_caption");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_revision_targets=", targets)) return 1;
		}
		if (rp_host_seed_has("kind=bundle_export")) {
			char bundle[48];
			if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "bundle=", bundle, sizeof(bundle))) {
				rp_copy_text(bundle, sizeof(bundle), "evidence");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_bundle=", bundle)) return 1;
		}
		if (rp_host_seed_has("kind=agentcompare")) {
			char profile[48];
			if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
				rp_copy_text(profile, sizeof(profile), "plain_ucore");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_compare_profile=", profile)) return 1;
		}
		if (rp_host_seed_has_workbench_action()) {
			char value[96];
			if (!rp_append_file("rp_report_text", "host_report_workbench_outputs=rp_runner,rp_revision,rp_package")) return 1;
			if (!rp_host_seed_copy_workbench_value("workbench=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "usable-workbench:RUN-900");
			}
			if (!rp_append_host_action_line("rp_report_text", "host_report_workbench=", value)) return 1;
			if (rp_host_seed_copy_workbench_value("workbench_title=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_title=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("question=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_question=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("task=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_task=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("title=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_note_title=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("manifest=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_manifest=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("bundle=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_report_text", "host_report_workbench_bundle=", value)) return 1;
			}
		}
	}
	if (!rp_write_file("rp_runner",
			   "runner=plain-ucore-stage-runner\n"
			   "inputs=2\n"
			   "stages=5\n"
			   "dag_edges=4\n"
			   "failed_stages=1\n"
			   "retries=1\n"
			   "cache_hits=1\n"
			   "logs=5\n"
			   "artifacts=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=artifact_ops;msg=artifact;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=research_request;msg=input;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_input")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.read_input")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_dag")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_log")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_artifact")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_report")) return 1;
	if (!rp_append_file("rp_tool", "tool=artifact_ops.write_chart")) return 1;
	if (!rp_append_status("input=ready")) return 1;
	if (!rp_append_status("request_form=ready")) return 1;
	if (!rp_append_status("upload_files=ready")) return 1;
	if (!rp_append_status("runner=ready")) return 1;
	if (!rp_append_status("stage_dag=ready")) return 1;
	if (!rp_append_status("artifact_ops=ready")) return 1;
	if (!rp_append_status("research_request=ready")) return 1;
	printf("rp_artifact_ops: inputs=2 stages=5 retries=1 artifacts=4 custom_requests=3 status=ready\n");
	return 0;
}
