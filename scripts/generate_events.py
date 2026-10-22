#!/usr/bin/env python3
"""Generate identifiable Syslog and CEF events over one persistent TCP connection."""

import argparse
import datetime as dt
import json
import os
import socket
import time
import uuid
from collections import Counter
from pathlib import Path


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_event(run_id: str, sequence: int, event_format: str, timestamp: str, hostname: str) -> bytes:
    if event_format == "syslog":
        facility = 22
        priority = facility * 8 + 6
        message = (
            f"<{priority}>1 {timestamp} {hostname} rsyslog-lab-generator - - - "
            f"run_id={run_id} sequence={sequence} generated_at={timestamp}"
        )
    elif event_format == "cef":
        facility = 23
        epoch_ms = int(dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
        message = (
            f"<{facility * 8 + 6}>1 {timestamp} {hostname} rsyslog-lab-generator - - - "
            f"CEF:0|rsyslog-lab|event-generator|1.0|LOAD-TEST|Distribution test|3|"
            f"run_id={run_id} sequence={sequence} rt={epoch_ms} dvc={hostname}"
        )
    else:
        raise ValueError(f"Unsupported format: {event_format}")
    return message.encode("utf-8")


def frame_event(payload: bytes) -> bytes:
    return str(len(payload)).encode("ascii") + b" " + payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Internal Load Balancer address or DNS name.")
    parser.add_argument("--port", type=int, default=514)
    parser.add_argument("--rate", type=float, default=100.0, help="Aggregate event rate per second.")
    parser.add_argument("--duration", type=float, default=600.0, help="Run duration in seconds.")
    parser.add_argument("--format", choices=("both", "syslog", "cef"), default="both")
    parser.add_argument("--run-id", default=uuid.uuid4().hex)
    parser.add_argument("--output-dir", default="/var/lib/rsyslog-lab/results")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rate <= 0 or args.duration <= 0:
        raise SystemExit("--rate and --duration must be positive.")
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    record_path = output_dir / f"{args.run_id}.jsonl"
    hostname = socket.gethostname().replace(" ", "-")
    count = int(round(args.rate * args.duration))
    formats = ("syslog", "cef")
    connection = None
    connected_count = 0
    failed_count = 0
    attempted_by_facility: Counter[str] = Counter()
    sent_by_facility: Counter[str] = Counter()
    started = time.monotonic()

    with record_path.open("w", encoding="utf-8", buffering=1) as records:
        for sequence in range(1, count + 1):
            scheduled = started + (sequence - 1) / args.rate
            delay = scheduled - time.monotonic()
            if delay > 0:
                time.sleep(delay)

            timestamp = utc_now()
            event_format = (
                formats[(sequence - 1) % len(formats)] if args.format == "both" else args.format
            )
            facility = "local6" if event_format == "syslog" else "local7"
            attempted_by_facility[facility] += 1
            payload = build_event(args.run_id, sequence, event_format, timestamp, hostname)
            outcome = "failed"
            error = None

            try:
                if connection is None:
                    connection = socket.create_connection((args.target, args.port), timeout=5)
                    connection.settimeout(5)
                connection.sendall(frame_event(payload))
                outcome = "sent"
                connected_count += 1
                sent_by_facility[facility] += 1
            except OSError as exc:
                error = str(exc)
                failed_count += 1
                if connection is not None:
                    connection.close()
                    connection = None

            records.write(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "sequence": sequence,
                        "generated_at": timestamp,
                        "format": event_format,
                        "facility": facility,
                        "outcome": outcome,
                        "error": error,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

    if connection is not None:
        connection.close()
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "attempted": count,
                "sendall_succeeded": connected_count,
                "send_errors": failed_count,
                "attempted_by_facility": dict(attempted_by_facility),
                "sendall_succeeded_by_facility": dict(sent_by_facility),
                "sender_log": str(record_path),
                "note": "sendall success is not confirmation of forwarder, collector, or Log Analytics receipt",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
