import os
from typing import Dict, List, Any, Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.messages import BaseMessage
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv
import json
import asyncio
import sys
import numpy as np
import aiohttp
import requests
import logging
from applicationca.chatbot.config.main import config
from applicationca.services.database_stats import get_database_stats
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('chat.log'),
        logging.StreamHandler()
    ],
    force=True  # Force the configuration to override any existing handlers
)
logger = logging.getLogger(__name__)
logger.propagate = True  # Allow propagation to parent loggers

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv()

# Import the required modules
from applicationca.services.BM25 import retrieve_chunks_from_db
from applicationca.contoller.vector import search

class OpenRouterChatLLM(BaseChatModel):
    """Custom LLM class for OpenRouter."""
    
    temperature: float = Field(default=0.7)
    api_key: str = Field(default="")
    api_url: str = Field(default="https://openrouter.ai/api/v1/chat/completions")
    model: str = Field(default="deepseek/deepseek-chat-v3-0324:free")
    
    def __init__(self, temperature: float = 0.7):
        from config.settings import settings as app_settings
        super().__init__(temperature=temperature, api_key=app_settings.OPENROUTER_API_KEY)
        
    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        """Synchronous method to generate chat completions."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Convert messages to OpenRouter format
        formatted_messages = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                formatted_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                formatted_messages.append({"role": "user", "content": msg.content})
            else:
                formatted_messages.append({"role": "assistant", "content": msg.content})
        
        data = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": self.temperature
        }
        
        logger.debug("Sending request to OpenRouter API")
        logger.debug(f"Model: {self.model}")
        logger.debug(f"Temperature: {self.temperature}")
        logger.debug(f"Message count: {len(formatted_messages)}")
        
        response = requests.post(self.api_url, headers=headers, json=data)
        
        if response.status_code != 200:
            logger.error("OpenRouter API request failed")
            logger.error(f"Status code: {response.status_code}")
            logger.error(f"Error response: {response.text}")
            raise Exception(f"OpenRouter API error: {response.text}")
        
        result = response.json()
        logger.debug(f"OpenRouter API response received (status: {response.status_code})")
        
        if "choices" not in result or not result["choices"]:
            logger.error("OpenRouter API returned no choices")
            raise Exception("No response from OpenRouter API")
        
        content = result["choices"][0]["message"]["content"]
        logger.debug(f"Generated response length: {len(content)} characters")
        return ChatResult(generations=[ChatGeneration(message=HumanMessage(content=content))])
        
    async def _acall(self, messages: List[BaseMessage], **kwargs) -> ChatResult:
        """Asynchronous method to generate chat completions."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": self.model,
            "messages": [{"role": msg.type, "content": msg.content} for msg in messages],
            "temperature": self.temperature
        }
        
        logger.debug("Sending async request to OpenRouter API")
        logger.debug(f"Model: {self.model}")
        logger.debug(f"Temperature: {self.temperature}")
        logger.debug(f"Message count: {len(messages)}")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(self.api_url, headers=headers, json=data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error("OpenRouter API async request failed")
                    logger.error(f"Status code: {response.status}")
                    logger.error(f"Error response: {error_text}")
                    raise Exception(f"OpenRouter API error: {error_text}")
                
                result = await response.json()
                logger.debug(f"OpenRouter API async response received (status: {response.status})")
                
                if "choices" not in result or not result["choices"]:
                    logger.error("OpenRouter API returned no choices")
                    raise Exception("No response from OpenRouter API")
                
                content = result["choices"][0]["message"]["content"]
                logger.debug(f"Generated async response length: {len(content)} characters")
                return ChatResult(generations=[ChatGeneration(message=HumanMessage(content=content))])
    
    def _llm_type(self) -> str:
        return "openrouter-chat"

# Initialize LLM instance at module level
llm = OpenRouterChatLLM(temperature=0.7)

# Define our state
class AgentState(BaseModel):
    """State for the data retrieval agent."""
    user_input: str
    user_id: Optional[str] = None
    folder_id: Optional[str] = None
    current_node: str = "plan"  # Track current node in the workflow
    current_step: int = 0  # Track current step in the execution plan
    execution_plan: List[Dict[str, Any]] = Field(default_factory=list)
    intermediate_results: List[Dict[str, Any]] = Field(default_factory=list)
    final_output: Optional[str] = None
    quality_score: float = 0.0
    data_sufficiency: bool = False
    iteration_count: int = 0
    thoughts: List[Dict[str, Any]] = Field(default_factory=list)
    thought_process: List[Dict[str, Any]] = Field(default_factory=list)
    required_data_types: List[str] = Field(default_factory=list)
    data_requirements: Dict[str, Any] = Field(default_factory=dict)
    data_completeness: Dict[str, Any] = Field(default_factory=dict)
    analysis_feedback: List[str] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
        
    def model_dump(self, **kwargs):
        """Custom serialization to handle numpy types and integers."""
        def convert_value(v):
            if isinstance(v, (np.float32, np.float64)):
                return float(v)
            elif isinstance(v, np.ndarray):
                return v.tolist()
            elif isinstance(v, (list, tuple)):
                return [convert_value(i) for i in v]
            elif isinstance(v, dict):
                return {k: convert_value(v) for k, v in v.items()}
            elif isinstance(v, (int, float)):
                return str(v) if isinstance(v, int) else v
            return v
        
        data = super().model_dump(**kwargs)
        return {k: convert_value(v) for k, v in data.items()}
    
    def json(self, **kwargs):
        """Custom JSON serialization."""
        return json.dumps(self.model_dump(**kwargs))

async def determine_search_strategy(query: str, user_id: Optional[str] = None, folder_id: Optional[str] = None, llm: OpenRouterChatLLM = None) -> Dict[str, Any]:
    """Determine the optimal search strategy based on the query and database stats."""
    logger.info(f"Query: {query[:100]}...")
    
    # Get database statistics if user_id is provided
    stats = None
    if user_id:
        try:
            stats = await get_database_stats(user_id, folder_id)
            logger.info(f"Database stats: {stats['total_documents']} documents, {stats['total_chunks']} chunks")
        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
    
    # Prepare system prompt with database stats if available
    system_prompt = """You are a search strategy expert. Analyze the query and determine the optimal search approach.
    Consider:
    1. Query specificity (broad vs specific)
    2. Required coverage (all items vs subset)
    3. Data volume (small vs large dataset)
    4. Time sensitivity
    5. Accuracy requirements
    
    Return a JSON object with:
    {
        "strategy": {
            "primary_method": "semantic" or "exact",
            "limit": number (based on expected dataset size),
            "confidence_threshold": number (0-1),
            "reasoning": "explanation of the strategy"
        },
        "fallback": {
            "method": "alternative method if primary fails",
            "limit": number
        }
    }
    """
    
    # Add database stats to the user message if available
    user_message = f"Query: {query}"
    if stats:
        user_message += f"\n\nDatabase stats:\n- Total documents: {stats['total_documents']}\n- Total chunks: {stats['total_chunks']}\n- Average chunks per document: {stats['avg_chunks_per_doc']}"
        if stats['doc_type_distribution']:
            user_message += "\n- Document type distribution: " + ", ".join([f"{k}: {v}" for k, v in stats['doc_type_distribution'].items()])
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message)
    ]
    
    try:
        response = await llm.ainvoke(messages)
        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        strategy_data = json.loads(content)
        if "strategy" not in strategy_data:
            raise ValueError("Response missing 'strategy' key")
        
        # Adjust limit based on database stats if available
        if stats and "limit" in strategy_data["strategy"]:
            total_chunks = stats["total_chunks"]
            if total_chunks < 1000:
                strategy_data["strategy"]["limit"] = min(strategy_data["strategy"]["limit"], 20)
            elif total_chunks < 10000:
                strategy_data["strategy"]["limit"] = min(strategy_data["strategy"]["limit"], 50)
            else:
                strategy_data["strategy"]["limit"] = min(strategy_data["strategy"]["limit"], 100)
        
        logger.info(f"Strategy: {strategy_data['strategy']['primary_method']}")
        return strategy_data
    except Exception as e:
        logger.error(f"Strategy error: {str(e)}")
        return {
            "strategy": {
                "primary_method": "hybrid",
                "limit": 50,
                "confidence_threshold": 0.7,
                "reasoning": "Default strategy due to error"
            },
            "fallback": {
                "method": "exact",
                "limit": 100
            }
        }

