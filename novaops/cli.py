"""Command-line entry points for document ingestion and cited questions."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from novaops.agent.graph import decide_agent_approval, invoke_agent
from novaops.core.config import Settings
from novaops.core.database import Database
from novaops.core.model import BedrockModel
from novaops.evals.compare import compare_reports
from novaops.evals.runner import evaluate_files
from novaops.retrieval.embeddings import BedrockEmbedder
from novaops.retrieval.ingest import load_document_chunks
from novaops.retrieval.store import FaissDocumentStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novaops")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="Build the local FAISS document index")
    ingest.add_argument(
        "--documents-dir",
        type=Path,
        help="Override the default dataset documents directory",
    )

    ask = commands.add_parser("ask", help="Ask a question and return a cited answer")
    ask.add_argument("question")
    ask.add_argument("--as", dest="caller_employee_id")
    ask.add_argument("--top-k", type=int, default=5)

    chat = commands.add_parser("chat", help="Run one turn through the shared Maya/Webex agent")
    chat.add_argument("message")
    chat.add_argument("--as", dest="caller_employee_id", required=True)
    chat.add_argument("--thread-id", required=True)
    chat.add_argument("--request-id", help="Honor an inbound X-Request-ID value")

    approve = commands.add_parser(
        "approve", help="Record an authorized Webex decision and resume its workflow"
    )
    approve.add_argument("--thread-id", required=True)
    approve.add_argument("--as", dest="approver_employee_id", required=True)
    approve.add_argument("--decision", choices=("approved", "rejected"), required=True)
    approve.add_argument("--reason", required=True)
    approve.add_argument("--request-id", help="Honor an inbound X-Request-ID value")

    evaluate = commands.add_parser(
        "eval", help="Score recorded runs against the binding golden-data checks"
    )
    evaluate.add_argument("--runs", type=Path, required=True)
    evaluate.add_argument(
        "--golden", type=Path, help="Override the default golden dataset"
    )
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument(
        "--baseline", type=Path, help="Compare against a saved evaluation report"
    )

    server = commands.add_parser("serve", help="Start the MCP tools server over HTTP")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=9876)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "eval":
        report = evaluate_files(
            runs_path=args.runs,
            **({"golden_path": args.golden} if args.golden else {}),
        )
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            if not isinstance(baseline, dict):
                raise ValueError("Baseline report must be a JSON object")
            report["comparison"] = compare_reports(report, baseline)
        rendered = json.dumps(report, indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return

    settings = Settings.from_env()

    if args.command == "ingest":
        _ingest(settings, args.documents_dir)
    elif args.command == "ask":
        _ask(
            settings,
            args.question,
            caller_employee_id=args.caller_employee_id,
            top_k=args.top_k,
        )
    elif args.command == "chat":
        result = asyncio.run(
            invoke_agent(
                settings,
                message=args.message,
                caller_employee_id=args.caller_employee_id,
                thread_id=args.thread_id,
                x_request_id=args.request_id,
            )
        )
        print(result["answer"])
        print(f"\nStatus: {result['status']}")
    elif args.command == "approve":
        result = asyncio.run(
            decide_agent_approval(
                settings,
                thread_id=args.thread_id,
                approver_employee_id=args.approver_employee_id,
                decision=args.decision,
                reason=args.reason,
                x_request_id=args.request_id,
            )
        )
        print(result["answer"])
        print(f"\nStatus: {result['status']}")
    elif args.command == "serve":
        _serve(settings, host=args.host, port=args.port)


def _ingest(settings: Settings, documents_dir: Path | None) -> None:
    source_dir = documents_dir or settings.data_dir / "documents"
    chunks = load_document_chunks(
        source_dir,
        chunk_words=settings.chunk_words,
        overlap_words=settings.overlap_words,
    )
    embedder = BedrockEmbedder(
        settings.bedrock_embedding_model_id, settings.aws_region
    )
    store = FaissDocumentStore.build(chunks, embedder)
    store.save(settings.index_path)
    print(f"Indexed {len(chunks)} chunks from {source_dir}")


def _ask(
    settings: Settings,
    question: str,
    *,
    caller_employee_id: str | None,
    top_k: int,
) -> None:
    if not question.strip():
        raise ValueError("Question must not be empty")
    if top_k <= 0:
        raise ValueError("--top-k must be positive")

    database = Database(settings.database_path, settings.data_dir)
    is_manager = False
    if caller_employee_id:
        if database.get_employee(caller_employee_id) is None:
            raise ValueError(f"Unknown caller employee id: {caller_employee_id}")
        is_manager = database.is_manager(caller_employee_id)

    embedder = BedrockEmbedder(
        settings.bedrock_embedding_model_id, settings.aws_region
    )
    store = FaissDocumentStore.load(
        settings.index_path,
        embedding_model_id=settings.bedrock_embedding_model_id,
    )
    results = store.search(
        question,
        embedder,
        is_manager=is_manager,
        limit=top_k,
    )
    if not results:
        print("No permitted documents matched the question.")
        return

    evidence = "\n\n".join(
        f"[{result.citation}]\n{result.chunk.text}" for result in results
    )
    answer = BedrockModel(settings).generate(
        f"Question: {question}\n\nEvidence:\n{evidence}",
        system=(
            "Answer using only the supplied evidence. Do not invent company facts. "
            "Cite factual claims with the exact source path in square brackets, such as "
            "[documents/policies/remote_work_policy.md]. If evidence is insufficient, say so."
        ),
    )
    citations = ", ".join(f"[{result.citation}]" for result in results)
    print(answer)
    print(f"\nSources: {citations}")


def _serve(settings: Settings, *, host: str, port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("--port must be between 1 and 65535")
    from novaops.mcp_server.server import create_server

    server = create_server(settings)
    server.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
