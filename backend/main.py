import os
from typing import Dict, List, Any, Tuple, Optional, Literal, TypedDict, Union, Callable
import json
import datetime
import uuid
from enum import Enum
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, FunctionMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from firebase_setup import db

from config.settings import settings
os.environ.setdefault("OPENAI_API_KEY", settings.OPENAI_API_KEY)

# Define task types
class TaskType(str, Enum):
    BALANCE_SHEET = "balance_sheet"
    ITR_FILING = "itr_filing"
    AUDIT = "audit"
    GST_FILING = "gst_filing"
    FINANCIAL_ANALYSIS = "financial_analysis"
    TAX_PLANNING = "tax_planning"


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


# Specialized system prompts for each agent type
AGENT_PROMPTS = {
    TaskType.BALANCE_SHEET: """You are an expert accountant specializing in balance sheet preparation.
You understand GAAP, IFRS, and industry-specific accounting standards. 
Your expertise is in properly categorizing assets, liabilities, and equity components.
Always ensure that Assets = Liabilities + Equity.
For each line item, provide clear descriptions and calculations, with references to accounting standards where appropriate.
""",

    TaskType.ITR_FILING: """You are an expert tax accountant specializing in Income Tax Return (ITR) filing.
You understand the latest tax codes, deductions, exemptions, and filing requirements.
Always verify that income sources are complete and properly categorized.
Ensure all calculations follow the current tax brackets and regulations.
For each deduction or credit claimed, provide the specific section of the tax code that applies.
""",

    TaskType.AUDIT: """You are an expert auditor with deep knowledge of audit procedures and standards.
You understand risk assessment, materiality considerations, and sampling methodologies.
Always maintain professional skepticism and independence in your analysis.
Provide clear documentation of procedures followed, evidence examined, and conclusions reached.
Your work should comply with International Standards on Auditing (ISA) or local audit standards.
""",

    TaskType.GST_FILING: """You are an expert in Goods and Services Tax (GST) compliance and filing.
You understand input tax credits, place of supply rules, and GST rate classifications.
Always ensure that GSTR forms are filled accurately with proper reconciliation.
Verify that input tax claims are valid and supported by proper documentation.
Your work should comply with the latest GST laws and regulations.
""",

    TaskType.FINANCIAL_ANALYSIS: """You are an expert financial analyst specializing in financial statement analysis.
You excel at calculating and interpreting financial ratios, trend analysis, and comparative studies.
Always provide context and industry benchmarks for your analysis.
Highlight red flags or areas of concern, as well as positive indicators.
Your analysis should be objective and data-driven, with clear explanations of methodologies used.
""",

    TaskType.TAX_PLANNING: """You are an expert tax planner specializing in legitimate tax optimization strategies.
You understand tax laws and regulations thoroughly and only suggest legal methods of tax efficiency.
Always consider the long-term implications of tax planning strategies.
Provide multiple options with pros, cons, and risk assessments for each strategy.
Your recommendations should be ethical, compliant with tax laws, and suitable for the specific client situation.
"""
}

# Define validation rules for different accounting tasks
VALIDATION_RULES = {
    TaskType.BALANCE_SHEET: [
        {
            "rule": "Assets must equal liabilities plus equity",
            "validation_function": lambda data: abs(
                data.get("total_assets", 0) - (data.get("total_liabilities", 0) + data.get("total_equity", 0))) < 0.01,
            "error_message": "Balance sheet doesn't balance: Assets must equal Liabilities + Equity"
        },
        {
            "rule": "All required sections must be present",
            "validation_function": lambda data: all(k in data for k in
                                                    ["current_assets", "non_current_assets", "current_liabilities",
                                                     "non_current_liabilities", "equity"]),
            "error_message": "Balance sheet is missing required sections"
        },
        {
            "rule": "Each line item must have a corresponding value",
            "validation_function": lambda data: all(
                isinstance(v, (int, float)) for k, v in data.items() if k.endswith("_total")),
            "error_message": "Some line items are missing values or have non-numeric values"
        }
    ],
    TaskType.ITR_FILING: [
        {
            "rule": "All income sources must be accounted for",
            "validation_function": lambda data: "income_sources" in data and len(data["income_sources"]) > 0,
            "error_message": "Income sources are missing or incomplete"
        },
        {
            "rule": "Tax calculations must be accurate",
            "validation_function": lambda data: "tax_calculated" in data and isinstance(data["tax_calculated"],
                                                                                        (int, float)),
            "error_message": "Tax calculation is missing or invalid"
        },
        {
            "rule": "All required personal information must be present",
            "validation_function": lambda data: all(
                k in data.get("taxpayer_info", {}) for k in ["name", "pan", "address"]),
            "error_message": "Required taxpayer information is missing"
        }
    ],
    # Add validation rules for other task types...
}

