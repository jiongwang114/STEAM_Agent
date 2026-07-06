from ..rag.vector_store import get_user_memory_collection


def recall_user_memory(
    user_id: str,
    query: str,
    top_k: int = 5,
) -> dict:
    """
    Retrieve relevant historical conversation snippets from the user's memory.
    """
    collection = get_user_memory_collection()

    raw = collection.query(
        query_texts=[query],
        n_results=top_k,
        where={"user_id": user_id},
    )

    memories = []
    if raw["ids"] and raw["ids"][0]:
        for i in range(len(raw["ids"][0])):
            meta = raw["metadatas"][0][i] if raw["metadatas"] else {}
            memories.append({
                "content": raw["documents"][0][i] if raw["documents"] else "",
                "timestamp": meta.get("timestamp", ""),
                "thread_id": meta.get("thread_id", ""),
                "similarity_score": round(1 - raw["distances"][0][i], 4) if raw.get("distances") else 0.0,
            })

    return {"memories": memories}
