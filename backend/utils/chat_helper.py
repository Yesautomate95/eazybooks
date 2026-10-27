import json

from openai import OpenAI
from tavily import TavilyClient

from finadvisor.chatbot.constants.eazybooks_secrets import open_ai_key

client = OpenAI(api_key=open_ai_key)

def extract_user_id(message):
    # Split the message by "user_id: " and take the second part
    parts = message.split("user_id: ")
    if len(parts) > 1:
        # Further split by spaces to isolate the user ID
        user_id = parts[1].split()[0]
        return user_id
    return None  # In case the format is not as expected

def truncate_user_id_regex(message):
    # Use regex to find and replace the 'user_id: <id>' pattern with an empty string
    truncated_message = re.sub(r"user_id:\s+\S+\s*", "", message)
    return truncated_message

def trim_message(message, keyword):
    keyword_index = message.find(keyword)
    if keyword_index != -1:
        return message[:keyword_index].strip()
    return message.strip()


def summarize_content(content):
    """
    Summarizes the given content using OpenAI's GPT API.
    """
    try:
         # Replace with your OpenAI API key
        response = client.chat.completions.create(model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": f"Please summarize the following content:\n{content}"}
        ],
        max_tokens=300)
        summary = response.choices[0].message.content
        return summary.strip()
    except Exception as e:
        return f"Error summa"

from config.settings import settings
tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)

async def perform_web_search(query):
    results = tavily.search(query=query, include_raw_content=True)
    print(results)
    return json.dumps({
        "results": [{
            "title": r["title"],
            "url": r["url"],
            "content": r["content"]
        } for r in results["results"]]
    })