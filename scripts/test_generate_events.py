import contextlib
import io
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

module_path = Path(__file__).with_name("generate_events.py")
spec = importlib.util.spec_from_file_location("generate_events", module_path)
generate_events = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate_events)


class GenerateEventsTests(unittest.TestCase):
    def test_syslog_uses_local6_and_contains_reconciliation_fields(self):
        payload = generate_events.build_event(
            "run-123", 17, "syslog", "2026-10-22T12:00:00.000Z", "generator"
        )
        self.assertTrue(payload.startswith(b"<182>1 "))
        self.assertIn(b"run_id=run-123", payload)
        self.assertIn(b"sequence=17", payload)

    def test_cef_uses_local7_and_contains_reconciliation_fields(self):
        payload = generate_events.build_event(
            "run-123", 18, "cef", "2026-10-22T12:00:00.000Z", "generator"
        )
        self.assertTrue(payload.startswith(b"<190>1 "))
        self.assertIn(b"CEF:0|", payload)
        self.assertIn(b"run_id=run-123", payload)
        self.assertIn(b"sequence=18", payload)

    def test_octet_counted_framing_prefix_is_utf8_byte_length(self):
        payload = "event-ä".encode("utf-8")
        frame = generate_events.frame_event(payload)
        length, body = frame.split(b" ", 1)
        self.assertEqual(int(length), len(body))
        self.assertEqual(body, payload)

    def test_run_sends_events_over_one_persistent_connection(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        received = []
        connection_count = []

        def receive():
            connection, _ = listener.accept()
            connection_count.append(1)
            with connection:
                buffer = b""
                while True:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    while b" " in buffer:
                        separator = buffer.index(b" ")
                        length = int(buffer[:separator])
                        frame_end = separator + 1 + length
                        if len(buffer) < frame_end:
                            break
                        received.append(buffer[separator + 1:frame_end])
                        buffer = buffer[frame_end:]
                self.assertEqual(buffer, b"")
            listener.close()

        server = threading.Thread(target=receive, daemon=True)
        server.start()
        previous_argv = sys.argv
        with tempfile.TemporaryDirectory() as output_dir:
            sys.argv = [
                "generate_events.py",
                "--target", "127.0.0.1",
                "--port", str(listener.getsockname()[1]),
                "--rate", "10",
                "--duration", "0.3",
                "--run-id", "persistent-run",
                "--output-dir", output_dir,
            ]
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(generate_events.main(), 0)
            finally:
                sys.argv = previous_argv
            server.join(timeout=5)
            self.assertFalse(server.is_alive())
            self.assertEqual(len(connection_count), 1)
            self.assertEqual(len(received), 3)
            self.assertTrue(all(b"run_id=persistent-run" in event for event in received))
            self.assertEqual([event.startswith(b"<182>") for event in received], [True, False, True])
            result = json.loads(output.getvalue())
            self.assertEqual(result["sendall_succeeded"], 3)
            self.assertEqual(result["attempted_by_facility"], {"local6": 2, "local7": 1})
            self.assertEqual(result["sendall_succeeded_by_facility"], {"local6": 2, "local7": 1})


if __name__ == "__main__":
    unittest.main()
