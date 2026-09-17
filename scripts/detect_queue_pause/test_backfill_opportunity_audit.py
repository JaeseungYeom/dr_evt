import csv
import os
import tempfile
import unittest

from backfill_opportunity_audit import (
    Job,
    recurring_families,
    replay_file,
    replay_files,
)


class BackfillAuditTests(unittest.TestCase):
    def test_detects_missed_easy_backfill(self):
        handle, path = tempfile.mkstemp(suffix="_trace.csv")
        os.close(handle)
        try:
            with open(path, "w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    ["job_submit_time", "begin_time", "end_time", "time_limit",
                     "num_nodes", "avgpcon"]
                )
                # Eight nodes are occupied. The five-node FCFS head is blocked,
                # while the later two-node job safely fits before its reservation.
                writer.writerow([-200, -100, 1000, 1100, 8, 800])
                writer.writerow([-90, 500, 600, 100, 5, 500])
                writer.writerow([-80, 400, 500, 100, 2, 200])
            result = replay_file(
                path, (0, 3600), [0], 10, 60, 3, 0.2, 0.5, 1000
            )[0]
            self.assertEqual(result["backfill_opportunity_jobs"], 1)
            self.assertEqual(result["missed_nonrecurring_backfill_jobs"], 1)
            self.assertEqual(result["missed_nonrecurring_backfill_families"], 1)
        finally:
            os.unlink(path)

    def test_recognizes_regular_family(self):
        family = (2, 600, 100)
        jobs = [
            Job(index, index * 86400, index * 86400 + 100, index * 86400 + 200,
                2, 600, family)
            for index in range(4)
        ]
        self.assertEqual(recurring_families(jobs, 3, 0.2), {family})

    def test_equal_or_larger_successful_start_discounts_miss(self):
        handle, path = tempfile.mkstemp(suffix="_trace.csv")
        os.close(handle)
        try:
            with open(path, "w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    ["job_submit_time", "begin_time", "end_time", "time_limit",
                     "num_nodes", "avgpcon"]
                )
                writer.writerow([-200, -100, 1000, 1100, 8, 800])
                writer.writerow([-90, 500, 600, 100, 5, 500])
                # This two-node backfill appears schedulable but remains queued.
                writer.writerow([-80, 400, 500, 100, 2, 200])
                # A larger job already pending at the snapshot starts promptly.
                writer.writerow([-70, 30, 40, 10, 3, 300])
            result = replay_file(
                path, (0, 3600), [0], 10, 60, 3, 0.2, 0.5, 1000
            )[0]
            self.assertGreaterEqual(result["size_controlled_backfill_jobs"], 1)
            self.assertEqual(result["missed_nonrecurring_backfill_jobs"], 0)
        finally:
            os.unlink(path)

    def test_release_delay_keeps_nodes_unavailable(self):
        handle, path = tempfile.mkstemp(suffix="_trace.csv")
        os.close(handle)
        try:
            with open(path, "w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    ["job_submit_time", "begin_time", "end_time", "time_limit",
                     "num_nodes", "avgpcon"]
                )
                writer.writerow([-200, -100, -30, 70, 8, 800])
            result = replay_file(
                path, (-100, 3600), [0], 10, 60, 3, 0.2, 0.5, 1000, 60
            )[0]
            self.assertEqual(result["observed_running_nodes"], 0)
            self.assertEqual(result["reclaiming_nodes"], 8)
            self.assertEqual(result["observed_free_nodes"], 2)
        finally:
            os.unlink(path)

    def test_combined_replay_includes_carry_in_jobs_from_other_files(self):
        work = tempfile.TemporaryDirectory()
        try:
            paths = []
            rows_by_file = (
                [(-200, -100, 1000, 1100, 8, 800)],
                [
                    (-90, 500, 600, 100, 5, 500),
                    (-80, 400, 500, 100, 2, 200),
                ],
            )
            for number, rows in enumerate(rows_by_file):
                path = os.path.join(work.name, "trace_{}.csv".format(number))
                paths.append(path)
                with open(path, "w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(
                        ["job_submit_time", "begin_time", "end_time",
                         "time_limit", "num_nodes", "avgpcon"]
                    )
                    writer.writerows(rows)

            result = replay_files(
                paths, (0, 3600), [0], 10, 60, 3, 0.2, 0.5, 1000
            )[0]
            self.assertEqual(result["observed_running_nodes"], 8)
            self.assertEqual(result["backfill_opportunity_jobs"], 1)
            self.assertEqual(result["missed_nonrecurring_backfill_jobs"], 1)
        finally:
            work.cleanup()


if __name__ == "__main__":
    unittest.main()
