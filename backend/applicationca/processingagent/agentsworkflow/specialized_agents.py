import os
from typing import Dict, List, Any, Tuple, Optional, Literal, TypedDict, Union, Callable, AsyncGenerator
import json
import datetime
import uuid
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, FunctionMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool
from firebase_setup import db
from applicationca.processingagent.contants.agents_contants import TaskType, AGENT_PROMPTS, VALIDATION_RULES, DATA_REQUIREMENTS
import asyncio

from config.settings import settings
os.environ.setdefault("OPENAI_API_KEY", settings.OPENAI_API_KEY)
os.environ.setdefault("TAVILY_API_KEY", settings.TAVILY_API_KEY)
TAVILY_API_KEY = settings.TAVILY_API_KEY
tool = TavilySearchResults(max_results=2)
tools = [tool]

# Add SSE Emitter class
class SSEEmitter:
    def __init__(self):
        self._queue = asyncio.Queue()
        self._active = True

    async def emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an SSE event"""
        if self._active:
            await self._queue.put({
                "event": event_type,
                "data": json.dumps(data)
            })

    async def stream(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream events from the queue"""
        while self._active:
            try:
                event = await self._queue.get()
                yield event
            except asyncio.CancelledError:
                break

    def stop(self):
        """Stop the emitter"""
        self._active = False

# Define our shared state
class AgentState(TypedDict):
    conversation_id: str
    messages: List[Union[HumanMessage, AIMessage, SystemMessage, FunctionMessage]]
    task_type: TaskType
    data: Dict[str, Any]
    missing_data: List[Dict[str, Any]]  # Enhanced with descriptions and types
    validation_errors: List[str]
    attempts: int
    next_action: str
    complete: bool
    calculation_results: Dict[str, Any]
    confidence_score: float
    last_updated: str
    agent_logs: List[str]
    final_response: str
    sse_emitter: SSEEmitter

# Initialize task-specific LLMs with different configurations for specialization
llms = {
    TaskType.BALANCE_SHEET: ChatOpenAI(model="gpt-4.1-mini", temperature=0.1).bind_tools(tools),  # Lower temperature for precision
    TaskType.ITR_FILING: ChatOpenAI(model="gpt-4.1-mini", temperature=0.1),
    TaskType.AUDIT: ChatOpenAI(model="gpt-4.1-mini", temperature=0.2),
    TaskType.GST_FILING: ChatOpenAI(model="gpt-4.1-mini", temperature=0.1),
    TaskType.FINANCIAL_ANALYSIS: ChatOpenAI(model="gpt-4.1-mini", temperature=0.3),  # Slightly higher for insights
    TaskType.TAX_PLANNING: ChatOpenAI(model="gpt-4.1-mini", temperature=0.3)
}


# Memory functions
def save_state_to_firebase(state: AgentState):
    """Save the current state to Firebase"""
    try:
        # Create a copy of the state without the SSEEmitter
        state_to_save = state.copy()
        if 'sse_emitter' in state_to_save:
            del state_to_save['sse_emitter']

        # Convert messages to a serializable format
        if 'messages' in state_to_save:
            state_to_save['messages'] = [
                {
                    'type': msg.__class__.__name__,
                    'content': msg.content
                }
                for msg in state_to_save['messages']
            ]

        # Save to Firebase
        db.collection('accounting_tasks').document(state['conversation_id']).set(state_to_save)
    except Exception as e:
        print(f"Error saving state to Firebase: {str(e)}")
        # Log the error but don't raise it to avoid disrupting the workflow


def load_state_from_firebase(conversation_id: str) -> Optional[Dict]:
    """Load a state from Firebase by conversation ID"""
    doc_ref = db.collection("accounting_states").document(conversation_id).get()
    if doc_ref.exists:
        state_data = doc_ref.to_dict()

        # Convert serialized messages back to objects
        messages = []
        for msg in state_data.get("messages", []):
            if msg["type"] == "HumanMessage":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["type"] == "AIMessage":
                messages.append(AIMessage(content=msg["content"]))
            elif msg["type"] == "SystemMessage":
                messages.append(SystemMessage(content=msg["content"]))
            elif msg["type"] == "FunctionMessage":
                messages.append(FunctionMessage(content=msg["content"]))

        state_data["messages"] = messages
        return state_data
    return None


