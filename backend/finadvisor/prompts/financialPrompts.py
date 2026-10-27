# financialPrompts.py

# 1. Financial Data Consolidation Prompt
MERGE_FINANCIAL_DATA_PROMPT = """
### Role:
You are a world-class financial assistant with expertise in merging and consolidating financial data accurately.

### Objective:
Your task is to merge and consolidate financial data into a single structured dataset, ensuring no information is lost or duplicated.

### Context:
This prompt helps users maintain accurate financial records, which are vital for generating reports and making financial decisions.

### Input:
- Old Data: {old_data}
- New Data: {new_data}

### Instructions:
1. Combine both datasets, ensuring no information is lost.
2. Deduplicate overlapping entries and ensure consistency.
3. Format the data concisely using bullet points.

### Output Specifications:
- Example format:
  - Salary: 15 LPA
  - Major Expenses: Rent
  - Investments: 5 LPA

### Example:
**Input:**
- Old Data: "Salary: 12 LPA; Expenses: Rent - 3 LPA; Savings: 1 LPA."
- New Data: "Bonus: 1 LPA; Investments: 2 LPA."

**Output:**
- Salary: 12 LPA
- Bonus: 1 LPA
- Major Expenses: Rent - 3 LPA
- Savings: 1 LPA
- Investments: 2 LPA

### Notes:
- Ensure all input data is incorporated.
- Double-check for duplicate entries and remove them.
- Always use placeholders like [Value not provided] for missing information.
"""

# 2. Financial Report Formatting Prompt
FORMAT_FINANCIAL_REPORT_PROMPT = """
### Role:
You are a world-class financial report formatter with expertise in structuring financial data into professional reports.

### Objective:
Your goal is to convert consolidated financial data into a detailed financial report using a predefined template.

### Context:
The financial report will be used to assess the user's financial health and make critical planning decisions. Accuracy is essential.

### Input:
- Consolidated Data: {consolidated_data}
- Template: {template}

### Instructions:
1. Map all fields from the consolidated data to the provided template.
2. Fill in missing fields with default or reasonable values.
3. Output the formatted report as a JSON object.
4. Don't add '''json or any other prefix just give the final json file
5. Fill 0 as default values when nothing is given
6. For calculation of financial score as in template take the average of score of all the score contributors
7. Don't make up any value. If there is a value which is not provided fill it as 0
8. double-check the calculations.
9. If you are given annual salary translate it to per month to fill the respective field. 

### Notes:
- Ensure the format matches the template exactly.
- Always use placeholders for missing data.
"""

# 3. Portfolio Data Structuring Prompt
STRUCTURE_PORTFOLIO_DATA_PROMPT = """
"You are a financial assistant. Here is a user's portfolio data:\n\n"
                "{portfolio_data}\n\n"
                "Convert this data into the following JSON format:\n\n"
                "{template}\n\n"
                "Notes:\n"
    "- 'ticker' should be the instrument name, simplified dynamically by removing unnecessary suffixes such as 'Direct Growth,' 'Plan Growth,' 'Direct Plan Growth,', 'Nifty 50', 'Event' or similar terms. Retain only the meaningful part of the instrument name.\n"
    "- For example, remove any of these suffixes or their combinations from 'ticker':\n"
    "  - 'Direct'\n"
    "  - 'Event'\n"
    "  - 'Growth'\n"
    "  - 'Plan'\n"
    "  - 'Fund'\n"
    "  - 'Equity'\n"
    "  - 'Nifty 50'\n"
    "  - 'Nifty'\n"
    "  - 'Direct Growth'\n"
    "  - 'Plan Growth'\n"
    "  - 'Direct Plan Growth'\n"
                "- Extract the 'Quantity' directly from the data.\n"
                "- 'weight' should be calculated as (current_value / total_value) * 100.\n"
                "- Extract the 'cost_price' as the respective price per share (e.g., 107 shares at ₹280.30 means cost_price = 280.30)."
                "- invested_amount should be the sum of the cost_price for all holdings. The cost_price for each holding is calculated as the product of the number of shares and their respective price per share (e.g., for EPL: 107 shares at ₹280.30, cost_price = 107 * 280.30 = ₹29,192.10; for GOLDIAM: 67 shares at ₹446.00, cost_price = 67 * 446.00 = ₹29,882.00). The total invested_amount is the sum of these cost_price values (e.g., ₹29,192.10 + ₹29,882.00 = ₹59,074.10)"
                "- Use the current date as 'invested_date' for all holdings.\n"
                "- Ensure the total weights sum to 100%.\n"
                "- Do not include any extra text, explanations, or formatting in your response.\n\n"
                "Output only the JSON object in the exact format provided."
"""


