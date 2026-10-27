import json

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Form
from utils.auth_utils import verify_jwt
from fastapi.responses import StreamingResponse, JSONResponse
from utils.stream import stream_generator
from applicationca.contoller.vector import search
from applicationca.services.BM25 import retrieve_chunks_from_db
from applicationca.chatbot.services.chat import ChatService
from applicationca.database.database_retrieval import get_user_chats, fetch_file_summaries, fetch_chunk_count
from applicationca.database.database_update import add_new_chat
from applicationca.services.Functions import get_last_messages
from applicationca.processingagent.agentsworkflow.specialized_agents import AccountingAgentRunner
from applicationca.agents.RagAgent import RagAgentRunner
from applicationca.processingagent.contants.agents_contants import TaskType
from applicationca.rbac import require_permission
import asyncio
chat_service = ChatService()


router = APIRouter()

# JWT Authentication Scheme
security = HTTPBearer()

@router.post("/get-all-chats", dependencies=[Depends(require_permission("chat.use"))])
async def get_all_chats(
client_id: str = Form(None),
lockr_id: str = Form(None),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Get all chats for the authenticated user.
    """
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    user_id = user_data.get('uid')
    chats = get_user_chats(user_id, client_id, lockr_id)
    return chats

@router.post("/chat", dependencies=[Depends(require_permission("advisor.use"))])
async def get_chat_response(
        chat_id: str = Form(...),
        message: str = Form(...),
        folder_id: str = Form(None),
        client_id: str = Form(None),
        lockr_id: str = Form(None),
        chatbot_id: str = Form(None),
        is_context_required: bool= Form(None),
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    print("In /chat")
    print(is_context_required)
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    user_id = user_data.get('uid')
    if(folder_id == None):
        folder_id = chatbot_id
    loop = asyncio.get_running_loop()

    # Run the synchronous function in an executor to avoid blocking the event loop.
    relevant_chunks_task = loop.run_in_executor(
        None, retrieve_chunks_from_db, message, folder_id, user_id, 20
    )
    # Directly call the asynchronous function.
    vector_chunks_task = search(message, user_id, folder_id)

    # Run both tasks concurrently.
    relevant_chunks, vector_chunks = await asyncio.gather(
        relevant_chunks_task, vector_chunks_task
    )

    relevant_texts = [
        f'file_name: {rc["filename"]}\ncontent: {rc["text"]}'
        for rc in relevant_chunks if rc["rerank_score"] > -3
    ]

    # Get text only from vector chunks with a positive rerank_score, including filename
    positive_vector_texts = [
        f'file_name: {vc["filename"]}\ncontent: {vc["text"]}'
        for vc in vector_chunks if vc["rerank_score"] > -3
    ]

    context_parts = relevant_texts + positive_vector_texts
    search_context = "\n\n".join(context_parts)
    user_message = ""
    if is_context_required:
        user_message+="Previous chats context: \n"
        user_message+=get_last_messages(user_id, chat_id)
        user_message+="\n\n"

    user_message += "User's query: \n"
    user_message += message
    user_message +="\n \n Internal parameters: \n"
    user_message += f"['user_id' : {user_id},  'folder_id': {folder_id}]"
    user_message += "\n \n Search context: \n"
    user_message += search_context

    response = chat_service.generate(chat_id, user_message, message, user_id, client_id, lockr_id)
    return StreamingResponse(stream_generator(response))

@router.post("/chat/langgraph", dependencies=[Depends(require_permission("advisor.use"))])
async def get_chat_response_langgraph(
        chat_id: str = Form(...),
        message: str = Form(...),
        folder_id: str = Form(None),
        client_id: str = Form(None),
        lockr_id: str = Form(None),
        chatbot_id: str = Form(None),
        is_context_required: bool= Form(None),
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    print("In /chat/langgraph")
    print(is_context_required)
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    user_id = user_data.get('uid')

    # if (folder_id == None):
    #     folder_id = chatbot_id

    async def event_generator():
        try:
            # Initialize the accounting agent
            agent_runner = RagAgentRunner()

            initial_data = fetch_file_summaries(user_id, folder_id)
            max_chunks = fetch_chunk_count(user_id, folder_id)

            # Start the task and get the SSE emitter
            emitter = await agent_runner.start_new_task(
                user_id=user_id,
                chat_id=chat_id,
                folder_id=folder_id,
                initial_data=initial_data,
                initial_message=message,
                max_chunks=max_chunks,
                previous_messages=get_last_messages(user_id, chat_id),
                client_id=client_id,
                lockr_id=lockr_id
            )

            # Stream events from the emitter
            async for event in emitter.stream():
                yield f"{json.dumps(event)}\n\n"

        except Exception as e:
            print(e)
            yield f"{json.dumps({'error': str(e)})}\n\n"
        finally:
            if 'emitter' in locals():
                emitter.stop()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@router.post("/chat/langgraph/advance", dependencies=[Depends(require_permission("advisor.use"))])
async def get_chat_response_advance(
        chat_id: str = Form(...),
        message: str = Form(...),
        folder_id: str = Form(None),
        client_id: str = Form(None),
        lockr_id: str = Form(None),
        chatbot_id: str = Form(None),
        is_context_required: bool = Form(None),
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    print("In /chat/langgraph/advance")
    print(is_context_required)
    token = credentials.credentials
    user_data = verify_jwt(token)
    if not user_data:
        return JSONResponse({"error": "Invalid token"}, status_code=401)

    user_id = user_data.get('uid')

    initial_data = {
        "company_name": "Example Corp",
        "reporting_period": "FY 2023",
        "assets": {
            "current_assets": {
                "cash": 50000,
                "accounts_receivable": 75000,
                "inventory": 125000
            },
            "non_current_assets": {
                "property_plant_equipment": 350000,
                "accumulated_depreciation": -50000,
                "investments": 100000
            }
        },
        "liabilities": {
            "current_liabilities": {
                "accounts_payable": 45000,
                "short_term_loans": 25000,
                "taxes_payable": 15000
            },
            "non_current_liabilities": {
                "long_term_loans": 200000,
                "bonds_payable": 100000
            }
        },
        "equity": {
            "common_stock": 100000,
            "retained_earnings": 165000
        }
    }

    async def event_generator():
        try:
            # Initialize the accounting agent
            agent_runner = AccountingAgentRunner()

            # Start the task and get the SSE emitter
            emitter = await agent_runner.start_new_task(
                task_type=TaskType.BALANCE_SHEET,
                initial_data=initial_data,
                initial_message=message
            )

            # Stream events from the emitter
            async for event in emitter.stream():
                yield f"{json.dumps(event)}\n\n"

        except Exception as e:
            yield f"{json.dumps({'error': str(e)})}\n\n"
        finally:
            if 'emitter' in locals():
                emitter.stop()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )
