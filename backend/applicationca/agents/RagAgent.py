import asyncio
import os
from typing import List, Dict, Any, Optional, Tuple, AsyncGenerator, TypedDict, Union
from pydantic import BaseModel, Field
import json
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, FunctionMessage
from langgraph.graph import StateGraph, END
from applicationca.contoller.vector import search_multi
from applicationca.services.BM25 import retrieve_chunks_from_db_advanced
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from applicationca.agents.lang_loop_agent import RagLoopAgentRunner
from langchain_community.tools.tavily_search import TavilySearchResults
from langgraph.prebuilt import ToolNode, tools_condition
from applicationca.services.Functions import get_or_create_session, store_message
from utils.sse_emitter import SSEEmitter
from applicationca.chatbot.tools.langgraph_tools import calculate
from applicationca.agents.generate_financial_response import FinancialRunner

finacial_runner = FinancialRunner()

load_dotenv()
from config.settings import settings

# --- Constants ---
APP_NAME = "financial_advisor_bot"
OPENAI_MODEL = settings.OPENAI_MODEL

# --- Helper Functions ---
def get_llm():
    return ChatOpenAI(
        model=OPENAI_MODEL
    )


# State definition
class RAGState(TypedDict):
    user_id: str
    messages: List[Union[HumanMessage, AIMessage, SystemMessage, FunctionMessage]]
    folder_id: str
    financial_response: str
    audit_feedback: str
    final_response: str
    iterations: int
    session_id: str
    query: str
    meta_data: str
    vector_queries: List[str]
    bm25_queries: List[str]
    relevant_files: List[str]
    search_context: str
    chunk_count: int
    max_chunks: int
    sufficient_data: bool
    missing_information: List[str]
    progress_message: str
    recommended_chunk_adjustment: int
    data_retrieval_count: int
    previous_messages: str
    sse_emitter: SSEEmitter


async def generate_llm_optimized_queries(state: RAGState) -> RAGState:
    llm = ChatOpenAI(temperature=0.5, model="gpt-4.1-mini")
    sse_emitter = state["sse_emitter"]

    # Send thinking message to user
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Analyzing your question to generate optimal search queries..."
    })

    # Set up the output parser
    parser = JsonOutputParser()
    format_instructions = parser.get_format_instructions()

    # Progress update
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Identifying key concepts and terms in your question..."
    })

    # Prepare information about previous queries and missing information
    previous_queries_info = ""
    if state["vector_queries"]:
        previous_queries_info += "- Vector queries: " + ", ".join(state["vector_queries"])

    if state["bm25_queries"]:
        previous_queries_info += "\n- BM25 queries: " + ", ".join(state["bm25_queries"])

    missing_info = ""
    if state["missing_information"]:
        missing_info = "- " + "\n- ".join(state["missing_information"])

    # Final progress update before query generation
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Formulating specialized queries to find the most relevant information..."
    })

    # Create system and user messages with direct content

    system_message = SystemMessage(
        content=f"""You are an advanced query optimization system for RAG (Retrieval Augmented Generation).
    Your task is to analyze a user query and generate optimized search queries for both vector search and BM25 retrieval.

    You will receive:
    1. A primary user query (highest priority)
    2. Document metadata that describes available information sources
    3. Previous search queries (if any) that have been tried
    4. Information that was missing from previous retrievals (if any)
    5. Parameters for retrieval configuration

    Your goal is to generate queries that will help retrieve the most relevant information to answer the user's question.
    {format_instructions}
    """
    )

    user_message = HumanMessage(
        content=f"""# PRIMARY USER QUERY (Highest Priority)
    {state["query"]}

    # DOCUMENT METADATA
    {state["meta_data"]}

    # RETRIEVAL PARAMETERS
    - Maximum chunks to retrieve: {state["max_chunks"]}

    # PREVIOUS RETRIEVAL ATTEMPTS (If available)
    {previous_queries_info if previous_queries_info else "None"}

    # MISSING INFORMATION FROM PREVIOUS RETRIEVAL (If available)
    {missing_info if missing_info else "None"}

    Please generate:

    1. VECTOR QUERIES (2-4):
       - Create semantic search queries for embedding-based retrieval
       - Focus on meaning rather than exact keywords
       - Incorporate context from metadata
       - Address any identified missing information

    2. BM25 QUERIES (2-4):
       - Create keyword-focused queries for term-based retrieval
       - Include specific technical terms from metadata
       - Use different keyword combinations

    3. RELEVANT FILES:
       - Identify which files from metadata are most likely relevant

    4. RECOMMENDED CHUNK COUNT:
       - Recommend how many chunks to retrieve (≤ {state["max_chunks"]})
       - Simple factual queries: fewer chunks (3-5)
       - Multi-perspective questions: medium chunks (5-10)
       - Complex/broad questions: more chunks (10-{state["max_chunks"]})
       - Increase if previous retrieval missed information

    Return your response in the required JSON format.
    """
    )

    messages = [system_message, user_message]

    # Execute the LLM call directly with messages and then parse the result
    response = await llm.ainvoke(messages)
    result = parser.parse(response.content)

    # Send completion update
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Searching... {result.get('vector_queries', [])}..... {result.get('bm25_queries', [])} ....."
    })

    # Update state with results
    state["vector_queries"] = result.get("vector_queries")
    state["bm25_queries"] = result.get("bm25_queries")
    state["relevant_files"] = result.get("relevant_files")

    # Get the recommended chunk count from the result
    recommended_count = result.get("recommended_chunk_count")

    # First, increase current chunk count by at most 10, or use recommended count if higher
    if "chunk_count" in state:
        state["chunk_count"] = max(state["chunk_count"] + 10, recommended_count)
    else:
        state["chunk_count"] = recommended_count

    # Then, ensure it doesn't exceed the maximum allowed chunks
    state["chunk_count"] = min(state["chunk_count"], state["max_chunks"])

    state["data_retrieval_count"] = state["data_retrieval_count"] + 1
    return state