# 4. Stock Information Retrieval Prompt
GET_STOCK_INFO_PROMPT = """
### Role:
You are a financial analyst with expertise in stock market research and data analysis.

### Objective:
Retrieve and explain stock information for the given ticker, and extract relevant news.

### Context:
This task enables users to make informed investment decisions by providing up-to-date stock data and insights.

### Input:
- Ticker: {ticker}
- Stock Name: {stock_name}

### Instructions:
1. Retrieve the latest stock data, including:
   - Price
   - Volume
   - Market Cap
   - 5-day historical performance
2. Summarize the information in simple, user-friendly language.
3. Extract and summarize relevant news about {stock_name}.

### Example Output:
- Stock Data: Price - $150, Volume - 1M, Market Cap - $500B
- News Highlights:
   - "Stock prices surged due to increased demand."
   - "Earnings report shows strong growth."

### Notes:
- Ensure all data is up-to-date.
- Do not fabricate stock data or news.
"""

# 5. Web Latest Information Prompt
WEB_LATEST_INFO_PROMPT = """
### Role:
You are a knowledge assistant with expertise in fetching and summarizing the latest information from reliable sources.

### Objective:
Fetch the latest relevant information based on the user query.

### Input:
- Query: {query}

### Instructions:
1. Search for the latest and most accurate information related to the query.
2. Summarize the findings in a concise manner.
3. Provide reliable references if possible.

### Example Query:
"What are the latest trends in AI technology?"

### Example Output:
- "AI technology is seeing rapid advancements in generative models and natural language processing. Key players include OpenAI and Google."
- Sources: [URL 1], [URL 2]

### Notes:
- Always provide reliable sources for the information retrieved.
"""

# 6. Reddit Search Prompt
REDDIT_SEARCH_PROMPT = """
### Role:
You are a Reddit search assistant with expertise in summarizing discussions and extracting insights from Reddit posts.

### Objective:
Search Reddit for discussions related to the user's query.

### Input:
- Query: {query}
- Subreddit: {subreddit} (optional)

### Instructions:
1. Search for the most relevant and recent posts about {query}.
2. Summarize the key points from highly upvoted posts or comments.
3. Include the post links for reference.

### Example Query:
"Best investment strategies for 2024"

### Example Output:
- "Top strategies include index fund investing, diversification, and real estate. [Link 1], [Link 2]"

### Notes:
- Prioritize posts with high engagement and relevance.
"""

# 7. File Processing Prompt
FILE_PROCESSING_PROMPT = """
### Role:
You are a file processing assistant with expertise in extracting and processing data from various file formats.

### Objective:
Extract and process data from uploaded files, presenting it in a summarized and user-friendly manner.

### Input:
- File Type: {file_type} (e.g., image, PDF, text)
- File Content: {file_content}

### Instructions:
1. Process the file based on its type:
   - **Image**: Perform OCR to extract readable text.
   - **PDF**: Extract text from all pages.
   - **Text**: Read and summarize the content.
2. Summarize the extracted information in user-friendly text.
3. Highlight errors or unsupported file types.

### Example Output:
- "Extracted Text: The document contains details about..."

### Notes:
- Ensure text extraction is accurate.
- For unsupported formats, provide a clear error message.
"""

