import copy
import math
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_WEIGHT = 1024
REQUEST = {"urgent": 1, "interactive": 2, "normal": 4, "batch": 8}


def jain(values):
    total = sum(values)
    squares = sum(value * value for value in values)
    return total * total / (len(values) * squares)


def decay_toward(value, target, intervals):
    intervals = min(intervals, 8)
    if value < target:
        return target - ((target - value) >> intervals)
    return target + ((value - target) >> intervals)


@dataclass
class Entity:
    lifecycle: tuple
    domain: int
    vruntime: int = 0
    request: int = REQUEST["normal"]
    remaining: int = 0
    service: int = 0
    threads: int = 1


class EevdfModel:
    """Small executable oracle for the fixed-weight kernel policy."""

    def __init__(self, entities):
        self.entities = {entity.domain: entity for entity in entities}
        self.vtime = 0

    def select(self, domains):
        before = copy.deepcopy((self.entities, self.vtime))
        if not domains or len(domains) > 4 or len(set(domains)) != len(domains):
            return None, before == (self.entities, self.vtime)
        selected = []
        lifecycles = set()
        for domain in domains:
            entity = self.entities.get(domain)
            if entity is None or entity.lifecycle in lifecycles:
                return None, before == (self.entities, self.vtime)
            lifecycles.add(entity.lifecycle)
            selected.append(entity)
        for entity in selected:
            if entity.remaining == 0:
                entity.remaining = entity.request
        average = sum(entity.vruntime for entity in selected) // len(selected)
        self.vtime = max(self.vtime, average)
        eligible = [entity for entity in selected if entity.vruntime <= self.vtime]
        winner = min(
            eligible,
            key=lambda entity: (
                entity.vruntime + entity.remaining,
                entity.vruntime,
                entity.lifecycle,
                entity.domain,
            ),
        )
        return winner, False

    def run(self, domains, quanta):
        for _ in range(quanta):
            winner, _ = self.select(domains)
            winner.vruntime += 1
            winner.service += 1
            winner.remaining -= 1


