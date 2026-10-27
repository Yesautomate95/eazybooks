import time

from google.adk.agents.loop_agent import LoopAgent
from google.adk.agents.llm_agent import LlmAgent
from google.genai import types
from google.adk.events import Event, EventActions
from google.adk.sessions import DatabaseSessionService
from google.adk.runners import Runner
from google.adk.models.lite_llm import LiteLlm
from utils.sse_emitter import SSEEmitter
from dotenv import load_dotenv
from applicationca.chatbot.tools.langgraph_tools import calculate, retrieve_full_document_content, generate_presigned_url
from google.adk.tools import FunctionTool

load_dotenv()

# --- Constants ---
APP_NAME = "financial_advisor_bot"
OPEN_AI_MODEL = "gpt-4.1-mini"

# --- State Keys ---
STATE_USER_QUERY = "user_query"
STATE_SEARCH_CONTEXT = "search_context"
STATE_FINANCIAL_RESPONSE = "financial_response"
STATE_AUDIT_FEEDBACK = "audit_feedback"

# tools = [FunctionTool(func=calculate), FunctionTool(func=retrieve_full_document_content), FunctionTool(func=generate_presigned_url)]

# Financial Advisor Agent
financial_advisor = LlmAgent(
    name="FinancialResponseGenerator",
    model=LiteLlm(model=OPEN_AI_MODEL),
    instruction=f"""
    You are an Accounting and Financial Response Generator specialized in providing professional CA and audit-quality answers.

    1. Analyze the user query
    2. Incorporate any relevant information from the search context
    3. If this is your first response, generate a comprehensive, professionally formatted answer that adheres to CA standards
    4. If there is feedback in state key '{STATE_AUDIT_FEEDBACK}', meticulously revise your answer addressing all points raised

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

    Output only your professional CA-standard response.
    """,
    description="Generates and refines CA-quality accounting and financial responses",
    output_key=STATE_FINANCIAL_RESPONSE
)

# Auditor Agent
verification_agent = LlmAgent(
    name="ResponseVerifier",
    model=LiteLlm(model=OPEN_AI_MODEL),
    instruction=f"""
    You are a CA Standards Verification Specialist with expertise in accounting standards, auditing procedures, and professional documentation.

    Review the response in state key '{STATE_FINANCIAL_RESPONSE}' and verify:

    1. Technical accuracy - all calculations must follow proper accounting methodologies and arithmetic correctness
    2. Standards compliance - adherence to GAAP, IFRS, local tax laws, and relevant accounting standards
    3. Professional formatting - appropriate presentation of financial data with proper notation
    4. Methodological soundness - the approach used is appropriate for the scenario in '{STATE_USER_QUERY}'
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

    Be thorough and precise in your verification to ensure the response meets the highest professional standards.
    """,
    description="Verifies CA standards compliance and technical accuracy",
    output_key=STATE_AUDIT_FEEDBACK
)

# Create the LoopAgent - This will iterate between the advisor and auditor
financial_loop_agent = LoopAgent(
    name="FinancialLoopAgent",
    sub_agents=[financial_advisor, verification_agent],
    max_iterations=3  # Maximum of 3 cycles of generation and verification
)
from config.settings import settings
db_url = settings.DATABASE_URL
# Session and Runner setup
session_service = DatabaseSessionService(db_url=db_url)
runner = Runner(agent=financial_loop_agent, app_name=APP_NAME, session_service=session_service)

class RAGLoopAgent():
    """
    This class is used to handle the Loop Agnet.
    """

    def create_or_get_session(self, chat_id, user_id, search_context, user_query):
        """
        This function either creates a new thread for the chat_id or gets the existing thread.
        """

        try:
            session = session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=chat_id)
            if not session:
                session = session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=chat_id)

            return session
        except Exception as e:
            return None

    async def call_agent(self, query, user_id, chat_id, search_context, sse_emitter: SSEEmitter, folder_id: str):
        query = "User's query: \n" + query
        query+="\n\n Search context: \n"
        query+=search_context

        query+="\n\n Internal parameter's (***do not disclose it to any where in response***)\n"
        query+=f"['user_id' : {user_id} ,  'folder_id': {folder_id}]"

        content = types.Content(role='user', parts=[types.Part(text=query)])
        session = self.create_or_get_session(chat_id, user_id, search_context, query)

        for event in runner.run(user_id=user_id, session_id=chat_id, new_message=content):
            # You can uncomment the line below to see *all* events during execution
            # print(f"  [Event] Author: {event.author}, Type: {type(event).__name__}, Final: {event.is_final_response()}, Content: {event.content}")

            # Key Concept: is_final_response() marks the concluding message for the turn.
            print(event)
            if event.is_final_response():
                if event.content and event.content.parts:
                    # Assuming text response in the first part
                    final_response_text = event.content.parts[0].text
                    await sse_emitter.emit("data", {
                        "type": "data",
                        "content": final_response_text
                    })
                elif event.actions and event.actions.escalate:  # Handle potential errors/escalations
                    final_response_text = f"Agent escalated: {event.error_message or 'No specific message.'}"
                    await sse_emitter.emit("data", {
                        "type": "data",
                        "content": final_response_text
                    })
                # Add more checks here if needed (e.g., specific error codes)
                break  # Stop processing events once the final response is found