# Define nodes for our LangGraph
def initialize_task(state: AgentState) -> AgentState:

    print("In Intialize task")
    """Initialize the agent with the task details and check for missing data"""
    # Save initial state
    if "conversation_id" not in state or not state["conversation_id"]:
        state["conversation_id"] = str(uuid.uuid4())

    task_type = state["task_type"]
    data = state["data"]

    # Log initialization
    state["agent_logs"] = state.get("agent_logs", []) + [
        f"Initializing {task_type} task at {datetime.datetime.now().isoformat()}"]

    # Check for missing required data
    missing = []
    if task_type in DATA_REQUIREMENTS:
        for field_info in DATA_REQUIREMENTS[task_type]:
            field = field_info["field"]
            if field_info["required"] and (field not in data or not data[field]):
                missing.append(field_info)

    # Add system message if not present
    messages = state["messages"]
    if not any(isinstance(msg, SystemMessage) for msg in messages):
        system_prompt = AGENT_PROMPTS.get(task_type, "You are an expert accounting agent.")
        messages.insert(0, SystemMessage(content=system_prompt))

    result = {
        **state,
        "messages": messages,
        "missing_data": missing,
        "next_action": "request_missing_data" if missing else "process_task"
    }

    # Save state to Firebase
    save_state_to_firebase(result)

    return result


async def process_task(state: AgentState) -> AgentState:
    """Process the accounting task using the specialized LLM"""
    messages = state["messages"]
    task_type = state["task_type"]
    data = state["data"]
    sse_emitter = state["sse_emitter"]

    # Log processing
    state["agent_logs"] = state["agent_logs"] + [
        f"Processing {task_type} task at {datetime.datetime.now().isoformat()}"]

    # Send processing start token
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Starting processing for {task_type.value} task..."
    })

    # Create a detailed prompt for the LLM
    task_prompt = f"""
    Complete the following accounting task: {task_type.value}.

    Here is the data provided:
    ```json
    {json.dumps(data, indent=2)}
    ```

    Please complete this task with extreme precision and accuracy. For each calculation or decision, explain your reasoning step by step.
    Format numeric values appropriately with 2 decimal places where applicable.
    Use proper accounting terminology and follow all relevant accounting standards.
    """

    # Add task-specific guidance
    if task_type in VALIDATION_RULES:
        task_prompt += f"\n\nEnsure your solution satisfies these validation rules:\n" + "\n".join(
            f"- {rule['rule']}" for rule in VALIDATION_RULES[task_type]
        )
        # Send validation rules token
        await sse_emitter.emit("thinking", {
            "type": "thinking",
            "content": "Applying validation rules..."
        })

    # Add specific output format instructions based on task
    if task_type == TaskType.BALANCE_SHEET:
        task_prompt += """
        Present your final balance sheet in a clearly structured format with:
        1. Assets section (with current and non-current subsections)
        2. Liabilities section (with current and non-current subsections)
        3. Equity section
        4. Totals for each section and subsection
        5. Verification that Assets = Liabilities + Equity

        Also include your final calculations in a structured JSON format at the end of your response, like this:
        ```json
        {
          "total_assets": 1000000,
          "total_liabilities": 600000,
          "total_equity": 400000,
          "current_assets": {...},
          "non_current_assets": {...},
          "current_liabilities": {...},
          "non_current_liabilities": {...},
          "equity": {...}
        }
        ```
        """
    elif task_type == TaskType.ITR_FILING:
        task_prompt += """
        Present your final ITR filing details with:
        1. Summary of income from all sources
        2. Detailed breakdown of applicable deductions with section references
        3. Calculation of taxable income
        4. Tax calculation with applicable rates
        5. Net tax payable or refundable amount

        Also include your final calculations in a structured JSON format at the end of your response.
        """

    # Send processing token
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": "Processing calculations and generating response..."
    })

    # Call the specialized LLM
    messages.append(HumanMessage(content=task_prompt))
    specialized_llm = llms.get(task_type, llms[TaskType.BALANCE_SHEET])  # Default to balance sheet LLM if not found
    ai_message = specialized_llm.invoke(messages)
    messages.append(ai_message)


    # Try to extract calculation results from the response
    calculation_results = {}
    try:
        response_text = ai_message.content
        if "```json" in response_text and "```" in response_text.split("```json")[1]:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
            calculation_results = json.loads(json_str)
    except:
        # If JSON extraction fails, add a note to the logs
        state["agent_logs"] = state["agent_logs"] + ["Failed to extract calculation results as JSON"]

    # Send processing complete token
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": "Processing complete. Starting validation..."
    })

    result = {
        **state,
        "messages": messages,
        "calculation_results": calculation_results,
        "next_action": "validate_response",
        "final_response": ai_message.content
    }

    # Save state to Firebase
    save_state_to_firebase(result)

    return result

