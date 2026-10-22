#!/usr/bin/env python3
"""Compare generator records with per-collector rsyslog capture logs."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

EVENT_PATTERN = re.compile(r"run_id=([^\s]+).*?sequence=(\d+)")
FACILITIES = ("local6", "local7")


def read_generator(path: Path, run_id: str) -> tuple[dict[str, set[int]], Counter[str]]:
    attempted: Counter[str] = Counter()
    successful: dict[str, set[int]] = {facility: set() for facility in FACILITIES}
    with path.open(encoding="utf-8") as records:
        for line in records:
            item = json.loads(line)
            if item.get("run_id") == run_id:
                facility = item.get("facility")
                if facility not in FACILITIES:
                    facility = "unknown"
                attempted[facility] += 1
                if item.get("outcome") == "sent":
                    successful.setdefault(facility, set()).add(int(item["sequence"]))
    return successful, attempted


def read_capture(path: Path, run_id: str) -> tuple[Counter[int], dict[str, Counter[int]], list[int]]:
    sequences: Counter[int] = Counter()
    sequences_by_facility: dict[str, Counter[int]] = {
        facility: Counter() for facility in FACILITIES
    }
    ordered_sequences: list[int] = []
    with path.open(encoding="utf-8", errors="replace") as capture:
        for line in capture:
            match = EVENT_PATTERN.search(line)
            if match and match.group(1) == run_id:
                sequence = int(match.group(2))
                fields = line.split(maxsplit=2)
                facility = fields[1] if len(fields) > 1 else "unknown"
                if facility not in FACILITIES:
                    facility = "unknown"
                sequences[sequence] += 1
                sequences_by_facility.setdefault(facility, Counter())[sequence] += 1
                ordered_sequences.append(sequence)
    return sequences, sequences_by_facility, ordered_sequences


def summarize_hop(
    captures: dict[Path, tuple[Counter[int], dict[str, Counter[int]], list[int]]],
    generator_successes: dict[str, set[int]],
) -> dict:
    combined: Counter[int] = Counter()
    combined_by_facility = {facility: Counter() for facility in FACILITIES}
    for counts, facility_counts, _ in captures.values():
        combined.update(counts)
        for facility, sequences in facility_counts.items():
            combined_by_facility.setdefault(facility, Counter()).update(sequences)

    total_rows = sum(combined.values())
    per_facility = {}
    for facility in sorted(set(generator_successes) | set(combined_by_facility)):
        counts = combined_by_facility.get(facility, Counter())
        observed = set(counts)
        expected = generator_successes.get(facility, set())
        per_facility[facility] = {
            "rows": sum(counts.values()),
            "unique_sequences": len(observed),
            "duplicates": sum(count - 1 for count in counts.values() if count > 1),
            "missing_from_generator_sendall": len(expected - observed),
            "not_in_generator_sendall": len(observed - expected),
        }
    return {
        "rows": total_rows,
        "unique_sequences": len(combined),
        "duplicates": sum(count - 1 for count in combined.values() if count > 1),
        "per_facility": per_facility,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generator-log", type=Path, required=True)
    parser.add_argument(
        "--forwarder-log",
        type=Path,
        action="append",
        default=[],
        help="Forwarder ingress capture log; repeat for each forwarder.",
    )
    parser.add_argument("collector_logs", nargs="+", type=Path)
    args = parser.parse_args()

    generator_successes, attempted_by_facility = read_generator(args.generator_log, args.run_id)
    collector_captures = {
        path: read_capture(path, args.run_id) for path in args.collector_logs
    }
    forwarder_captures = {
        path: read_capture(path, args.run_id) for path in args.forwarder_log
    }
    total_collector_rows = sum(sum(counts.values()) for counts, _, _ in collector_captures.values())
    collector_facility_totals: Counter[str] = Counter()
    for _, facility_counts, _ in collector_captures.values():
        for facility, counts in facility_counts.items():
            collector_facility_totals[facility] += sum(counts.values())
    print(json.dumps({
        "run_id": args.run_id,
        "generator": {
            "attempted": sum(attempted_by_facility.values()),
            "attempted_by_facility": dict(attempted_by_facility),
            "sendall_succeeded": sum(len(sequences) for sequences in generator_successes.values()),
            "sendall_succeeded_by_facility": {
                facility: len(sequences) for facility, sequences in generator_successes.items()
            },
        },
        "forwarder_ingress": {
            str(path): summarize_hop({path: capture}, generator_successes)
            for path, capture in forwarder_captures.items()
        },
        "forwarder_ingress_combined": summarize_hop(forwarder_captures, generator_successes),
        "collector_receipt_combined": summarize_hop(collector_captures, generator_successes),
        "collector_rows": total_collector_rows,
        "collectors": {
            str(path): {
                "rows": sum(counts.values()),
                "unique_sequences": len(counts),
                "duplicate_rows": sum(count - 1 for count in counts.values() if count > 1),
                "share_percent": round(100 * sum(counts.values()) / total_collector_rows, 2)
                if total_collector_rows else 0,
                "per_facility": {
                    facility: {
                        "rows": sum(sequences.values()),
                        "unique_sequences": len(sequences),
                        "duplicates": sum(count - 1 for count in sequences.values() if count > 1),
                        "share_percent": round(
                            100 * sum(sequences.values()) / collector_facility_totals[facility], 2
                        ) if collector_facility_totals[facility] else 0,
                    }
                    for facility, sequences in facility_counts.items()
                },
                "out_of_order_transitions": sum(
                    current < previous for previous, current in zip(ordered, ordered[1:])
                ),
            }
            for path, (counts, facility_counts, ordered) in collector_captures.items()
        },
        "caveat": "Forwarder logs record local rsyslog ingress; collector logs record collector-side rsyslog receipt. Neither proves AMA or Log Analytics ingestion.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
