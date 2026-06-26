#include <stdio.h>
#include <research_platform_state.h>

static int append_artifact_manifest_input(void)
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
	rp_copy_text(line, sizeof(line), "host_artifact_manifest_input=");
	rp_append_text(line, sizeof(line), file);
	rp_append_text(line, sizeof(line), ";kind=");
	rp_append_text(line, sizeof(line), kind);
	rp_append_text(line, sizeof(line), ";sha256=");
	rp_append_text(line, sizeof(line), sha);
	rp_append_text(line, sizeof(line), ";bytes=");
	rp_append_text(line, sizeof(line), bytes);
	rp_append_text(line, sizeof(line), ";source=");
	rp_append_text(line, sizeof(line), source);
	return rp_append_file("rp_artifact_manifest", line);
}

static int append_artifact_manifest_derive(void)
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
	rp_copy_text(line, sizeof(line), "host_artifact_manifest_derive=");
	rp_append_text(line, sizeof(line), input);
	rp_append_text(line, sizeof(line), ";output=");
	rp_append_text(line, sizeof(line), output);
	rp_append_text(line, sizeof(line), ";operation=");
	rp_append_text(line, sizeof(line), operation);
	rp_append_text(line, sizeof(line), ";stage=");
	rp_append_text(line, sizeof(line), stage);
	rp_append_text(line, sizeof(line), ";sha256=");
	rp_append_text(line, sizeof(line), sha);
	return rp_append_file("rp_artifact_manifest", line);
}

static int append_artifact_manifest_chart(void)
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
	rp_copy_text(line, sizeof(line), "host_artifact_manifest_chart=");
	rp_append_text(line, sizeof(line), chart);
	rp_append_text(line, sizeof(line), ";type=");
	rp_append_text(line, sizeof(line), chart_type);
	rp_append_text(line, sizeof(line), ";data_file=");
	rp_append_text(line, sizeof(line), data_file);
	rp_append_text(line, sizeof(line), ";points=");
	rp_append_text(line, sizeof(line), points);
	return rp_append_file("rp_artifact_manifest", line);
}