async def plan_approach(state: AgentState) -> Dict[str, Any]:
    """Plan how to approach the data retrieval task using ReAct framework."""
    logger.info("\n=== Planning ===")
    logger.info(f"Query: {state.user_input}")
    
    # Get search strategy
    search_strategy = await determine_search_strategy(state.user_input, state.user_id, state.folder_id, llm)
    
    logger.info(f"Method: {search_strategy['strategy']['primary_method']}")
    logger.info(f"Limit: {search_strategy['strategy']['limit']}")
    logger.info(f"Reasoning: {search_strategy['strategy']['reasoning']}")
    
    system_prompt = """You are a strategic planning expert for data retrieval.
    Given a user's request and search strategy, create a step-by-step plan using the ReAct framework:
    1. REASON about what information is needed and how to get it
    2. Plan appropriate ACTIONS to retrieve the data
    3. Consider potential challenges and fallback approaches
    
    Available actions:
    - SEARCH_SEMANTIC: Use vector search for semantic understanding
    - SEARCH_EXACT: Use database search for exact matches
    - COMBINE_RESULTS: Merge and analyze results from different sources
    - GET_DATABASE_STATS: Get statistics about the database
    - FILTER_RESULTS: Apply specific filters to narrow down results
    - SUMMARIZE: Create a concise summary of findings
    - VERIFY_COVERAGE: Check if we have comprehensive results
    
    IMPORTANT: Your response must be a valid JSON object with this exact structure:
    {
        "reasoning": "Your step-by-step reasoning",
        "plan": [
            {
                "step": "1",
                "action": "action name",
                "reason": "why this action",
                "details": {
                    "limit": number,
                    "confidence_threshold": number,
                    "other_params": {}
                }
            }
        ]
    }
    """
    
    user_message = f"User request: {state.user_input}\n\nSearch strategy: {json.dumps(search_strategy, indent=2)}"
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message)
    ]
    
    try:
        response = await llm.ainvoke(messages)
        plan_data = response.content.strip()
        
        if "```json" in plan_data:
            plan_data = plan_data.split("```json")[1].split("```")[0].strip()
        elif "```" in plan_data:
            plan_data = plan_data.split("```")[1].split("```")[0].strip()
        
        parsed_plan = json.loads(plan_data)
        
        # Validate actions
        valid_actions = {
            "SEARCH_SEMANTIC", "SEARCH_EXACT", "GET_DATABASE_STATS", "COMBINE_RESULTS",
            "FILTER_RESULTS", "SUMMARIZE", "VERIFY_COVERAGE"
        }
        
        for step in parsed_plan["plan"]:
            if step["action"] not in valid_actions:
                logger.error(f"Invalid action in plan: {step['action']}")
                raise ValueError(f"Invalid action: {step['action']}")
            if step["action"] in ["SEARCH_SEMANTIC", "SEARCH_EXACT"]:
                step["details"] = {
                    "limit": search_strategy["strategy"]["limit"],
                    "confidence_threshold": search_strategy["strategy"]["confidence_threshold"]
                }
        
        logger.info("\nPlan:")
        for step in parsed_plan["plan"]:
            logger.info(f"  {step['step']}. {step['action']}: {step['reason']}")
        
        return {
            "execution_plan": parsed_plan["plan"],
            "thoughts": state.thoughts + [{
                "stage": "planning",
                "thought": parsed_plan["reasoning"],
                "strategy": search_strategy
            }]
        }
    except Exception as e:
        logger.error(f"Planning error: {str(e)}")
        return {
            "thoughts": state.thoughts + [{
                "stage": "planning",
                "error": str(e)
            }]
        }