def make_final_response(state: AgentState)->AgentState:
    """Process the accounting task using the specialized LLM"""
    messages = state["messages"]
    task_type = state["task_type"]

    # Log processing
    state["agent_logs"] = state["agent_logs"] + [
        f"Processing {task_type} task at {datetime.datetime.now().isoformat()}"]


    # Create a detailed prompt for the LLM
    task_prompt = f"""
        Format this response
        {state["final_response"]}
        Use proper accounting terminology and follow all relevant accounting standards.
        """

    # Call the specialized LLM
    messages.append(HumanMessage(content=task_prompt))
    specialized_llm = llms.get(task_type, llms[TaskType.BALANCE_SHEET])  # Default to balance sheet LLM if not found
    ai_message = specialized_llm.invoke(messages)
    messages.append(ai_message)

    result = {
        **state,
        "messages": messages,
        "next_action": END
    }

    return result

async def validate_response(state: AgentState) -> AgentState:
    """Validate the generated response against accounting rules and completeness"""
    messages = state["messages"]
    task_type = state["task_type"]
    response_content = messages[-1].content
    calculation_results = state["calculation_results"]
    sse_emitter = state["sse_emitter"]

    # Log validation
    state["agent_logs"] = state["agent_logs"] + [f"Validating response for {task_type} task"]

    # Send validation start token
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Starting validation for {task_type.value} task..."
    })

    # Create a detailed validation prompt
    validation_prompt = f"""
    You are an accounting compliance validator. Review the following accounting work for {task_type.value} and identify any errors, inconsistencies, or missing information:

    WORK TO VALIDATE:
    {response_content}

    CALCULATION RESULTS:
    ```json
    {json.dumps(calculation_results, indent=2)}
    ```

    Validate against these criteria:
    """

    if task_type in VALIDATION_RULES:
        for rule in VALIDATION_RULES[task_type]:
            validation_prompt += f"\n- {rule['rule']}"
            # Send validation rule token
            await sse_emitter.emit("thinking", {
                "type": "thinking",
                "content": f"Validating rule: {rule['rule']}"
            })

    validation_prompt += """
    Your validation must be extremely thorough and precise. Check:
    1. Mathematical accuracy of all calculations
    2. Compliance with accounting/tax principles
    3. Internal consistency of all numbers
    4. Completeness of the analysis
    5. Proper formatting and presentation

    Respond with a detailed validation report in this JSON format:
    ```json
    {
        "valid": true/false,
        "confidence_score": 0.0-1.0,
        "errors": [
            {"description": "Error description", "severity": "high/medium/low", "location": "Where the error occurs"}
        ],
        "missing_data": [
            {"field": "field_name", "description": "What information is needed", "type": "string/number/object"}
        ],
        "accuracy_assessment": "Detailed assessment of overall accuracy"
    }
    ```
    """

    # Call LLM for validation (always use a low temperature for validation)
    validation_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.1)
    messages.append(HumanMessage(content=validation_prompt))

    # Send processing token
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": "Processing validation results..."
    })

    validation_response = validation_llm.invoke(messages)
    messages.append(validation_response)

    # Extract validation results
    try:
        # Find JSON block in the response
        validation_text = validation_response.content
        if "```json" in validation_text and "```" in validation_text.split("```json")[1]:
            json_str = validation_text.split("```json")[1].split("```")[0].strip()
            validation_result = json.loads(json_str)
        else:
            # Try to find JSON without markdown code blocks
            import re
            json_match = re.search(r'\{.*\}', validation_text, re.DOTALL)
            if json_match:
                validation_result = json.loads(json_match.group())
            else:
                raise ValueError("No JSON found in validation response")
    except:
        # Fallback if we can't parse JSON
        validation_result = {
            "valid": False,
            "confidence_score": 0.3,
            "errors": [
                {"description": "Failed to parse validation response", "severity": "high", "location": "validator"}],
            "missing_data": [],
            "accuracy_assessment": "Unable to assess accuracy due to validation parsing failure"
        }
        state["agent_logs"] = state["agent_logs"] + ["Failed to parse validation result as JSON"]

    # Extract errors and missing data
    validation_errors = [f"{error.get('description', 'Unknown error')} (Severity: {error.get('severity', 'unknown')})"
                         for error in validation_result.get("errors", [])]

    missing_data = validation_result.get("missing_data", [])
    confidence_score = validation_result.get("confidence_score", 0.5)

    # Send validation summary token
    await sse_emitter.emit("thinking", {
        "type": "thinking",
        "content": f"Validation complete. Confidence score: {confidence_score:.2f}"
    })

    result = {
        **state,
        "messages": messages,
        "validation_errors": validation_errors,
        "missing_data": missing_data,
        "confidence_score": confidence_score,
        "attempts": state["attempts"] + 1,
        "next_action": "determine_next_step"
    }

    # Save state to Firebase
    save_state_to_firebase(result)

    return result


