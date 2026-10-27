from bs4 import BeautifulSoup
import requests

from utils.chat_helper import summarize_content


def fetch_content_from_url(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        # Extract meaningful text from the page
        text = soup.get_text(separator='\n')
        return text[:5000]  # Limit to first 5000 characters to avoid overly long responses
    except requests.RequestException as e:
        return f"Error fetching content from URL: {url}. Error: {str(e)}"



async def internet_search(query, **kwargs):
    # max_results = 5  # Hardcoded max results
    # api_key = google_api_key
    # search_engine_id = google_search_engine_id
    # url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={api_key}&cx={search_engine_id}"

    try:
        # response = requests.get(url)
        # response.raise_for_status()
        # results = response.json()
        # combined_content = ""
        #
        # for item in results.get('items', [])[:max_results]:
        #     link = item['link']
        #     # Fetch content for each link
        #     content = fetch_content_from_url(link)
        #     if "Error" not in content:
        #         combined_content += content + "\n\n"  # Combine content
        #
        # return combined_content
        return ""
    except requests.RequestException as e:
        return f"An error occurred while performing the search: {str(e)}"


def summarize_search_results(query):
    # Fetch and aggregate content
    content = internet_search(query)

    # Check if content was successfully fetched
    if "Error" in content:
        return content

    # Summarize using the API
    summary = summarize_content(content)
    return summary