import contextlib
import io
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

module_path = Path(__file__).with_name("summarize_capture.py")
spec = importlib.util.spec_from_file_location("summarize_capture", module_path)
summarize_capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize_capture)


class SummarizeCaptureTests(unittest.TestCase):
    def test_compares_facility_counts_across_forwarders_and_collectors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generator_log = root / "generator.jsonl"
            generator_log.write_text(
                "\n".join(
                    json.dumps({
                        "run_id": "run-1",
                        "sequence": sequence,
                        "facility": "local6" if sequence % 2 else "local7",
                        "outcome": "sent",
                    })
                    for sequence in range(1, 5)
                ) + "\n",
                encoding="utf-8",
            )
            forwarder_log = root / "forwarder.log"
            forwarder_log.write_text(
                "2026-10-22T12:00:00Z local6 run_id=run-1 sequence=1\n"
                "2026-10-22T12:00:01Z local7 run_id=run-1 sequence=2\n",
                encoding="utf-8",
            )
            collector_one = root / "collector-one.log"
            collector_one.write_text(
                "2026-10-22T12:00:00Z local6 run_id=run-1 sequence=1\n"
                "2026-10-22T12:00:01Z local7 run_id=run-1 sequence=2\n",
                encoding="utf-8",
            )
            collector_two = root / "collector-two.log"
            collector_two.write_text(
                "2026-10-22T12:00:02Z local6 run_id=run-1 sequence=3\n"
                "2026-10-22T12:00:03Z local7 run_id=run-1 sequence=4\n",
                encoding="utf-8",
            )

            successes, attempted = summarize_capture.read_generator(generator_log, "run-1")
            forwarder_capture = summarize_capture.read_capture(forwarder_log, "run-1")
            collectors = {
                collector_one: summarize_capture.read_capture(collector_one, "run-1"),
                collector_two: summarize_capture.read_capture(collector_two, "run-1"),
            }
            forwarder_summary = summarize_capture.summarize_hop(
                {forwarder_log: forwarder_capture}, successes
            )
            collector_summary = summarize_capture.summarize_hop(collectors, successes)

            self.assertEqual(attempted, {"local6": 2, "local7": 2})
            self.assertEqual(forwarder_summary["rows"], 2)
            self.assertEqual(forwarder_summary["per_facility"]["local6"]["missing_from_generator_sendall"], 1)
            self.assertEqual(collector_summary["rows"], 4)
            self.assertEqual(collector_summary["unique_sequences"], 4)
            self.assertEqual(collector_summary["duplicates"], 0)

            previous_argv = sys.argv
            sys.argv = [
                "summarize_capture.py",
                "--run-id", "run-1",
                "--generator-log", str(generator_log),
                "--forwarder-log", str(forwarder_log),
                str(collector_one),
                str(collector_two),
            ]
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(summarize_capture.main(), 0)
            finally:
                sys.argv = previous_argv
            result = json.loads(output.getvalue())
            self.assertEqual(result["forwarder_ingress_combined"]["rows"], 2)
            self.assertEqual(result["collector_receipt_combined"]["rows"], 4)
            self.assertEqual(result["collectors"][str(collector_one)]["per_facility"]["local6"]["share_percent"], 50.0)


if __name__ == "__main__":
    unittest.main()