def determine_next_step(state: AgentState) -> AgentState:
    """Determine what to do next based on validation"""
    validation_errors = state["validation_errors"]
    missing_data = state["missing_data"]
    attempts = state["attempts"]
    confidence_score = state["confidence_score"]

    # Log decision
    state["agent_logs"] = state["agent_logs"] + [
        f"Determining next step. Errors: {len(validation_errors)}, Missing data: {len(missing_data)}, " +
        f"Attempts: {attempts}, Confidence: {confidence_score}"
    ]

    # Success case: no errors, no missing data, and high confidence
    if not validation_errors and not missing_data and confidence_score >= 0.9:
        result = {
            **state,
            "complete": True,
            "next_action": "make_final_response",
            "agent_logs": state["agent_logs"] + ["Task completed successfully with high confidence"]
        }
        save_state_to_firebase(result)
        return result

    # Need human help: many attempts or low confidence despite multiple tries
    if attempts >= 3 or (attempts >= 2 and confidence_score < 0.7):
        result = {
            **state,
            "next_action": "request_human_help",
            "agent_logs": state["agent_logs"] + ["Requesting human help after multiple attempts"]
        }
        save_state_to_firebase(result)
        return result

    # Missing data case
    if missing_data:
        result = {
            **state,
            "next_action": "request_missing_data",
            "agent_logs": state["agent_logs"] + ["Missing data detected, requesting from user"]
        }
        save_state_to_firebase(result)
        return result

    # Errors case
    if validation_errors:
        result = {
            **state,
            "next_action": "fix_errors",
            "agent_logs": state["agent_logs"] + ["Validation errors detected, attempting to fix"]
        }
        save_state_to_firebase(result)
        return result

    # Default case (should rarely reach here)
    result = {
        **state,
        "next_action": END,
        "agent_logs": state["agent_logs"] + ["Default end condition reached"]
    }
    save_state_to_firebase(result)
    return result


def request_missing_data(state: AgentState) -> AgentState:
    """Create a structured request for missing data"""
    messages = state["messages"]
    missing = state["missing_data"]

    # Create a user-friendly form for data collection
    form_fields = []
    for item in missing:
        field_name = item.get("field", "unknown_field")
        description = item.get("description", "No description available")
        field_type = item.get("type", "string")

        field_info = f"• {field_name}: {description} (Type: {field_type})"
        form_fields.append(field_info)

    request_message = f"""I need additional information to complete this accounting task accurately:

MISSING INFORMATION:
{chr(10).join(form_fields)}

Please provide the requested information in a structured format. 
You can use JSON format for complex data, or simply respond with the values in a clear format.

Example response:
```
{missing[0].get('field') if missing else 'field_name'}: Your value here
{missing[1].get('field') if len(missing) > 1 else 'another_field'}: Another value
```

This information is critical for accurate accounting work and compliance with standards.
"""

    messages.append(AIMessage(content=request_message))

    result = {
        **state,
        "messages": messages,
        "next_action": END,  # End the cycle to get user input
        "agent_logs": state["agent_logs"] + ["Requested missing data from user"]
    }

    # Save state to Firebase
    save_state_to_firebase(result)

    return result