# Define data requirements with more detailed structure
DATA_REQUIREMENTS = {
    TaskType.BALANCE_SHEET: [
        {"field": "company_name", "description": "Legal name of the company", "type": "string", "required": True},
        {"field": "reporting_period", "description": "Period the balance sheet covers (e.g., 'Q1 2023', 'FY 2022-23')",
         "type": "string", "required": True},
        {"field": "assets", "description": "Detailed breakdown of company assets", "type": "object", "required": True,
         "subfields": [
             {"field": "current_assets", "description": "Assets expected to be converted to cash within one year",
              "type": "object", "required": True},
             {"field": "non_current_assets",
              "description": "Long-term assets not expected to be converted to cash within one year", "type": "object",
              "required": True}
         ]},
        {"field": "liabilities", "description": "Detailed breakdown of company liabilities", "type": "object",
         "required": True,
         "subfields": [
             {"field": "current_liabilities", "description": "Obligations due within one year", "type": "object",
              "required": True},
             {"field": "non_current_liabilities", "description": "Long-term obligations not due within one year",
              "type": "object", "required": True}
         ]},
        {"field": "equity", "description": "Ownership interest in the company", "type": "object", "required": True}
    ],
    TaskType.ITR_FILING: [
        {"field": "tax_year", "description": "The financial year for which tax is being filed (e.g., '2023-24')",
         "type": "string", "required": True},
        {"field": "taxpayer_info", "description": "Personal information of the taxpayer", "type": "object",
         "required": True,
         "subfields": [
             {"field": "name", "description": "Full legal name", "type": "string", "required": True},
             {"field": "pan", "description": "Permanent Account Number", "type": "string", "required": True},
             {"field": "address", "description": "Current residential address", "type": "string", "required": True},
             {"field": "date_of_birth", "description": "Date of birth", "type": "string", "required": True},
             {"field": "aadhaar", "description": "Aadhaar number", "type": "string", "required": True}
         ]},
        {"field": "income_sources", "description": "All sources of income for the tax year", "type": "array",
         "required": True},
        {"field": "deductions", "description": "All applicable deductions", "type": "array", "required": True},
        {"field": "tax_paid_to_date", "description": "Taxes already paid (TDS, advance tax, etc.)", "type": "number",
         "required": True}
    ],
    # Add requirements for other task types...
}

# Initialize task-specific LLMs with different configurations for specialization
llms = {
    TaskType.BALANCE_SHEET: ChatOpenAI(model="gpt-4.1-mini", temperature=0.1),  # Lower temperature for precision
    TaskType.ITR_FILING: ChatOpenAI(model="gpt-4.1-mini", temperature=0.1),
    TaskType.AUDIT: ChatOpenAI(model="gpt-4.1-mini", temperature=0.2),
    TaskType.GST_FILING: ChatOpenAI(model="gpt-4.1-mini", temperature=0.1),
    TaskType.FINANCIAL_ANALYSIS: ChatOpenAI(model="gpt-4.1-mini", temperature=0.3),  # Slightly higher for insights
    TaskType.TAX_PLANNING: ChatOpenAI(model="gpt-4.1-mini", temperature=0.3)
}


