"""CLIs: `sre-ingest` (lint, chunk, embed, sync) and `sre-kb-search` (query the index)."""

from __future__ import annotations

import argparse
import json
import sys

from sre_agent.knowledge import KB_DIR
from sre_agent.knowledge.chunking import STRATEGIES
from sre_agent.knowledge.docs import load_dir
from sre_agent.knowledge.lint import lint_dir


def ingest_main() -> None:
    p = argparse.ArgumentParser(
        description="Index the lab knowledge base into pgvector (incremental)."
    )
    p.add_argument(
        "--variant", nargs="+", choices=sorted(STRATEGIES), default=sorted(STRATEGIES)
    )
    args = p.parse_args()

    failed = {k: v for k, v in lint_dir(KB_DIR).items() if v}
    if failed:  # layer 0 gate: never index a document that fails lint (D45)
        for path, errs in failed.items():
            print(f"{path}: {'; '.join(errs)}", file=sys.stderr)
        sys.exit("lint failed; nothing indexed (run sre-doc-lint)")

    from sre_agent.knowledge.embed import embed_documents
    from sre_agent.knowledge.store import connect, init_schema, sync

    docs = load_dir(KB_DIR)
    with connect() as conn:
        init_schema(conn)
        for variant in args.variant:
            chunks = [c for d in docs for c in STRATEGIES[variant](d)]
            stats = sync(conn, variant, chunks, embed_documents)
            print(
                json.dumps(
                    {
                        "variant": variant,
                        "docs": len(docs),
                        "chunks": len(chunks),
                        **stats,
                    }
                )
            )


def search_main() -> None:
    p = argparse.ArgumentParser(description="Search the knowledge base.")
    p.add_argument("query")
    p.add_argument("--mode", choices=["hybrid", "vector", "keyword"], default="hybrid")
    p.add_argument("--variant", choices=sorted(STRATEGIES), default="section")
    p.add_argument("-k", type=int, default=5)
    args = p.parse_args()

    from sre_agent.knowledge.store import connect, search

    with connect() as conn:
        for h in search(
            conn, args.query, variant=args.variant, mode=args.mode, k=args.k
        ):
            print(f"{h.score:.4f}  {h.doc_id:7} {h.heading[:30]:30}  {h.title}")