async def retrieve_search_context(state: RAGState) -> RAGState:
    """Retrieve relevant documents from vector store and BM25."""
    loop = asyncio.get_running_loop()
    sse_emitter = state["sse_emitter"]

    # Run the synchronous function in an executor to avoid blocking the event loop.
    relevant_chunks_task = loop.run_in_executor(
        None, retrieve_chunks_from_db_advanced, state["bm25_queries"], state["folder_id"], state["user_id"], 10,
        state["chunk_count"]
    )

    # Directly call the asynchronous function.
    vector_chunks_task = search_multi(state["vector_queries"], state["user_id"], state["folder_id"],
                                      state["chunk_count"])

    # Run both tasks concurrently.
    relevant_chunks, vector_chunks = await asyncio.gather(
        relevant_chunks_task, vector_chunks_task
    )

    # Combine chunks from both sources and deduplicate by chunk_id
    # Use a dictionary to handle the deduplication - higher score wins if there's a duplicate
    combined_chunks = {}

    # Process BM25 results
    for chunk in relevant_chunks:
        chunk_id = chunk["chunk_id"]
        if chunk_id not in combined_chunks or chunk["score"] > combined_chunks[chunk_id]["score"]:
            combined_chunks[chunk_id] = chunk

    # Process vector search results
    for chunk in vector_chunks:
        chunk_id = chunk["chunk_id"]
        if chunk_id not in combined_chunks or chunk["score"] > combined_chunks[chunk_id]["score"]:
            combined_chunks[chunk_id] = chunk

    # Sort all unique chunks by score in descending order
    unique_chunks = sorted(combined_chunks.values(), key=lambda x: x["score"], reverse=True)

    # Limit to the requested chunk count if needed
    final_chunks = unique_chunks[:state["chunk_count"]]

    # Format the chunks for the context
    context_parts = [
        f'file_name: {chunk["file_name"]}\nfolder_id: {chunk["folder_id"]}\ncontent: {chunk["content"]}'
        for chunk in final_chunks
    ]

    # Send completion message
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Found {len(final_chunks)} unique chunks across both search methods. Compiling results..."
    })

    search_context = "\n\n".join(context_parts)
    state["search_context"] = search_context

    return state


