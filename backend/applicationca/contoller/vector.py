import asyncio
from typing import List, Dict, Any

from fastapi import APIRouter, Depends, Query
from pinecone import Pinecone
from langchain_openai import OpenAIEmbeddings
import mimetypes
from applicationca.aws.constants.AwsConstants import USER_DATA_S3_BUCKET_NAME
from applicationca.aws.s3.S3Operations import get_file_data
from applicationca.services.Functions import get_user_response
from applicationca.rbac import require_permission
from sentence_transformers import CrossEncoder

from config.settings import settings

router = APIRouter()

# Initialize Pinecone
pc = Pinecone(api_key=settings.PINECONE_API_KEY)
index = pc.Index(settings.PINECONE_INDEX_NAME)

# Load AI models
text_embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=settings.OPENAI_API_KEY)

# Function to generate text embeddings
def get_text_embedding(text):
    return text_embeddings.embed_query(text)

# Function to convert query to embedding
def get_query_embedding(query: str):
    """
    Convert a text query into an embedding using OpenAI's text embedding model.

    Args:
        query (str): The text query to convert.

    Returns:
        list: The embedding vector of the query.
    """
    return text_embeddings.embed_query(query)

# Function to search the vector database
def search_vector_db(query_embedding, top_k=10):
    """
    Search the Pinecone index for the most relevant vectors based on the query embedding.

    Args:
        query_embedding (list): The embedding vector of the query.
        top_k (int): The number of top results to return.

    Returns:
        list: A list of the top_k most relevant results.
    """
    # print("query embedding", query_embedding)
    results = index.query(
        vector=query_embedding,
        top_k=top_k,
        include_metadata=True
    )
    return results

# Lazy-loaded reranking model
_reranker = None

def get_reranker():
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _reranker

async def search(query: str, user_id: str = None, folder: str = None):
    try:
        query_vector = get_text_embedding(query)

        # Build filters
        filter_conditions = {}
        if user_id:
            filter_conditions["user_id"] = user_id
        if folder:
            filter_conditions["folder"] = folder

        # Search Pinecone
        results = index.query(vector=query_vector, top_k=20, include_metadata=True, filter=filter_conditions)

        # Extract candidate results
        candidates = []
        for match in results["matches"]:
            filename = match["metadata"].get("filename", "Unknown") + "." + match["metadata"].get("type", "pdf")
            text = match["metadata"].get("text", "No text available")
            candidates.append((filename, text, match["score"]))

        # Only proceed with reranking if we have candidates
        if candidates:
            # Rerank results
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
            # Return empty list if no candidates found
            final_results = []

        return final_results
    except Exception as e:
        return []


async def search_multi(queries: List[str], user_id: str = None, folder: str = None, chunk_count: int = 20) -> List[Dict[str, Any]]:
    """
    Optimized search function that processes multiple queries concurrently.

    Args:
        queries: List of search queries
        user_id: Optional user ID to filter by
        folder: Optional folder to filter by

    Returns:
        List of deduplicated chunks in descending order of relevance score
    """
    try:
        # Build filter conditions once
        filter_conditions = {}
        if user_id:
            filter_conditions["user_id"] = user_id
        if folder:
            filter_conditions["folder"] = folder

        # Helper function to process a single query
        async def process_query(query: str):
            try:
                # Get embedding for this query
                query_vector = get_text_embedding(query)

                # Search Pinecone
                results = index.query(
                    vector=query_vector,
                    top_k=chunk_count,
                    include_metadata=True,
                    filter=filter_conditions
                )

                # Return the matches
                return results["matches"]
            except Exception as e:
                print(f"Error processing query '{query}': {e}")
                return []

        # Execute all queries concurrently
        tasks = [process_query(query) for query in queries]
        all_results = await asyncio.gather(*tasks)

        # Deduplicate results (keep highest score when duplicates found)
        unique_results = {}
        for result_batch in all_results:
            for match in result_batch:
                match_id = match["id"]
                if match_id not in unique_results or match["score"] > unique_results[match_id]["score"]:
                    unique_results[match_id] = {
                        "file_name": match["metadata"].get("filename", "Unknown") + "." + match["metadata"].get("type",
                                                                                                                "pdf"),
                        "chunk_id": match["metadata"].get("chunk_id"),
                        "content": match["metadata"].get("text", "No text available"),
                        "folder_id": match["metadata"].get("folder"),
                        "score": float(match["score"])
                    }

        # Convert to list and sort by score (descending)
        chunks = sorted(unique_results.values(), key=lambda x: x["score"], reverse=True)

        return chunks

    except Exception as e:
        print(f"Search error: {e}")
        return []

async def form_query(message, user_id):
    simplified_results = await search(message)
    user_message = ""
    for result in simplified_results:
        user_message+="Document Name: " + result.get("id")
        file_content = get_file_data(user_id, result.get("id"), USER_DATA_S3_BUCKET_NAME)
        file_type = get_file_type_from_content(result.get("id"))
        user_message+="\nExtracted Text: " + await get_user_response(file_content, file_type)
        user_message+="\n"
    return user_message

def get_file_type_from_content(file_name):
    return mimetypes.guess_type(file_name)[0]


@router.get("/search", dependencies=[Depends(require_permission("documents.read"))])
async def search_endpoint(
    query: str = Query(..., description="Search query text"),
    user_id: str = Query(None, description="User ID for filtering"),
    folder: str = Query(None, description="Folder name to filter results")
):
    return await search(query, user_id, folder)
