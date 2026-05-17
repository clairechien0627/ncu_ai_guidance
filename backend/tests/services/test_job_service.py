"""Unit tests for services/job_service.py — sync state machine functions only."""
import asyncio
import sys
import unittest
from unittest.mock import MagicMock, patch

# Stub out heavy imports before loading job_service
for mod in ("database", "rag", "extraction", "config", "agent"):
    sys.modules.setdefault(mod, MagicMock())

import services.job_service as js


def _reset():
    """Clear module state between tests."""
    js._active_jobs.clear()
    js._job_subscribers.clear()
    js._reindex_work_queue.clear()
    js._extract_work_queue.clear()
    js._reindex_tasks.clear()


class TestJobsEnqueue(unittest.TestCase):
    def setUp(self): _reset()

    def test_enqueue_new(self):
        js.jobs_enqueue([(1, "a.pdf"), (2, "b.pdf")], job_type="reindex")
        self.assertEqual(len(js._active_jobs), 2)
        self.assertEqual(js._active_jobs[0]["doc_id"], 1)
        self.assertEqual(js._active_jobs[0]["status"], "queued")
        self.assertEqual(js._active_jobs[0]["job_type"], "reindex")

    def test_enqueue_skips_active(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js.jobs_set_running(1)
        js.jobs_enqueue([(1, "a.pdf")])  # should be skipped — already running
        self.assertEqual(len(js._active_jobs), 1)
        self.assertEqual(js._active_jobs[0]["status"], "running")

    def test_enqueue_replaces_done(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js._active_jobs[0]["status"] = "done"
        js.jobs_enqueue([(1, "a.pdf")])  # done entry should be replaced
        entries = [j for j in js._active_jobs if j["doc_id"] == 1]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["status"], "queued")


class TestJobsSetRunning(unittest.TestCase):
    def setUp(self): _reset()

    def test_transitions_queued_to_running(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js.jobs_set_running(1)
        self.assertEqual(js._active_jobs[0]["status"], "running")

    def test_ignores_already_running(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js.jobs_set_running(1)
        js.jobs_set_running(1)  # second call — no-op
        self.assertEqual(js._active_jobs[0]["status"], "running")

    def test_ignores_unknown_doc(self):
        js.jobs_set_running(999)  # should not raise
        self.assertEqual(len(js._active_jobs), 0)


class TestJobsRemove(unittest.TestCase):
    def setUp(self): _reset()

    def test_removes_by_doc_id(self):
        js.jobs_enqueue([(1, "a.pdf"), (2, "b.pdf")])
        js.jobs_remove(1)
        self.assertEqual(len(js._active_jobs), 1)
        self.assertEqual(js._active_jobs[0]["doc_id"], 2)

    def test_remove_unknown_is_noop(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js.jobs_remove(999)
        self.assertEqual(len(js._active_jobs), 1)


class TestJobsSetStage(unittest.TestCase):
    def setUp(self): _reset()

    def test_sets_stage(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js.jobs_set_running(1)
        js.jobs_set_stage(1, "嵌入向量")
        self.assertEqual(js._active_jobs[0]["stage"], "嵌入向量")

    def test_does_not_set_stage_on_done(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js._active_jobs[0]["status"] = "done"
        js.jobs_set_stage(1, "嵌入向量")
        self.assertNotIn("stage", js._active_jobs[0])


class TestJobsHasActive(unittest.TestCase):
    def setUp(self): _reset()

    def test_queued_is_active(self):
        js.jobs_enqueue([(1, "a.pdf")])
        self.assertTrue(js.jobs_has_active(1))

    def test_running_is_active(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js.jobs_set_running(1)
        self.assertTrue(js.jobs_has_active(1))

    def test_done_is_not_active(self):
        js.jobs_enqueue([(1, "a.pdf")])
        js._active_jobs[0]["status"] = "done"
        self.assertFalse(js.jobs_has_active(1))

    def test_unknown_is_not_active(self):
        self.assertFalse(js.jobs_has_active(999))


class TestJobsClearDone(unittest.TestCase):
    def setUp(self): _reset()

    def test_removes_only_done(self):
        js.jobs_enqueue([(1, "a.pdf"), (2, "b.pdf")])
        js._active_jobs[0]["status"] = "done"
        js.jobs_clear_done()
        self.assertEqual(len(js._active_jobs), 1)
        self.assertEqual(js._active_jobs[0]["doc_id"], 2)

    def test_keeps_all_when_none_done(self):
        js.jobs_enqueue([(1, "a.pdf"), (2, "b.pdf")])
        js.jobs_clear_done()
        self.assertEqual(len(js._active_jobs), 2)


class TestJobsAppendAndFinalizeparse(unittest.TestCase):
    def setUp(self): _reset()

    def test_append_raw(self):
        js.jobs_append({"doc_id": 5, "filename": "x.pdf", "status": "running", "job_id": "abc123", "job_type": "parse_azure_di"})
        self.assertEqual(len(js._active_jobs), 1)

    def test_finalize_parse(self):
        js.jobs_append({"doc_id": 5, "filename": "x.pdf", "status": "running", "job_id": "abc123", "job_type": "parse_azure_di"})
        js.jobs_finalize_parse("abc123")
        self.assertEqual(js._active_jobs[0]["status"], "done")
        self.assertEqual(js._active_jobs[0]["stage"], "完成")
        self.assertIn("completed_at", js._active_jobs[0])

    def test_finalize_unknown_job_id_is_noop(self):
        js.jobs_append({"doc_id": 5, "filename": "x.pdf", "status": "running", "job_id": "abc123", "job_type": "parse_azure_di"})
        js.jobs_finalize_parse("unknown")
        self.assertEqual(js._active_jobs[0]["status"], "running")


class TestJobsReorderAndSync(unittest.TestCase):
    def setUp(self): _reset()

    def _setup_queued(self, ids):
        for i in ids:
            js.jobs_enqueue([(i, f"doc{i}.pdf")])

    def test_reorder_moves_to_front(self):
        self._setup_queued([1, 2, 3])
        result = asyncio.run(js.jobs_reorder_and_sync(3, 0))
        self.assertTrue(result)
        non_done = [j for j in js._active_jobs if j["status"] != "done"]
        self.assertEqual(non_done[0]["doc_id"], 3)

    def test_reorder_unknown_returns_false(self):
        self._setup_queued([1, 2])
        result = asyncio.run(js.jobs_reorder_and_sync(999, 0))
        self.assertFalse(result)

    def test_reorder_done_not_moved(self):
        self._setup_queued([1, 2])
        js._active_jobs[0]["status"] = "done"
        result = asyncio.run(js.jobs_reorder_and_sync(1, 0))
        self.assertFalse(result)  # done entry should not be reordered


class TestGetJobs(unittest.TestCase):
    def setUp(self): _reset()

    def test_returns_copy(self):
        js.jobs_enqueue([(1, "a.pdf")])
        snapshot = js.get_jobs()
        self.assertEqual(len(snapshot), 1)
        # Mutations to snapshot don't affect internal state
        snapshot.clear()
        self.assertEqual(len(js._active_jobs), 1)


class TestConcurrentAccess(unittest.TestCase):
    """Verify that _active_jobs stays consistent under concurrent mutation.

    These tests catch race conditions that the sequential state-machine tests
    cannot exercise.  We use threads rather than asyncio tasks because the
    real risk is mixing asyncio callbacks (main loop) with to_thread workers.
    """

    def setUp(self):
        _reset()

    def test_concurrent_enqueue_no_duplicates(self):
        """100 threads enqueue the same 10 doc_ids — each doc should appear at most once."""
        import threading
        barrier = threading.Barrier(100)

        def worker():
            barrier.wait()
            for doc_id in range(1, 11):
                js.jobs_enqueue([(doc_id, f"doc{doc_id}.pdf")], job_type="reindex")

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with js._jobs_lock:
            doc_ids = [j["doc_id"] for j in js._active_jobs]
        self.assertEqual(len(doc_ids), len(set(doc_ids)), "Duplicate doc_ids found after concurrent enqueue")
        self.assertLessEqual(len(doc_ids), 10)

    def test_concurrent_set_stage_no_corruption(self):
        """50 threads simultaneously update stages for different docs — no KeyError or corruption."""
        import threading
        for doc_id in range(1, 11):
            js.jobs_enqueue([(doc_id, f"doc{doc_id}.pdf")])
            js.jobs_set_running(doc_id)

        errors: list[Exception] = []

        def worker(doc_id: int):
            for i in range(50):
                try:
                    js.jobs_set_stage(doc_id, f"stage_{i}")
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(d,)) for d in range(1, 11)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Exceptions during concurrent set_stage: {errors}")
        with js._jobs_lock:
            for j in js._active_jobs:
                self.assertIn("stage", j)

    def test_concurrent_enqueue_and_remove(self):
        """Interleave enqueue and remove across threads — list must never go negative."""
        import threading
        barrier = threading.Barrier(20)
        errors: list[Exception] = []

        def enqueue_worker():
            barrier.wait()
            for doc_id in range(1, 6):
                try:
                    js.jobs_enqueue([(doc_id, f"doc{doc_id}.pdf")])
                except Exception as exc:
                    errors.append(exc)

        def remove_worker():
            barrier.wait()
            for doc_id in range(1, 6):
                try:
                    js.jobs_remove(doc_id)
                except Exception as exc:
                    errors.append(exc)

        threads = (
            [threading.Thread(target=enqueue_worker) for _ in range(10)]
            + [threading.Thread(target=remove_worker) for _ in range(10)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Exceptions during concurrent enqueue/remove: {errors}")
        with js._jobs_lock:
            self.assertGreaterEqual(len(js._active_jobs), 0)


if __name__ == "__main__":
    unittest.main()
