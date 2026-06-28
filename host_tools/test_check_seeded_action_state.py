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
        self.assertEqual(len(actions), 8)
        self.assertEqual(
            [action["path"] for action in actions],
            [
                "/actions/research/rerun",
                "/actions/research/artifact-derive",
                "/actions/host-workflow/run",
                "/actions/research/llm-relay-request",
                "/actions/research/workbench",
                "/actions/research/workbench-task",
                "/actions/research/project-release-gate",
                "/actions/workflow-portability/run",
            ],
        )
        self.assertEqual(
            checker.seeded_action_kinds(),
            [
                "research_rerun",
                "artifact_derive",
                "host_workflow",
                "llm_relay_request",
                "workbench",
                "workbench_task",
                "project_release_gate",
                "workflow_portability",
            ],
        )
        action = actions[0]
        self.assertEqual(action["path"], "/actions/research/rerun")
        payload = action["payload"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["run_id"], "RUN-999-rerun")
        self.assertEqual(payload["parent_run"], "RUN-999")
        self.assertEqual(payload["provider"], "template")

    def test_validate_extracted_state_accepts_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            state = run_dir / "state-extracted"
            write(state / "rp_host_action_seed", "kind=research_rerun;run_id=RUN-999-rerun;parent_run=RUN-999\n")
            write(
                state / "rp_input",
                "host_action_rerun_id=RUN-999-rerun\n"
                "host_action_rerun_parent=RUN-999\n"
                "host_action_rerun_provider=template\n",
            )
            write(
                state / "rp_runner",
                "host_action_rerun=usable-run:RUN-999-rerun;parent=RUN-999;status=completed\n"
                "host_action_workflow=wf-host-999;run_id=RUN-999;engine=host-runner;status=ready\n"
                "host_action_kind=research_rerun\n",
            )
            write(
                state / "rp_report_text",
                "host_report_rerun_id=RUN-999-rerun\n"
                "host_report_rerun_parent=RUN-999\n"
                "host_report_workbench_task=draft\n",
            )
            write(
                state / "rp_artifact_manifest",
                "host_manifest_rerun=RUN-999-rerun;parent=RUN-999;status=ready\n"
                "host_artifact_manifest_derive=raw-counts.csv;output=normalized-counts.csv;operation=normalize;stage=analyze;sha256=sha-derived-999\n"
                "host_manifest_workbench_task=draft\n",
            )
            write(
                state / "rp_artifact",
                "host_artifact_derive=raw-counts.csv;output=normalized-counts.csv;operation=normalize;stage=analyze;sha256=sha-derived-999\n",
            )
            write(
                state / "rp_stage_dag",
                "host_workflow_id=wf-host-999\n"
                "host_workflow_dag=ingest>analyze>report\n",
            )
            write(
                state / "rp_stage_state",
                "host_workflow_run_id=RUN-999\n"
                "host_workflow_retry_stage=analyze\n",
            )
            write(state / "rp_llm_packets", "host_llm_packet_request=llm-999\n")
            write(
                state / "rp_llm_routes",
                "host_llm_route=review_summary\n"
                "host_llm_route_provider=template\n",
            )
            write(state / "rp_llm_guard", "host_llm_guard_status=passed\n")
            write(state / "rp_web_bundle", "host_action_project_release_gate=approved\n")
            write(
                state / "rp_wfio",
                "host_portability_import=wfimp-999;format=snakemake;source=Snakefile\n"
                "host_portability_target=agentos_ucore\n"
                "host_portability_compare_profile=plain-vs-agentos\n",
            )

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
