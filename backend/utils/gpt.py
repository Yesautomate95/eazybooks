import openai
from openai import OpenAI
from finadvisor.chatbot.constants.eazybooks_secrets import open_ai_key

# Lazy-initialized OpenAI client
_client = None

def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=open_ai_key)
    return _client

def run_gpt_completion(messages, model="gpt-4.1-mini", max_tokens=2000, temperature=0.1):
    """
    Wrapper function for OpenAI GPT Completion API (updated for openai>=1.0.0).
    """
    try:
        print("Sending request to OpenAI...")
        response = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature)
        # Extract the generated content from the response
        if response.choices and len(response.choices) > 0:
            return response.choices[0].message.content
        else:
            print("Error: No choices found in the response.")
            return None
    except openai.OpenAIError as e:
        return None
    except Exception as e:
        return None
