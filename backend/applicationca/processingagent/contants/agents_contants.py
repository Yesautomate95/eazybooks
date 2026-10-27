from enum import Enum
# Define task types
class TaskType(str, Enum):
    BALANCE_SHEET = "balance_sheet"
    ITR_FILING = "itr_filing"
    AUDIT = "audit"
    GST_FILING = "gst_filing"
    FINANCIAL_ANALYSIS = "financial_analysis"
    TAX_PLANNING = "tax_planning"

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