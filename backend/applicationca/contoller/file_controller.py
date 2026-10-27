from typing import List, Optional
import traceback
import logging
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import File, Form, UploadFile
from utils.file_helper import extract_text_from_docx, extract_text_from_xlsx, extract_text_from_pdf, \
    extract_text_from_image

from applicationca.database.database_retrieval import get_files_by_user, get_lockers_and_clients_by_user, \
    get_files_by_locker, get_files_by_client
from applicationca.database.database_update import upload_new_file, upload_new_files, create_connection, add_new_lockr, add_new_client
from applicationca.database.database_retrieval import get_files_by_user  # Import the correct function
from utils.auth_utils import verify_jwt
import asyncio
from applicationca.aws.s3.S3Operations import upload_to_s3, delete_file_from_s3
from applicationca.aws.constants.AwsConstants import USER_DATA_S3_BUCKET_NAME
from applicationca.services.BM25 import retrieve_chunks_from_db
from applicationca.contoller.vector import index, get_text_embedding  # Import Pinecone index
from applicationca.rbac import require_permission
from firebase_admin import auth
import boto3
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError
import io
from langchain_text_splitters import RecursiveCharacterTextSplitter
from uuid import uuid4

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

router = APIRouter()

# JWT Authentication Scheme
security = HTTPBearer()

# Text splitter for chunking documents
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
    separators=["\n\n", "\n", ". ", " ", ""]
)


def extract_text_from_file(file_bytes: bytes, filename: str, content_type: str) -> str:
    """Extract text from a file based on its type."""
    filename_lower = filename.lower()
    if content_type and content_type.startswith("image/"):
        return extract_text_from_image(file_bytes)
    elif filename_lower.endswith('.pdf'):
        return extract_text_from_pdf(file_bytes)
    elif filename_lower.endswith('.docx'):
        return extract_text_from_docx(file_bytes)
    elif filename_lower.endswith('.xlsx'):
        return extract_text_from_xlsx(file_bytes)
    else:
        try:
            return file_bytes.decode("utf-8")
        except Exception:
            return ""


def process_and_store_chunks(user_id: str, folder_id: str, filename: str, file_bytes: bytes, content_type: str):
    """
    Extract text from a file, chunk it, store chunks in the CHUNKS table,
    and upsert embeddings to Pinecone.
    """
    try:
        # 1. Extract text
        text = extract_text_from_file(file_bytes, filename, content_type)
        if not text or not text.strip():
            logger.warning(f"No text extracted from file: {filename}")
            return

        # 2. Split into chunks
        chunks = text_splitter.split_text(text)
        if not chunks:
            logger.warning(f"No chunks created from file: {filename}")
            return

        logger.info(f"Created {len(chunks)} chunks from file: {filename}")

        # 3. Generate embeddings for all chunks
        chunk_records = []
        pinecone_vectors = []
        for i, chunk_text in enumerate(chunks):
            chunk_id = str(uuid4())
            try:
                embedding = get_text_embedding(chunk_text)
            except Exception as e:
                logger.error(f"Error generating embedding for chunk {i} of {filename}: {e}")
                continue

            chunk_records.append((chunk_id, filename, folder_id, user_id, chunk_text))
            pinecone_vectors.append({
                "id": chunk_id,
                "values": embedding,
                "metadata": {
                    "filename": filename.rsplit('.', 1)[0] if '.' in filename else filename,
                    "chunk_id": chunk_id,
                    "folder": str(folder_id),
                    "user_id": str(user_id),
                    "text": chunk_text[:1000],  # Pinecone metadata size limit
                    "type": filename.rsplit('.', 1)[-1].lower() if '.' in filename else "txt"
                }
            })

        if not chunk_records:
            logger.warning(f"No chunks with embeddings for file: {filename}")
            return

        # 4. Store chunks in PostgreSQL CHUNKS table
        connection = create_connection()
        if connection:
            try:
                cursor = connection.cursor()
                insert_query = """
                    INSERT INTO CHUNKS (chunk_id, document_id, folder_id, user_id, content)
                    VALUES (%s, %s, %s, %s, %s)
                """
                cursor.executemany(insert_query, chunk_records)
                connection.commit()
                logger.info(f"Stored {len(chunk_records)} chunks in DB for file: {filename}")
            except Exception as e:
                connection.rollback()
                logger.error(f"Error storing chunks in DB for {filename}: {e}")
            finally:
                cursor.close()
                connection.close()
        else:
            logger.error("Failed to connect to database for storing chunks")

        # 5. Upsert embeddings to Pinecone in batches of 100
        batch_size = 100
        for i in range(0, len(pinecone_vectors), batch_size):
            batch = pinecone_vectors[i:i + batch_size]
            try:
                index.upsert(vectors=batch)
            except Exception as e:
                logger.error(f"Error upserting to Pinecone for {filename} (batch {i}): {e}")

        logger.info(f"Upserted {len(pinecone_vectors)} vectors to Pinecone for file: {filename}")

    except Exception as e:
        logger.error(f"Error in process_and_store_chunks for {filename}: {e}")
        logger.error(traceback.format_exc())


