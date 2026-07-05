#!/usr/bin/env python3
"""
R.A.G. Cold-Disk Hardware I/O Benchmark Harness
Forces true hardware disk reads by purging OS page cache between operations.

HOW TO EXECUTE:
  1. Execute directly with sudo:
     sudo python3 benchmarks/benchmark_rag.py
"""

import contextlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

# Resolve pathing logic for nested benchmarks/ location
SCRIPT_DIR = Path(__file__).resolve().parent
RAG_PROJECT_DIR = SCRIPT_DIR.parent
if RAG_PROJECT_DIR.exists() and str(RAG_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_PROJECT_DIR))

try:
    from src.engine import main as rag_main
except ImportError:
    print(
        f"CRITICAL ERROR: Could not import R.A.G. engine from {RAG_PROJECT_DIR}",
        file=sys.stderr,
    )
    sys.exit(1)


def purge_system_page_cache():
    """Injects system-level cache drop commands. Fails loudly if unsuccessful."""
    os_name = platform.system()
    try:
        if os_name == "Linux":
            cmd = ["sh", "-c", "sync && echo 3 > /proc/sys/vm/drop_caches"]
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
            )
            if res.returncode != 0:
                raise RuntimeError(
                    res.stderr.decode().strip() or "sudo/root privilege missing"
                )
        elif os_name == "Darwin":
            cmd = ["purge"]
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
            )
            if res.returncode != 0:
                raise RuntimeError(
                    res.stderr.decode().strip() or "root privilege required"
                )
        else:
            raise NotImplementedError(
                f"OS page cache drop not implemented for: {os_name}"
            )
    except Exception as err:
        print(
            "\n=================================================================",
            file=sys.stderr,
        )
        print(
            "CRITICAL FAILURE: PAGE CACHE DROP COULD NOT BE EXECUTED!", file=sys.stderr
        )
        print(f"Reason: {err}", file=sys.stderr)
        print(
            "Cold-disk benchmarking requires root privileges (sudo) to drop OS caches.",
            file=sys.stderr,
        )
        print(
            "=================================================================\n",
            file=sys.stderr,
        )
        sys.exit(1)


def create_synthetic_repo(
    repo_dir: Path, num_files: int, num_dirs: int = 10, file_size_kb: int = 64
):
    """Populates a synthetic file tree with unique uncompressed payloads."""
    for i in range(num_files):
        dir_idx = i % num_dirs
        sub_dir = repo_dir / f"folder_{dir_idx}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        file_path = sub_dir / f"file_{i}.txt"
        content = f"FILE_HEADER_{i}\n" + (f"DATA_LINE_{i}_" * (file_size_kb * 64))
        file_path.write_text(content, encoding="utf-8")


def run_cold_disk_command(cmd_args: list[str]) -> tuple[float, float]:
    """Purges OS cache, executes R.A.G. command, returning (duration_sec, peak_mem_mb)."""
    purge_system_page_cache()  # Enforce cold disk state

    tracemalloc.start()
    t0 = time.perf_counter()

    # Suppress internal command output (e.g. diff output, commit status) during benchmark
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = rag_main(cmd_args)

    elapsed = time.perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if exit_code != 0:
        raise RuntimeError(
            f"Command {' '.join(cmd_args)} returned exit code {exit_code}"
        )

    return elapsed, peak / (1024 * 1024)


def main():
    print("=========================================================")
    print("     R.A.G. COLD-DISK HARDWARE I/O BENCHMARK HARNESS     ")
    print("=========================================================")

    print("[*] Validating OS cache drop capabilities...")
    purge_system_page_cache()
    print("[+] System cache drop access verified.\n")

    test_tiers = [100, 500, 1000]
    benchmark_summary = []

    for count in test_tiers:
        temp_dir = Path(tempfile.mkdtemp(prefix="rag_cold_bench_"))
        original_cwd = os.getcwd()

        try:
            os.chdir(temp_dir)
            print(f"[*] Benchmarking Cold-Disk Tier: {count} files (64KB each)...")

            # 1. INIT
            t_init, mem_init = run_cold_disk_command(["init"])

            # Synthesize synthetic files on disk
            create_synthetic_repo(
                temp_dir, num_files=count, num_dirs=10, file_size_kb=64
            )

            # 2. ADD (Cold Disk Read)
            t_add, mem_add = run_cold_disk_command(["add", "."])

            # 3. COMMIT
            t_commit, mem_commit = run_cold_disk_command(
                ["commit", "-m", f"Initial cold commit {count} files"]
            )

            # 4. STATUS (Cold Disk Read & Hash Check)
            t_status, mem_status = run_cold_disk_command(["status"])

            # Modify 20 files to force diff comparison
            for i in range(min(20, count)):
                mod_file = temp_dir / f"folder_{i % 10}" / f"file_{i}.txt"
                if mod_file.exists():
                    mod_file.write_text(
                        "COLD_DIFF_LINE_INJECTED\n" + mod_file.read_text(),
                        encoding="utf-8",
                    )

            # 5. DIFF (Cold Disk Read & Diff Calculation)
            t_diff, mem_diff = run_cold_disk_command(["diff"])

            tier_metrics = {
                "file_count": count,
                "init": {
                    "duration_ms": round(t_init * 1000, 2),
                    "peak_mem_mb": round(mem_init, 2),
                },
                "cold_add": {
                    "duration_ms": round(t_add * 1000, 2),
                    "peak_mem_mb": round(mem_add, 2),
                },
                "cold_commit": {
                    "duration_ms": round(t_commit * 1000, 2),
                    "peak_mem_mb": round(mem_commit, 2),
                },
                "cold_status": {
                    "duration_ms": round(t_status * 1000, 2),
                    "peak_mem_mb": round(mem_status, 2),
                },
                "cold_diff": {
                    "duration_ms": round(t_diff * 1000, 2),
                    "peak_mem_mb": round(mem_diff, 2),
                },
            }
            benchmark_summary.append(tier_metrics)
            print(
                f"    COLD ADD: {tier_metrics['cold_add']['duration_ms']}ms | COLD COMMIT: {tier_metrics['cold_commit']['duration_ms']}ms | COLD STATUS: {tier_metrics['cold_status']['duration_ms']}ms | COLD DIFF: {tier_metrics['cold_diff']['duration_ms']}ms"
            )

        finally:
            os.chdir(original_cwd)
            shutil.rmtree(temp_dir, ignore_errors=True)

    print("\n[+] Structured Cold-Disk Performance Summary:")
    print(json.dumps(benchmark_summary, indent=2))


if __name__ == "__main__":
    main()
