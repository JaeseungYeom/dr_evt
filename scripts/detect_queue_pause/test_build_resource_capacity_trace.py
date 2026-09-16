import unittest

from build_resource_capacity_trace import build_rows


class ResourceCapacityTraceTests(unittest.TestCase):
    def test_capacity_scenarios(self):
        timeline = [
            {"start": str(hour * 3600), "end": str((hour + 1) * 3600)}
            for hour in range(4)
        ]
        periods = [
            {
                "start": 3600, "end": 7200, "state": "reduced_capacity",
                "potential_effective_capacity_lower_nodes": 60,
            },
            {
                "start": 7200, "end": 10800,
                "state": "queue_pause_or_maintenance",
                "potential_effective_capacity_lower_nodes": 20,
            },
        ]
        with_reduced = build_rows(timeline, periods, 100, True, "JST")
        without_reduced = build_rows(timeline, periods, 100, False, "JST")
        self.assertEqual(
            [row["normal_queue_capacity_nodes"] for row in with_reduced],
            [100, 60, 0, 100],
        )
        self.assertEqual(
            [row["normal_queue_capacity_nodes"] for row in without_reduced],
            [100, 100, 0, 100],
        )
        self.assertEqual(with_reduced[0]["timezone"], "JST")
        self.assertIn("start_local", with_reduced[0])
        self.assertIn("end_local", with_reduced[0])


if __name__ == "__main__":
    unittest.main()
