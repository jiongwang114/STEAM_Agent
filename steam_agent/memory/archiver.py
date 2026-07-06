from datetime import datetime, timezone

from ..rag.embedder import embed
from ..rag.vector_store import get_user_memory_collection


def archive_conversation(
    user_id: str,
    thread_id: str,
    user_message: str,
    assistant_reply: str,
):
    """
    Archive a conversation turn into the Chroma user_memory collection.
    Called after each response is sent to the user.
    """
    if not user_message.strip() or not assistant_reply.strip():
        return

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
        }],
    )