def request_human_help(state: AgentState) -> AgentState:
    """Request human intervention with structured guidance"""
    messages = state["messages"]
    validation_errors = state["validation_errors"]
    missing_data = state["missing_data"]
    calculation_results = state["calculation_results"]
    confidence_score = state["confidence_score"]

    # Create a detailed analysis of the issues
    help_message = f"""I need your expertise to resolve some challenges with this accounting task.

CURRENT STATUS:
- Confidence score: {confidence_score:.2f}/1.00
- Attempts: {state["attempts"]}
- Task type: {state["task_type"].value}

ISSUES IDENTIFIED:
"""

    if validation_errors:
        help_message += "VALIDATION ERRORS:\n" + "\n".join(f"- {error}" for error in validation_errors) + "\n\n"

    if missing_data:
        help_message += "MISSING INFORMATION:\n" + "\n".join(
            f"- {item.get('field', 'Unknown field')}: {item.get('description', 'No description')}"
            for item in missing_data
        ) + "\n\n"

    help_message += f"""
CURRENT CALCULATIONS:
```json
{json.dumps(calculation_results, indent=2)}
```

How would you like me to proceed? Options:
1. Provide the missing information
2. Guide me on how to fix specific errors
3. Override my work with your expert solution
4. Adjust the approach for this accounting task

Please let me know which option you prefer and provide your guidance.
"""

    messages.append(AIMessage(content=help_message))

    result = {
        **state,
        "messages": messages,
        "next_action": END,  # End the cycle to get human guidance
        "agent_logs": state["agent_logs"] + ["Requested human expertise to resolve issues"]
    }

    # Save state to Firebase
    save_state_to_firebase(result)

    return result


def fix_errors(state: AgentState) -> AgentState:
    """Attempt to fix validation errors with a more thorough approach"""
    messages = state["messages"]
    validation_errors = state["validation_errors"]
    confidence_score = state["confidence_score"]
    task_type = state["task_type"]
    calculation_results = state["calculation_results"]

    # Create a detailed error-fixing prompt
    fix_prompt = f"""
    The following issues were found in the accounting work for {task_type.value}:

    VALIDATION ERRORS:
    {chr(10).join(f"• {error}" for error in validation_errors)}

    CURRENT CALCULATIONS:
    ```json
    {json.dumps(calculation_results, indent=2)}
    ```

    CURRENT CONFIDENCE SCORE: {confidence_score:.2f}/1.00

    Please perform a complete re-analysis and fix all identified issues. For each error:
    1. Explain why the error occurred
    2. Show your corrected calculation methodology
    3. Provide the updated result

    Maintain strict accuracy and adherence to accounting principles. Re-verify ALL calculations, even those not flagged with errors.

    Present your completely corrected solution in a clear, structured format.
    Include the final calculations in JSON format at the end of your response.
    """

    messages.append(HumanMessage(content=fix_prompt))
    specialized_llm = llms.get(task_type, llms[TaskType.BALANCE_SHEET])
    ai_message = specialized_llm.invoke(messages)
    messages.append(ai_message)

    # Try to extract updated calculation results
    try:
        response_text = ai_message.content
        if "```json" in response_text and "```" in response_text.split("```json")[1]:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
            updated_results = json.loads(json_str)
            calculation_results = updated_results
    except:
        state["agent_logs"] = state["agent_logs"] + ["Failed to extract updated calculation results"]

    result = {
        **state,
        "messages": messages,
        "calculation_results": calculation_results,
        "next_action": "validate_response",  # Go back to validation
        "agent_logs": state["agent_logs"] + ["Attempted to fix validation errors"]
    }

    # Save state to Firebase
    save_state_to_firebase(result)

    return result


def human_input_handler(state: AgentState, human_input: str) -> AgentState:
    """Handle human input in response to missing data or help requests with structured parsing"""
    messages = state["messages"]
    messages.append(HumanMessage(content=human_input))
    data = state["data"]
    missing_data = state["missing_data"]

    # Log human input received
    state["agent_logs"] = state["agent_logs"] + ["Received human input, processing..."]

    # Check what type of input we're handling
    if state["next_action"] == "request_missing_data" or len(missing_data) > 0:
        # Parse the input for missing data fields
        updated_data = parse_human_input(human_input, missing_data, data)

        result = {
            **state,
            "data": updated_data,
            "messages": messages,
            "next_action": "process_task",  # Resume processing
            "missing_data": []
        }
        # Keep track of which fields we've successfully parsed
        remaining_missing = [item for item in missing_data if item.get("field") not in updated_data]

        result = {
            **state,
            "data": updated_data,
            "messages": messages,
            "next_action": "process_task" if not remaining_missing else "request_missing_data",
            # Resume processing only if all data is provided
            "missing_data": remaining_missing,
            "agent_logs": state["agent_logs"] + [
                f"Updated data with human input, remaining missing fields: {len(remaining_missing)}"]
        }
    elif state["next_action"] == "request_human_help":
        # Human is helping with a complex issue - check if they're providing a solution
        if "override" in human_input.lower() or "solution" in human_input.lower():
            result = {
                **state,
                "messages": messages,
                "complete": True,  # Consider the task complete with human override
                "next_action": END,
                "agent_logs": state["agent_logs"] + ["Human expert provided override solution"]
            }
        else:
            # Human provided guidance but not a complete solution
            result = {
                **state,
                "messages": messages,
                "next_action": "process_task",  # Start over with new guidance
                "attempts": 0,  # Reset attempts counter
                "agent_logs": state["agent_logs"] + ["Received guidance from human expert, restarting process"]
            }
    else:
        # Default case - just continue with the process
        result = {
            **state,
            "messages": messages,
            "next_action": "process_task",
            "agent_logs": state["agent_logs"] + ["Received human input, continuing process"]
        }

        # Save state to Firebase
    save_state_to_firebase(result)

    return result


