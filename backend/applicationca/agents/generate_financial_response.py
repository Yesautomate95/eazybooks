from typing import List, Union, Annotated, Dict, Any
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages
from dotenv import load_dotenv
from langchain_community.tools.tavily_search import TavilySearchResults
from langgraph.prebuilt import ToolNode, tools_condition
from applicationca.chatbot.tools.langgraph_tools import calculate, retrive_document, get_document_content
from utils.sse_emitter import SSEEmitter

load_dotenv()

tool = TavilySearchResults(max_results=2)
tools = [tool, calculate, get_document_content, retrive_document]
tool_node = ToolNode(tools=tools)
OPENAI_MODEL = "gpt-4.1-mini"


def get_llm():
    return ChatOpenAI(
        model=OPENAI_MODEL
    ).bind_tools(tools)


class GeneratFinancialResponse(TypedDict):
    user_id: str
    messages: Annotated[list, add_messages]
    folder_id: str
    financial_response: str
    audit_feedback: str
    query: str
    relevant_files: List[str]
    search_context: str
    previous_messages: str
    sse_emitter: SSEEmitter
    # Required fields for tool handling
    tool_calls: List[Dict[str, Any]]
    tool_call_id: str
    # Add a counter to prevent infinite loops
    tool_call_count: int


# --- Custom condition to limit tool calls ---
def limited_tools_condition(state):
    """Modified tool condition that checks the number of tool calls to prevent loops."""
    # First increment the counter
    state["tool_call_count"] += 1

    # If we've made too many tool calls, force an END
    if state["tool_call_count"] >= 5:  # Limit to 5 tool calls
        if state["sse_emitter"]:
            # We can't await here, so we can't use emit
            pass  # You might want to log this situation
        return END

    # Otherwise use the regular tools_condition
    return tools_condition(state)


# --- Node Functions ---
async def generate_financial_response(state: GeneratFinancialResponse) -> GeneratFinancialResponse:
    """Generates a professional CA-quality financial response."""
    llm = get_llm()
    sse_emitter = state["sse_emitter"]

    # Instead of appending to existing messages, create a new structured conversation
    messages = state["messages"] or []

    # Only add system message if this is the first message
    if len(messages) == 0:
        # System message with definition and overall instructions
        system_message = """You are an Accounting and Financial Response Generator specialized in providing professional CA and audit-quality answers.

        Your responses must include:
        - Precise financial calculations following standard accounting methodologies
        - Correct application of accounting principles and standards (GAAP, IFRS, etc. as appropriate)
        - Professional tabular formatting for financial data (with aligned decimal points and appropriate currency notations)
        - Citations of relevant accounting standards or tax provisions when applicable
        - Clear breakdown of complex financial concepts in audit-ready presentation
        - Proper terminology used by CA professionals
        - Appropriate disclaimers where necessary
        
        IMPORTANT: All calculations must be performed with precision and should be cross-verified for accuracy.
        Format your answer as a CA professional would present it in a formal report or advisory document.
        """

        messages.append({"role": "system", "content": system_message})

        # Add context as assistant message
        context_message = "I have the following information to help answer your query:\n\n"

        # Include search context
        if state["search_context"]:
            context_message += f"SEARCH RESULTS:\n{state['search_context']}\n\n"

        # Include relevant documents
        if state["relevant_files"]:
            context_message += f"AVAILABLE DOCUMENTS: {', '.join(state['relevant_files'])}\n\n"

        # Include previous conversation history
        if state["previous_messages"]:
            context_message += f"PREVIOUS CONVERSATION:\n{state['previous_messages']}\n\n"

        # Include feedback if available
        if state.get("audit_feedback"):
            context_message += f"FEEDBACK TO ADDRESS:\n{state['audit_feedback']}\n\n"

        messages.append({"role": "assistant", "content": context_message})

        # Add the user query as highest priority
        messages.append({"role": "user", "content": state["query"]})

    # If we've called too many tools, add a forceful instruction
    if state["tool_call_count"] >= 3:
        messages.append({
            "role": "user",
            "content": "You have called multiple tools already. Please provide your final answer now without calling any more tools. Synthesize what you have learned and give your best financial response."
        })

    # Additional thinking update
    if sse_emitter:
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Generating financial response...."
        })

    ai_message = llm.invoke(messages)
    messages.append(ai_message)

    # Send completion update
    if sse_emitter:
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Response draft complete. Moving to verification..."
        })

    result = {
        **state,
        "messages": messages,
        "financial_response": ai_message.content
    }

    return result


def create_workflow_financial():
    workflow = StateGraph(GeneratFinancialResponse)
    workflow.add_node("tools", tool_node)
    workflow.add_node("generate_financial_response", generate_financial_response)

    # Add conditional edges with our custom condition that limits tool calls
    workflow.add_conditional_edges(
        "generate_financial_response",
        limited_tools_condition,
        {
            "tools": "tools",  # Route to tools node when tool calls are detected
            END: END  # End the workflow when no tool calls are detected or max calls reached
        }
    )

    # Any time a tool is called, we return to the chatbot to decide the next step
    workflow.add_edge("tools", "generate_financial_response")

    workflow.set_entry_point("generate_financial_response")

    return workflow


class FinancialRunner:
    def __init__(self):
        self.workflow = create_workflow_financial().compile()

    async def start_new_task(self, user_id: str, folder_id: str, audit_feedback: str, query: str,
                             relevant_files: List[str], search_context: str, previous_messages: str,
                             sse_emitter: SSEEmitter):
        initial_state = {
            "user_id": user_id,
            "messages": [],
            "financial_response": "",
            "audit_feedback": audit_feedback,
            "folder_id": folder_id,
            "query": query,
            "relevant_files": relevant_files,
            "search_context": search_context,
            "previous_messages": previous_messages,
            "sse_emitter": sse_emitter,
            # Initialize required fields for tool handling
            "tool_calls": [],
            "tool_call_id": "",
            # Initialize the tool call counter to prevent loops
            "tool_call_count": 0
        }

        result = self.workflow.astream(initial_state, stream_mode="values")
        answer = ""
        async for event in result:
            if "financial_response" in event:
                answer = event["financial_response"]
        return answer