async def execute_step(state: AgentState) -> Dict[str, Any]:
    """Execute the current step in the execution plan."""
    # Check if the execution plan is empty
    if not state.execution_plan:
        logger.info("No execution plan, returning to planning")
        return {
            "current_step": 0,
            "thoughts": state.thoughts + [{
                "stage": "execution",
                "action": "REPLAN",
                "result": "No execution plan, returning to planning"
            }]
        }
    
    # Check if we've completed all steps
    if state.current_step >= len(state.execution_plan):
        logger.info("All steps completed, ending execution")
        return {
            "current_step": state.current_step,
            "thoughts": state.thoughts + [{
                "stage": "execution",
                "action": "COMPLETE",
                "result": "All steps completed"
            }]
        }
    
    logger.info(f"\n=== Step {state.current_step + 1}: {state.execution_plan[state.current_step]['action']} ===")
    logger.info(f"Reason: {state.execution_plan[state.current_step]['reason']}")
    
    current_step = state.execution_plan[state.current_step]
    action = current_step["action"]
    
    try:
        if action == "SEARCH_SEMANTIC":
            # Get database stats first
            stats = await get_database_stats(state.user_id, state.folder_id)
            logger.info(f"Database stats: {stats['total_documents']} documents, {stats['total_chunks']} chunks")
            
            # Perform semantic search - remove limit and confidence_threshold parameters
            results = await search(
                query=state.user_input,
                user_id=state.user_id,
                folder=state.folder_id
            )
            
            if not results:
                logger.info("No semantic search results found")
                # If this is the last step, set a final output
                if state.current_step == len(state.execution_plan) - 1:
                    return {
                        "intermediate_results": state.intermediate_results + [{
                            "step": state.current_step,
                            "action": action,
                            "results": [],
                            "metadata": {
                                "total_results": 0
                            }
                        }],
                        "current_step": state.current_step + 1,
                        "thoughts": state.thoughts + [{
                            "stage": "execution",
                            "action": action,
                            "result": "No results found"
                        }],
                        "final_output": "No results found for your query. Please try a different search term or check if the documents exist in the database."
                    }
                return {
                    "intermediate_results": state.intermediate_results + [{
                        "step": state.current_step,
                        "action": action,
                        "results": [],
                        "metadata": {
                            "total_results": 0
                        }
                    }],
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results found"
                    }]
                }
            
            # Filter results by confidence threshold if specified
            confidence_threshold = current_step.get("details", {}).get("confidence_threshold", 0)
            if confidence_threshold > 0:
                results = [r for r in results if r.get("score", 0) >= confidence_threshold]
            
            logger.info(f"Found {len(results)} results")
            return {
                "intermediate_results": state.intermediate_results + [{
                    "step": state.current_step,
                    "action": action,
                    "results": results,
                    "metadata": {
                        "total_results": len(results)
                    }
                }],
                "current_step": state.current_step + 1,
                "thoughts": state.thoughts + [{
                    "stage": "execution",
                    "action": action,
                    "result": f"Found {len(results)} results"
                }]
            }
            
        elif action == "SEARCH_EXACT":
            # Perform exact search
            limit = current_step.get("details", {}).get("limit", 100)  # Default to 100 for better coverage
            results = retrieve_chunks_from_db(
                query=state.user_input,
                folder_id=state.folder_id,
                user_id=state.user_id,
                top_k=limit
            )
            
            if not results:
                logger.info("No exact search results found")
                # If this is the last step, set a final output
                if state.current_step == len(state.execution_plan) - 1:
                    return {
                        "intermediate_results": state.intermediate_results + [{
                            "step": state.current_step,
                            "action": action,
                            "results": [],
                            "metadata": {
                                "limit": limit,
                                "total_results": 0
                            }
                        }],
                        "current_step": state.current_step + 1,
                        "thoughts": state.thoughts + [{
                            "stage": "execution",
                            "action": action,
                            "result": "No results found"
                        }],
                        "final_output": "No results found for your query. Please try a different search term or check if the documents exist in the database."
                    }
                return {
                    "intermediate_results": state.intermediate_results + [{
                        "step": state.current_step,
                        "action": action,
                        "results": [],
                        "metadata": {
                            "limit": limit,
                            "total_results": 0
                        }
                    }],
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results found"
                    }]
                }
            
            logger.info(f"Found {len(results)} results")
            return {
                "intermediate_results": state.intermediate_results + [{
                    "step": state.current_step,
                    "action": action,
                    "results": results,
                    "metadata": {
                        "limit": limit,
                        "total_results": len(results)
                    }
                }],
                "current_step": state.current_step + 1,
                "thoughts": state.thoughts + [{
                    "stage": "execution",
                    "action": action,
                    "result": f"Found {len(results)} results"
                }]
            }
            
        elif action == "VERIFY_COVERAGE":
            if not state.intermediate_results:
                logger.info("No results to verify coverage")
                # If this is the last step, set a final output
                if state.current_step == len(state.execution_plan) - 1:
                    return {
                        "current_step": state.current_step + 1,
                        "thoughts": state.thoughts + [{
                            "stage": "execution",
                            "action": action,
                            "result": "No results to verify"
                        }],
                        "final_output": "No results found for your query. Please try a different search term or check if the documents exist in the database."
                    }
                return {
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results to verify"
                    }]
                }
            
            all_results = []
            for result in state.intermediate_results:
                if result["action"] in ["SEARCH_SEMANTIC", "SEARCH_EXACT"]:
                    all_results.extend(result.get("results", []))
            
            if not all_results:
                logger.info("No results found in any search")
                return {
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results found"
                    }]
                }
            
            unique_results = []
            seen_content = set()
            for result in all_results:
                content = result.get("content", "")
                if content and content not in seen_content:
                    seen_content.add(content)
                    unique_results.append(result)
            
            logger.info(f"Unique results: {len(unique_results)}")
            
            system_prompt = """Analyze the search results and verify if we have comprehensive coverage.
            Consider:
            1. Are all expected items present?
            2. Are there any obvious gaps?
            3. Should we perform additional searches?
            
            Return a JSON object with:
            {
                "is_complete": boolean,
                "missing_items": ["list of potentially missing items"],
                "confidence": number (0-1),
                "recommendation": "what to do next"
            }
            """
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Query: {state.user_input}\n\nResults: {json.dumps(unique_results)}")
            ]
            
            try:
                response = await llm.ainvoke(messages)
                verification = json.loads(response.content)
            except json.JSONDecodeError:
                logger.error("Failed to parse LLM response as JSON")
                verification = {
                    "is_complete": False,
                    "missing_items": ["Failed to analyze results"],
                    "confidence": 0.0,
                    "recommendation": "Try alternative search"
                }
            
            logger.info(f"Coverage: {verification['is_complete']} (confidence: {verification['confidence']})")
            logger.info(f"Recommendation: {verification['recommendation']}")
            
            if not verification["is_complete"]:
                logger.info("Adding additional search")
                return {
                    "intermediate_results": state.intermediate_results + [{
                        "step": state.current_step,
                        "action": action,
                        "results": verification
                    }],
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": f"Coverage: {verification['recommendation']}"
                    }],
                    "execution_plan": state.execution_plan + [{
                        "step": str(len(state.execution_plan) + 1),
                        "action": "SEARCH_EXACT",
                        "reason": "Additional search needed",
                        "details": {
                            "limit": 100,
                            "confidence_threshold": 0.5
                        }
                    }]
                }
            
            return {
                "intermediate_results": state.intermediate_results + [{
                    "step": state.current_step,
                    "action": action,
                    "results": verification
                }],
                "current_step": state.current_step + 1,
                "thoughts": state.thoughts + [{
                    "stage": "execution",
                    "action": action,
                    "result": "Coverage verified"
                }]
            }
            
        elif action == "FILTER_RESULTS":
            if not state.intermediate_results:
                logger.info("No results to filter")
                # If this is the last step, set a final output
                if state.current_step == len(state.execution_plan) - 1:
                    return {
                        "current_step": state.current_step + 1,
                        "thoughts": state.thoughts + [{
                            "stage": "execution",
                            "action": action,
                            "result": "No results to filter"
                        }],
                        "final_output": "No results found for your query. Please try a different search term or check if the documents exist in the database."
                    }
                return {
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results to filter"
                    }]
                }
            
            latest_result = state.intermediate_results[-1]
            if "results" not in latest_result or not latest_result["results"]:
                logger.info("No results in latest step to filter")
                return {
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results to filter"
                    }]
                }
            
            latest_results = latest_result["results"]
            filters = current_step.get("details", {})
            
            try:
                if isinstance(filters, dict):
                    filter_terms = list(filters.values())
                elif isinstance(filters, (list, tuple)):
                    filter_terms = list(filters)
                elif isinstance(filters, str):
                    filter_terms = [filters]
                else:
                    filter_terms = []
                
                filter_terms = [str(term).lower() for term in filter_terms if term]
                logger.info(f"Filters: {filter_terms}")
                
                if not filter_terms:
                    filtered_results = latest_results
                else:
                    if isinstance(latest_results, str):
                        sections = [s.strip() for s in latest_results.split('\n') if s.strip()]
                        filtered_sections = []
                        for section in sections:
                            if all(term in section.lower() for term in filter_terms):
                                filtered_sections.append(section)
                        filtered_results = '\n'.join(filtered_sections) if filtered_sections else latest_results
                    elif isinstance(latest_results, (list, tuple)):
                        filtered_results = [
                            result for result in latest_results
                            if all(term in str(result).lower() for term in filter_terms)
                        ]
                        filtered_results = filtered_results if filtered_results else latest_results
                    elif isinstance(latest_results, dict):
                        filtered_results = {
                            k: v for k, v in latest_results.items()
                            if all(term in str(v).lower() for term in filter_terms)
                        }
                        filtered_results = filtered_results if filtered_results else latest_results
                    else:
                        filtered_results = latest_results
                
                logger.info(f"Filtered: {len(filtered_results) if isinstance(filtered_results, (list, tuple)) else 'N/A'} results")
                return {
                    "intermediate_results": state.intermediate_results + [{
                        "step": state.current_step,
                        "action": action,
                        "results": filtered_results
                    }],
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "Results filtered"
                    }]
                }
                
            except Exception as e:
                logger.error(f"Filter error: {str(e)}")
                return {
                    "intermediate_results": state.intermediate_results + [{
                        "step": state.current_step,
                        "action": action,
                        "results": latest_results
                    }],
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": f"Filter error: {str(e)}"
                    }]
                }
            
        elif action == "SUMMARIZE":
            if not state.intermediate_results:
                logger.info("No results to summarize")
                # If this is the last step, set a final output
                if state.current_step == len(state.execution_plan) - 1:
                    return {
                        "current_step": state.current_step + 1,
                        "thoughts": state.thoughts + [{
                            "stage": "execution",
                            "action": action,
                            "result": "No results to summarize"
                        }],
                        "final_output": "No results found for your query. Please try a different search term or check if the documents exist in the database."
                    }
                return {
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results to summarize"
                    }]
                }
            
            latest_result = state.intermediate_results[-1]
            if "results" not in latest_result or not latest_result["results"]:
                logger.info("No results in latest step to summarize")
                return {
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results to summarize"
                    }]
                }
            
            latest_results = latest_result["results"]
            
            def convert_to_serializable(obj):
                if isinstance(obj, (np.float32, np.float64)):
                    return float(obj)
                elif isinstance(obj, (list, tuple)):
                    return [convert_to_serializable(item) for item in obj]
                elif isinstance(obj, dict):
                    return {k: convert_to_serializable(v) for k, v in obj.items()}
                return obj
            
            serializable_results = convert_to_serializable(latest_results)
            
            system_prompt = """Create a clear, concise summary of the findings.
            Focus on:
            1. Key information
            2. Direct answers to the user's query
            3. Important context
            4. Confidence level in the results
            
            Format the summary in a user-friendly way.
            """
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Original query: {state.user_input}\n\nResults to summarize: {json.dumps(serializable_results)}")
            ]
            response = await llm.ainvoke(messages)
            
            logger.info("Summary generated")
            return {
                "intermediate_results": state.intermediate_results + [{
                    "step": state.current_step,
                    "action": action,
                    "results": response.content
                }],
                "current_step": state.current_step + 1,
                "thoughts": state.thoughts + [{
                    "stage": "execution",
                    "action": action,
                    "result": "Summary created"
                }],
                "final_output": response.content
            }
            
        elif action == "COMBINE_RESULTS":
            if not state.intermediate_results:
                logger.info("No results to combine")
                # If this is the last step, set a final output
                if state.current_step == len(state.execution_plan) - 1:
                    return {
                        "current_step": state.current_step + 1,
                        "thoughts": state.thoughts + [{
                            "stage": "execution",
                            "action": action,
                            "result": "No results to combine"
                        }],
                        "final_output": "No results found for your query. Please try a different search term or check if the documents exist in the database."
                    }
                return {
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No results to combine"
                    }]
                }
            
            def make_serializable(obj):
                if isinstance(obj, (np.float32, np.float64)):
                    return float(obj)
                elif isinstance(obj, np.ndarray):
                    return obj.tolist()
                elif isinstance(obj, (list, tuple)):
                    return [make_serializable(item) for item in obj]
                elif isinstance(obj, dict):
                    return {k: make_serializable(v) for k, v in obj.items()}
                return obj
            
            serialized_results = []
            for result in state.intermediate_results:
                if "results" in result:
                    serialized_results.append({
                        "step": result["step"],
                        "results": make_serializable(result["results"])
                    })
            
            if not serialized_results:
                logger.info("No valid results to combine")
                return {
                    "current_step": state.current_step + 1,
                    "thoughts": state.thoughts + [{
                        "stage": "execution",
                        "action": action,
                        "result": "No valid results to combine"
                    }]
                }
            
            context = "\n".join([
                f"Results from step {result['step']}: {json.dumps(result['results'])}"
                for result in serialized_results
            ])
            
            system_prompt = """Analyze and combine the search results.
            Create a coherent response that:
            1. Removes duplicates
            2. Ranks by relevance
            3. Synthesizes information
            4. Provides clear citations
            
            Return your analysis in a clear, structured format.
            """
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Original query: {state.user_input}\n\nSearch results:\n{context}")
            ]
            response = await llm.ainvoke(messages)
            
            logger.info("Results combined")
            return {
                "intermediate_results": state.intermediate_results + [{
                    "step": state.current_step,
                    "action": action,
                    "results": response.content
                }],
                "current_step": state.current_step + 1,
                "thoughts": state.thoughts + [{
                    "stage": "execution",
                    "action": action,
                    "result": "Results combined"
                }]
            }
            
        elif action == "GET_DATABASE_STATS":
            stats = await get_database_stats(state.user_id, state.folder_id)
            logger.info(f"Database stats: {stats['total_documents']} documents, {stats['total_chunks']} chunks")
            return {
                "intermediate_results": state.intermediate_results + [{
                    "step": state.current_step,
                    "action": action,
                    "results": stats
                }],
                "current_step": state.current_step + 1,
                "thoughts": state.thoughts + [{
                    "stage": "execution",
                    "action": action,
                    "result": "Database stats retrieved"
                }] 
            }        
                    
    except Exception as e:
        logger.error(f"Step error: {str(e)}")
        return {
            "current_step": state.current_step + 1,
            "thoughts": state.thoughts + [{
                "stage": "execution",
                "error": f"Error in {action}: {str(e)}"
            }],
            "final_output": f"An error occurred while processing your query: {str(e)}"
        }

