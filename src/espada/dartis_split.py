"""Plan acquisition-isolated development partitions from cached metadata only."""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .dartis import parse_dartis_metadata, _file_sha256


def group_tokens(record):
    day = record.start_time[:10]
    date.fromisoformat(day)  # Missing acquisition metadata must fail closed.
    tokens = {"day:" + day}
    for scene in record.sentinel_id.split(";"):
        scene = scene.strip()
        if scene:
            tokens.add("scene:" + scene)
            # Include every constituent acquisition date for mosaicked patches.
            for stamp in re.findall(r"_(\d{8})T\d{6}", scene):
                tokens.add("day:" + date.fromisoformat(stamp).isoformat())
    return tokens


def plan_partitions(records, reviewed_names, seed=2614307):
    records = sorted(records, key=lambda row: row.jpg_file)
    names = [row.jpg_file for row in records]
    if len(set(names)) != len(names) or not reviewed_names or not set(reviewed_names) <= set(names):
        raise ValueError("Duplicate metadata names, empty audit, or unknown reviewed images")
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owners = {}
    for i, row in enumerate(records):
        for token in sorted(group_tokens(row)):
            if token in owners:
                parent[find(i)] = find(owners[token])
            else:
                owners[token] = i
    groups = defaultdict(list)
    for i, row in enumerate(records):
        groups[find(i)].append(row)
    output = []
    for rows in groups.values():
        identity = "\n".join(row.jpg_file for row in rows)
        group_id = hashlib.sha256(identity.encode()).hexdigest()
        value = int(hashlib.sha256(f"{seed}:{group_id}".encode()).hexdigest()[:8], 16) / 2**32
        if any(row.jpg_file in reviewed_names for row in rows):
            partition = "reviewed_development"
        else:
            partition = "train" if value < .70 else "validation" if value < .85 else "reserved_test"
        for row in rows:
            output.append({**asdict(row), "partition": partition, "group_id": group_id})
    return sorted(output, key=lambda row: row["jpg_file"])


def prepare(metadata, audit, destination):
    records = parse_dartis_metadata(metadata.read_text(encoding="utf-8"))
    with audit.open(encoding="utf-8-sig", newline="") as stream:
        reviewed = {row["jpg_file"] for row in csv.DictReader(stream)}
    rows = plan_partitions(records, reviewed)
    summary = {}
    for partition in ("reviewed_development", "train", "validation", "reserved_test"):
        selected = [row for row in rows if row["partition"] == partition]
        summary[partition] = {"images": len(selected),
                              "groups": len({row["group_id"] for row in selected}),
                              "subsets": dict(sorted(Counter(row["subset"] for row in selected).items()))}
    adequate = all(all(summary[p]["subsets"].get(s, 0) > 0 for s in ("ow", "oc", "nw", "nc"))
                   for p in ("train", "validation", "reserved_test"))
    report = {
        "status": "PLAN_READY" if adequate else "INSUFFICIENT_COVERAGE",
        "seed": 2614307, "metadata_sha256": _file_sha256(metadata),
        "reviewed_manifest_sha256": _file_sha256(audit), "partitions": summary,
        "policy": "Connected components sharing any source scene or UTC acquisition date; reviewed components excluded from new validation/test. Deterministic 70/15/15 group assignment.",
        "limitations": [
            "Metadata-only reservation; no new image downloads, training or inference.",
            "DARTIS has object boxes, not pixel masks. Do not fill boxes as segmentation ground truth.",
            "Exact and perceptual duplicate checks across partitions are required after download.",
            "Different dates may still show the same persistent event or location; event/geographic isolation needs a further audit.",
            "POSEatSea's prior training overlap is unknown; this reservation cannot prove an independent blind test for that checkpoint.",
            "Do not change the seed or allocation in response to model scores.",
        ],
    }
    # An existing plan must never be silently changed by a metadata/seed revision.
    payload = json.dumps({"report": report, "rows": rows}, indent=2) + "\n"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "partition_plan.json"
    if target.exists() and target.read_text(encoding="utf-8") != payload:
        raise ValueError("Existing plan differs; use a new explicit output directory for review")
    target.write_text(payload, encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.metadata, args.audit, args.out)
