"""CLI: python -m job_hunter <run|export|rejected|mark-applied>"""

import argparse
import asyncio
import uuid

from job_hunter import db


def _run(args) -> None:
    from job_hunter.graph import graph

    sectors = [args.only] if args.only else ["industry", "academic"]
    run_id = uuid.uuid4().hex[:8]
    result = asyncio.run(graph.ainvoke(
        {"run_id": run_id, "sectors": sectors, "dry_run": args.dry_run},
        config={"run_name": f"job-hunter {run_id}", "tags": sectors},
    ))
    print(
        f"run {run_id}: candidates={len(result.get('candidates', []))} "
        f"verified={len(result.get('verified', []))} rejected={len(result.get('rejected', []))} "
        f"scored={len(result.get('scored', []))}{' (dry run, nothing saved)' if args.dry_run else ''}"
    )


def main() -> None:
    p = argparse.ArgumentParser(prog="job-hunter")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="search, verify, score and store postings")
    r.add_argument("--only", choices=["industry", "academic"])
    r.add_argument("--dry-run", action="store_true", help="don't write to the database")

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
