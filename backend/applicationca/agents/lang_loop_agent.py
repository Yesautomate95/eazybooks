import asyncio
import os
from typing import Dict, List, Any, TypedDict, Optional, Annotated, Literal, Union
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, FunctionMessage
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from utils.sse_emitter import SSEEmitter

load_dotenv()
from config.settings import settings

# --- Constants ---
APP_NAME = "financial_advisor_bot"
OPENAI_MODEL = settings.OPENAI_MODEL


# --- State Definition ---
class AgentState(TypedDict):
    user_id: str
    messages: List[Union[HumanMessage, AIMessage, SystemMessage, FunctionMessage]]
    folder_id: str
    user_query: str
    search_context: str
    financial_response: str
    audit_feedback: str
    final_response: str
    iterations: int
    sse_emitter: SSEEmitter


# --- Helper Functions ---
def get_llm():
    return ChatOpenAI(
        model=OPENAI_MODEL,
        temperature=0
    )


# --- Node Functions ---
async def generate_financial_response(state: AgentState) -> AgentState:
    """Generates a professional CA-quality financial response."""
    llm = get_llm()

    messages = state["messages"]
    # Build the prompt template
    task_prompt = f"""
    You are an Accounting and Financial Response Generator specialized in providing professional CA and audit-quality answers.

    1. Analyze the user query
    2. Incorporate any relevant information from the search context
    3. If this is your first response, generate a comprehensive, professionally formatted answer that adheres to CA standards
    4. If there is feedback, meticulously revise your answer addressing all points raised

    Your response must include:
    - Precise financial calculations following standard accounting methodologies
    - Correct application of accounting principles and standards (GAAP, IFRS, etc. as appropriate)
    - Professional tabular formatting for financial data (with aligned decimal points and appropriate currency notations)
    - Citations of relevant accounting standards or tax provisions when applicable
    - Clear breakdown of complex financial concepts in audit-ready presentation
    - Proper terminology used by CA professionals
    - Appropriate disclaimers where necessary

    Ensure all numerical data is cross-verified and consistent throughout your response.
    Format your answer as a CA professional would present it in a formal report or advisory document.

    User Query: {state["user_query"]}

    Search Context: {state["search_context"]}

    """

    if state.get("audit_feedback"):
        task_prompt += f"\n\nPrevious Feedback to Address:\n{state['audit_feedback']}"

    task_prompt += "\n\nOutput only your professional CA-standard response."

    # Add feedback section if available
    feedback_section = ""
    if state.get("audit_feedback"):
        feedback_section = f"Previous Feedback to Address:\n{state['audit_feedback']}"

    # Set up the prompt with values
    messages.append(HumanMessage(content=task_prompt))

    ai_message = llm.invoke(messages)
    messages.append(ai_message)

    result = {
        **state,
        "messages": messages,
        "financial_response": ai_message.content,
        "next_action": "verify_response"
    }

    return result


async def verify_response(state: AgentState) -> AgentState:
    """Verifies the financial response against CA standards."""
    llm = get_llm()

    messages = state["messages"]

    task_prompt = f"""
    You are a CA Standards Verification Specialist with expertise in accounting standards, auditing procedures, and professional documentation.

    Review the response and verify:

    1. Technical accuracy - all calculations must follow proper accounting methodologies and arithmetic correctness
    2. Standards compliance - adherence to GAAP, IFRS, local tax laws, and relevant accounting standards
    3. Professional formatting - appropriate presentation of financial data with proper notation
    4. Methodological soundness - the approach used is appropriate for the scenario
    5. Completeness - all aspects of the query have been adequately addressed
    6. Disclaimer appropriateness - necessary disclaimers are present where required
    7. Citation accuracy - references to standards, laws, or provisions are correct

    If you identify any issues:
    1. Begin with "REVISIONS REQUIRED:"
    2. List each issue by category (e.g., "Calculation Error:", "Standards Non-compliance:", etc.)
    3. Provide specific guidance on how each issue should be corrected with reference to appropriate standards

    If the response meets all CA professional standards:
    1. Begin with "VERIFICATION COMPLETE:"
    2. Confirm the response meets all professional standards for CA documentation

    User Query: {state["user_query"]}

    Financial Response to Verify:
    {state["financial_response"]}

    Be thorough and precise in your verification to ensure the response meets the highest professional standards.
    """

    # Set up the prompt with values
    messages.append(HumanMessage(content=task_prompt))

    ai_message = llm.invoke(messages)
    messages.append(ai_message)

    result = {
        **state,
        "messages": messages,
        "audit_feedback": ai_message.content,
        "iterations": state["iterations"] + 1,
        "next_action" : "determine_next_step"
    }

    return result


