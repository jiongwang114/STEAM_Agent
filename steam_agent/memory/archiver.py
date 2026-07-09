"""Archive conversation turns to both Chroma (semantic) and SQLite (structured)."""

from datetime import datetime, timezone

from ..rag.embedder import embed
from ..rag.vector_store import get_user_memory_collection
from .message_store import add_message


def archive_conversation(
    user_id: str,
    thread_id: str,
    user_message: str,
    assistant_reply: str,
    turn_number: int = 1,
):
    """Called after each response. Writes to Chroma and SQLite in parallel."""
    if not user_message.strip() or not assistant_reply.strip():
        return

    # ==== Chroma: semantic retrieval (existing) ====
    text = f"User: {user_message}\nAssistant: {assistant_reply}"
    timestamp = datetime.now(timezone.utc).isoformat()
    doc_id = f"{user_id}_{thread_id}_{timestamp}"

    embedding = embed([text])
    collection = get_user_memory_collection()
    collection.add(
        ids=[doc_id],
        embeddings=embedding,
        documents=[text],
        metadatas=[{
            "user_id": user_id,
            "thread_id": thread_id,
            "timestamp": timestamp,
            "turn_number": turn_number,
        }],
    )

    # ==== SQLite: structured lookup (new) ====
    add_message(user_id, thread_id, turn_number, "user", user_message)
    add_message(user_id, thread_id, turn_number, "assistant", assistant_reply)
