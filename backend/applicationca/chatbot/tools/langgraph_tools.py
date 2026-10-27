import json
from typing_extensions import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from sympy import sympify, SympifyError
from applicationca.database.database_retrieval import get_file_content
from applicationca.aws.constants.AwsConstants import USER_DATA_S3_BUCKET_NAME, FILE_VIEW_URL


@tool
def get_document_content(filename: str, state: Annotated[dict, InjectedState]) -> str:
    """
    Retrieves the complete content of a document when existing information is insufficient.

    This function should ONLY be called when the current available information about a document is
    insufficient to answer the user's query. It fetches the entire content of a specified document
    from storage based on the provided parameters, returning the full document as a JSON string.

    Args:
        filename (str): The name of the file to retrieve

    Returns:
        str: JSON string containing either the document content or an error message

    Usage Notes:
        - Use this as a last resort after checking if existing document metadata or cached snippets
          can satisfy the query
        - Retrieving full documents is resource-intensive; avoid unnecessary calls
        - Always verify the user has appropriate access permissions before retrieving content
    """
    try:
        user_id = state["user_id"]
        folder_id = state["folder_id"]
        print("retrieve_full_document ")
        print(f"{user_id} {filename} {folder_id}")
        content = json.dumps(get_file_content(user_id, filename, folder_id))
        return content
    except Exception as e:
        error_msg = f"Error retrieving document: {str(e)}"
        print(error_msg)
        return json.dumps({"error": error_msg})


@tool
def calculate(expression: str) -> str:
    """
    Evaluates a mathematical expression and returns the result.

    This function takes a string representing a mathematical expression, evaluates it using the
    SymPy library, and returns the result in both string and float format. It can handle various
    mathematical operations, functions, and symbols supported by SymPy. If the expression is invalid,
    it returns an error message.

    Args:
        expression (str): A string representing the mathematical expression to evaluate

    Returns:
        str: JSON string containing the evaluation result as both string and float, or an error message
             if the expression is invalid. The result format is:
             {"result_str": "string_representation", "result_float": numerical_value}
    """
    try:
        print(expression)
        # Evaluate the expression using sympy
        result = sympify(expression).evalf()
        # Convert the result to a string for JSON serialization
        result_str = str(result)
        # Optionally, you could convert to float if the result is numeric
        result_float = float(result)
        # Create a dictionary with the results
        output = {
            "result_str": result_str,
            "result_float": result_float
        }
        # Dump the dictionary to a JSON string
        output = json.dumps(output)
        return output
    except SympifyError:
        return json.dumps({"error": "Invalid math expression."})


@tool
def retrive_document(filename: str, folder_id:str, state: Annotated[dict, InjectedState]) -> str:
    """
    Generates a pre-signed URL for any document referenced in responses.

    Generates a pre-signed URL for any document referenced in responses.

    This function should be called automatically for EVERY document mentioned or referenced in a
    response to a user query and also when user ask for any document or documents. It creates a temporary, secure URL that provides time-limited access
    to the referenced file stored in cloud storage. When responding to users about any document,
    always include these download links.


    Args:
        filename (str): The name of the file to generate a URL for
        folder_id (str): The folder_id for the document to fetch

    Returns:
        str: A pre-signed URL that can be used to download the file temporarily

    Usage Notes:
        - Generate download links for all documents mentioned in responses
        - Always include a phrase like "You can download [document name] here: [URL]" in responses
        - Format should follow: "I referred to [document name] ([download link]) in my answer"
        - Links expire after a set period, so they should be used promptly
        - Verify user permissions before generating links

    Raises:
        Exception: If there's an error generating the pre-signed URL
    """
    try:
        user_id = state["user_id"]
        state_folder_id = state["folder_id"]
        if(state_folder_id != None):
            folder_id = state_folder_id
        object_key = f"{user_id}/{folder_id}/{filename}"
        return FILE_VIEW_URL + "/" + object_key

    except Exception as e:
        raise Exception(f"Error generating pre-signed URL: {str(e)}")