def determine_next_step(state: AgentState) -> Literal["continue", "finish"]:
    new_iterations = state["iterations"]

    # Check if we've reached the maximum iterations
    if new_iterations >= 3:
        result = {
            **state,
            "next_action": "prepare_final_response"
        }
        return result

    # Check if verification is complete or if revisions are required
    audit_feedback = state.get("audit_feedback", "")
    if audit_feedback.startswith("VERIFICATION COMPLETE:"):
        result = {
            **state,
            "next_action": "prepare_final_response"
        }
        return result
    else:
        result = {
            **state,
            "next_action": "generate_financial_response"
        }

        return result


async def prepare_final_response(state: AgentState) -> AgentState:
    """
    Final LLM pass to enhance formatting and standardization of the response
    according to professional financial standards.
    """
    llm = get_llm()

    messages = state["messages"]

    task_prompt = f"""
    You are a Financial Documentation Specialist with expertise in presenting financial information in professionally formatted documents.

    Your task is to take an already verified financial response and enhance its presentation to meet the highest standards
    of financial documentation without changing the substance of the analysis.

    Focus on:
    1. Optimizing the layout and visual hierarchy of information
    2. Ensuring consistent tabular formatting with properly aligned numerical data and decimal points
    3. Applying standard financial notation conventions (currency symbols, percentages, ratios)
    4. Formatting headings, subheadings, and sections according to professional financial report standards
    5. Ensuring appropriate spacing and indentation for readability
    6. Using professional financial document typography and styling
    7. Maintaining a formal, authoritative tone throughout

    Original Query: {state["user_query"]}

    Verified Financial Response to Format:
    {state["financial_response"]}

    Format this into a polished, professional financial document that would be presentable to executives or clients.
    Make sure to maintain all technical content, calculations, and analysis while enhancing the presentation.
    """

    # Set up the prompt with values
    messages.append(HumanMessage(content=task_prompt))

    ai_message = llm.invoke(messages)
    messages.append(ai_message)

    result = {
        **state,
        "messages": messages,
        "financial_response": ai_message.content,
        "next_action": END
    }

    return result


# --- Graph Construction ---
def build_financial_advisor_graph():
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("generate_financial_response", generate_financial_response)
    workflow.add_node("verify_response", verify_response)
    workflow.add_node("determine_next_step", determine_next_step)
    workflow.add_node("prepare_final_response", prepare_final_response)

    # Add edges
    workflow.add_edge("generate_financial_response", "verify_response")

    def route_determine_next_step(state):
        if state["next_action"] == "generate_financial_response":
            return "generate_financial_response"
        else:
            return "prepare_final_response"

    # Add conditional edge based on verification results
    workflow.add_conditional_edges(
        "verify_response",
        route_determine_next_step,
        {
            "generate_financial_response": "generate_financial_response",
            "prepare_final_response": "prepare_final_response"
        }
    )


    workflow.add_edge("prepare_final_response", END)

    # Set the entry point
    workflow.set_entry_point("generate_financial_response")

    return workflow

# Interface to run the accounting agent
class RagLoopAgentRunner:
    def __init__(self):
        self.workflow = build_financial_advisor_graph().compile()

    async def start_new_task(self, user_id, folder_id, user_query: str, search_context: str, sse_emitter: SSEEmitter) -> SSEEmitter:

        print("Starting new task")
        # Create initial state
        initial_state = {
            "user_id": user_id,
            "messages": [HumanMessage(content=user_query)],
            "folder_id": folder_id,
            "user_query": user_query,
            "search_context":search_context,
            "financial_response": "",
            "audit_feedback": "",
            "final_response": "",
            "iterations": 0,
            "sse_emitter": sse_emitter
        }

        # Send initial SSE event
        await sse_emitter.emit("start", {
            "type": "thinking",
            "content": f"Starting to prepare final response. Initializing validation..."
        })

        # Run the workflow in the background
        asyncio.create_task(self._run_workflow(initial_state, sse_emitter))

        return sse_emitter

    async def _run_workflow(self, initial_state: Dict[str, Any], sse_emitter: SSEEmitter):
        """Run the workflow in the background"""
        try:
            # Run the workflow
            result = self.workflow.astream(initial_state, stream_mode="messages")
            async for msg, metadata in result:
                if msg.type == "AIMessageChunk" and metadata["langgraph_node"] == "prepare_final_response":
                    await sse_emitter.emit("data", {
                        "type": "data",
                        "content": msg.content
                    })

            # Send completion SSE event
            await sse_emitter.emit("complete", {
                "type": "complete",
                "content": "Task completed successfully"
            })
        except Exception as e:
            print(f"Error in workflow: {str(e)}")
            await sse_emitter.emit("error", {
                "type": "error",
                "content": f"Error occurred: {str(e)}"
            })
        finally:
            sse_emitter.stop()