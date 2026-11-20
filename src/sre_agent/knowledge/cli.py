"""CLIs: `sre-ingest` (lint, chunk, embed, sync), `sre-kb-search` (query the index) and `sre-eval-retrieval` (recall@k, MRR)."""

from __future__ import annotations

import argparse
import json
import sys

from sre_agent.knowledge import KB_DIR
from sre_agent.knowledge.chunking import STRATEGIES
from sre_agent.knowledge.docs import load_dir
from sre_agent.knowledge.lint import lint_dir
from sre_agent.knowledge.store import DEFAULT_MAX_PER_DOC, DEFAULT_MODE, DEFAULT_VARIANT


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
    p.add_argument(
        "--mode", choices=["hybrid", "vector", "keyword"], default=DEFAULT_MODE
    )
    p.add_argument("--variant", choices=sorted(STRATEGIES), default=DEFAULT_VARIANT)
    p.add_argument("-k", type=int, default=5)
    p.add_argument(
        "--max-per-doc",
        type=int,
        default=DEFAULT_MAX_PER_DOC,
        help="0 = no cap",
    )
    args = p.parse_args()

    from sre_agent.knowledge.store import connect, search

    with connect() as conn:
        for h in search(
            conn,
            args.query,
            variant=args.variant,
            mode=args.mode,
            k=args.k,
            max_per_doc=args.max_per_doc or None,
        ):
            print(f"{h.score:.4f}  {h.doc_id:7} {h.heading[:30]:30}  {h.title}")


def eval_main() -> None:
    p = argparse.ArgumentParser(
        description="Retrieval eval: recall@k and MRR per chunking variant, search mode "
        "and per-document cap (0 tokens)."
    )
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--json", action="store_true", help="one JSON line per combination")
    args = p.parse_args()

    from functools import lru_cache, partial

    from sre_agent.knowledge.embed import embed_query
    from sre_agent.knowledge.retrieval_eval import evaluate, load_queries
    from sre_agent.knowledge.store import connect, search

    queries = load_queries(KB_DIR / "queries.yaml")
    cached_embed = lru_cache(maxsize=None)(embed_query)
    results = []
    with connect() as conn:
        for variant in sorted(STRATEGIES):
            for mode in ("vector", "keyword", "hybrid"):
                for cap in (None, 2, 1):
                    results.append(
                        evaluate(
                            queries,
                            partial(
                                search,
                                conn,
                                variant=variant,
                                mode=mode,
                                k=args.k,
                                max_per_doc=cap,
                                embed_query=cached_embed,
                            ),
                            variant=variant,
                            mode=mode,
                            max_per_doc=cap,
                        )
                    )
    results.sort(key=lambda r: (r.recall, r.mrr), reverse=True)
    if args.json:
        for r in results:
            print(json.dumps(r.__dict__))
        return
    styles = sorted(results[0].by_style)
    print(f"{len(queries)} queries, top-{args.k} chunks\n")
    print(
        f"{'variant':14} {'mode':8} {'cap':>3}  {'recall':>6} {'MRR':>5} {'docs':>4}  "
        + " ".join(f"{s[:6]:>6}" for s in styles)
        + "  misses"
    )
    for r in results:
        cap = "-" if r.max_per_doc is None else str(r.max_per_doc)
        print(
            f"{r.variant:14} {r.mode:8} {cap:>3}  {r.recall:6.2f} {r.mrr:5.2f} "
            f"{r.distinct_docs:4.1f}  "
            + " ".join(f"{r.by_style[s]:6.2f}" for s in styles)
            + f"  {','.join(r.misses)}"
        )
