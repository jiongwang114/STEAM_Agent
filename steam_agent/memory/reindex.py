"""Rebuild multilingual semantic memory from the authoritative SQLite archive."""

from __future__ import annotations

import argparse

from ..rag.embedder import embed_memory
from ..rag.vector_store import get_user_memory_collection, reset_user_memory_collection
from .archiver import retry_pending_archives
from .message_store import get_all_conversation_turns


def rebuild_memory_index(*, reset: bool = False, batch_size: int = 64) -> dict:
    turns = get_all_conversation_turns()
    collection = reset_user_memory_collection() if reset else get_user_memory_collection()
    indexed = 0
    for start in range(0, len(turns), batch_size):
        batch = turns[start:start + batch_size]
        documents = [
            f"User: {item['user_message']}\nAssistant: {item['assistant_reply']}"
            for item in batch
        ]
        collection.upsert(
            ids=[
                f"{item['user_id']}:{item['thread_id']}:{item['turn_number']}"
                for item in batch
            ],
            embeddings=embed_memory(documents),
            documents=documents,
            metadatas=[{
                "user_id": item["user_id"],
                "thread_id": item["thread_id"],
                "turn_number": item["turn_number"],
                "timestamp": item["timestamp"],
            } for item in batch],
        )
        indexed += len(batch)
    return {"turns_found": len(turns), "turns_indexed": indexed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild multilingual user memory index")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--retry-archives",
        action="store_true",
        help="Replay SQLite archive tasks whose semantic write failed",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    if args.retry_archives:
        print(retry_pending_archives())
    else:
        print(rebuild_memory_index(reset=args.reset, batch_size=args.batch_size))


if __name__ == "__main__":
    main()