# Memory functions
def save_state_to_firebase(state: AgentState):
    """Save the current state to Firebase"""
    state_copy = dict(state)

    # Convert message objects to serializable format
    messages_serializable = []
    for msg in state_copy["messages"]:
        if isinstance(msg, (HumanMessage, AIMessage, SystemMessage, FunctionMessage)):
            messages_serializable.append({
                "type": msg.__class__.__name__,
                "content": msg.content
            })

    state_copy["messages"] = messages_serializable
    state_copy["last_updated"] = datetime.datetime.now().isoformat()

    # Save to Firebase
    db.collection("accounting_states").document(state_copy["conversation_id"]).set(state_copy)

    # Add to log
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "action": "state_saved",
        "conversation_id": state_copy["conversation_id"]
    }
    db.collection("agent_logs").document().set(log_entry)


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


def process_task(state: AgentState) -> AgentState:
    """Process the accounting task using the specialized LLM"""
    messages = state["messages"]
    task_type = state["task_type"]
    data = state["data"]

    # Log processing
    state["agent_logs"] = state["agent_logs"] + [
        f"Processing {task_type} task at {datetime.datetime.now().isoformat()}"]

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

    result = {
        **state,
        "messages": messages,
        "calculation_results": calculation_results,
        "next_action": "validate_response"
    }

    # Save state to Firebase
    save_state_to_firebase(result)

    return result


def validate_response(state: AgentState) -> AgentState:
    """Validate the generated response against accounting rules and completeness"""
    messages = state["messages"]
    task_type = state["task_type"]
    response_content = messages[-1].content
    calculation_results = state["calculation_results"]

    # Log validation
    state["agent_logs"] = state["agent_logs"] + [f"Validating response for {task_type} task"]

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
    validation_llm = ChatOpenAI(model="gpt-4.1", temperature=0.1)
    messages.append(HumanMessage(content=validation_prompt))
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
            "next_action": END,
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

    def start_new_task(self, task_type: TaskType, initial_data: Dict[str, Any], initial_message: str) -> str:
        """Start a new accounting task"""
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
            "agent_logs": ["Task initialized"]
        }

        # Run the workflow
        result = self.workflow.stream(initial_state)

        for event in result:
            for value in event.values():
                print("Assistant:", value["messages"][-1].content)

        # Return the conversation ID for future references
        return result

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


# Example usage
if __name__ == "__main__":
    print(os.environ.get("OPENAI_API_KEY"))
    # Initialize the agent
    agent = AccountingAgentRunner()

    # Example: Start a new balance sheet task
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

    initial_message = "Please create a balance sheet for my company based on the provided data."

    conversation_id = agent.start_new_task(TaskType.BALANCE_SHEET, initial_data, initial_message)
    print(f"Started new task with conversation ID: {conversation_id}")

    # Example: Continue the task with additional information
    try:
        status = agent.get_task_status(conversation_id)
        if status.get("missing_data"):
            print("Missing data:", status["missing_data"])

            # Provide missing information
            agent.continue_task(conversation_id, """
                    Here's the missing information:
                    total_assets: 650000
                    total_liabilities: 385000
                    total_equity: 265000
                    """)
    except Exception as e:
        print(f"Error: {e}")

# API endpoints (for Flask/FastAPI implementation)
"""
@app.route("/api/accounting/tasks", methods=["POST"])
def create_task():
    data = request.json
    task_type = TaskType(data.get("task_type", "balance_sheet"))
    initial_data = data.get("data", {})
    initial_message = data.get("message", "Please process this accounting task.")

    agent = AccountingAgentRunner()
    conversation_id = agent.start_new_task(task_type, initial_data, initial_message)

    return jsonify({"conversation_id": conversation_id})

@app.route("/api/accounting/tasks/<conversation_id>", methods=["GET"])
def get_task(conversation_id):
    agent = AccountingAgentRunner()
    status = agent.get_task_status(conversation_id)
    return jsonify(status)

@app.route("/api/accounting/tasks/<conversation_id>/messages", methods=["POST"])
def add_message(conversation_id):
    data = request.json
    human_input = data.get("message", "")

    agent = AccountingAgentRunner()
    result = agent.continue_task(conversation_id, human_input)

    return jsonify({
        "conversation_id": result["conversation_id"],
        "complete": result["complete"],
        "next_action": result["next_action"],
        "confidence_score": result["confidence_score"]
    })
"""

# Example:
# result = run_accounting_agent("balance_sheet", {"company_name": "ABC Corp", "reporting_period": "Q1 2023"})