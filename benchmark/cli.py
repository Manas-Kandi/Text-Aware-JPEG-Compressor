from __future__ import annotations

import argparse
import json

import server
from benchmark.analysis import analyze


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="JPEG-versus-text context benchmark")
    commands = root.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="start a benchmark and wait for completion")
    start.add_argument("--model", default=None, help="pinned vision model; omitted = the app picks a free one")
    start.add_argument("--lengths", nargs="+", type=int, default=[16, 32, 64, 128])
    start.add_argument("--seeds", nargs="+", type=int, default=[1103, 2207, 3301, 4409, 5519])
    start.add_argument("--skip-closed-loop", action="store_true", help="run only the primary paired comparison")
    resume = commands.add_parser("resume", help="resume an interrupted benchmark")
    resume.add_argument("run_id")
    status = commands.add_parser("status", help="show run status")
    status.add_argument("run_id", nargs="?")
    regenerate = commands.add_parser("analyze", help="regenerate summary files and charts")
    regenerate.add_argument("run_id")
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "start":
        run = server.submit_research_benchmark({
            "model": args.model or server.default_benchmark_model(),
            "lengths": sorted(set(args.lengths)),
            "seeds": list(dict.fromkeys(args.seeds)),
            "closed_loop": not args.skip_closed_loop,
        })
        with server.BENCHMARK_FUTURES_LOCK:
            future = server.BENCHMARK_FUTURES[run["id"]]
        future.result()
        print(json.dumps(server.get_research_benchmark(run["id"]), indent=2))
    elif args.command == "resume":
        run = server.resume_research_benchmark(args.run_id)
        if run["status"] != "complete":
            with server.BENCHMARK_FUTURES_LOCK:
                future = server.BENCHMARK_FUTURES[args.run_id]
            future.result()
        print(json.dumps(server.get_research_benchmark(args.run_id), indent=2))
    elif args.command == "status":
        result = server.get_research_benchmark(args.run_id) if args.run_id else {"runs": server.list_research_benchmarks()}
        if result is None:
            raise SystemExit("Run not found")
        print(json.dumps(result, indent=2))
    else:
        run = server.get_research_benchmark(args.run_id)
        if not run:
            raise SystemExit("Run not found")
        observations = server.rows("SELECT * FROM benchmark_v2_observations WHERE run_id=?", (args.run_id,))
        summary = analyze(observations, server.BENCHMARK_RUNS / args.run_id)
        server.execute("UPDATE benchmark_v2_runs SET summary=?,updated_at=? WHERE id=?", (json.dumps(summary), server.now_iso(), args.run_id))
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