async def evaluate_data_sufficiency(state: RAGState) -> RAGState:
    """
    Evaluate if the retrieved data is sufficient to answer the financial query.
    This function performs a detailed analysis of the retrieved content against
    the user's query to identify information gaps and quality issues.
    """
    llm = ChatOpenAI(temperature=0.1, model="gpt-4.1-mini")
    sse_emitter = state["sse_emitter"]

    # Construct context from retrieved chunks
    context = state["search_context"]
    original_query = state["query"]

    # Format previous missing information for context
    previous_missing = state["missing_information"]
    previous_missing_str = "\n- " + "\n- ".join(previous_missing) if previous_missing else "None"


    # Create system message with clear financial expertise focus
    system_prompt = """You are an expert financial data analyst with qualifications in accounting, auditing, financial reporting, and regulatory compliance.

    TASK: Your task is to evaluate if the provided information is sufficient to answer the user's financial query with professional-level accuracy.

    FINANCIAL INFORMATION EVALUATION CRITERIA:
    1. Completeness - Does the information cover all required financial data points, statements, or processes?
    2. Accuracy - Do the financial figures, calculations, and statements appear correct and consistent?
    3. Specificity - Is the level of financial detail adequate for the query's complexity?
    4. Relevance - Is the information directly applicable to the specific financial question?
    5. Temporal Context - Is the time period clearly defined and appropriate for the query?
    6. Regulatory Compliance - Are relevant financial regulations, standards, or frameworks addressed?
    7. Source Quality - Do the sources have sufficient authority for financial guidance?

    MISSING INFORMATION CATEGORIES:
    • Numerical Data: Financial figures, calculations, percentages, ratios
    • Temporal Data: Dates, fiscal periods, timelines, deadlines
    • Regulatory Information: Financial standards, legal requirements, compliance details
    • Procedural Information: Process steps, methodologies, accounting practices
    • Contextual Information: Industry standards, market conditions, comparative benchmarks
    • Source Attribution: Citations, data provenance, authority references

    For each identified gap, provide specific details about what financial information is missing and why it matters.
    """

    # Create user message with clear structure and examples
    human_prompt = f"""USER QUERY:
    {original_query}

    RETRIEVED INFORMATION:
    {context}

    SOURCE INFORMATION:
    {state["meta_data"]}

    PREVIOUS RETRIEVAL GAPS:
    {previous_missing_str}

    RETRIEVAL ATTEMPT: {state["data_retrieval_count"]} of 3 maximum attempts

    Please conduct a comprehensive financial data sufficiency analysis by addressing:

    1. Is the retrieved information SUFFICIENT to provide a complete, accurate, and professional financial answer?

    2. What SPECIFIC financial information is missing, if any? For each missing item:
       - Describe exactly what financial data is needed
       - Explain why this information is critical to answering the query
       - Categorize by type (numerical, temporal, regulatory, procedural, contextual, source)

    3. Assess the QUALITY of the retrieved information:
       - Relevance to the query (high/medium/low)
       - Specificity of financial details (specific/general)
       - Reliability of information sources (if apparent)
       - Coverage of essential financial concepts in the query

    Output your analysis in the following JSON format:
    {{
        "sufficient": boolean,
        "missing_information": [
            "Detailed description of missing financial item 1",
            "Detailed description of missing financial item 2",
            ...
        ]
    }}
    """

    # Create the chat prompt template with both messages
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt)
    ])

    # Convert the template to a prompt value
    prompt_value = prompt_template.format_prompt()

    response = llm.invoke(prompt_value)
    parser = JsonOutputParser()

    try:
        evaluation = parser.parse(response.content)
    except Exception as e:
        # More robust fallback parsing with logging
        print(f"Error parsing LLM response: {e}")
        try:
            # Attempt to extract JSON using regex as a fallback
            import re
            import json
            json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
            if json_match:
                evaluation = json.loads(json_match.group(0))
            else:
                raise ValueError("No JSON found in response")
        except:
            # Ultimate fallback if all parsing fails
            evaluation = {
                "sufficient": False,
                "missing_information": ["Unable to parse evaluation, treating as insufficient"],
                "confidence": 50,
                "retrieval_quality_assessment": "Unable to assess due to parsing failure"
            }

    # Update state with comprehensive evaluation data
    state["sufficient_data"] = evaluation["sufficient"]
    state["missing_information"] = evaluation.get("missing_information", [])

    # Determine next step with more detailed progress messages
    if state["sufficient_data"] or state["data_retrieval_count"] >= 3:
        # Send thinking update
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Information sufficient to provide a complete financial answer. Proceeding to response generation..."
        })

        state["progress_message"] = f"Found sufficient information to answer"
        return {
            **state,
            "next_action": "generate_financial_response"
        }
    elif state["chunk_count"] >= state["max_chunks"]:
        # Send thinking update
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Retrieved maximum document chunks. Some information may still be missing, but proceeding with best available data..."
        })

        state[
            "progress_message"] = f"Retrieved maximum number of chunks ({state['chunk_count']}). Some information may still be missing in areas {state['missing_information']}"
        return {
            **state,
            "next_action": "generate_financial_response"
        }
    else:

        state["progress_message"] = f"Initial information retrieved. Expanding search to find..."
        return {
            **state,
            "next_action": "generate_llm_optimized_queries"
        }


