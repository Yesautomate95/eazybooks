from fastapi import Query

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from typing import Optional

from pydantic import BaseModel

from applicationca.database.database_retrieval import (
    get_lockers_and_clients_by_user, 
    get_files_by_locker, 
    get_files_by_client, 
    get_file_info, 
    get_chats_by_locker, 
    get_chats_by_client, 
    get_chat_info, 
    get_all_user_chats,
    get_files_by_user,
    get_chats_by_user,
    get_files_by_chat,
    get_user_dashboard_data,
    get_clients_with_details_optimized,
    get_lockers_with_details_optimized
)
from applicationca.database.database_update import add_new_user, add_new_lockr, add_new_client, add_new_chat, delete_lockr, delete_client
from utils.auth_utils import verify_jwt
from applicationca.rbac import require_permission

router = APIRouter()
security = HTTPBearer()


@router.post("/add-user")
async def add_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        # Extract token from Authorization header
        token = credentials.credentials

        # Verify Firebase JWT token and get user data
        user_data = verify_jwt(token)
        user_id = user_data["uid"]  # Extract user ID from token

        # Add the user to the database (no UUID conversion needed)
        add_new_user(user_id=user_id)

        return {"message": "User added successfully", "user_id": user_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class LockerRequest(BaseModel):
    name: str

@router.post("/create-locker", dependencies=[Depends(require_permission("documents.create"))])
async def create_locker(data: LockerRequest,
                        credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')
        lockr_id = add_new_lockr(user_id=user_id, name=data.name)
        print({"message": "Locker created successfully", "lockr_id": lockr_id, "user_id": user_id})
        return {"message": "Locker created successfully", "lockr_id": lockr_id, "user_id": user_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ClientRequest(BaseModel):
    name: str


@router.post("/create-client", dependencies=[Depends(require_permission("clients.create"))])
async def create_client(data: ClientRequest,
                        credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')
        client_id = add_new_client(user_id=user_id, name=data.name)

        # Return success response
        return {
            "message": "Client created successfully",
            "client_id": client_id,
            "user_id": user_id,
            "name": data.name
        }

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ChatRequest(BaseModel):
    user_id: str
    client_id: Optional[str] = None
    lockr_id: Optional[str] = None
    chat_name: str
    chat_description: Optional[str] = None
    chat_type: Optional[str] = None


@router.post("/create-chat", dependencies=[Depends(require_permission("chat.use"))])
async def create_chat(data: ChatRequest):
    try:
        if not data.client_id and not data.lockr_id:
            raise HTTPException(status_code=400, detail="Either client_id or lockr_id must be provided")
        
        chat_id = add_new_chat(
            user_id=data.user_id,
            client_id=data.client_id,
            lockr_id=data.lockr_id,
            chat_name=data.chat_name,
            chat_description=data.chat_description,
            chat_type=data.chat_type
        )

        # Return success response
        return {
            "message": "Chat created successfully",
            "chat_id": chat_id,
            "user_id": data.user_id,
            "chat_name": data.chat_name
        }

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-lockers", dependencies=[Depends(require_permission("documents.read"))])
async def get_lockers(user_id:str=Query(...)):
    try:
        # Ensure the user_id is a valid UUID
        user_uuid = user_id

        # Fetch lockers and clients associated with the user
        user_items = get_lockers_and_clients_by_user(user_uuid)

        # Filter lockers only
        lockers = [
            {"lockr_id": item["id"], "name": item["name"]}
            for item in user_items if item["type"] == "LOCKR"
        ]

        return {"lockers": lockers}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/get-clients", dependencies=[Depends(require_permission("clients.read"))])
async def get_clients(user_id: str = Query(...)):
    try:
        # Fetch lockers and clients associated with the user
        user_items = get_lockers_and_clients_by_user(user_id)

        # Filter clients only
        clients = [
            {"client_id": item["id"], "name": item["name"]}
            for item in user_items if item["type"] == "CLIENT"
        ]

        return {"clients": clients}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/get-files-by-locker", dependencies=[Depends(require_permission("documents.read"))])
async def get_files_by_locker_endpoint(lockr_id: str = Query(...)):
    try:
        # Fetch files associated with the locker
        files = get_files_by_locker(lockr_id)

        if files is None:
            raise HTTPException(status_code=404, detail="No files found for this locker.")

        return {"files": files}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-files-by-client", dependencies=[Depends(require_permission("documents.read"))])
async def get_files_by_client_endpoint(client_id: str = Query(...)):
    try:
        # Fetch files associated with the client
        files = get_files_by_client(client_id)

        if files is None or len(files) == 0:
            raise HTTPException(status_code=404, detail="No files found for this client.")

        return {"files": files}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-files-by-user", dependencies=[Depends(require_permission("documents.read"))])
async def get_files_by_user_endpoint(user_id: str = Query(...)):
    try:
        # Fetch files associated with the user
        files = get_files_by_user(user_id)

        if files is None or len(files) == 0:
            raise HTTPException(status_code=404, detail="No files found for this user.")

        return {"files": files}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-files-by-chat", dependencies=[Depends(require_permission("documents.read"))])
async def get_files_by_chat_endpoint(chat_id: str = Query(...)):
    try:
        # Fetch files associated with the chat
        files = get_files_by_chat(chat_id)

        if files is None or len(files) == 0:
            raise HTTPException(status_code=404, detail="No files found for this chat.")

        return {"files": files}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-file-info", dependencies=[Depends(require_permission("documents.read"))])
async def get_file_info_endpoint(file_id: str = Query(...)):
    try:
        # Fetch file information
        file_info = get_file_info(file_id)

        if not file_info:
            raise HTTPException(status_code=404, detail="File not found.")

        return {"file": file_info}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-chats-by-locker", dependencies=[Depends(require_permission("chat.use"))])
async def get_chats_by_locker_endpoint(lockr_id: str = Query(...)):
    try:
        # Fetch chats associated with the locker
        chats = get_chats_by_locker(lockr_id)

        if chats is None:
            raise HTTPException(status_code=404, detail="No chats found for this locker.")

        return {"chats": chats}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-chats-by-client", dependencies=[Depends(require_permission("chat.use"))])
async def get_chats_by_client_endpoint(client_id: str = Query(...)):
    try:
        # Fetch chats associated with the client
        chats = get_chats_by_client(client_id)

        if chats is None or len(chats) == 0:
            raise HTTPException(status_code=404, detail="No chats found for this client.")

        return {"chats": chats}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-chats-by-user", dependencies=[Depends(require_permission("chat.use"))])
async def get_chats_by_user_endpoint(user_id: str = Query(...)):
    try:
        # Fetch chats associated with the user
        chats = get_chats_by_user(user_id)

        if chats is None or len(chats) == 0:
            raise HTTPException(status_code=404, detail="No chats found for this user.")

        return {"chats": chats}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-chat-info", dependencies=[Depends(require_permission("chat.use"))])
async def get_chat_info_endpoint(chat_id: str = Query(...)):
    try:
        # Fetch chat information
        chat_info = get_chat_info(chat_id)

        if not chat_info:
            raise HTTPException(status_code=404, detail="Chat not found.")

        return {"chat": chat_info}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-all-user-chats", dependencies=[Depends(require_permission("chat.use"))])
async def get_all_user_chats_endpoint(user_id: str = Query(...)):
    try:
        # Fetch all chats for the user
        chats = get_all_user_chats(user_id)

        if chats is None:
            raise HTTPException(status_code=404, detail="No chats found for this user.")

        return {"chats": chats}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-user-dashboard", dependencies=[Depends(require_permission("clients.read"))])
async def get_user_dashboard_endpoint(user_id: str = Query(...)):
    """
    Get all data needed for a user's dashboard in a single API call.
    This is more efficient than making separate calls for each type of data.
    
    Args:
        user_id: The ID of the user
        
    Returns:
        A dictionary containing all dashboard data including:
        - items: Lockers and clients
        - recent_files: Recent files
        - recent_chats: Recent chats
        - counts: Counts of lockers, clients, files, and chats
    """
    try:
        # Get all dashboard data in a single call
        dashboard_data = get_user_dashboard_data(user_id)
        
        if dashboard_data is None:
            raise HTTPException(status_code=404, detail="Failed to retrieve dashboard data.")
            
        return dashboard_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-lockers-with-details", dependencies=[Depends(require_permission("documents.read"))])
async def get_lockers_with_details(credentials: HTTPAuthorizationCredentials = Depends(security)):

    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')
        # Use the optimized function to get lockers with details in fewer database calls
        lockers_with_details = get_lockers_with_details_optimized(user_id)
        
        if lockers_with_details is None:
            # This indicates a database error occurred
            raise HTTPException(
                status_code=500, 
                detail="Database error occurred while retrieving locker details"
            )
        
        if len(lockers_with_details) == 0:
            # This indicates no lockers were found for the user
            return {"lockers": []}
            
        return {"lockers": lockers_with_details}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    except Exception as e:
        import traceback
        print("Error fetching lockers:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-clients-with-details", dependencies=[Depends(require_permission("clients.read"))])
async def get_clients_with_details(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')
        # Use the optimized function to get clients with details in fewer database calls
        clients_with_details = get_clients_with_details_optimized(user_id)
        
        if clients_with_details is None:
            # This indicates a database error occurred
            raise HTTPException(
                status_code=500, 
                detail="Database error occurred while retrieving client details"
            )
        
        if len(clients_with_details) == 0:
            # This indicates no clients were found for the user
            return {"clients": []}
            
        return {"clients": clients_with_details}

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/delete-locker", dependencies=[Depends(require_permission("documents.delete"))])
async def delete_locker_endpoint(lockr_id: str = Query(...),
                               credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Delete a locker and all its associated data (files and chats).
    Requires authentication.
    """
    try:
        # Verify the JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')
        
        # Delete the locker and all associated data
        success = await delete_lockr(lockr_id, user_id)
        
        if success:
            return {
                "message": "Locker and all associated data deleted successfully",
                "lockr_id": lockr_id
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to delete locker and associated data")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/delete-client", dependencies=[Depends(require_permission("clients.delete"))])
async def delete_client_endpoint(client_id: str = Query(...),
                               credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Delete a client and all its associated data (files and chats).
    Requires authentication.
    """
    try:
        print(client_id)
        # Verify the JWT token
        token = credentials.credentials
        user_data = verify_jwt(token)
        if not user_data:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_id = user_data.get('uid')
        
        # Delete the client and all associated data
        success = await delete_client(client_id, user_id)
        
        if success:
            return {
                "message": "Client and all associated data deleted successfully",
                "client_id": client_id
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to delete client and associated data")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))