static int append_artifact_manifest_package(void)
{
	char package[64];
	char manifest[64];
	char files[32];
	char status[32];
	char line[220];
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_package", "package=", package, sizeof(package))) {
		rp_copy_text(package, sizeof(package), "artifact-bundle.zip");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_package", "manifest=", manifest, sizeof(manifest))) {
		rp_copy_text(manifest, sizeof(manifest), "artifact-manifest.json");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_package", "files=", files, sizeof(files))) {
		rp_copy_text(files, sizeof(files), "5");
	}
	if (!rp_host_seed_copy_value_for_kind("kind=artifact_package", "status=", status, sizeof(status))) {
		rp_copy_text(status, sizeof(status), "ready");
	}
	rp_copy_text(line, sizeof(line), "host_artifact_manifest_package=");
	rp_append_text(line, sizeof(line), package);
	rp_append_text(line, sizeof(line), ";manifest=");
	rp_append_text(line, sizeof(line), manifest);
	rp_append_text(line, sizeof(line), ";files=");
	rp_append_text(line, sizeof(line), files);
	rp_append_text(line, sizeof(line), ";status=");
	rp_append_text(line, sizeof(line), status);
	return rp_append_file("rp_artifact_manifest", line);
}

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_stage_dag", "failed_stage=align");
	ok = ok && rp_file_contains("rp_stage_log", "first_attempt status=failed");
	ok = ok && rp_file_contains("rp_input_fastq", "@RUN-042-read-1");
	ok = ok && rp_file_contains("rp_artifact", "normalized_read=RUN-042-read-2");
	ok = ok && rp_file_contains("rp_artifact", "align_row=RUN-042-read-2;diffs=2");
	ok = ok && rp_file_contains("rp_artifact", "\"reads\":2");
	ok = ok && rp_file_contains("rp_artifact", "geneB=11");
	ok = ok && rp_file_contains("rp_artifact", "status=recovered");
	ok = ok && rp_file_contains("rp_runner", "stages=5");
	ok = ok && rp_file_contains("rp_input", "custom_run=usable-run:RUN-900");
	ok = ok && rp_file_contains("rp_input", "custom_requests=3");
	ok = ok && rp_file_contains("rp_input", "custom_dataset_rows=3");
	ok = ok && rp_file_contains("rp_input", "library_sources=1");
	ok = ok && rp_file_contains("rp_knowledge", "library_tag=reusable");
	if (!ok) return 1;

	if (!rp_write_file("rp_stage_state",
			   "run_id=RUN-042\n"
			   "stage=ingest;order=1;input=rp_input_fastq;attempts=1;state=done\n"
			   "stage=align;order=2;input=rp_artifact:rp_normalized_fastq;attempts=2;state=recovered\n"
			   "stage=profile;order=3;input=rp_artifact:rp_align_table;attempts=1;state=cached\n"
			   "stage=review;order=4;input=rp_claimrec;attempts=1;state=accepted\n"
			   "stage=package;order=5;input=rp_report_text;attempts=1;state=ready\n"
			   "command=ingest:read_fastq;output=rp_artifact:rp_normalized_fastq\n"
			   "command=align:agent-align;output=rp_artifact:rp_align_table;first_status=failed;second_status=recovered\n"
			   "command=profile:derive_metrics;output=rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv;cache=hit\n"
			   "command=review:claim_review;output=rp_review;claims=8\n"
			   "command=package:assemble;output=rp_package;report=rp_report_text\n"
			   "dependency_checks=5\n"
			   "outputs=5\n"
			   "stages=5\n"
			   "done=5\n"
			   "recovered=1\n"
			   "cached=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_cache_index",
			   "run_id=RUN-042\n"
			   "cache_key=ingest:RUN-042;state=miss;source=rp_input_fastq\n"
			   "cache_key=align:RUN-042;state=refreshed;source=rp_artifact\n"
			   "cache_key=profile:RUN-042;state=hit;source=rp_compute\n"
			   "cache_key=review:RUN-042;state=miss;source=rp_claimrec\n"
			   "cache_key=package:RUN-042;state=miss;source=rp_report_text\n"
			   "cache_policy=content_keyed\n"
			   "reuse_stage=profile\n"
			   "refreshed_stage=align\n"
			   "cache_records=5\n"
			   "cache_hits=1\n"
			   "cache_misses=4\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_retry_plan",
			   "run_id=RUN-042\n"
			   "retry_items=1\n"
			   "failed_stage=align\n"
			   "retry_stage=align\n"
			   "attempts=2\n"
			   "failure_reason=tool_output_missing\n"
			   "rerun_inputs=rp_input_fastq\n"
			   "rerun_outputs=rp_artifact\n"
			   "skip_stages=ingest,profile,review,package\n"
			   "dedupe_key=RUN-042:align\n"
			   "minimal_rerun=1\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_run_events",
			   "run_id=RUN-042\n"
			   "event=1;stage=ingest;action=read_input;status=done\n"
			   "event=2;stage=align;action=first_attempt;status=failed\n"
			   "event=3;stage=align;action=retry_scheduled;status=ready\n"
			   "event=4;stage=align;action=rerun;status=recovered\n"
			   "event=5;stage=profile;action=cache_lookup;status=hit\n"
			   "event=6;stage=review;action=claim_review;status=accepted\n"
			   "event=7;stage=package;action=manifest_ready;status=ready\n"
			   "event=8;stage=run;action=finish;status=ready\n"
			   "decision=retry_align_only;reason=tool_output_missing\n"
			   "report_ref=rp_report_text\n"
			   "evidence_ref=rp_evidence\n"
			   "events=8\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_artifact_manifest",
			   "run_id=RUN-042\n"
			   "manifest=plain-ucore-runner-artifacts\n"
			   "record=1;kind=input;path=rp_input_fastq;status=ready\n"
			   "record=2;kind=prepared_input;path=rp_artifact;section=rp_normalized_fastq;status=ready\n"
			   "record=3;kind=alignment;path=rp_artifact;section=rp_align_table;status=ready\n"
			   "record=4;kind=metrics;path=rp_artifact;section=rp_metrics_json;status=ready\n"
			   "record=5;kind=counts;path=rp_artifact;section=rp_gene_counts_csv;status=ready\n"
			   "record=6;kind=artifact;path=rp_artifact;status=recovered\n"
			   "record=7;kind=report;path=rp_report_text;status=ready\n"
			   "record=8;kind=chart;path=rp_chart_data;status=ready\n"
			   "record=9;kind=archive;path=rp_artifact;section=rp_archive_manifest;status=ready\n"
			   "support=stage_log;path=rp_stage_log;status=ready\n"
			   "support=package_index;path=rp_package;status=ready\n"
			   "dossier=artifact-detail;source=rp_artifact;stage_log=rp_stage_log;chart=rp_chart_data;review_pack=rp_review_pack;status=ready\n"
			   "dossier_item=raw_input;path=rp_input_fastq;status=ready\n"
			   "dossier_item=alignment;path=rp_artifact;section=rp_align_table;status=ready\n"
			   "dossier_item=metrics;path=rp_artifact;section=rp_metrics_json;status=ready\n"
			   "dossier_item=review_link;path=rp_review_pack;status=host_materialized\n"
			   "dossier_check=workflow_stage;source=rp_stage_state;stage=align;status=recovered\n"
			   "dossier_check=event_trace;source=rp_run_events;event=4;status=recovered\n"
			   "dossier_check=review_gate;source=rp_review_dashboard;gate=artifact_manifest;status=pass\n"
			   "dossier_check=llm_quality;source=rp_llmeval;status=host_checked\n"
			   "manifest_records=4\n"
			   "real_artifact_items=5\n"
			   "support_entries=2\n"
			   "status=ready\n")) {
		return 1;
	}
	if (rp_host_seed_count() > 0) {
		if (!rp_append_file("rp_artifact_manifest", "host_manifest_payload_applied=1")) return 1;
		if (rp_host_seed_has("kind=research_run")) {
			char seed_run[48];
			if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
				rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
			}
			if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_run_id=", seed_run)) return 1;
		}
		if (rp_host_seed_has("kind=revision_task")) {
			char targets[80];
			if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "targets=", targets, sizeof(targets))) {
				rp_copy_text(targets, sizeof(targets), "methods,chart_caption");
			}
			if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_revision_targets=", targets)) return 1;
		}
		if (rp_host_seed_has("kind=notebook_export")) {
			char format[32];
			if (!rp_host_seed_copy_value_for_kind("kind=notebook_export", "format=", format, sizeof(format))) {
				rp_copy_text(format, sizeof(format), "ipynb");
			}
			if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_notebook_format=", format)) return 1;
		}
		if (rp_host_seed_has("kind=bundle_export")) {
			char bundle[48];
			if (!rp_host_seed_copy_value_for_kind("kind=bundle_export", "bundle=", bundle, sizeof(bundle))) {
				rp_copy_text(bundle, sizeof(bundle), "evidence");
			}
			if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_bundle=", bundle)) return 1;
		}
		if (rp_host_seed_has("kind=agentcompare")) {
			char profile[48];
			if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
				rp_copy_text(profile, sizeof(profile), "plain_ucore");
			}
			if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_compare_profile=", profile)) return 1;
		}
		if (rp_host_seed_has_workbench_action()) {
			char value[96];
			if (!rp_append_file("rp_artifact_manifest", "host_manifest_workbench_outputs=rp_runner,rp_revision,rp_package")) return 1;
			if (!rp_host_seed_copy_workbench_value("workbench=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "usable-workbench:RUN-900");
			}
			if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_workbench=", value)) return 1;
			if (rp_host_seed_copy_workbench_value("task=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_workbench_task=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("runbook_format=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_workbench_runbook_format=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("timeline_format=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_workbench_timeline_format=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("manifest=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_workbench_manifest=", value)) return 1;
			}
			if (rp_host_seed_has("kind=workbench_file_manifest") ||
			    rp_host_seed_has("kind=workbench_file_verify")) {
				if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "files=", value, sizeof(value)) &&
				    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "files=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "9");
				}
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_file_count=", value)) return 1;
				if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "sha_records=", value, sizeof(value)) &&
				    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "sha_records=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "9");
				}
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_sha_records=", value)) return 1;
			}
			if (rp_host_seed_has("kind=workbench_file_verify")) {
				if (!rp_append_file("rp_artifact_manifest", "host_manifest_file_verify=passed")) return 1;
				if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "verified=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "9");
				}
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_verified_files=", value)) return 1;
				if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "missing=", value, sizeof(value))) {
					rp_copy_text(value, sizeof(value), "0");
				}
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_missing_files=", value)) return 1;
			}
			if (rp_host_seed_copy_workbench_value("bundle=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_artifact_manifest", "host_manifest_workbench_bundle=", value)) return 1;
			}
		}
		if (rp_host_seed_has_artifact_action()) {
			if (!rp_append_file("rp_artifact_manifest", "host_artifact_manifest_actions=applied")) return 1;
			if (rp_host_seed_has("kind=artifact_input") && !append_artifact_manifest_input()) return 1;
			if (rp_host_seed_has("kind=artifact_derive") && !append_artifact_manifest_derive()) return 1;
			if (rp_host_seed_has("kind=artifact_log") && !rp_append_file("rp_artifact_manifest", "host_artifact_manifest_log=rp_stage_log")) return 1;
			if (rp_host_seed_has("kind=artifact_chart") && !append_artifact_manifest_chart()) return 1;
			if (rp_host_seed_has("kind=artifact_package") && !append_artifact_manifest_package()) return 1;
		}
	}
	if (rp_host_seed_has("kind=host_workflow") || rp_host_seed_has("kind=host_workflow_export")) {
		char workflow_id[64];
		char run_id[48];
		char engine[32];
		char stages[16];
		char dag[64];
		char failed_stage[32];
		char retry_stage[32];
		char cache_hit_stage[32];
		char worker_slots[16];
		char queue_depth[16];
		char observer_events[16];
		char retry_reason[48];
		char cache_policy[32];
		char format[32];
		char bundle[48];
		char line[160];
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "workflow_id=", workflow_id, sizeof(workflow_id)) &&
		    !rp_host_seed_copy_value_for_kind("kind=host_workflow_export", "workflow_id=", workflow_id, sizeof(workflow_id))) {
			rp_copy_text(workflow_id, sizeof(workflow_id), "wf-host-plain");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "run_id=", run_id, sizeof(run_id)) &&
		    !rp_host_seed_copy_value_for_kind("kind=host_workflow_export", "run_id=", run_id, sizeof(run_id))) {
			rp_copy_text(run_id, sizeof(run_id), "RUN-042");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "engine=", engine, sizeof(engine))) {
			rp_copy_text(engine, sizeof(engine), "plain-c-runner");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "stages=", stages, sizeof(stages))) {
			rp_copy_text(stages, sizeof(stages), "5");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "dag=", dag, sizeof(dag))) {
			rp_copy_text(dag, sizeof(dag), "ingest>align>profile>review>package");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "failed_stage=", failed_stage, sizeof(failed_stage))) {
			rp_copy_text(failed_stage, sizeof(failed_stage), "align");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "retry_stage=", retry_stage, sizeof(retry_stage))) {
			rp_copy_text(retry_stage, sizeof(retry_stage), failed_stage);
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "cache_hit_stage=", cache_hit_stage, sizeof(cache_hit_stage))) {
			rp_copy_text(cache_hit_stage, sizeof(cache_hit_stage), "profile");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "worker_slots=", worker_slots, sizeof(worker_slots)) &&
		    !rp_host_seed_copy_value_for_kind("kind=host_workflow", "max_workers=", worker_slots, sizeof(worker_slots))) {
			rp_copy_text(worker_slots, sizeof(worker_slots), "4");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "queue_depth=", queue_depth, sizeof(queue_depth))) {
			rp_copy_text(queue_depth, sizeof(queue_depth), "8");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "observer_events=", observer_events, sizeof(observer_events))) {
			rp_copy_text(observer_events, sizeof(observer_events), "9");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "retry_reason=", retry_reason, sizeof(retry_reason))) {
			rp_copy_text(retry_reason, sizeof(retry_reason), "tool_output_missing");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow", "cache=", cache_policy, sizeof(cache_policy))) {
			rp_copy_text(cache_policy, sizeof(cache_policy), "content");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_export", "format=", format, sizeof(format))) {
			rp_copy_text(format, sizeof(format), "json");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_export", "bundle=", bundle, sizeof(bundle))) {
			rp_copy_text(bundle, sizeof(bundle), "workflow-export.zip");
		}
		if (!rp_append_file("rp_stage_dag", "host_workflow_payload=applied")) return 1;
		if (!rp_append_host_action_line("rp_stage_dag", "host_workflow_id=", workflow_id)) return 1;
		if (!rp_append_host_action_line("rp_stage_dag", "host_workflow_engine=", engine)) return 1;
		if (!rp_append_host_action_line("rp_stage_dag", "host_workflow_dag=", dag)) return 1;
		if (!rp_append_host_action_line("rp_stage_dag", "host_workflow_stages=", stages)) return 1;
		if (!rp_append_file("rp_stage_state", "host_workflow_state=executed")) return 1;
		if (!rp_append_host_action_line("rp_stage_state", "host_workflow_run_id=", run_id)) return 1;
		if (!rp_append_host_action_line("rp_stage_state", "host_workflow_engine=", engine)) return 1;
		if (!rp_append_host_action_line("rp_stage_state", "host_workflow_failed_stage=", failed_stage)) return 1;
		if (!rp_append_host_action_line("rp_stage_state", "host_workflow_retry_stage=", retry_stage)) return 1;
		if (!rp_append_host_action_line("rp_stage_state", "host_workflow_cache_hit_stage=", cache_hit_stage)) return 1;
		if (!rp_append_host_action_line("rp_stage_state", "host_workflow_worker_slots=", worker_slots)) return 1;
		if (!rp_append_host_action_line("rp_stage_state", "host_workflow_queue_depth=", queue_depth)) return 1;
		if (!rp_append_host_action_line("rp_cache_index", "host_workflow_cache_policy=", cache_policy)) return 1;
		if (!rp_append_host_action_line("rp_cache_index", "host_workflow_cache_hit_stage=", cache_hit_stage)) return 1;
		if (!rp_append_host_action_line("rp_retry_plan", "host_workflow_retry_stage=", retry_stage)) return 1;
		if (!rp_append_host_action_line("rp_retry_plan", "host_workflow_retry_reason=", retry_reason)) return 1;
		if (!rp_append_host_action_line("rp_run_events", "host_workflow_event=started;workflow=", workflow_id)) return 1;
		rp_copy_text(line, sizeof(line), "host_workflow_event=retry;stage=");
		rp_append_text(line, sizeof(line), retry_stage);
		rp_append_text(line, sizeof(line), ";reason=");
		rp_append_text(line, sizeof(line), retry_reason);
		if (!rp_append_file("rp_run_events", line)) return 1;
		if (!rp_append_file("rp_run_events", "host_workflow_event=finished;status=ready")) return 1;
		if (!rp_append_host_action_line("rp_worker", "host_workflow_worker_slots=", worker_slots)) return 1;
		if (!rp_append_host_action_line("rp_worker", "host_workflow_queue_depth=", queue_depth)) return 1;
		if (!rp_append_host_action_line("rp_execobs", "host_workflow_observer_events=", observer_events)) return 1;
		if (!rp_append_host_action_line("rp_execobs", "host_workflow_retry_reason=", retry_reason)) return 1;
		if (!rp_append_host_action_line("rp_execobs", "host_workflow_worker_slots=", worker_slots)) return 1;
		rp_copy_text(line, sizeof(line), "host_manifest_workflow=");
		rp_append_text(line, sizeof(line), workflow_id);
		rp_append_text(line, sizeof(line), ";run_id=");
		rp_append_text(line, sizeof(line), run_id);
		rp_append_text(line, sizeof(line), ";engine=");
		rp_append_text(line, sizeof(line), engine);
		rp_append_text(line, sizeof(line), ";stages=");
		rp_append_text(line, sizeof(line), stages);
		if (!rp_append_file("rp_artifact_manifest", line)) return 1;
		rp_copy_text(line, sizeof(line), "host_manifest_workflow_export=");
		rp_append_text(line, sizeof(line), bundle);
		rp_append_text(line, sizeof(line), ";format=");
		rp_append_text(line, sizeof(line), format);
		if (!rp_append_file("rp_artifact_manifest", line)) return 1;
		rp_copy_text(line, sizeof(line), "host_action_workflow=");
		rp_append_text(line, sizeof(line), workflow_id);
		rp_append_text(line, sizeof(line), ";run_id=");
		rp_append_text(line, sizeof(line), run_id);
		rp_append_text(line, sizeof(line), ";engine=");
		rp_append_text(line, sizeof(line), engine);
		rp_append_text(line, sizeof(line), ";status=ready");
		if (!rp_append_file("rp_runner", line)) return 1;
		rp_copy_text(line, sizeof(line), "host_action_workflow_runtime=retry_stage:");
		rp_append_text(line, sizeof(line), retry_stage);
		rp_append_text(line, sizeof(line), ";cache_hit:");
		rp_append_text(line, sizeof(line), cache_hit_stage);
		rp_append_text(line, sizeof(line), ";workers:");
		rp_append_text(line, sizeof(line), worker_slots);
		if (!rp_append_file("rp_runner", line)) return 1;
		if (!rp_append_host_action_line("rp_runner", "host_action_workflow_export=", bundle)) return 1;
		if (!rp_append_host_action_line("rp_runner", "host_action_workflow_export_format=", format)) return 1;
	}
	if (rp_host_seed_has_host_workflow_step_action()) {
		char workflow_id[64];
		char run_id[48];
		char value[96];
		char other[96];
		char third[96];
		char fourth[96];
		char fifth[96];
		char line[220];
		if (!rp_host_seed_copy_host_workflow_value("workflow_id=", workflow_id, sizeof(workflow_id))) {
			rp_copy_text(workflow_id, sizeof(workflow_id), "wf-host-plain");
		}
		if (!rp_host_seed_copy_host_workflow_value("run_id=", run_id, sizeof(run_id))) {
			rp_copy_text(run_id, sizeof(run_id), "RUN-042");
		}
		if (!rp_append_file("rp_stage_state", "host_workflow_steps=applied")) return 1;
		if (rp_host_seed_has("kind=host_workflow_stage")) {
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_stage", "stage=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "align");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_stage", "attempt=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "2");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_stage", "status=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "failed");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_stage", "command=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "align_reads");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_stage", "duration_ms=", fifth, sizeof(fifth))) {
				rp_copy_text(fifth, sizeof(fifth), "1200");
			}
			rp_copy_text(line, sizeof(line), "host_workflow_stage_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";attempt=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";status=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";command=");
			rp_append_text(line, sizeof(line), fourth);
			rp_append_text(line, sizeof(line), ";duration_ms=");
			rp_append_text(line, sizeof(line), fifth);
			if (!rp_append_file("rp_stage_state", line)) return 1;
			rp_copy_text(line, sizeof(line), "host_workflow_event=stage_attempt;workflow=");
			rp_append_text(line, sizeof(line), workflow_id);
			rp_append_text(line, sizeof(line), ";run_id=");
			rp_append_text(line, sizeof(line), run_id);
			rp_append_text(line, sizeof(line), ";stage=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";status=");
			rp_append_text(line, sizeof(line), third);
			if (!rp_append_file("rp_run_events", line)) return 1;
		}
		if (rp_host_seed_has("kind=host_workflow_cache")) {
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_cache", "stage=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "profile");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_cache", "cache_key=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "cache:profile:R1");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_cache", "cache_result=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "hit");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_cache", "cache_policy=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "content");
			}
			rp_copy_text(line, sizeof(line), "host_workflow_cache_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";key=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";result=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";policy=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_append_file("rp_cache_index", line)) return 1;
		}
		if (rp_host_seed_has("kind=host_workflow_retry")) {
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_retry", "stage=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "align");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_retry", "retry_reason=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "checksum_mismatch");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_retry", "next_attempt=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "3");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_retry", "decision=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "rerun_stage");
			}
			rp_copy_text(line, sizeof(line), "host_workflow_retry_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";reason=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";next_attempt=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";decision=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_append_file("rp_retry_plan", line)) return 1;
		}
		if (rp_host_seed_has("kind=host_workflow_artifact")) {
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_artifact", "artifact=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "align.bam");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_artifact", "artifact_kind=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "alignment");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_artifact", "sha256=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "sha-host-artifact");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_artifact", "bytes=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "2048");
			}
			rp_copy_text(line, sizeof(line), "host_workflow_artifact_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";kind=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";sha256=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";bytes=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_append_file("rp_artifact_manifest", line)) return 1;
		}
		if (rp_host_seed_has("kind=host_workflow_report")) {
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_report", "report=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "workflow-report.md");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_report", "format=", other, sizeof(other))) {
				rp_copy_text(other, sizeof(other), "markdown");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_report", "sections=", third, sizeof(third))) {
				rp_copy_text(third, sizeof(third), "5");
			}
			if (!rp_host_seed_copy_value_for_kind("kind=host_workflow_report", "status=", fourth, sizeof(fourth))) {
				rp_copy_text(fourth, sizeof(fourth), "ready");
			}
			rp_copy_text(line, sizeof(line), "host_workflow_report_action=");
			rp_append_text(line, sizeof(line), value);
			rp_append_text(line, sizeof(line), ";format=");
			rp_append_text(line, sizeof(line), other);
			rp_append_text(line, sizeof(line), ";sections=");
			rp_append_text(line, sizeof(line), third);
			rp_append_text(line, sizeof(line), ";status=");
			rp_append_text(line, sizeof(line), fourth);
			if (!rp_append_file("rp_report_text", line)) return 1;
			if (!rp_append_file("rp_artifact_manifest", line)) return 1;
		}
	}
	if (!rp_append_file("rp_runner", "custom_runs=3")) return 1;
	if (!rp_append_file("rp_runner", "dynamic_input_runs=4")) return 1;
	if (!rp_append_file("rp_runner", "dynamic_run=usable-run:RUN-904;source=api;status=queued;next=validate")) return 1;
	if (!rp_append_file("rp_runner", "dynamic_replay_plan=RUN-900->RUN-904;shared_template=usable-template:workspace-900")) return 1;
	if (!rp_append_file("rp_runner", "human_review_id=usable-review:RUN-900:1")) return 1;
	if (!rp_append_file("rp_runner", "human_review_decision=needs_revision")) return 1;
	if (!rp_append_file("rp_runner", "revision_task_id=usable-revision-task:RUN-900:1")) return 1;
	if (!rp_append_file("rp_runner", "revision_requested_changes=2")) return 1;
	if (!rp_append_file("rp_runner", "revision_change=methods_retry_scope;status=applied")) return 1;
	if (!rp_append_file("rp_runner", "revision_change=chart_caption;status=applied")) return 1;
	if (!rp_append_file("rp_runner", "revision_status=completed")) return 1;
	if (!rp_append_file("rp_runner", "revision_run=usable-run:RUN-900-rev1")) return 1;
	if (!rp_append_file("rp_runner", "revision_delta=rp_revision")) return 1;
	if (!rp_append_file("rp_runner", "custom_source=rp_input")) return 1;
	if (!rp_append_file("rp_runner", "custom_dataset_rows=3")) return 1;
	if (!rp_append_file("rp_runner", "custom_agent_decisions=15")) return 1;
	if (!rp_append_file("rp_runner", "library_source_count=1")) return 1;
	if (!rp_append_file("rp_runner", "library_source=usable-source:library2026:1")) return 1;
	if (!rp_append_file("rp_runner", "bibliography_entries=3")) return 1;
	if (!rp_append_file("rp_runner", "citation_plan_entries=3")) return 1;
	if (!rp_append_file("rp_runner", "custom_analysis=mean_control:12,mean_treatment:20,stronger:treatment")) return 1;
	if (!rp_append_file("rp_runner", "custom_analysis_2=mean_control:8,mean_treatment:13,stronger:treatment")) return 1;
	if (!rp_append_file("rp_runner", "custom_analysis_3=mean_control:30,mean_treatment:28,stronger:control")) return 1;
	if (!rp_append_file("rp_runner", "custom_status=ok")) return 1;
	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		if (!rp_append_host_action_line("rp_runner", "host_action_run=usable-run:", seed_run)) return 1;
		if (!rp_append_file("rp_runner", "host_action_kind=research_run")) return 1;
		if (!rp_append_file("rp_runner", "host_action_stages=5")) return 1;
		if (!rp_append_file("rp_runner", "host_action_artifacts=6")) return 1;
		if (!rp_append_file("rp_runner", "host_action_report=host action research task completed from seeded inbox")) return 1;
		if (!rp_append_file("rp_runner", "host_action_status=completed")) return 1;
	}
	if (rp_host_seed_has("kind=agentcompare")) {
		char profile[48];
		char line[120];
		if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
			rp_copy_text(profile, sizeof(profile), "plain_ucore");
		}
		rp_copy_text(line, sizeof(line), "host_action_compare=");
		rp_append_text(line, sizeof(line), profile);
		rp_append_text(line, sizeof(line), ";status=ready");
		if (!rp_append_file("rp_runner", line)) return 1;
	}
	if (rp_host_seed_has("kind=revision_run")) {
		char revision_run[48];
		char line[128];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_run", "run_id=", revision_run, sizeof(revision_run))) {
			rp_copy_text(revision_run, sizeof(revision_run), "RUN-900");
		}
		rp_copy_text(line, sizeof(line), "host_action_revision_run=usable-run:");
		rp_append_text(line, sizeof(line), revision_run);
		rp_append_text(line, sizeof(line), "-rev2;status=completed");
		if (!rp_append_file("rp_runner", line)) return 1;
	}
	if (!rp_append_file("rp_runner", "real_artifact_items=5")) return 1;
	if (!rp_append_file("rp_runner", "derived_alignment=rp_artifact:rp_align_table")) return 1;
	if (!rp_append_file("rp_runner", "derived_metrics=rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv")) return 1;
	if (!rp_append_file("rp_ack", "ack=workflow_runner;msg=runner;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=custom_research;msg=runner;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.read_dag")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.read_input")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.write_stage_state")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.write_cache_index")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.write_retry_plan")) return 1;
	if (!rp_append_file("rp_tool", "tool=workflow_runner.write_manifest")) return 1;
	if (!rp_append_status("workflow_runner=ready")) return 1;
	if (!rp_append_status("stage_state=ready")) return 1;
	if (!rp_append_status("cache_index=ready")) return 1;
	if (!rp_append_status("retry_plan=ready")) return 1;
	if (!rp_append_status("artifact_manifest=ready")) return 1;
	if (!rp_append_status("custom_research=ready")) return 1;
	if (!rp_append_status("revision_task=ready")) return 1;
	printf("rp_workflow_runner: stages=5 events=8 retries=1 cache_hits=1 custom_runs=3 status=ready\n");
	return 0;
}