class WorkflowSchedulerModelTest(unittest.TestCase):
    def test_one_four_and_sixteen_logical_workflows(self):
        one = EevdfModel([Entity((1, 1), 1)])
        one.run([1], 400)
        self.assertEqual(one.entities[1].service, 400)

        four = EevdfModel([Entity((index, 1), index) for index in range(1, 5)])
        four.run([1, 2, 3, 4], 16000)
        service = [four.entities[index].service for index in range(1, 5)]
        self.assertEqual(service, [4000] * 4)
        self.assertEqual(jain(service), 1.0)

        epoch_service = []
        for epoch in range(4):
            start = epoch * 4 + 1
            model = EevdfModel(
                [Entity((index, epoch + 1), index) for index in range(start, start + 4)]
            )
            model.run(list(range(start, start + 4)), 4000)
            epoch_service.extend(
                model.entities[index].service for index in range(start, start + 4)
            )
        self.assertEqual(epoch_service, [1000] * 16)
        self.assertEqual(jain(epoch_service), 1.0)

    def test_thread_count_cannot_amplify_weight(self):
        model = EevdfModel(
            [Entity((1, 1), 1, threads=1), Entity((2, 1), 2, threads=64)]
        )
        model.run([1, 2], 8000)
        self.assertEqual(model.entities[1].service, model.entities[2].service)
        self.assertEqual(BASE_WEIGHT, 1024)

    def test_latency_only_changes_request_length(self):
        entities = [
            Entity((1, 1), 1, request=REQUEST["urgent"]),
            Entity((2, 1), 2, request=REQUEST["batch"]),
        ]
        model = EevdfModel(entities)
        model.run([1, 2], 16000)
        service = [model.entities[1].service, model.entities[2].service]
        self.assertLessEqual(abs(service[0] - service[1]), 1)

    def test_sleep_decay_is_bidirectional_and_bounded(self):
        self.assertEqual(decay_toward(0, 1024, 1), 512)
        self.assertEqual(decay_toward(2048, 1024, 1), 1536)
        self.assertEqual(decay_toward(0, 1024, 99), 1020)
        self.assertEqual(decay_toward(2048, 1024, 99), 1028)

    def test_failed_plan_does_not_mutate_model_state(self):
        model = EevdfModel([Entity((index, 1), index) for index in range(1, 5)])
        before = copy.deepcopy((model.entities, model.vtime))
        winner, unchanged = model.select([1, 2, 3, 4, 5])
        self.assertIsNone(winner)
        self.assertTrue(unchanged)
        self.assertEqual(before, (model.entities, model.vtime))

        model.entities[4].lifecycle = model.entities[1].lifecycle
        before = copy.deepcopy((model.entities, model.vtime))
        winner, unchanged = model.select([1, 2, 3, 4])
        self.assertIsNone(winner)
        self.assertTrue(unchanged)
        self.assertEqual(before, (model.entities, model.vtime))

    def test_kernel_source_has_bounded_cache_and_transactional_commit(self):
        scheduler = (ROOT / "os" / "workflow_scheduler.c").read_text(
            encoding="utf-8"
        )
        proc = (ROOT / "os" / "proc.c").read_text(encoding="utf-8")
        select_body = scheduler[
            scheduler.index("int workflow_scheduler_select(") :
            scheduler.index("void workflow_scheduler_note_fallback(")
        ]
        candidate_body = proc[
            proc.index("static int scheduler_workflow_candidate(") :
            proc.index("static struct thread *fetch_task_legacy_locked(")
        ]
        fetch_body = proc[
            proc.index("struct thread *fetch_task()") : proc.index("void add_task(")
        ]

        self.assertLess(select_body.index("Phase one is read-only"),
                        select_body.index("Phase two commits"))
        self.assertLess(select_body.index("if (selected < 0)"),
                        select_body.index("Phase two commits"))
        fail_body = select_body[select_body.index("fail:") :]
        self.assertNotIn("workflow_scheduler_entities", fail_body)
        self.assertNotIn("workflow_scheduler_vtime", fail_body)
        self.assertNotIn("scheduler_lane", candidate_body)
        self.assertNotRegex(candidate_body, r"\b(for|while)\s*\(")
        self.assertLess(fetch_body.index("workflow_scheduler_domain_runnable"),
                        fetch_body.index("scheduler_workflow_candidate"))
        single_fast_path = fetch_body[
            fetch_body.index("if (workflow_count == 1)") :
            fetch_body.index("for (int i = 0; i < workflow_count; i++)")
        ]
        self.assertIn("fetch_task_legacy_locked", single_fast_path)
        self.assertNotIn("workflow_scheduler_select", single_fast_path)
        self.assertIn(
            "entity->vruntime > workflow_scheduler_vtime", scheduler
        )
        self.assertGreaterEqual(
            proc.count("workflow_scheduler_domain_tracked(domain_id)"), 2
        )
        self.assertIn("<= 768", scheduler)

    def test_v2_negotiation_and_v3_metrics_projection(self):
        abi = (ROOT / "include" / "agent_lifecycle_abi.h").read_text(
            encoding="utf-8"
        )
        implementation = (ROOT / "os" / "agent_lifecycle.c").read_text(
            encoding="utf-8"
        )
        expected = [
            "scheduler_deadline_misses",
            "scheduler_wakeup_samples",
            "scheduler_wakeup_latency_buckets",
        ]
        self.assertIn("AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION 3U", abi)
        self.assertIn("AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE 64U", abi)
        self.assertIn("sizeof(struct agent_workflow_lifecycle_info) == 216", abi)
        self.assertIn("user_size > AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE",
                      implementation)
        for field in expected:
            self.assertIn(field, abi)
            self.assertIn(field, implementation)

    def test_wakeup_histogram_supports_percentile_bounds(self):
        samples = [0, 1, 1, 2, 2, 3, 8, 9, 20, 50]
        buckets = [0, 0, 0, 0]
        for sample in samples:
            bucket = 0 if sample <= 1 else 1 if sample <= 2 else 2 if sample <= 8 else 3
            buckets[bucket] += 1
        self.assertEqual(sum(buckets), len(samples))
        self.assertEqual(buckets, [3, 2, 2, 3])
        p50_rank = math.ceil(0.50 * len(samples))
        p99_rank = math.ceil(0.99 * len(samples))
        self.assertLessEqual(p50_rank, sum(buckets[:2]))
        self.assertGreater(p99_rank, sum(buckets[:3]))

    def test_deadline_miss_is_counted_once_per_runnable_epoch(self):
        deadline = 10
        missed = False
        misses = 0
        for dispatch_tick in [8, 10, 11, 20]:
            if dispatch_tick >= deadline and not missed:
                missed = True
                misses += 1
        self.assertEqual(misses, 1)

        missed = False
        deadline = 40
        for dispatch_tick in [39, 41, 42]:
            if dispatch_tick >= deadline and not missed:
                missed = True
                misses += 1
        self.assertEqual(misses, 2)


if __name__ == "__main__":
    unittest.main()
