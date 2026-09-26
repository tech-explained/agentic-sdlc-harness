"""harness CLI: validate-fixtures, demo, run-eval, replay, verify-results."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURES = REPO_ROOT / "fixtures"
DEFAULT_RESULTS = REPO_ROOT / "results"


def _ensure_importable() -> None:
    src = REPO_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def cmd_validate_fixtures(args: argparse.Namespace) -> int:
    _ensure_importable()
    from harness.cli.validate import validate_fixtures

    ok = validate_fixtures(Path(args.fixtures))
    return 0 if ok else 1


def cmd_demo(args: argparse.Namespace) -> int:
    _ensure_importable()
    from harness.agents.stub import StubAgent
    from harness.evals.driver import Driver, DriverConfig, Scenario
    from harness.orchestrator.checkpoints import ScriptedCheckpoint

    fixtures = Path(args.fixtures)
    results = Path(args.results)
    task_id = args.task
    config = DriverConfig(
        fixtures_root=fixtures,
        results_dir=results / "demo",
        checkpoint=ScriptedCheckpoint({}),
    )
    driver = Driver(config, StubAgent(fixtures))
    row = driver.run(Scenario(task_id=task_id, scenario="clean"))
    print(json.dumps(row, indent=2, default=str))
    print(f"\noutcome={row['outcome']} retries={row['retries']} "
          f"log={row['decision_log_path']}")
    return 0


def cmd_run_eval(args: argparse.Namespace) -> int:
    _ensure_importable()
    from harness.cli.eval import run_measured_evaluation

    t0 = time.monotonic()
    summary = run_measured_evaluation(
        fixtures_root=Path(args.fixtures),
        results_dir=Path(args.results),
    )
    dt = time.monotonic() - t0
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nevaluation complete in {dt:.1f}s")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    _ensure_importable()
    from harness.evals.replay import replay_run

    if args.log:
        logs = [Path(args.log)]
    else:
        logs = sorted((Path(args.results) / "runs").glob("*/decisions.jsonl"))
    failures = 0
    for log in logs:
        report = replay_run(log)
        status = "OK" if not report.errors and report.row_matches else "FAIL"
        if status == "FAIL":
            failures += 1
        print(f"[{status}] {log}: records={report.records} "
              f"accepted={report.accepted} reproduced={report.decisions_reproduced} "
              f"terminal={report.terminal_state}")
        for err in report.errors:
            print(f"    error: {err}")
        for mm in report.decision_mismatches:
            print(f"    mismatch: {mm}")
    return 1 if failures else 0


def cmd_verify_results(args: argparse.Namespace) -> int:
    _ensure_importable()
    from harness.cli.eval import verify_results

    ok = verify_results(Path(args.results))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Deterministic control layer for agentic software delivery (research prototype).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-fixtures", help="validate task fixtures and fault manifests")
    p.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    p.set_defaults(func=cmd_validate_fixtures)

    p = sub.add_parser("demo", help="run one clean scenario end-to-end")
    p.add_argument("--task", default="ll-discount")
    p.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    p.add_argument("--results", default=str(DEFAULT_RESULTS))
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("run-eval", help="run the full measured stub-agent evaluation")
    p.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    p.add_argument("--results", default=str(DEFAULT_RESULTS))
    p.set_defaults(func=cmd_run_eval)

    p = sub.add_parser("replay", help="replay decision logs and verify integrity")
    p.add_argument("--log", default=None, help="single decisions.jsonl to replay")
    p.add_argument("--results", default=str(DEFAULT_RESULTS))
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("verify-results", help="verify results/ against decision logs")
    p.add_argument("--results", default=str(DEFAULT_RESULTS))
    p.set_defaults(func=cmd_verify_results)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