# --- Node Functions ---
async def generate_financial_response(state: RAGState) -> RAGState:
    """Generates a professional CA-quality financial response."""
    answer = await finacial_runner.start_new_task(state["user_id"], state["folder_id"], state["audit_feedback"],
                                                  state["query"], state["relevant_files"], state["search_context"],
                                                  state["previous_messages"], state["sse_emitter"])
    result = {
        **state,
        "financial_response": answer,
        "next_action": "verify_response"
    }
    return result

async def verify_response(state: RAGState) -> RAGState:
    """Verifies the financial response against CA standards."""
    llm = get_llm()
    sse_emitter = state["sse_emitter"]

    # Send thinking update
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Verifying response against professional standards and regulations..."
    })

    messages = state["messages"]

    task_prompt = f"""
    You are a CA Standards Verification Specialist with expertise in accounting standards, auditing procedures, and professional documentation.

    Review the response and verify:

    1. Technical accuracy - all calculations must follow proper accounting methodologies and arithmetic correctness
    2. Standards compliance - adherence to GAAP, IFRS, local tax laws, and relevant accounting standards
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
    2. Confirm the response meets all professional standards for CA documentation.

    User Query: {state["query"]}

    Financial Response to Verify:
    {state["financial_response"]}
    
    this was the Search Context: {state["search_context"]}

    this was the Previous Chats: {state["previous_messages"]}

    Be thorough and precise in your verification to ensure the response meets the highest professional standards.
    """

    # Additional thinking update
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Checking calculations, standards compliance, and professional formatting..."
    })

    # Set up the prompt with values
    messages.append(HumanMessage(content=task_prompt))

    ai_message = llm.invoke(messages)
    messages.append(ai_message)

    # Get verification result to customize thinking message
    verification_result = ai_message.content
    if verification_result.startswith("VERIFICATION COMPLETE:"):
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Verification successful. Response meets all professional standards."
        })
    else:
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Identifying areas for improvement to meet professional standards..."
        })

    result = {
        **state,
        "messages": messages,
        "audit_feedback": ai_message.content,
        "iterations": state["iterations"] + 1,
        "next_action": "determine_next_step"
    }

    return result


async def determine_next_step(state: RAGState) -> RAGState:
    new_iterations = state["iterations"]
    sse_emitter = state["sse_emitter"]

    print("in determine")

    # Check if we've reached the maximum iterations
    if new_iterations >= 3:
        # Send thinking update
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Maximum refinement iterations reached. Finalizing the response..."
        })

        result = {
            **state,
            "next_action": "prepare_final_response"
        }
        return result

    # Check if verification is complete or if revisions are required
    audit_feedback = state.get("audit_feedback", "")
    if audit_feedback.startswith("VERIFICATION COMPLETE:"):
        # Send thinking update
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Verification passed. Preparing final professional response..."
        })

        result = {
            **state,
            "next_action": "prepare_final_response"
        }
        return result
    else:
        # Send thinking update
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": f"Making revisions based on professional standards feedback (iteration {new_iterations})..."
        })

        result = {
            **state,
            "next_action": "generate_financial_response"
        }

        return result


