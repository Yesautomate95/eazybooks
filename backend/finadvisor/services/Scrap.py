import snscrape.modules.twitter as sntwitter
import requests
import ssl

import ssl
import certifi




# Define a custom requests session
session = requests.Session()
session.verify = False  # Disable SSL verification (not secure for production)
sntwitter.TwitterSearchScraper.http_client = session

def scrape_twitter(keyword, limit=100):
    """Scrape tweets containing a specific keyword."""
    tweets = []
    try:
        for tweet in sntwitter.TwitterSearchScraper(f'{keyword} lang:en since:2023-01-01').get_items():
            if len(tweets) == limit:
                break
            tweets.append(tweet.content)
    except Exception as e:
        print(f"Error scraping Twitter")
    return tweets

if __name__ == "__main__":
    sector = "IT"
    print(f"Scraping tweets for sector: {sector}")
    tweets = scrape_twitter(sector, limit=100)
    print(f"Retrieved {len(tweets)} tweets.")
    print("SSL Certificate Location:", ssl.get_default_verify_paths().openssl_cafile)
    print("Certifi Bundle Location:", certifi.where())