# FastAPI Endpoints
@router.post("/upload/", dependencies=[Depends(require_permission("documents.create"))])
async def upload_files(
        client_id: str = Form(None),
        lockr_id: str = Form(None),
        files: List[UploadFile] = File(...),
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    try:
        token = credentials.credentials

        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')

        if not files:
            raise HTTPException(status_code=400, detail="No files provided")

        folder_id = lockr_id
        if (lockr_id == None):
            folder_id = client_id
        # Create asynchronous tasks for each file upload, passing the folder_id to each task
        tasks = [upload_to_s3(file, folder_id, user_id) for file in files]
        s3_results = await asyncio.gather(*tasks)
        upload_new_files(
            user_id=user_id,
            files=files,
            s3_results=s3_results,
            lockr_id=lockr_id,
            client_id=client_id
        )

        # Process files: extract text, chunk, generate embeddings, store in DB + Pinecone
        for file, s3_result in zip(files, s3_results):
            file_bytes = s3_result.get("file_content")
            if file_bytes:
                process_and_store_chunks(
                    user_id=user_id,
                    folder_id=folder_id,
                    filename=file.filename,
                    file_bytes=file_bytes,
                    content_type=file.content_type
                )

        return {"message": f"{len(files)} files uploaded successfully to folder '{folder_id}'!"}
    except Exception as e:
        logger.error(f"Error in upload_files: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload_db/", dependencies=[Depends(require_permission("documents.create"))])
async def upload_file_db(
        user_id: str = Form(...),
        lockr_id: Optional[str] = Form(None),
        client_id: Optional[str] = Form(None),
        files: List[UploadFile] = File(...),
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    if not lockr_id and not client_id:
        raise HTTPException(status_code=400, detail="Either lockr_id or client_id must be provided")
    #
    # # Upload files to S3 asynchronously
    # tasks = [upload_to_s3(file, user_id) for file in files]
    # await asyncio.gather(*tasks)

    # Store file details in the database
    for file in files:
        upload_new_file(user_id, lockr_id, client_id, file.filename, metadata=None)

    return {"message": f"{len(files)} files uploaded successfully and recorded in DB!"}


@router.get("/query/", dependencies=[Depends(require_permission("documents.read"))])
async def query_documents(
        query: str = Form(...),
        user_id: str = Form(...),
        folder_id: str = Form(None),
        top_k: int = Form(...)
):
    """Endpoint to query documents and retrieve relevant chunks using pg_trgm."""
    relevant_chunks = retrieve_chunks_from_db(query, folder_id, user_id, top_k)
    if not relevant_chunks:
        raise HTTPException(status_code=404, detail="No relevant chunks found")
    return {"query": query, "relevant_chunks": relevant_chunks}


@router.post("/search-suggested-files", dependencies=[Depends(require_permission("documents.read"))])
async def search_files(
        search_context: str = Form(...),
        files: List[UploadFile] = File(...),
):
    search_context_lower = search_context.lower()
    matching_files = []

    for uploaded_file in files:
        filename_lower = uploaded_file.filename.lower()
        # Check if lockerName is in the filename.
        if search_context_lower in filename_lower:
            matching_files.append(uploaded_file.filename)
            continue

        # Read file bytes for content search.
        file_bytes = await uploaded_file.read()
        ext = filename_lower.split('.')[-1]
        text_content = ""
        file_type = uploaded_file.content_type
        if file_type.startswith("image/"):
            text_content = extract_text_from_image(file_bytes)
        elif ext == "pdf":
            text_content = extract_text_from_pdf(file_bytes)
        elif ext == "docx":
            text_content = extract_text_from_docx(file_bytes)
        elif ext == "xlsx":
            text_content = extract_text_from_xlsx(file_bytes)
        else:
            # For plain text files or others, try a naive decode.
            try:
                text_content = file_bytes.decode(errors="ignore")
            except Exception as e:
                print(f"Error decoding {uploaded_file.filename}: {e}")

        if search_context_lower in text_content.lower():
            matching_files.append(uploaded_file.filename)

    return {"matching_files": matching_files}


@router.post("/view-file/", dependencies=[Depends(require_permission("documents.read"))])
async def view_file(
        file_id: str = Form(..., description="File ID to view"),
        token: str = Form(..., description="Authentication token")
):
    try:
        # Authenticate user using the token
        try:
            decoded_token = verify_jwt(token)
            user_id = decoded_token["uid"]
            logger.debug(f"Authenticated user_id: {user_id}")
        except Exception as e:
            logger.error(f"Token verification failed: {str(e)}")
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        # Get all files for the user and find the specific file
        user_files = get_files_by_user(user_id)
        file_info = next((file for file in user_files if file['file_id'] == UUID(file_id)), None)

        if not file_info:
            logger.error(f"File not found: {file_id}")
            return JSONResponse({"error": "File not found"}, status_code=404)

        # Construct S3 key from file info
        if file_info['lockr_id']:
            s3_key = f"{user_id}/{file_info['lockr_id']}/{file_info['file_name']}"
        else:
            s3_key = f"{user_id}/{file_info['client_id']}/{file_info['file_name']}"

        # Initialize S3 client
        s3_client = boto3.client('s3')
        bucket_name = USER_DATA_S3_BUCKET_NAME

        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
            content_type = response.get('ContentType', 'application/octet-stream')

            # For PDFs and images, we want to display them inline
            disposition = 'inline'

            # Set appropriate content type for PDFs
            if file_info["file_name"].lower().endswith('.pdf'):
                content_type = 'application/pdf'
            # For images
            elif any(file_info["file_name"].lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                content_type = f'image/{file_info["file_name"].split(".")[-1].lower()}'

            # Properly encode the filename for Content-Disposition header
            filename = file_info["file_name"]
            encoded_filename = filename.encode('utf-8').decode('latin-1')

            return StreamingResponse(
                io.BytesIO(response['Body'].read()),
                media_type=content_type,
                headers={
                    'Content-Disposition': f'{disposition}; filename*=UTF-8\'\'{encoded_filename}',
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache',
                }
            )
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == 'NoSuchKey':
                logger.error(f"File not found in S3: {s3_key}")
                raise HTTPException(status_code=404, detail="File not found in storage")
            else:
                logger.error(f"S3 error: {str(e)}")
                raise HTTPException(status_code=500, detail="Error retrieving file from storage")

    except Exception as e:
        logger.error(f"Error in view_file: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete-file/", dependencies=[Depends(require_permission("documents.delete"))])
async def delete_file(
        file_id: str = Form(..., description="File ID to delete"),
        folder_id: Optional[str] = Form(None,
                                        description="Optional folder ID (locker_id or client_id) containing the file"),
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Delete a file from both S3 and the database.
    If folder_id is not provided, it will be fetched from the database.
    """
    try:
        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')

        # Get file information from database
        user_files = get_files_by_user(user_id)
        file_info = next((file for file in user_files if file['file_id'] == UUID(file_id)), None)

        if not file_info:
            raise HTTPException(status_code=404, detail="File not found")

        # Get the folder_id from database if not provided
        db_folder_id = file_info['lockr_id'] or file_info['client_id']
        if not db_folder_id:
            raise HTTPException(status_code=400, detail="File is not associated with any folder")

        # If folder_id is provided, verify it matches the database
        if folder_id and db_folder_id != folder_id:
            raise HTTPException(status_code=400, detail="Provided folder_id does not match the file's folder")

        # Delete file from S3
        await delete_file_from_s3(
            user_id=user_id,
            file_name=file_info['file_name'],
            folder_id=db_folder_id
        )

        # Step 1: Retrieve all matching vectors using metadata filter
        query_results = index.query(
            vector=[0.0] * 1536,  # Dummy vector (assuming text-embedding-3-small)
            top_k=10000,  # Large value to fetch all matches
            filter={
                "fileName": file_info['file_name'],
                "folder": str(db_folder_id),
                "user_id": user_id
            }
        )

        # Step 2: Extract all vector IDs
        ids_to_delete = [match["id"] for match in query_results.get("matches", [])]

        # Optional: Print how many vectors were found
        print(f"Found {len(ids_to_delete)} vectors matching the filter.")

        # If needed, delete the fetched vector IDs
        if ids_to_delete:
            index.delete(ids=ids_to_delete)

        # Delete file record from database and get associated chunks
        connection = create_connection()
        if connection:
            try:
                cursor = connection.cursor()

                delete_chunks_query = "DELETE FROM CHUNKS WHERE document_id = %s AND folder_id = %s"
                cursor.execute(delete_chunks_query, (file_info['file_name'], str(db_folder_id)))

                # Delete file record
                delete_query = "DELETE FROM FILES WHERE file_id = %s AND user_id = %s"
                cursor.execute(delete_query, (str(file_id), user_id))

                connection.commit()

                return {
                    "message": "File deleted successfully",
                    "file_id": file_id,
                    "file_name": file_info['file_name'],
                    "folder_id": db_folder_id
                }

            except Exception as e:
                connection.rollback()
                raise HTTPException(status_code=500, detail=f"Error deleting file from database: {str(e)}")
            finally:
                cursor.close()
                connection.close()
        else:
            raise HTTPException(status_code=500, detail="Failed to connect to database")

    except Exception as e:
        logger.error(f"Error in delete_file: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


def calculate_text_similarity(file_embedding, chunks, user_id):
    """
    Calculate content similarity using both vector similarity and text chunks.
    Returns a score between 0 and 1.
    """
    if not chunks:
        return 0.0
    
    # Get embeddings for all chunks and calculate max similarity
    max_score = 0
    for chunk in chunks:
        chunk_embedding = get_text_embedding(chunk["content"])
        similarity = index.query(
            vector=file_embedding,
            top_k=1,
            vector_to_match=chunk_embedding
        )
        score = similarity["matches"][0]["score"] if similarity["matches"] else 0
        max_score = max(max_score, score)
    
    return max_score

def calculate_name_similarity(filename1, filename2):
    """
    Calculate similarity between two filenames.
    Returns a score between 0 and 1.
    """
    # Remove extension and convert to lowercase
    name1 = filename1.rsplit('.', 1)[0].lower()
    name2 = filename2.rsplit('.', 1)[0].lower()
    
    # Convert special characters to spaces
    name1 = ''.join(c if c.isalnum() else ' ' for c in name1)
    name2 = ''.join(c if c.isalnum() else ' ' for c in name2)
    
    # Split into words
    words1 = set(name1.split())
    words2 = set(name2.split())
    
    # Calculate Jaccard similarity
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    
    return intersection / union if union > 0 else 0

def get_file_category(filename):
    """
    Get general category of file based on extension.
    """
    ext = filename.rsplit('.', 1)[-1].lower()
    
    document_types = {'pdf', 'doc', 'docx', 'txt', 'rtf'}
    spreadsheet_types = {'xlsx', 'xls', 'csv'}
    image_types = {'jpg', 'jpeg', 'png', 'gif', 'bmp'}
    presentation_types = {'ppt', 'pptx'}
    
    if ext in document_types:
        return 'document'
    elif ext in spreadsheet_types:
        return 'spreadsheet'
    elif ext in image_types:
        return 'image'
    elif ext in presentation_types:
        return 'presentation'
    else:
        return 'other'

def calculate_destination_similarity(file_info, destination_files, user_id):
    """
    Calculate overall similarity between a file and a destination's files.
    Returns a tuple of (similarity_score, matching_file_name).
    """
    max_score = 0
    best_matching_file = None
    
    # Extract file info
    filename = file_info["filename"]
    file_embedding = file_info["embedding"]
    file_category = get_file_category(filename)
    
    for dest_file in destination_files:
        # Skip if file categories don't match
        if get_file_category(dest_file["file_name"]) != file_category:
            continue
            
        # Calculate name similarity
        name_sim = calculate_name_similarity(filename, dest_file["file_name"])
        
        # Get chunks and calculate content similarity
        folder_id_str = str(dest_file["folder_id"])
        chunks = retrieve_chunks_from_db("", folder_id_str, str(user_id))
        content_sim = calculate_text_similarity(file_embedding, chunks, user_id)
        
        # Weighted combination of similarities
        # Give more weight to content similarity if we have it
        if content_sim > 0:
            combined_score = 0.7 * content_sim + 0.3 * name_sim
        else:
            combined_score = name_sim
            
        if combined_score > max_score:
            max_score = combined_score
            best_matching_file = dest_file["file_name"]
    
    return max_score, best_matching_file

def calculate_text_similarity_batch(file_embedding, chunks_list):
    """
    Calculate content similarity for multiple chunks in a batch.
    Returns a score between 0 and 1.
    """
    if not chunks_list:
        return 0.0
    
    # Combine all chunks into one query for efficiency
    chunk_texts = [chunk["content"] for chunk in chunks_list]
    try:
        # Single batch embedding call instead of multiple calls
        chunk_embeddings = [get_text_embedding(text) for text in chunk_texts]
        
        # Single batch similarity query
        similarity = index.query(
            vector=file_embedding,
            top_k=len(chunk_embeddings),
            vector_to_match=chunk_embeddings[0] if chunk_embeddings else None
        )
        
        # Get max similarity score
        max_score = max(match["score"] for match in similarity["matches"]) if similarity["matches"] else 0
        return max_score
    except Exception as e:
        logger.error(f"Error in similarity calculation: {str(e)}")
        return 0.0

def calculate_destination_similarity_batch(file_info, destination_files, chunks_by_folder):
    """
    Calculate overall similarity between a file and destination files in batch.
    Returns a tuple of (similarity_score, matching_file_name).
    
    Args:
        file_info: Dictionary containing uploaded file info with 'filename' and 'embedding'
        destination_files: List of dictionaries containing database file info with 'filename' and 'folder_id'
        chunks_by_folder: Dictionary mapping folder IDs to their text chunks
    """
    max_score = 0
    best_matching_file = None
    
    # Extract file info
    filename = file_info["filename"]
    file_embedding = file_info["embedding"]
    file_category = get_file_category(filename)
    
    # Group files by folder for batch processing
    files_by_folder = {}
    for dest_file in destination_files:
        folder_id = dest_file["folder_id"]
        if folder_id not in files_by_folder:
            files_by_folder[folder_id] = []
        files_by_folder[folder_id].append(dest_file)
    
    # Process each folder's files in batch
    for folder_id, folder_files in files_by_folder.items():
        # Filter files by category
        matching_category_files = [
            f for f in folder_files 
            if get_file_category(f["filename"]) == file_category  # Use 'filename' instead of 'file_name'
        ]
        
        if not matching_category_files:
            continue
        
        # Calculate name similarities in batch
        name_similarities = [
            (calculate_name_similarity(filename, f["filename"]), f["filename"])  # Use 'filename' instead of 'file_name'
            for f in matching_category_files
        ]
        
        # Get chunks for this folder
        folder_chunks = chunks_by_folder.get(str(folder_id), [])
        
        # Calculate content similarity once per folder
        content_sim = calculate_text_similarity_batch(file_embedding, folder_chunks)
        
        # Combine scores for each file
        for name_sim, matching_filename in name_similarities:
            if content_sim > 0:
                combined_score = 0.7 * content_sim + 0.3 * name_sim
            else:
                combined_score = name_sim
                
            if combined_score > max_score:
                max_score = combined_score
                best_matching_file = matching_filename
    
    return max_score, best_matching_file

@router.post("/smart-upload/", dependencies=[Depends(require_permission("documents.create"))])
async def smart_upload_files(
    files: List[UploadFile] = File(...),
    destination_type: str = Form(..., description="Type of destination: 'LOCKR' or 'CLIENT'"),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Intelligently routes files to appropriate lockers or clients based on content analysis.
    Creates new lockers/clients if needed.
    """
    try:
        # Validate destination type
        destination_type = destination_type.upper()
        if destination_type not in ["LOCKR", "CLIENT"]:
            raise HTTPException(status_code=400, detail="destination_type must be either 'LOCKR' or 'CLIENT'")

        # Verify JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')

        # Get existing lockers and clients
        user_items = get_lockers_and_clients_by_user(user_id)
        destinations = [item for item in user_items if item["type"] == destination_type]

        # Pre-fetch all destination files and chunks in batch
        destination_files = {}
        chunks_by_folder = {}
        
        # Create tasks for parallel fetching
        async def fetch_destination_data(dest):
            """Fetch files and chunks for a destination."""
            if destination_type == "LOCKR":
                files = get_files_by_locker(dest["id"])
            else:
                files = get_files_by_client(dest["id"])

            if files:
                # Convert database rows to file objects with the correct attributes
                processed_files = []
                for file in files:
                    processed_file = {
                        "filename": file["file_name"],  # Map file_name to filename
                        "folder_id": dest["id"],  # Add folder_id
                        "content_type": file["file_type"],  # Map file_type to content_type
                        "size": file["file_size"] if "file_size" in file else 0,  # Add size if available
                        "last_modified": file["file_last_modified"] if "file_last_modified" in file else None,  # Add last_modified if available
                        "file_link": file["file_link"] if "file_link" in file else None  # Add file_link if available
                    }
                    processed_files.append(processed_file)

                # Get chunks for this folder
                chunks = retrieve_chunks_from_db("", str(dest["id"]), str(user_id))
                return dest["id"], processed_files, chunks
            return dest["id"], [], []
        
        # Fetch data for all destinations in parallel
        fetch_tasks = [fetch_destination_data(dest) for dest in destinations]
        destination_data = await asyncio.gather(*fetch_tasks)
        
        # Organize fetched data
        for dest_id, files_data, chunks in destination_data:
            if files_data:
                destination_files[dest_id] = files_data
            if chunks:
                chunks_by_folder[str(dest_id)] = chunks

        # Process each uploaded file
        results = []
        for upload_file in files:
            try:
                # Ensure upload_file is an UploadFile object
                if isinstance(upload_file, dict):
                    logger.error(f"Received dictionary instead of UploadFile: {upload_file}")
                    raise HTTPException(status_code=400, detail="Invalid file format received")

                # Read file content and extract text
                file_content = await upload_file.read()
                file_type = upload_file.content_type
                text_content = ""

                # Extract text based on file type
                if file_type.startswith("image/"):
                    text_content = extract_text_from_image(file_content)
                elif upload_file.filename.lower().endswith('.pdf'):
                    text_content = extract_text_from_pdf(file_content)
                elif upload_file.filename.lower().endswith('.docx'):
                    text_content = extract_text_from_docx(file_content)
                elif upload_file.filename.lower().endswith('.xlsx'):
                    text_content = extract_text_from_xlsx(file_content)
                else:
                    try:
                        text_content = file_content.decode("utf-8")
                    except:
                        text_content = ""

                # Get embeddings for file content
                file_embedding = get_text_embedding(text_content) if text_content else None
                
                # Prepare file info for similarity calculation
                file_info = {
                    "filename": upload_file.filename,
                    "embedding": file_embedding,
                    "content": text_content
                }

                # Find best matching destination
                best_match = None
                best_score = -1
                best_matching_file = None

                # Calculate similarities for all destinations in batch
                for dest in destinations:
                    dest_files = destination_files.get(dest["id"], [])
                    if not dest_files:
                        continue

                    # Calculate similarity with this destination
                    score, matching_file = calculate_destination_similarity_batch(
                        file_info,
                        dest_files,
                        chunks_by_folder
                    )

                    if score > best_score:
                        best_score = score
                        best_match = dest["id"]
                        best_matching_file = matching_file

                # If no good match found or score too low, create new destination
                if best_score < 0.5 or not best_match:
                    # Create new destination with a clean name from the filename
                    base_name = upload_file.filename.split('.')[0]  # Remove extension
                    # Convert snake_case or kebab-case to Title Case
                    base_name = ' '.join(word.capitalize() for word in base_name.replace('-', '_').split('_'))
                    # Limit length while preserving whole words
                    if len(base_name) > 30:
                        words = base_name[:30].split()
                        base_name = ' '.join(words[:-1]) if len(words) > 1 else words[0]
                    
                    if destination_type == "LOCKR":
                        dest_id = add_new_lockr(user_id=user_id, name=base_name)
                    else:
                        dest_id = add_new_client(user_id=user_id, name=base_name)
                    best_match = dest_id

                # Upload file to the best match
                await upload_file.seek(0)  # Reset file pointer
                if destination_type == "LOCKR":
                    await upload_files(
                        lockr_id=best_match,
                        client_id=None,
                        files=[upload_file],
                        credentials=credentials
                    )
                else:
                    await upload_files(
                        lockr_id=None,
                        client_id=best_match,
                        files=[upload_file],
                        credentials=credentials
                    )

                results.append({
                    "file_name": upload_file.filename,
                    "destination_id": best_match,
                    "destination_type": destination_type,
                    "similarity_score": best_score,
                    "matching_file": best_matching_file,
                    "is_new": best_score < 0.5
                })

            except Exception as e:
                logger.error(f"Error processing file {getattr(upload_file, 'filename', 'unknown')}: {str(e)}")
                logger.error(traceback.format_exc())
                results.append({
                    "file_name": getattr(upload_file, 'filename', 'unknown'),
                    "error": str(e)
                })

        return {"results": results}

    except Exception as e:
        logger.error(f"Error in smart_upload_files: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/search-documents/", dependencies=[Depends(require_permission("documents.read"))])
async def search_documents(
    query: str = Query(..., description="Search query text"),
    top_k: int = Query(10, description="Maximum number of results to return"),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Search for documents using semantic search and return relevant chunks.
    Only returns results with positive relevance scores.
    """
    try:
        # Verify JWT token and get user_id
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        user_id = user_data.get('uid')

        # Get relevant chunks using BM25 search
        chunks = retrieve_chunks_from_db(query, None, user_id, top_k)
        logger.info(f"Retrieved {len(chunks)} chunks from BM25 search")

        # Get all files for the user to map file_name to file_id
        user_files = get_files_by_user(user_id)
        file_name_to_id = {file['file_name']: file['file_id'] for file in user_files}
        file_name_to_link = {file['file_name']: file['file_link'] for file in user_files}
        logger.info(f"Found {len(user_files)} files for user {user_id}")

        # Group chunks by document and create previews
        document_previews = {}
        for chunk in chunks:
            # Skip chunks with negative relevance scores
            if chunk["rerank_score"] < 0:
                continue
                
            filename = chunk["filename"]
            if filename not in document_previews:
                document_previews[filename] = {
                    "document_id": filename,
                    "file_id": file_name_to_id.get(filename),
                    "file_link": file_name_to_link.get(filename),
                    "preview": chunk["text"],
                    "relevance_score": float(chunk["rerank_score"]),
                    "folder_id": chunk.get("folder_id")
                }
            else:
                # Append content to existing preview
                document_previews[filename]["preview"] += "\n" + chunk["text"]
                # Update relevance score if this chunk has a higher score
                if chunk["rerank_score"] > document_previews[filename]["relevance_score"]:
                    document_previews[filename]["relevance_score"] = float(chunk["rerank_score"])

        # Convert to list and sort by relevance score
        results = list(document_previews.values())
        results.sort(key=lambda x: x["relevance_score"], reverse=True)

        response = {
            "query": query,
            "total_results": len(results),
            "documents": results
        }
        
        # Log the response structure
        return response

    except Exception as e:
        logger.error(f"Error in search_documents: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))