"""Corpus commands: collect documents, normalize them, build an index and search it.

    uv run privframe-corpus fetch configs/corpus/smoke.yaml    # collect the documents a list names
    uv run privframe-corpus parse                               # raw → normalized units
    uv run privframe-corpus index [--profile baseline-v2]       # embed and index with the profile's rag.* parts
    uv run privframe-corpus search "촬영 동의와 공표" [-k 5] [--kind prec] [--mode bm25] [--no-rerank]
    uv run privframe-corpus show pipa 제25조의2                  # original text of a document or one unit
    uv run privframe-corpus status

The tools do not know which documents belong in the corpus: that is the list file's job (configs/corpus/*.yaml).
Storage and index layout: privframe.core.corpus. Index and search parts: privframe.core.retrieval.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from privframe.core.corpus import CorpusStore
from privframe.core.lawgokr import LawGoKr
from privframe.core.parsers import parse_all
from privframe.core.pipeline import load_catalog, load_profile, resolve, service_profiles
from privframe.core.retrieval import Index, RetrievalError, RetrievalSpec, build_index, get_document
from privframe.core.selection import Halted, Rules, Selection, llm_grader, publish, report
from privframe.core.settings import ROOT, CoreSettings, secret
from privframe.core.sources import FetchContext, Outcome, fetch_all, http_client


def open_store(settings: CoreSettings) -> CorpusStore:
    return CorpusStore(settings.data_dir / "corpus")


def load_list(path: Path) -> tuple[list[dict[str, Any]], bool | None]:
    """The documents of a list file, and what the list says about indexing its new documents (`defaults.index`)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("documents")
    if not isinstance(entries, list):
        raise SystemExit(f"{path}: 최상위에 documents 목록이 있어야 합니다")
    return entries, (data.get("defaults") or {}).get("index")


def fetch(settings: CoreSettings, list_file: Path) -> list[Outcome]:
    oc = secret(settings.law_oc)
    entries, default_index = load_list(list_file)
    with http_client() as http:
        ctx = FetchContext(root=ROOT, http=http, law=LawGoKr(oc, client=http) if oc else None)
        return fetch_all(entries, open_store(settings), ctx, default_index)


def select(settings: CoreSettings, rules_file: Path, version: str, *, until: str | None = None, directory: Path | None = None, cost_cap: float | None = None) -> tuple[Selection, Path | None]:
    """Run the selection (M4-1) with the models the rules name. The run folder is named by version and rules,
    so a changed rules file starts a new run; grades and originals are reused across runs."""
    rules = Rules(rules_file)
    base = settings.data_dir / "corpus" / "selection"
    oc = secret(settings.law_oc)
    grading = rules["grading"]
    with http_client() as http:
        law = LawGoKr(oc, client=http) if oc else None
        graders = {role: llm_grader(settings, grading["slot"], grading[role]) for role in ("model", "check_model")}
        selection = Selection(rules, open_store(settings), law, directory or base / f"{version}-{rules.sha[:8]}", version=version, graders=graders, root=ROOT, grade_cache=base / "grades.jsonl", cost_cap=cost_cap)
        selection.run(until=until)
        list_file = publish(selection, ROOT / "configs" / "corpus" / "lists")[0] if until is None else None
    return selection, list_file


def spec_for(settings: CoreSettings, profile_id: str | None, overrides: dict[str, str] | None = None) -> tuple[RetrievalSpec, str, str]:
    """The retrieval settings of a profile (default: the service profile). Returns (spec, profile id, config hash)."""
    profile_id = profile_id or service_profiles(settings.pipeline_dir)["default"]
    resolved = resolve(load_catalog(settings.pipeline_dir), load_profile(settings.pipeline_dir, profile_id), overrides)
    return RetrievalSpec.from_profile(resolved), profile_id, resolved.config_hash


