from typing import List
import logging
from finadvisor.chatbot.constants.eazybooks_secrets import get_reddit

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("praw")
logger.setLevel(logging.INFO)

async def search_reddit(query: str, subreddit: str = "CreditCardsIndia", limit: int = 10) -> List[str]:
    """Search a subreddit for detailed discussions related to a query."""
    subreddit_obj = get_reddit().subreddit(subreddit)
    results = []

    for submission in subreddit_obj.search(query, limit=limit):
        # Fetch key insights from posts
        results.append(f"{submission.title}: {submission.selftext[:300]}...")  # Short snippet of post content

    return results


def fetch_comments(submission, max_comments: int = 20):
    """Fetch detailed comments for a Reddit post."""
    submission.comments.replace_more(limit=0)  # Avoid "MoreComments" placeholders
    comments = []
    for comment in submission.comments.list()[:max_comments]:  # Fetch more comments
        comments.append({
            "author": comment.author.name if comment.author else "Unknown",
            "body": comment.body,
            "score": comment.score,
        })
    return comments
