from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_seeded_action_state as checker


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class SeededActionStateTests(unittest.TestCase):
    def test_seeded_action_payload_is_representative(self) -> None:
        actions = checker.seeded_actions()
        self.assertEqual(len(actions), 44)
        paths = [action["path"] for action in actions]
        for path in [
            "/actions/research/rerun",
            "/actions/research/dataset",
            "/actions/research/literature-search",
            "/actions/research/artifact-package",
            "/actions/host-workflow/stage-attempt",
            "/actions/research/llm-relay-response",
            "/actions/research/workbench-file-verify",
            "/actions/research/dataset-run-comparison",
            "/actions/research/project-action-execute",
            "/actions/research/study-protocol-reproduction-package-action-execute",
            "/actions/research/project-delivery",
            "/actions/workflow-portability/package",
            "/actions/agentcompare/run",
        ]:
            self.assertIn(path, paths)
        kinds = checker.seeded_action_kinds()
        for kind in [
            "research_rerun",
            "dataset",
            "evidence_protocol",
            "artifact_package",
            "host_workflow_stage",
            "llm_relay_fallback",
            "workbench_file_verify",
            "dataset_run_comparison",
            "project_action_execute",
            "study_protocol_reproduction_package_action_execute",
            "project_delivery",
            "workflow_portability_package",
            "agentcompare",
        ]:
            self.assertIn(kind, kinds)
        action = actions[0]
        self.assertEqual(action["path"], "/actions/research/rerun")
        payload = action["payload"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["run_id"], "RUN-999-rerun")
        self.assertEqual(payload["parent_run"], "RUN-999")
        self.assertEqual(payload["provider"], "template")
        workflow = actions[12]["payload"]
        self.assertIsInstance(workflow, dict)
        self.assertEqual(workflow["retry_stage"], "align")
        self.assertEqual(workflow["cache_hit_stage"], "profile")
        portability = actions[36]["payload"]
        self.assertIsInstance(portability, dict)
        self.assertEqual(portability["target_runtime"], "agentos-ucore")

    def test_seeded_route_coverage_reports_host_relation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host_dir = root / "host"
            api = host_dir / "agent_platform" / "api_server.py"
            api.parent.mkdir(parents=True, exist_ok=True)
            api.write_text(
                "\n".join(f'if path == "{action["path"]}":' for action in checker.seeded_actions())
                + '\nif path == "/actions/research/workbench-note":\n',
                encoding="utf-8",
            )

            coverage = checker.seeded_route_coverage(root, host_dir)

            self.assertEqual(coverage["status"], "ready")
            self.assertEqual(coverage["host_action_routes"], 45)
            self.assertEqual(coverage["seeded_known_routes"], 44)
            self.assertEqual(coverage["seeded_host_kinds"], 44)
            self.assertIn("workbench_note", coverage["uncovered_host_kinds"])

    def test_validate_extracted_state_accepts_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            state = run_dir / "state-extracted"
            write(
                state / "rp_host_action_seed",
                "\n".join(f"kind={kind}" for kind in checker.seeded_action_kinds())
                + "\n"
                + "run_id=RUN-999-rerun\n"
                + "parent_run=RUN-999\n"
                + "title=Reusable response table\n"
                + "citation_key=agentlibrary2026\n"
                + "workflow_id=wf-host-999\n"
                + "request_id=host-q1\n"
                + "response_id=host-r1\n"
                + "compare_profile=compare-profile:host-nextflow:migration\n",
            )
            write(
                state / "rp_input",
                "host_action_rerun_id=RUN-999-rerun\n"
                "host_action_rerun_parent=RUN-999\n"
                "host_action_rerun_provider=template\n"
                "host_action_dataset=registered\n"
                "host_action_dataset_title=Reusable response table\n"
                "host_action_library_citation=agentlibrary2026\n"
                "host_action_template_name=Reusable response comparison\n",
            )
            write(
                state / "rp_runner",
                "host_action_rerun=usable-run:RUN-999-rerun;parent=RUN-999;status=completed\n"
                "host_action_workflow=wf-host-999;run_id=RUN-999;engine=host-runner;status=ready\n"
                "host_action_kind=research_rerun\n"
                "host_action_workbench_file_verify=passed\n",
            )
            write(
                state / "rp_lit",
                "host_action_literature_query=agent workflow provenance\n"
                "host_action_protocol_title=Agent workflow evidence protocol\n",
            )
            write(state / "rp_knowledge", "host_action_evidence_included=3\n")
            write(
                state / "rp_report_text",
                "host_report_rerun_id=RUN-999-rerun\n"
                "host_report_rerun_parent=RUN-999\n"
                "host_report_workbench_task=draft\n"
                "host_workflow_report_action=workflow-report.md;format=markdown;sections=5;status=ready\n",
            )
            write(
                state / "rp_artifact_manifest",
                "host_manifest_rerun=RUN-999-rerun;parent=RUN-999;status=ready\n"
                "host_artifact_manifest_derive=raw-counts.csv;output=normalized-counts.csv;operation=normalize;stage=analyze;sha256=sha-derived-999\n"
                "host_manifest_workbench_task=draft\n"
                "host_workflow_artifact_action=align.bam;kind=alignment;sha256=sha-host-artifact;bytes=4096\n",
            )
            write(
                state / "rp_artifact",
                "host_artifact_input=reads_R1.fastq;kind=fastq;sha256=sha-input-999;bytes=2048;source=upload\n"
                "host_artifact_derive=raw-counts.csv;output=normalized-counts.csv;operation=normalize;stage=analyze;sha256=sha-derived-999\n",
            )
            write(state / "rp_stage_log", "host_artifact_log=align.log;stage=align;level=warn;message=quality_gate_retry\n")
            write(state / "rp_chart_data", "host_artifact_chart=qc-chart.json;type=line;data_file=normalized-counts.csv;points=12\n")
            write(state / "rp_package", "host_artifact_package=artifact-bundle.zip;manifest=artifact-manifest.json;files=5;status=ready\n")
            write(
                state / "rp_stage_dag",
                "host_workflow_id=wf-host-999\n"
                "host_workflow_dag=ingest>analyze>report\n",
            )
            write(
                state / "rp_stage_state",
                "host_workflow_run_id=RUN-999\n"
                "host_workflow_stage_action=align;attempt=2;status=failed;command=align_reads;duration_ms=1200\n",
            )
            write(state / "rp_cache_index", "host_workflow_cache_action=profile;key=cache:RUN-999:profile;result=hit;policy=content\n")
            write(state / "rp_retry_plan", "host_workflow_retry_action=align;reason=quality_gate;next_attempt=3;decision=rerun_stage\n")
            write(state / "rp_llm_packets", "host_llm_packet_request=host-q1\n")
            write(state / "rp_llm_resp", "host_llm_response_id=host-r1\n")
            write(state / "rp_llm_fallback", "host_llm_fallback_case=missing_cloud_key\n")
            write(
                state / "rp_llm_routes",
                "host_llm_route=review_summary\n"
                "host_llm_route_provider=template\n",
            )
            write(state / "rp_llm_guard", "host_llm_guard_status=passed\n")
            write(
                state / "rp_usableds",
                "host_action_dataset_ops=applied;preview_dataset=usable-dataset:response-table;preview_rows=6;dataset_run=usable-run:dataset:1;run_comparison=stable;status=ready\n",
            )
            write(
                state / "rp_usableproj",
                "host_action_project_scaffold=lab-gene-x;template=scaffold-template:starter;workspace=workspace/lab-gene-x;files=8;status=ready\n"
                "host_action_project_launch=lab-gene-x;scaffold=scaffold:lab-gene-x:starter;workbench=usable-workbench:RUN-900;run=usable-run:RUN-900;provider=template;status=ready\n",
            )
            write(
                state / "rp_usablepack",
                "host_action_project_action_execute=lab-gene-x;action=usable-project-action:RUN-042:1;key=build_reproduction_package;provider=template;result=completed;status=ready\n",
            )
            write(
                state / "rp_studyproto",
                "host_action_study_protocol=applied;protocol=usable-study-protocol:variant-calling-qc;title=Variant calling QC;launch=study-protocol-launch:RUN-042;reproduction_package=study-protocol-reproduction-package:RUN-042;action_execute_result=passed;status=ready\n",
            )
            write(
                state / "rp_web_bundle",
                "host_action_project_release_gate=approved\n"
                "host_action_project_provenance_graph=exported\n"
                "host_action_project_delivery=project-bundle.zip\n",
            )
            write(
                state / "rp_wfio",
                "host_portability_import=workflow-import:host-nextflow;format=nextflow;source=main.host.nf\n"
                "host_portability_target=agentos-ucore\n"
                "host_portability_compare_profile=compare-profile:host-nextflow:migration\n"
                "host_portability_import_action=workflow-import:host-nextflow;format=nextflow;source=main.host.nf;normalized_steps=15;adapter=adapter:nextflow\n"
                "host_portability_plan_action=workflow-migration-plan:host-nextflow;target=agentos-ucore;steps=9;risks=4\n"
                "host_portability_package_action=workflow-portability-host.zip;format=zip;import=workflow-import:host-nextflow;bundle=workflow-portability-host.zip\n",
            )
            write(state / "rp_agentcmp", "host_action_compare_profile=plain-vs-agentos\n")

            self.assertEqual(checker.validate_extracted_state("plain", run_dir), [])

    def test_validate_extracted_state_reports_missing_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            state = run_dir / "state-extracted"
            write(state / "rp_host_action_seed", "kind=research_rerun;run_id=RUN-999-rerun;parent_run=RUN-999\n")
            write(state / "rp_input", "host_action_rerun_id=RUN-999-rerun\n")
            write(state / "rp_runner", "host_action_kind=research_rerun\n")
            write(state / "rp_report_text", "host_report_rerun_id=RUN-999-rerun\n")
            write(state / "rp_artifact_manifest", "status=ready\n")

            failures = checker.validate_extracted_state("agentos", run_dir)

            self.assertTrue(any("rp_input missing host_action_rerun_parent=RUN-999" in item for item in failures))
            self.assertTrue(any("rp_artifact_manifest missing host_manifest_rerun" in item for item in failures))
            self.assertTrue(any("missing rp_artifact" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
