"""Command-line entry point. Run `python -m chronicle --help`."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
import time

from .demo import demo_snapshots
from .engine import KnowledgeSystem
from .io import load_snapshots


def main() -> int:
    parser = argparse.ArgumentParser(description="Chronicle — inspect what the evidence supported at a point in time.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--snapshots", help="Local participant ZIP, kit directory, or snapshot directory")
    source.add_argument("--demo", action="store_true", help="Use the included fictional credit union")
    parser.add_argument("--database", default=":memory:", help="Optional private SQLite database to create or reopen")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="Answer a question with an inspectable evidence trace")
    ask.add_argument("question")
    ask.add_argument("--as-of")
    commands.add_parser("conflicts", help="List active disagreements and historical changes")
    commands.add_parser("snapshots", help="List the available observations")
    changes = commands.add_parser("changes", help="Inspect changes introduced by a snapshot")
    changes.add_argument("snapshot_id", nargs="?")
    serve = commands.add_parser("serve", help="Open the local evidence inspector")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ks = KnowledgeSystem(args.database)
    try:
        start = time.perf_counter()
        snapshots = demo_snapshots() if args.demo else load_snapshots(args.snapshots) if args.snapshots else []
        for snapshot in snapshots:
            ks.ingest(snapshot)
        if not ks.store.snapshots():
            parser.error("Choose --demo, --snapshots PATH, or an existing --database.")
        elapsed = time.perf_counter() - start
        if args.command == "serve":
            from .server import serve
            serve(ks, args.port, args.demo, elapsed)
            return 0
        if args.command == "ask":
            result = ks.explain(args.question, args.as_of).to_dict()
        elif args.command == "conflicts":
            result = [asdict(c) for c in ks.conflicts()]
        elif args.command == "snapshots":
            result = ks.store.snapshots()
        else:
            result = ks.store.changes(args.snapshot_id or ks.store.at(None)["id"])
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        ks.close()


if __name__ == "__main__":
    raise SystemExit(main())