async def should_continue(state: AgentState) -> Literal["continue", "end"]:
    """Determine if we should continue executing steps or end."""
    # If we have a final output, we're done
    if state.final_output is not None:
        return "end"
    
    # If we have no execution plan, we need to plan
    if not state.execution_plan:
        return "continue"
    
    # If we've completed all steps in the plan
    if state.current_step >= len(state.execution_plan):
        return "end"
    
    # Continue with the current plan
    return "continue"

def create_agent_graph():
    """Create the agent workflow graph."""
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("plan", plan_approach)
    workflow.add_node("execute", execute_step)
    
    # Define the edges
    workflow.set_entry_point("plan")
    workflow.add_conditional_edges(
        "execute",
        should_continue,
        {
            "continue": "execute",
            "end": END
        }
    )
    workflow.add_edge("plan", "execute")
    
    return workflow.compile()

async def data_retrieval_agent(query: str, user_id: Optional[str] = None, folder_id: Optional[str] = None) -> str:
    """Execute the data retrieval agent."""
    logger.info(f"Starting data retrieval agent for query: {query[:100]}...")
    
    # Initialize the agent state
    initial_state = AgentState(
        user_input=query,
        user_id=user_id,
        folder_id=folder_id,
        thought_process=[],
        execution_plan=[],
        current_step=0,
        intermediate_results=[],
        final_output=None,
        required_data_types=[],
        data_requirements={},
        data_completeness={},
        iteration_count=0,
        analysis_feedback=[]
    )
    
    try:
        # Create and compile the agent graph
        agent_graph = create_agent_graph()
        
        # Run the graph asynchronously
        final_state = await agent_graph.ainvoke(initial_state)
        
        # Extract the final output from the state dictionary
        final_output = final_state.get("final_output", "No response generated.")
        logger.info("Data retrieval agent completed successfully")
        logger.info("\n=== Final Output ===")
        logger.info(final_output)
        
        return final_output
    except Exception as e:
        logger.error(f"=== Error in Data Retrieval ===")
        logger.error(f"Error Type: {type(e).__name__}")
        logger.error(f"Error Message: {str(e)}")
        return f"Error: {str(e)}"

if __name__ == "__main__":
    # Test the data retrieval agent
    query = "Give me me the balance sheet of Baldeep"
    user_id = "qwToUMaVRpbhui9XTqnx6Rog2gj1"  # Baldeep Singh Chhabra's user_id
    folder_id = "0a2f28aa-449b-409c-8519-6f1aa27371cc"  # Pitch Decks folder
    
    logger.info(f"Testing data retrieval agent with query: {query}")
    
    # Only use asyncio.run() when running directly
    result = asyncio.run(data_retrieval_agent(query, user_id, folder_id))
    logger.info("Test completed successfully")