def prepare_final_response(state: RAGState) -> RAGState:
    """
    Final LLM pass to enhance formatting and standardization of the response
    according to professional financial standards.
    """
    llm = get_llm()

    messages = state["messages"]

    task_prompt = f"""
    You are a professional financial advisor presenting information to a client. 

    Original Query: {state["query"]}

    Verified Response to Format:
    {state["financial_response"]}

    Your task is to reformat and enhance this verified financial information. Follow these guidelines:

    1. Start with a direct, concise answer to the original query
    2. Use clear, professional language while avoiding unnecessary jargon
    3. Format numerical data consistently (percentages, dollar amounts, dates)
    4. Organize information with appropriate headers and bullet points for improved readability
    5. Highlight key insights and actionable recommendations
    6. Maintain complete factual accuracy - do not add new financial advice beyond what's in the verified response
    7. End with a brief summary if the response is lengthy

    The goal is to present this information in a way that is clear, professional, and immediately useful to the client.
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

# Define the workflow
def create_financial_rag_workflow():
    workflow = StateGraph(RAGState)

    # Add your nodes
    workflow.add_node("generate_llm_optimized_queries", generate_llm_optimized_queries)
    workflow.add_node("retrieve_search_context", retrieve_search_context)  # Assuming you have this function
    workflow.add_node("evaluate_data_sufficiency", evaluate_data_sufficiency)
    # Add nodes
    workflow.add_node("generate_financial_response", generate_financial_response)
    workflow.add_node("verify_response", verify_response)
    workflow.add_node("determine_next_step", determine_next_step)
    workflow.add_node("prepare_final_response", prepare_final_response)

    # Add standard edges
    workflow.add_edge("generate_llm_optimized_queries", "retrieve_search_context")
    workflow.add_edge("retrieve_search_context", "evaluate_data_sufficiency")

    def route_data_next_step(state):
        if state["next_action"] == "generate_financial_response":
            return "generate_financial_response"
        else:
            return "generate_llm_optimized_queries"

    workflow.add_conditional_edges("evaluate_data_sufficiency",
          route_data_next_step,
          {
              "generate_financial_response": "generate_financial_response",
              "generate_llm_optimized_queries": "generate_llm_optimized_queries"
          }
    )

    # Add edges
    workflow.add_edge("generate_financial_response", "verify_response")
    workflow.add_edge("verify_response", "determine_next_step")

    def route_determine_next_step(state):
        print(state["next_action"])
        if state["next_action"] == "generate_financial_response":
            return "generate_financial_response"
        else:
            return "prepare_final_response"

    # Add conditional edge based on verification results
    workflow.add_conditional_edges(
        "determine_next_step",
        route_determine_next_step,
        {
            "generate_financial_response": "generate_financial_response",
            "prepare_final_response": "prepare_final_response"
        }
    )

    workflow.add_edge("prepare_final_response", END)

    # Set the entry point
    workflow.set_entry_point("generate_llm_optimized_queries")

    return workflow


# Interface to run the accounting agent
class RagAgentRunner:
    def __init__(self):
        self.workflow = create_financial_rag_workflow().compile()

    async def start_new_task(self, user_id, chat_id, folder_id, initial_data: str, initial_message: str, max_chunks: int, previous_messages: str, client_id: str, lockr_id: str) -> SSEEmitter:
        """Start a new accounting task and return the SSE emitter"""
        # Create SSE emitter
        sse_emitter = SSEEmitter()

        # Create initial state
        initial_state = {
            "user_id": user_id,
            "messages": [HumanMessage(content=initial_message)],
            "financial_response": "",
            "audit_feedback": "",
            "final_response": "",
            "iterations": 0,
            "session_id": chat_id,
            "folder_id": folder_id,
            "query": initial_message,
            "meta_data": initial_data,
            "vector_queries":[],
            "bm25_queries":[],
            "relevant_files":[],
            "search_context":"",
            "chunk_count": 10,
            "max_chunks": max_chunks,
            "sufficient_data": 0,
            "missing_information": [],
            "progress_message": "",
            "recommended_chunk_adjustment":0,
            "data_retrieval_count": 0,
            "previous_messages": previous_messages,
            "sse_emitter": sse_emitter
        }

        # Send initial SSE event
        await sse_emitter.emit("start", {
            "type": "thinking",
            "content": f"I'm analyzing your financial question. Starting the research process..."
        })

        # Run the workflow in the background
        asyncio.create_task(self._run_workflow(initial_state, sse_emitter, client_id, lockr_id))

        return sse_emitter

    async def _run_workflow(self, initial_state: Dict[str, Any], sse_emitter: SSEEmitter, client_id: str, lockr_id: str):
        """Run the workflow in the background"""
        try:
            # Send initial detailed thinking message
            await sse_emitter.emit("thinking", {
                "type": "thinking",
                "content": f"Identifying key concepts in your question and planning my research approach..."
            })

            result = self.workflow.astream(initial_state, stream_mode="messages")
            response = ""
            async for msg, metadata in result:
                if msg.type == "AIMessageChunk" and metadata["langgraph_node"] == "prepare_final_response":
                    response+=msg.content
                    await sse_emitter.emit("data", {
                        "type": "data",
                        "content": msg.content
                    })

            session_id = get_or_create_session(initial_state["user_id"], initial_state["query"], initial_state["session_id"], client_id, lockr_id)
            await store_message(initial_state["user_id"], session_id, "user", initial_state["query"])
            await store_message(initial_state["user_id"], session_id, "bot", response)
            # Send completion SSE event
        except Exception as e:
            print(e)
            await sse_emitter.emit("data", {
                "type": "data",
                "content": "I'm sorry, we're experiencing technical difficulties right now. Please try again in a few moments."
            })
        finally:
            sse_emitter.stop()