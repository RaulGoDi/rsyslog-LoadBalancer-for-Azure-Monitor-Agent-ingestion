#!/usr/bin/env python3
"""Compare generator records with per-collector rsyslog capture logs."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

EVENT_PATTERN = re.compile(r"run_id=([A-Za-z0-9-]+).*?sequence=(\d+)")


def read_generator(path: Path, run_id: str) -> tuple[set[int], int]:
    successful = set()
    attempted = 0
    with path.open(encoding="utf-8") as records:
        for line in records:
            item = json.loads(line)
            if item.get("run_id") == run_id:
                attempted += 1
                if item.get("outcome") == "sent":
                    successful.add(int(item["sequence"]))
    return successful, attempted


def read_capture(path: Path, run_id: str) -> tuple[Counter[int], list[int]]:
    sequences: Counter[int] = Counter()
    ordered_sequences = []
    with path.open(encoding="utf-8", errors="replace") as capture:
        for line in capture:
            match = EVENT_PATTERN.search(line)
            if match and match.group(1) == run_id:
                sequence = int(match.group(2))
                sequences[sequence] += 1
                ordered_sequences.append(sequence)
    return sequences, ordered_sequences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generator-log", type=Path, required=True)
    parser.add_argument("collector_logs", nargs="+", type=Path)
    args = parser.parse_args()

    generator_successes, attempted = read_generator(args.generator_log, args.run_id)
    captures = {path: read_capture(path, args.run_id) for path in args.collector_logs}
    combined: Counter[int] = Counter()
    for counts, _ in captures.values():
        combined.update(counts)

    duplicate_rows = sum(count - 1 for count in combined.values() if count > 1)
    received_sequences = set(combined)
    missing_sequences = generator_successes - received_sequences
    unexpected_sequences = received_sequences - generator_successes
    total_rows = sum(combined.values())
    print(json.dumps({
        "run_id": args.run_id,
        "generator_attempted": attempted,
        "generator_sendall_succeeded": len(generator_successes),
        "collector_rows": total_rows,
        "collector_unique_sequences": len(received_sequences),
        "duplicates": duplicate_rows,
        "not_seen_at_collectors": len(missing_sequences),
        "collector_sequences_not_in_generator_success_log": len(unexpected_sequences),
        "collectors": {
            str(path): {
                "rows": sum(counts.values()),
                "unique_sequences": len(counts),
                "duplicate_rows": sum(count - 1 for count in counts.values() if count > 1),
                "share_percent": round(100 * sum(counts.values()) / total_rows, 2) if total_rows else 0,
                "out_of_order_transitions": sum(
                    current < previous for previous, current in zip(ordered, ordered[1:])
                ),
            }
            for path, (counts, ordered) in captures.items()
        },
        "caveat": "TCP sendall success is not proof of delivery or Log Analytics ingestion.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