def parse_human_input(input_text: str, missing_data: List[Dict[str, Any]], existing_data: Dict[str, Any]) -> Dict[
    str, Any]:
    """Parse structured or unstructured human input to extract missing data"""
    updated_data = dict(existing_data)  # Make a copy to avoid modifying the original

    # Try to parse JSON if present
    try:
        if "```json" in input_text and "```" in input_text.split("```json")[1]:
            json_str = input_text.split("```json")[1].split("```")[0].strip()
            json_data = json.loads(json_str)
            updated_data.update(json_data)
            return updated_data

        if "{" in input_text and "}" in input_text:
            # Try to find and parse a JSON object in the text
            import re
            json_match = re.search(r'\{.*\}', input_text, re.DOTALL)
            if json_match:
                json_data = json.loads(json_match.group())
                updated_data.update(json_data)
                return updated_data
    except:
        pass  # If JSON parsing fails, continue with line-by-line parsing

    # Look for field: value patterns in the text
    for missing_item in missing_data:
        field = missing_item.get("field")
        if not field:
            continue

        patterns = [
            rf"{field}:\s*(.*?)(?:\n|$)",  # Note the 'r' prefix
            rf"{field}=\s*(.*?)(?:\n|$)",
            rf"{field}\s*:\s*(.*?)(?:\n|$)",
            rf"{field}\s*=\s*(.*?)(?:\n|$)"
        ]

        for pattern in patterns:
            import re
            match = re.search(pattern, input_text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()

                # Try to convert to appropriate type if specified
                field_type = missing_item.get("type", "string")
                if field_type == "number" or field_type == "float":
                    try:
                        value = float(value)
                    except:
                        pass
                elif field_type == "integer" or field_type == "int":
                    try:
                        value = int(value)
                    except:
                        pass
                elif field_type == "boolean" or field_type == "bool":
                    value = value.lower() in ["true", "yes", "1"]

                # Handle nested fields with dot notation
                if "." in field:
                    parts = field.split(".")
                    current = updated_data
                    for i, part in enumerate(parts[:-1]):
                        if part not in current:
                            current[part] = {}
                        current = current[part]
                    current[parts[-1]] = value
                else:
                    updated_data[field] = value
                break

    return updated_data


# Create the workflow graph
def create_accounting_agent_workflow():
    """Create and configure the workflow for the accounting agent"""
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("initialize_task", initialize_task)
    workflow.add_node("process_task", process_task)
    workflow.add_node("validate_response", validate_response)
    workflow.add_node("determine_next_step", determine_next_step)
    workflow.add_node("request_missing_data", request_missing_data)
    workflow.add_node("request_human_help", request_human_help)
    workflow.add_node("fix_errors", fix_errors)
    workflow.add_node("make_final_response", make_final_response)

    # Conditional edges from initialize_task
    def route_initialize_task(state):
        if state["next_action"] == "request_missing_data":
            return "request_missing_data"
        elif state["next_action"] == "process_task":
            return "process_task"
        else:
            raise ValueError(f"Unknown next_action from initialize_task: {state['next_action']}")

    workflow.add_conditional_edges(
        "initialize_task",
        route_initialize_task,
        {
            "request_missing_data": "request_missing_data",
            "process_task": "process_task",
        }
    )

    # Unconditional edge
    workflow.add_edge("process_task", "validate_response")

    # Unconditional edge
    workflow.add_edge("validate_response", "determine_next_step")

    # Conditional edges from determine_next_step
    def route_determine_next_step(state):
        if state["next_action"] == "request_missing_data":
            return "request_missing_data"
        elif state["next_action"] == "request_human_help":
            return "request_human_help"
        elif state["next_action"] == "fix_errors":
            return "fix_errors"
        elif state["next_action"] == "make_final_response":
            return "make_final_response"
        elif state["next_action"] == END:
            return END
        else:
            raise ValueError(f"Unknown next_action from determine_next_step: {state['next_action']}")

    workflow.add_conditional_edges(
        "determine_next_step",
        route_determine_next_step,
        {
            "request_missing_data": "request_missing_data",
            "request_human_help": "request_human_help",
            "fix_errors": "fix_errors",
            "make_final_response": "make_final_response",
            END: END,
        }
    )

    workflow.add_edge("request_missing_data", END)
    workflow.add_edge("request_human_help", END)

    workflow.add_edge("fix_errors", "validate_response")

    # Set the entry point
    workflow.set_entry_point("initialize_task")

    return workflow


# Interface to run the accounting agent
class AccountingAgentRunner:
    def __init__(self):
        self.workflow = create_accounting_agent_workflow().compile()

    async def start_new_task(self, task_type: TaskType, initial_data: Dict[str, Any], initial_message: str) -> SSEEmitter:
        """Start a new accounting task and return the SSE emitter"""
        # Create SSE emitter
        sse_emitter = SSEEmitter()

        # Create initial state
        initial_state = {
            "conversation_id": str(uuid.uuid4()),
            "messages": [HumanMessage(content=initial_message)],
            "task_type": task_type,
            "data": initial_data,
            "missing_data": [],
            "validation_errors": [],
            "attempts": 0,
            "next_action": "",
            "complete": False,
            "calculation_results": {},
            "confidence_score": 0.0,
            "last_updated": datetime.datetime.now().isoformat(),
            "agent_logs": ["Task initialized"],
            "sse_emitter": sse_emitter
        }

        # Send initial SSE event
        await sse_emitter.emit("start", {
            "type": "thinking",
            "content": f"Starting {task_type.value} task. Initializing validation..."
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
                if msg.type == "AIMessageChunk" and metadata["langgraph_node"] == "make_final_response":
                    await sse_emitter.emit("data", {
                        "type": "data",
                        "content": msg.content
                    })

            # Send completion SSE event
            await sse_emitter.emit("complete", {
                "type": "complete",
                "content": f"Completed {initial_state['task_type'].value} task successfully"
            })
        except Exception as e:
            await sse_emitter.emit("error", {
                "type": "error",
                "content": f"Error occurred: {str(e)}"
            })
        finally:
            sse_emitter.stop()

    def continue_task(self, conversation_id: str, human_input: str) -> Dict[str, Any]:
        """Continue an existing accounting task with new human input"""
        # Load the existing state
        state = load_state_from_firebase(conversation_id)
        if not state:
            raise ValueError(f"No conversation found with ID: {conversation_id}")

        # Add the human input handler
        config = {"condition": lambda state: True}
        config["state_transition"] = lambda state, message: human_input_handler(state, message)

        # Create a sub-workflow for handling the input
        input_workflow = StateGraph(AgentState)
        input_workflow.add_node("process_input", config)
        input_workflow.set_entry_point("process_input")
        compiled_input_workflow = input_workflow.compile()

        # Run the input handler
        intermediate_state = compiled_input_workflow.invoke(state, human_input)

        # Continue the main workflow from the appropriate point
        if intermediate_state["next_action"] != END:
            return self.workflow.invoke(intermediate_state)
        else:
            return intermediate_state

    def get_task_status(self, conversation_id: str) -> Dict[str, Any]:
        """Get the current status of a task"""
        state = load_state_from_firebase(conversation_id)
        if not state:
            return {"error": "Conversation not found"}

        # Return a user-friendly summary
        return {
            "conversation_id": state["conversation_id"],
            "task_type": state["task_type"],
            "complete": state["complete"],
            "confidence_score": state["confidence_score"],
            "last_updated": state["last_updated"],
            "missing_data": state["missing_data"],
            "validation_errors": state["validation_errors"],
            "calculation_results": state["calculation_results"],
            "next_action": state["next_action"]
        }


# Example:
# result = run_accounting_agent("balance_sheet", {"company_name": "ABC Corp", "reporting_period": "Q1 2023"})