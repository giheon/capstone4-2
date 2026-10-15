"""CLI uses the same HTTP use cases as the UI; it never opens worker Chroma."""
import argparse
import json
import time
from pathlib import Path

import httpx


def wait_record(client: httpx.Client, kind: str, record_id: str, timeout: float = 1200) -> dict:
    deadline = time.monotonic() + timeout
    active = {"UPLOADED", "QUEUED", "INDEXING", "ANALYZING", "DECIDING", "RENDERING", "RUNNING"}
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
    sub.add_parser("app-config")
    sub.add_parser("seed")
    listing = sub.add_parser("list")
    listing.add_argument("kind", choices=["documents", "indexes", "videos", "runs", "profiles", "executions"])
    for command in ("upload-document", "upload-video"):
        upload = sub.add_parser(command)
        upload.add_argument("path", type=Path)
    index = sub.add_parser("index")
    index.add_argument("document_ids", nargs="+")
    index.add_argument("--profile", default="baseline")
    index.add_argument("--stop-after")
    run = sub.add_parser("run")
    run.add_argument("--video-id", required=True)
    run.add_argument("--index-id")
    run.add_argument("--context", default="")
    run.add_argument("--purpose")
    run.add_argument("--audience")
    run.add_argument("--stop-after")
    run.add_argument("--profile", default="baseline")
    wait = sub.add_parser("wait")
    wait.add_argument("kind", choices=["runs", "indexes", "documents", "executions"])
    wait.add_argument("id")
    for command in ("decide", "render", "report", "revise"):
        action = sub.add_parser(command)
        action.add_argument("id")
        if command in {"decide", "render"}:
            action.add_argument("--stop-after")
    override = sub.add_parser("override")
    override.add_argument("id")
    override.add_argument("--file", type=Path, required=True, help="JSON {treatments, no_faces_confirmed}")
    review = sub.add_parser("identity-review")
    review.add_argument("id")
    review.add_argument("--file", type=Path, required=True,
                        help="JSON {revision, groups, persons, confirmed_group_ids}")
    split = sub.add_parser("split-track")
    split.add_argument("id")
    split.add_argument("--track-id", required=True)
    split.add_argument("--frame", type=int, required=True, help="First frame of the second segment")
    split.add_argument("--revision", type=int, required=True)
    fork = sub.add_parser("fork")
    fork.add_argument("id")
    fork.add_argument("--profile")
    fork.add_argument("--index-id")
    fork.add_argument("--context")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--profile", default="fixture")
    evaluate.add_argument("--target", choices=["end-to-end", "index", "retrieval", "vision", "decision"], default="end-to-end")
    activate = sub.add_parser("activate-index")
    activate.add_argument("id")
    activate.add_argument("--profile", default="baseline")
    for command in ("pause", "resume"):
        item = sub.add_parser(command)
        item.add_argument("id")
    inspect = sub.add_parser("execution")
    inspect.add_argument("id")
    inspect.add_argument("--node")
    graph = sub.add_parser("graph")
    graph.add_argument("name")
    graph.add_argument("--output", type=Path)
    component = sub.add_parser("component")
    component.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export-openapi":
        from privframe.api import create_app
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))
        print(args.output)
        return
    with httpx.Client(base_url=args.url, timeout=60) as client:
        if args.command in {"health", "app-config"}:
            result = client.get(f"/api/{args.command}")
        elif args.command == "activate-index":
            result = client.post(f"/api/indexes/{args.id}/activate", json={"profile": args.profile})
        elif args.command in {"pause", "resume"}:
            result = client.post(f"/api/executions/{args.id}/{args.command}")
        elif args.command == "execution":
            suffix = f"/outputs/{args.node}" if args.node else ""
            result = client.get(f"/api/executions/{args.id}{suffix}")
        elif args.command == "component":
            result = client.post("/api/executions", json=json.loads(args.file.read_text()))
        elif args.command == "graph":
            result = client.get(f"/api/graphs/{args.name}")
            result.raise_for_status()
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(result.json()["mermaid"])
                print(args.output)
                return
        elif args.command == "seed":
            result = client.post("/api/documents/seed")
        elif args.command == "list":
            result = client.get(f"/api/{args.kind}")
        elif args.command.startswith("upload-"):
            kind = "documents" if args.command == "upload-document" else "videos"
            with args.path.open("rb") as file:
                result = client.post(f"/api/{kind}", files={"file": (args.path.name, file)})
        elif args.command == "index":
            result = client.post("/api/indexes", json={"document_ids": args.document_ids, "profile": args.profile, "stop_after": args.stop_after})
        elif args.command == "run":
            result = client.post("/api/runs", json={"video_id": args.video_id, "index_id": args.index_id,
                                 "profile": args.profile, "stop_after": args.stop_after,
                                 "scenario": {"context": args.context, "use_purpose": args.purpose, "audience_scope": args.audience}})
        elif args.command == "wait":
            print(json.dumps(wait_record(client, args.kind, args.id), ensure_ascii=False, indent=2))
            return
        elif args.command in {"decide", "render", "report", "revise"}:
            result = client.post(f"/api/runs/{args.id}/{args.command}",
                                 json={"stop_after": args.stop_after} if args.command in {"decide", "render"} else None)
        elif args.command == "override":
            result = client.put(f"/api/runs/{args.id}/overrides", json=json.loads(args.file.read_text()))
        elif args.command == "identity-review":
            result = client.post(f"/api/runs/{args.id}/identity-review", json=json.loads(args.file.read_text()))
        elif args.command == "split-track":
            result = client.post(f"/api/runs/{args.id}/split-track", json={
                "revision": args.revision, "track_id": args.track_id, "frame": args.frame,
            })
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
            if args.target == "end-to-end":
                records = evaluate_manifest(client, args.manifest, args.profile)
            else:
                from privframe.component_evaluation import evaluate_components
                records = evaluate_components(client, args.manifest, args.profile, args.target)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
            print(args.output)
            return
        result.raise_for_status()
        print(json.dumps(result.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
