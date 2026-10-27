
from typing import List

from applicationca.database.database_retrieval import create_connection
from sentence_transformers import CrossEncoder
from collections import OrderedDict

# Lazy-loaded reranking model
_reranker = None

def get_reranker():
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _reranker

def retrieve_chunks_from_db(query: str, folder_id: str, user_id: str, top_k: int = 20) -> List[dict]:
    """Retrieve top-k relevant chunks using pg_trgm for fast text search."""
    conn = create_connection()
    cursor = conn.cursor()

    # Use pg_trgm for ranking
    if folder_id:
        cursor.execute("""
            SELECT document_id, chunk_id, content, similarity(content, %s) AS score
            FROM chunks
            WHERE folder_id = %s AND user_id = %s
            ORDER BY score DESC
            LIMIT %s
        """, (query, folder_id, user_id, top_k))
    else:
        cursor.execute("""
            SELECT document_id, chunk_id, content, similarity(content, %s) AS score
            FROM chunks
            WHERE user_id = %s
            ORDER BY score DESC
            LIMIT %s
        """, (query, user_id, top_k))


    chunks = [{"file_name": row[0], "chunk_id": row[1], "content": row[2], "score": float(row[3])} for row in cursor.fetchall()]

    conn.close()
    # Extract candidate results
    candidates = []

    for chunk in chunks:
        candidates.append((chunk.get("file_name"), chunk.get("content"), chunk.get("score")))

    # Rerank results only if candidates exist
    if candidates:  # This checks if candidates list is not empty
        rerank_inputs = [(query, doc[1]) for doc in candidates]
        rerank_scores = get_reranker().predict(rerank_inputs)

        # Sort by rerank score
        reranked_results = sorted(zip(candidates, rerank_scores), key=lambda x: x[1], reverse=True)

        # Format output
        final_results = []
        for (filename, text, original_score), rerank_score in reranked_results:
            final_results.append({
                "filename": filename,
                "text": text,
                "original_score": original_score,
                "rerank_score": rerank_score
            })
    else:
        # Return empty list if no candidates
        final_results = []

    return final_results


def retrieve_chunks_from_db_advanced(queries: List[str], folder_id: str, user_id: str, top_k_per_query: int = 10,
                                     top_k_total: int = 20) -> List[dict]:
    """
    Advanced chunk retrieval that gets top-k results per query, then deduplicates and returns top results overall.
    Uses PostgreSQL window functions for more efficient processing.

    Args:
        queries: List of search queries
        folder_id: Optional folder ID to filter by
        user_id: User ID to filter by
        top_k_per_query: Maximum number of chunks to consider per query
        top_k_total: Maximum total chunks to return after deduplication

    Returns:
        List of unique chunks across all queries, ordered by relevance
    """
    if not queries:
        return []

    conn = create_connection()
    cursor = conn.cursor()

    # Using Common Table Expression (CTE) with window functions
    cte_parts = []
    params = []

    for i, query in enumerate(queries):
        if folder_id:
            cte_parts.append(f"""
                select_{i} AS (
                    SELECT 
                        document_id, 
                        chunk_id, 
                        content, 
                        folder_id,
                        similarity(content, %s) AS score,
                        ROW_NUMBER() OVER (ORDER BY similarity(content, %s) DESC) as rank
                    FROM chunks
                    WHERE folder_id = %s AND user_id = %s
                )
            """)
            params.extend([query, query, folder_id, user_id])
        else:
            cte_parts.append(f"""
                select_{i} AS (
                    SELECT 
                        document_id, 
                        chunk_id, 
                        content,
                        folder_id,
                        similarity(content, %s) AS score,
                        ROW_NUMBER() OVER (ORDER BY similarity(content, %s) DESC) as rank
                    FROM chunks
                    WHERE user_id = %s
                )
            """)
            params.extend([query, query, user_id])

    # Build the complete SQL with CTEs
    union_parts = [f"SELECT document_id, chunk_id, content, folder_id, score FROM select_{i} WHERE rank <= %s" for i in
                   range(len(queries))]
    params.extend([top_k_per_query] * len(queries))

    # Fixed SQL: Order first by chunk_id for DISTINCT ON, then by max_score
    sql = "WITH " + ", ".join(cte_parts) + " " + \
          "SELECT DISTINCT ON (chunk_id) document_id, chunk_id, content, folder_id, MAX(score) as max_score " + \
          "FROM (" + " UNION ALL ".join(union_parts) + ") AS combined " + \
          "GROUP BY document_id, chunk_id, content, folder_id " + \
          "ORDER BY chunk_id, max_score DESC LIMIT %s"

    # Alternative approach - remove DISTINCT ON and use a subquery with ROW_NUMBER:
    # sql = "WITH " + ", ".join(cte_parts) + " " + \
    #       "SELECT document_id, chunk_id, content, max_score FROM " + \
    #       "(SELECT document_id, chunk_id, content, MAX(score) as max_score, " + \
    #       "ROW_NUMBER() OVER (PARTITION BY chunk_id ORDER BY MAX(score) DESC) as rn " + \
    #       "FROM (" + " UNION ALL ".join(union_parts) + ") AS combined " + \
    #       "GROUP BY document_id, chunk_id, content) ranked " + \
    #       "WHERE rn = 1 " + \
    #       "ORDER BY max_score DESC LIMIT %s"

    params.append(top_k_total)

    cursor.execute(sql, params)


    # Build the result list
    chunks = [
        {
            "file_name": row[0],
            "chunk_id": row[1],
            "content": row[2],
            "folder_id": row[3],
            "score": float(row[4])
        }
        for row in cursor.fetchall()
    ]

    conn.close()
    return chunks