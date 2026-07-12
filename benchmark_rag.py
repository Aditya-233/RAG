#!/usr/bin/env python3

# HOW TO EXECUTE: sudo python3 benchmark.py
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

# Target the newly refactored single main file
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from rag import main as rag_main
except ImportError:
    print("CRITICAL ERROR: Could not import 'rag.py'. Ensure it is in the same directory.", file=sys.stderr)
    sys.exit(1)


def purge_system_page_cache():
    os_name = platform.system()
    try:
        if os_name == "Linux":
            cmd = ["sh", "-c", "sync && echo 3 > /proc/sys/vm/drop_caches"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if res.returncode != 0:
                raise RuntimeError(res.stderr.decode().strip() or "sudo/root privilege missing")
        elif os_name == "Darwin":
            cmd = ["purge"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if res.returncode != 0:
                raise RuntimeError(res.stderr.decode().strip() or "root privilege required")
    except Exception as err:
        print(err)
        print("Cold-disk benchmarking requires root privileges (sudo).", file=sys.stderr)
        sys.exit(1)


def create_synthetic_repo(repo_dir: Path, num_files: int, num_dirs: int = 10, file_size_kb: int = 64):
    for i in range(num_files):
        dir_idx = i % num_dirs
        sub_dir = repo_dir / f"folder_{dir_idx}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        file_path = sub_dir / f"file_{i}.txt"
        content = f"FILE_HEADER_{i}\n" + (f"DATA_LINE_{i}_" * (file_size_kb * 64))
        file_path.write_text(content, encoding="utf-8")


def run_cold_disk_command(cmd_args: list[str]) -> tuple[float, float]:
    purge_system_page_cache()
    tracemalloc.start()
    t0 = time.perf_counter()

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = rag_main(cmd_args)

    elapsed = time.perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if exit_code != 0:
        raise RuntimeError(f"Command {' '.join(cmd_args)} returned exit code {exit_code}")

    return elapsed, peak / (1024 * 1024)


def main():
    print("=========================================================")
    print("  R.A.G. OPTIMIZED COLD-DISK HARDWARE I/O BENCHMARK      ")
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

            t_init, mem_init = run_cold_disk_command(["init"])

            create_synthetic_repo(temp_dir, num_files=count, num_dirs=10, file_size_kb=64)

            t_add, mem_add = run_cold_disk_command(["add", "."])
            t_commit, mem_commit = run_cold_disk_command(["commit", "-m", f"Cold commit {count} files"])

            # Modify some files to test OS-stat speedup
            for i in range(min(20, count)):
                mod_file = temp_dir / f"folder_{i % 10}" / f"file_{i}.txt"
                if mod_file.exists():
                    mod_file.write_text("DIFF_LINE\n" + mod_file.read_text(), encoding="utf-8")

            t_status, mem_status = run_cold_disk_command(["status"])

            tier_metrics = {"file_count": count, "init": {"duration_ms": round(t_init * 1000, 2), "peak_mem_mb": round(mem_init, 2)}, "cold_add": {"duration_ms": round(t_add * 1000, 2), "peak_mem_mb": round(mem_add, 2)}, "cold_commit": {"duration_ms": round(t_commit * 1000, 2), "peak_mem_mb": round(mem_commit, 2)}, "cold_status": {"duration_ms": round(t_status * 1000, 2), "peak_mem_mb": round(mem_status, 2)}}
            benchmark_summary.append(tier_metrics)
            print(f"    ADD: {tier_metrics['cold_add']['duration_ms']}ms | COMMIT: {tier_metrics['cold_commit']['duration_ms']}ms | STATUS: {tier_metrics['cold_status']['duration_ms']}ms")

        finally:
            os.chdir(original_cwd)
            shutil.rmtree(temp_dir, ignore_errors=True)

    print("\n[+] Structured Cold-Disk Performance Summary:")
    print(json.dumps(benchmark_summary, indent=2))


if __name__ == "__main__":
    main()
