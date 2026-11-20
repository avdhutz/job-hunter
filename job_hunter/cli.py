"""CLI: python -m job_hunter <run|export|rejected|mark-applied>"""

import argparse
import asyncio
import uuid
from collections import Counter

from job_hunter import db


def format_summary(run_id: str, result: dict, run_budget, dry_run: bool) -> str:
    """End-of-run report: per-org counts, rejections by reason, tokens, estimated cost."""
    lines = [f"run {run_id}{' (dry run, nothing saved)' if dry_run else ''}"]
    lines.append(
        f"candidates={len(result.get('candidates', []))} verified={len(result.get('verified', []))}"
        f" rejected={len(result.get('rejected', []))} scored={len(result.get('scored', []))}")
    lines.append("\nper org:")
    for s in sorted(result.get("org_stats", []), key=lambda s: s["org"]):
        detail = ", ".join(f"{k}={v}" for k, v in s.items() if k != "org")
        lines.append(f"  {s['org']}: {detail}")
    reasons = Counter(r.reason for r in result.get("rejected", []))
    if reasons:
        lines.append("\nrejected by reason: " + ", ".join(f"{k}={v}" for k, v in reasons.most_common()))
    lines.append("\ntokens:")
    for model, t in run_budget.tokens.items():
        lines.append(f"  {model}: in={t['input']:,} out={t['output']:,}")
    if run_budget.web_searches:
        lines.append(f"  web searches: {run_budget.web_searches}")
    lines.append(f"estimated cost: ${run_budget.cost_usd:.2f} (cap ${run_budget.max_cost_usd:.2f})")
    if run_budget.exceeded:
        lines.append("RUN CUT SHORT: cost cap reached, some orgs/postings were skipped")
    return "\n".join(lines)


def _run(args) -> None:
    from job_hunter import budget
    from job_hunter.graph import graph

    sectors = [args.only] if args.only else ["industry", "academic"]
    run_id = uuid.uuid4().hex[:8]
    run_budget = budget.start_run(args.max_cost)
    result = asyncio.run(graph.ainvoke(
        {"run_id": run_id, "sectors": sectors, "dry_run": args.dry_run},
        config={"run_name": f"job-hunter {run_id}", "tags": sectors},
    ))
    print(format_summary(run_id, result, run_budget, args.dry_run))
    if args.dry_run:
        for s in result.get("scored", [])[:20]:
            p = s.posting
            print(f"  {s.final_score:5.1f}  {p.company} | {p.role} | {p.link}")


def main() -> None:
    p = argparse.ArgumentParser(prog="job-hunter")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="search, verify, score and store postings")
    r.add_argument("--only", choices=["industry", "academic"])
    r.add_argument("--dry-run", action="store_true", help="don't write to the database")
    r.add_argument("--max-cost", type=float, help="override run_budget.max_cost_usd (USD)")

    sub.add_parser("export", help="write data/jobs.csv")
    sub.add_parser("rejected", help="show rejections from the latest run")
    m = sub.add_parser("mark-applied", help="set applied=1 for a job id")
    m.add_argument("job_id", type=int)

    args = p.parse_args()
    if args.cmd == "run":
        _run(args)
    elif args.cmd == "export":
        print(f"wrote {db.export_csv()}")
    elif args.cmd == "rejected":
        for row in db.latest_rejected():
            print(f"[{row['reason']}] {row['company']} | {row['role']} | {row['link']}")
    elif args.cmd == "mark-applied":
        print("ok" if db.mark_applied(args.job_id) else "no such job id")


if __name__ == "__main__":
    main()