def open_index(settings: CoreSettings, profile_id: str | None = None, overrides: dict[str, str] | None = None) -> Index:
    spec, _, _ = spec_for(settings, profile_id, overrides)
    return build_index(open_store(settings), spec, device=settings.torch_device)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fetch", help="목록 파일의 문서를 수집")
    p.add_argument("list_file", type=Path)
    p = sub.add_parser("parse", help="원문을 단위로 분할")
    p.add_argument("doc_ids", nargs="*")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("index", help="프로필의 rag.* 부품으로 색인")
    p.add_argument("--profile")
    p = sub.add_parser("search", help="검색")
    p.add_argument("query")
    p.add_argument("--profile")
    p.add_argument("-k", type=int)
    p.add_argument("--kind", action="append")
    p.add_argument("--mode", choices=["hybrid-rrf", "dense", "bm25"])
    p.add_argument("--no-rerank", action="store_true")
    p = sub.add_parser("show", help="문서나 단위의 원문")
    p.add_argument("ref")
    p.add_argument("unit", nargs="?")
    sub.add_parser("status", help="등록부와 색인 현황")
    p = sub.add_parser("select", help="선정 규칙으로 후보를 찾아 원문을 확보하고 채택 목록을 만든다 (M4-1)")
    p.add_argument("rules_file", type=Path)
    p.add_argument("--version", default="v1")
    p.add_argument("--until", choices=Selection.STEPS, help="이 단계까지만 (목록은 만들지 않는다)")
    p.add_argument("--cost-cap", type=float, help="모델 비용 상한(USD). 기본은 규칙 파일의 값. 올려서 같은 실행을 이어 갈 수 있다")
    args = parser.parse_args(argv)
    settings = CoreSettings()
    store = open_store(settings)

    if args.command == "fetch":
        outcomes = fetch(settings, args.list_file)
        for o in outcomes:
            print(f"[{o.status:>11}] {o.entry_id}  {o.detail}")
        return 1 if any(o.status == "failed" for o in outcomes) else 0
    if args.command == "select":
        try:
            selection, list_file = select(settings, args.rules_file, args.version, until=args.until, cost_cap=args.cost_cap)
        except Halted as error:
            print(f"멈춤: {error}\n목록은 만들지 않았습니다. 확보한 원문과 채점은 남아 있으니 같은 명령으로 이어서 하면 됩니다.", file=sys.stderr)
            return 75  # EX_TEMPFAIL: the run is sound and the API has to come back, unlike a failure (1)
        summary = report(selection)
        print(json.dumps({k: summary[k] for k in ("version", "stages", "relations", "changes", "unconfirmed", "grading")}, ensure_ascii=False, indent=1))
        print(f"기록: {selection.dir}" + (f"\n목록: {list_file}" if list_file else ""))
        return 0
    if args.command == "parse":
        results = parse_all(store, args.doc_ids or None, force=args.force)
        for record, status in results:
            print(f"{record.doc_id:<32} {status}")
        return 1 if any(s.startswith("error") for _, s in results) else 0
    try:
        if args.command == "index":
            index = open_index(settings, args.profile)
            print(json.dumps(index.meta, ensure_ascii=False, indent=1))
        elif args.command == "search":
            index = open_index(settings, args.profile)
            for e in index.search(args.query, k=args.k, kinds=args.kind, mode=args.mode, rerank=not args.no_rerank):
                print(f"{e.score:>8.4f}  {e.citation} › {e.label}  [{e.doc_id} {e.unit_id}]  {e.ranks}")
                print(f"          {e.text[:160].replace(chr(10), ' ')}")
        elif args.command == "show":
            print(json.dumps(get_document(store, args.ref, args.unit), ensure_ascii=False, indent=1))
        elif args.command == "status":
            records = store.records()
            parsed = {d.record.doc_id: len(d.units) for d in store.documents()}
            print(f"저장 위치 {store.root}")
            print(f"문서 {len(records)}건 · 종류별 {dict(Counter(r.kind for r in records))} · 분할 완료 {len(parsed)}건 · 단위 {sum(parsed.values())}개")
            for r in records:
                print(f"  {r.doc_id:<32} {r.kind:<10} {r.format:<5} {r.size:>9,}B  단위 {parsed.get(r.doc_id, '—')}")
            for meta in sorted(store.index_dir.glob("*/meta.json")):
                m = json.loads(meta.read_text(encoding="utf-8"))
                print(f"색인 {m['fingerprint']} · 문서 {len(m['documents'])} · 청크 {m['chunks']} · {m['spec']['embedding_ref']} · {m['built_at']}")
    except RetrievalError as error:
        print(f"오류: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
