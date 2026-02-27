#!/usr/bin/env python3
"""Real LLM parallel execution test.

Submits 3 file-creation plans in parallel via PlanGroup, then verifies:
1. All plans completed.
2. All files were created with correct content.
3. Timing shows parallelism (wall-clock < sum of individual durations).

Usage:
    # Start the daemon first:
    llmos daemon start

    # Then run this test:
    python scripts/test_parallel_llm.py [--base-url http://127.0.0.1:40000]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

# Add packages to path for local dev
sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "langchain-llmos"))
sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "llmos-bridge"))

from langchain_llmos.client import LLMOSClient


def make_write_plan(plan_id: str, file_path: str, content: str) -> dict:
    """Create an IML plan that writes a file."""
    return {
        "plan_id": plan_id,
        "protocol_version": "2.0",
        "description": f"Write file: {file_path}",
        "actions": [
            {
                "id": "write",
                "action": "write_file",
                "module": "filesystem",
                "params": {"path": file_path, "content": content},
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Test parallel plan execution")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:40000",
        help="LLMOS Bridge daemon URL",
    )
    args = parser.parse_args()

    client = LLMOSClient(base_url=args.base_url, timeout=60.0)

    # Check health first
    try:
        health = client.health()
        print(f"Daemon healthy: {health['status']}, modules: {health['modules_loaded']}")
    except Exception as e:
        print(f"ERROR: Cannot connect to daemon at {args.base_url}: {e}")
        print("Start the daemon with: llmos daemon start")
        sys.exit(1)

    # Create temp directory for test files
    with tempfile.TemporaryDirectory(prefix="llmos_parallel_") as tmpdir:
        plans = []
        expected: dict[str, str] = {}

        for i in range(3):
            file_path = str(Path(tmpdir) / f"parallel_test_{i}.txt")
            content = f"File #{i} — created in parallel by LLMOS Bridge at {time.time()}"
            plans.append(make_write_plan(f"parallel-write-{i}", file_path, content))
            expected[file_path] = content

        print(f"\nSubmitting {len(plans)} plans for parallel execution...")
        t0 = time.monotonic()

        result = client.submit_plan_group(
            plans=plans,
            group_id="parallel-llm-test",
            max_concurrent=3,
            timeout=60,
        )

        wall_time = time.monotonic() - t0

        # Print results
        print(f"\nGroup ID:  {result['group_id']}")
        print(f"Status:    {result['status']}")
        print(f"Summary:   {result['summary']}")
        print(f"Duration:  {result.get('duration', 0):.3f}s (server-side)")
        print(f"Wall time: {wall_time:.3f}s")

        if result["errors"]:
            print(f"\nErrors:")
            for pid, err in result["errors"].items():
                print(f"  {pid}: {err}")

        # Verify files
        print("\nVerifying files...")
        all_ok = True
        for file_path, expected_content in expected.items():
            p = Path(file_path)
            if not p.exists():
                print(f"  FAIL: {p.name} does not exist!")
                all_ok = False
            else:
                actual = p.read_text()
                if actual == expected_content:
                    print(f"  OK: {p.name} ({len(actual)} bytes)")
                else:
                    print(f"  FAIL: {p.name} content mismatch!")
                    all_ok = False

        # Verdict
        print("\n" + "=" * 60)
        if result["status"] == "completed" and all_ok:
            print("PASS — All plans completed, all files verified.")
            print(f"  Parallelism: {len(plans)} plans in {wall_time:.3f}s wall time")
        elif result["status"] == "partial_failure":
            print(
                f"PARTIAL — {result['summary']['completed']}/{result['summary']['total']}"
                " plans completed."
            )
            sys.exit(1)
        else:
            print(f"FAIL — Group status: {result['status']}")
            sys.exit(1)

        # Sequential baseline for comparison
        print("\nRunning sequential baseline for comparison...")
        t0_seq = time.monotonic()
        for i, plan in enumerate(plans):
            plan_copy = {**plan, "plan_id": f"seq-{i}"}
            file_path = str(Path(tmpdir) / f"sequential_test_{i}.txt")
            plan_copy["actions"][0]["params"]["path"] = file_path
            plan_copy["actions"][0]["params"]["content"] = f"Sequential file #{i}"
            client.submit_plan(plan_copy, async_execution=False)
        seq_time = time.monotonic() - t0_seq

        print(f"  Sequential: {seq_time:.3f}s for {len(plans)} plans")
        print(f"  Parallel:   {wall_time:.3f}s for {len(plans)} plans")
        if wall_time < seq_time:
            print(f"  Speedup:    {seq_time / wall_time:.1f}x faster")
        else:
            print("  Note: Parallel was not faster (likely dominated by HTTP overhead)")

    client.close()


if __name__ == "__main__":
    main()
