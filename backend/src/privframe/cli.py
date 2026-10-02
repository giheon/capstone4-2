"""CLI uses the same HTTP use cases as the UI; it never opens worker Chroma."""
import argparse
import json
import time
from pathlib import Path

import httpx


def wait_record(client: httpx.Client, kind: str, record_id: str, timeout: float = 1200) -> dict:
    deadline = time.monotonic() + timeout
    active = {"UPLOADED", "QUEUED", "INDEXING", "ANALYZING", "DECIDING", "RENDERING"}
    while time.monotonic() < deadline:
        result = client.get(f"/api/{kind}/{record_id}")
        result.raise_for_status()
        record = result.json()
        if record["status"] not in active:
            return record
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {kind} {record_id}")


def main():
    parser = argparse.ArgumentParser(description="PrivFrame API client and development tools")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export-openapi")
    export.add_argument("--output", type=Path, required=True)
    sub.add_parser("health")
    sub.add_parser("seed")
    listing = sub.add_parser("list")
    listing.add_argument("kind", choices=["documents", "indexes", "videos", "runs", "profiles"])
    for command in ("upload-document", "upload-video"):
        upload = sub.add_parser(command)
        upload.add_argument("path", type=Path)
    index = sub.add_parser("index")
    index.add_argument("document_ids", nargs="+")
    index.add_argument("--profile", default="baseline")
    run = sub.add_parser("run")
    run.add_argument("--video-id", required=True)
    run.add_argument("--index-id")
    run.add_argument("--context", required=True)
    run.add_argument("--profile", default="baseline")
    wait = sub.add_parser("wait")
    wait.add_argument("kind", choices=["runs", "indexes"])
    wait.add_argument("id")
    for command in ("decide", "render", "report", "revise"):
        action = sub.add_parser(command)
        action.add_argument("id")
    override = sub.add_parser("override")
    override.add_argument("id")
    override.add_argument("--file", type=Path, required=True, help="JSON {treatments, no_faces_confirmed}")
    fork = sub.add_parser("fork")
    fork.add_argument("id")
    fork.add_argument("--profile")
    fork.add_argument("--index-id")
    fork.add_argument("--context")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--profile", default="fixture")
    args = parser.parse_args()
    if args.command == "export-openapi":
        from privframe.api import create_app
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))
        print(args.output)
        return
    with httpx.Client(base_url=args.url, timeout=60) as client:
        if args.command == "health":
            result = client.get("/api/health")
        elif args.command == "seed":
            result = client.post("/api/documents/seed")
        elif args.command == "list":
            result = client.get(f"/api/{args.kind}")
        elif args.command.startswith("upload-"):
            kind = "documents" if args.command == "upload-document" else "videos"
            with args.path.open("rb") as file:
                result = client.post(f"/api/{kind}", files={"file": (args.path.name, file)})
        elif args.command == "index":
            result = client.post("/api/indexes", json={"document_ids": args.document_ids, "profile": args.profile})
        elif args.command == "run":
            result = client.post("/api/runs", json={"video_id": args.video_id, "index_id": args.index_id,
                                 "profile": args.profile, "scenario": {"context": args.context}})
        elif args.command == "wait":
            print(json.dumps(wait_record(client, args.kind, args.id), ensure_ascii=False, indent=2))
            return
        elif args.command in {"decide", "render", "report", "revise"}:
            result = client.post(f"/api/runs/{args.id}/{args.command}")
        elif args.command == "override":
            result = client.put(f"/api/runs/{args.id}/overrides", json=json.loads(args.file.read_text()))
        elif args.command == "fork":
            body = {}
            if args.profile:
                body["profile"] = args.profile
            if args.index_id:
                body["index_id"] = args.index_id
            if args.context:
                body["scenario"] = {"context": args.context}
            result = client.post(f"/api/runs/{args.id}/fork", json=body)
        elif args.command == "evaluate":
            from privframe.evaluation import evaluate_manifest
            records = evaluate_manifest(client, args.manifest, args.profile)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
            print(args.output)
            return
        result.raise_for_status()
        print(json.dumps(result.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
