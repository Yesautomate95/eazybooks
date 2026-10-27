from vanna.legacy.openai import OpenAI_Chat
import pandas as pd
from tabulate import tabulate
import logging
import re
from typing import Tuple, Dict, Any, Optional, List
from psycopg2.extras import RealDictCursor
from applicationca.database.database_retrieval import get_connection
from applicationca.chatbot.constants.eazybooks_secrets import open_ai_key

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TallyVanna(OpenAI_Chat):
    def __init__(self, config=None):
        if config is None:
            config = {}
        config['allow_llm_to_see_data'] = True  # Enable LLM to see data
        super().__init__(config=config)
        self._ddl = []
        self._documentation = []
        self._question_sql = []
        self.user_id = None  # Add user_id attribute
        self.company_name = None  # Add company_name attribute

    def set_user_id(self, user_id: str):
        """Set the user_id for filtering data"""
        print(f"Setting user_id in Vanna instance: {user_id}")
        self.user_id = user_id

    def add_ddl(self, ddl: str) -> None:
        """Add DDL to training data"""
        self._ddl.append(ddl)

    def add_documentation(self, documentation: str) -> None:
        """Add documentation to training data"""
        self._documentation.append(documentation)

    def add_question_sql(self, question: str, sql: str) -> None:
        """Add question-SQL pair to training data"""
        self._question_sql.append({"question": question, "sql": sql})

    def generate_embedding(self, data: str) -> list:
        """Generate embedding for the given data"""
        return super().generate_embedding(data)

    def get_related_ddl(self, question: str) -> list:
        """Get related DDL for the question"""
        return self._ddl

    def get_related_documentation(self, question: str) -> list:
        """Get related documentation for the question"""
        return self._documentation

    def get_similar_question_sql(self, question: str) -> list:
        """Get similar question-SQL pairs"""
        return self._question_sql

    def get_training_data(self) -> pd.DataFrame:
        """Get all training data"""
        return pd.DataFrame(self._question_sql)

    def remove_training_data(self, id: str) -> None:
        """Remove training data by ID"""
        # Since we're using lists, we'll implement a simple removal
        if id in self._question_sql:
            self._question_sql.remove(id)
            
    def refine_user_query(self, original_query: str, error_message: str = None, is_empty_result: bool = False) -> str:
        """
        Uses the LLM to refine the user's original query when there are errors or empty results
        
        Args:
            original_query: The original user query
            error_message: The error message received (if any)
            is_empty_result: Whether the original query returned empty results
            
        Returns:
            Refined user query that should lead to a better SQL query
        """
        # Get database schema information
        table_metadata = "\n".join(self._ddl)
        documentation = "\n".join(self._documentation)
        
        # Use the LLM to generate a refined query
        system_prompt = """
        You are an expert data analyst who specializes in translating vague or incorrect user queries 
        into more precise queries for a Tally ERP database.
        
        You are given:
        1. A user's original query that either produced an error or returned no results
        2. The database schema and documentation
        3. Error message (if there was one)
        
        Your task is to rewrite the user's query to be more accurate, considering:
        - The actual table and column names available in the schema
        - Proper entity naming as shown in the documentation
        - Being more specific about relationships between entities
        - Adding relevant filters or conditions that match the user's intent
        - Handling potential typos in entity names by suggesting correct ones
        - Always including user_id filter in the query
        - Always including company_name filter in the query
        
        DO NOT write SQL. Just rewrite the user's question in natural language, but with more precision 
        to ensure it will generate valid SQL when processed.
        """
        
        error_context = f"""
        Error message received:
        ```
        {error_message}
        ```
        """ if error_message else ""
        
        empty_result_context = """
        The query executed successfully but returned no results.
        """ if is_empty_result else ""
        
        # Add company context if available
        company_context = f"""
        This query is for the company: '{self.company_name}'
        """ if self.company_name else ""
        
        user_prompt = f"""
        User's original query: "{original_query}"
        
        {error_context}
        {empty_result_context}
        {company_context}
        
        DATABASE SCHEMA:
        ```sql
        {table_metadata}
        ```
        
        DATABASE DOCUMENTATION:
        ```
        {documentation}
        ```
        
        Please rewrite the user's query to be more precise and accurate based on the available schema.
        Ensure entity names match what's in the database. Return only the rewritten query as a simple text string.
        Make sure to include both user_id and company_name filters in the rewritten query.
        """
        
        # Get the refined query from the LLM
        try:
            # Use OpenAI API directly similar to how the rest of the class is using it
            import openai
            
            # Make sure we have an API key
            openai.api_key = self.config.get('api_key')
            
            # Create a completion with the same parameters as before
            response = openai.chat.completions.create(
                model=self.config['model'],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )
            
            refined_query = response.choices[0].message.content.strip()
            
            # Remove any potential markdown formatting or quotes
            if refined_query.startswith('"') and refined_query.endswith('"'):
                refined_query = refined_query[1:-1]
            elif refined_query.startswith("'") and refined_query.endswith("'"):
                refined_query = refined_query[1:-1]
                
            return refined_query
        except Exception as e:
            print(f"Error while trying to refine user query: {e}")
            return original_query  # Return original query if refinement fails

    def generate_sql(self, question: str, allow_llm_to_see_data: bool = True) -> str:
        """Generate SQL from natural language question"""
        # Get the base SQL from the parent class
        logger.info(f"Generating SQL for question: {question}")
        sql = super().generate_sql(question, allow_llm_to_see_data)
        logger.info(f"Base SQL before modifications: {sql}")
        
        # If we have a user_id set, ensure it's included in the query
        if self.user_id:
            logger.info(f"Adding user_id filter for: {self.user_id}")
            # Replace :user_id with actual value
            sql = sql.replace(":user_id", f"'{self.user_id}'")
        
        # If we have a company_name set, ensure it's included in the query
        if self.company_name:
            logger.info(f"Adding company_name filter for: {self.company_name}")
            sql = sql.replace(":company_name", f"'{self.company_name}'")
            
        logger.info(f"Final SQL query: {sql}")
        return sql

    def _transform_sql_params(self, sql: str) -> Tuple[str, Dict[str, Any]]:
        """Transform SQLAlchemy-style parameters to psycopg2 style parameters.
        
        Args:
            sql: SQL query with SQLAlchemy-style parameters (:param)
            
        Returns:
            Tuple containing:
            - Modified SQL with %s placeholders
            - Dictionary of parameter values
        """
        params = {}
        # Extract parameters from SQL using regex
        param_matches = re.findall(r':(\w+)', sql)
        
        # Build params dictionary
        for param in param_matches:
            if param == 'user_id' and self.user_id:
                params[param] = self.user_id
            # Add more parameter mappings here as needed
        
        # Replace :param with %s
        modified_sql = re.sub(r':\w+', '%s', sql)
        
        # Convert params dict to list in order of appearance
        param_values = [params[param] for param in param_matches]
        
        return modified_sql, param_values

    def execute_with_fallback(self, question: str, max_attempts: int = 3) -> Tuple[Optional[pd.DataFrame], str, Optional[str]]:
        """Execute a query with fallback and refinement logic."""
        attempts = 0
        last_error = None
        current_query = question

        while attempts < max_attempts:
            try:
                # Generate SQL
                sql = self.generate_sql(question=current_query, allow_llm_to_see_data=True)
                logger.info(f"Generated SQL: {sql}")
                
                # Execute the SQL
                logger.info("Executing SQL query...")
                result = self.run_sql(sql)
                
                logger.info(f"Query result type: {type(result)}")
                if isinstance(result, pd.DataFrame):
                    logger.info(f"Result shape: {result.shape}")
                    logger.info(f"Result columns: {result.columns.tolist()}")
                    logger.info(f"First few rows: {result.head().to_dict()}")
                
                # Handle empty results case
                if isinstance(result, pd.DataFrame) and result.empty:
                    logger.warning("Query returned empty results")
                    attempts += 1
                    
                    if attempts >= max_attempts:
                        break
                        
                    # Refine the query for empty results
                    current_query = self.refine_user_query(current_query, is_empty_result=True)
                    logger.info(f"Refined query: {current_query}")
                    continue
                
                # Convert date and decimal columns to string format if result is a DataFrame
                if isinstance(result, pd.DataFrame):
                    for col in result.columns:
                        # Handle date columns
                        if 'date' in col.lower():
                            try:
                                result[col] = pd.to_datetime(result[col], format='%Y-%m-%d')
                                result[col] = result[col].dt.strftime('%Y-%m-%d')
                                logger.debug(f"Converted date column: {col}")
                            except:
                                logger.warning(f"Failed to convert date column: {col}")
                                pass
                        
                        # Handle numeric columns (including decimal)
                        if pd.api.types.is_numeric_dtype(result[col]):
                            result[col] = result[col].astype(float)
                            logger.debug(f"Converted numeric column: {col}")
                    
                # Success case
                if attempts > 0:
                    status_msg = f"✅ Query successful after {attempts+1} attempts with refinements."
                else:
                    status_msg = "✅ Query executed successfully."
                logger.info(status_msg)
                return result, status_msg, sql
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"Error executing query: {last_error}")
                attempts += 1
                
                if attempts >= max_attempts:
                    break
                
                logger.info(f"Attempt {attempts} failed, trying to refine query...")
                
                # Refine the user query based on the error
                current_query = self.refine_user_query(current_query, error_message=last_error)
                logger.info(f"Refined query after error: {current_query}")
        
        # If we exit the loop, all attempts failed
        logger.error(f"Failed after {max_attempts} attempts. Last error: {last_error}")
        return None, f"❌ Failed after {max_attempts} attempts. Last error: {last_error}", None

    def run_sql(self, sql: str, params: Optional[List] = None) -> pd.DataFrame:
        """Run SQL query and return results as a pandas DataFrame.
        
        Args:
            sql: SQL query to execute
            params: Optional list of parameters for the query
        
        Returns:
            pandas DataFrame with query results
        """
        try:
            # Get connection from pool
            conn = get_connection()
            if not conn:
                raise Exception("Could not get database connection")

            cursor = conn.cursor(cursor_factory=RealDictCursor)
            try:
                # Execute query with parameters if provided
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)
                    
                # Fetch results
                results = cursor.fetchall()
                
                # Convert to DataFrame
                df = pd.DataFrame(results)
                return df
                
            finally:
                cursor.close()
                conn.close()
                
        except Exception as e:
            logger.error(f"Error executing SQL: {e}")
            raise e


from config.settings import settings

# Initialize Vanna with OpenAI
vn = TallyVanna(config={
    'api_key': settings.OPENAI_API_KEY,
    'model': settings.OPENAI_MODEL
})

# Lazy PostgreSQL connection - connect on first use, not at import time
_vn_connected = False

def _ensure_vn_connected():
    global _vn_connected
    if _vn_connected:
        return
    _db = settings.db_components
    vn.connect_to_postgres(
        host=_db["host"],
        dbname=_db["dbname"],
        user=_db["user"],
        password=_db["password"],
        port=_db["port"],
        sslmode='require'
    )
    _vn_connected = True


def train_tally_structure(vanna_instance=None):
    """Train Vanna with Tally database structure

    Args:
        vanna_instance: The Vanna instance to train (if None, uses global vn)
    """
    # Use provided instance or fall back to global
    if vanna_instance is None:
        _ensure_vn_connected()
    vn_instance = vanna_instance or vn
    
    # Train with complete table definitions
    vn_instance.train(ddl="""
    -- Configuration table
    CREATE TABLE config (
        name varchar(64) NOT NULL PRIMARY KEY,
        value varchar(1024),
        user_id varchar(255),
        company_name varchar(255)
    );

    -- Master Tables
    CREATE TABLE mst_group (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        primary_group varchar(1024) NOT NULL DEFAULT '',
        is_revenue smallint,
        is_deemedpositive smallint,
        is_reserved smallint,
        affects_gross_profit smallint,
        sort_position integer,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_ledger (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        alias varchar(256) NOT NULL DEFAULT '',
        description varchar(64) NOT NULL DEFAULT '',
        notes varchar(64) NOT NULL DEFAULT '',
        is_revenue smallint,
        is_deemedpositive smallint,
        opening_balance numeric DEFAULT 0,
        closing_balance numeric DEFAULT 0,
        mailing_name varchar(256) NOT NULL DEFAULT '',
        mailing_address varchar(1024) NOT NULL DEFAULT '',
        mailing_state varchar(256) NOT NULL DEFAULT '',
        mailing_country varchar(256) NOT NULL DEFAULT '',
        mailing_pincode varchar(64) NOT NULL DEFAULT '',
        email varchar(256) NOT NULL DEFAULT '',
        it_pan varchar(64) NOT NULL DEFAULT '',
        gstn varchar(64) NOT NULL DEFAULT '',
        gst_registration_type varchar(64) NOT NULL DEFAULT '',
        gst_supply_type varchar(64) NOT NULL DEFAULT '',
        gst_duty_head varchar(16) NOT NULL DEFAULT '',
        tax_rate numeric DEFAULT 0,
        bank_account_holder varchar(256) NOT NULL DEFAULT '',
        bank_account_number varchar(64) NOT NULL DEFAULT '',
        bank_ifsc varchar(64) NOT NULL DEFAULT '',
        bank_swift varchar(64) NOT NULL DEFAULT '',
        bank_name varchar(64) NOT NULL DEFAULT '',
        bank_branch varchar(64) NOT NULL DEFAULT '',
        bill_credit_period integer NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_vouchertype (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        numbering_method varchar(64) NOT NULL DEFAULT '',
        is_deemedpositive smallint,
        affects_stock smallint,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_uom (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        formalname varchar(256) NOT NULL DEFAULT '',
        is_simple_unit smallint NOT NULL,
        base_units varchar(1024) NOT NULL,
        additional_units varchar(1024) NOT NULL,
        conversion integer NOT NULL,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_godown (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        address varchar(1024) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_stock_group (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_stock_item (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        alias varchar(256) NOT NULL DEFAULT '',
        description varchar(64) NOT NULL DEFAULT '',
        notes varchar(64) NOT NULL DEFAULT '',
        part_number varchar(256) NOT NULL DEFAULT '',
        uom varchar(32) NOT NULL DEFAULT '',
        alternate_uom varchar(32) NOT NULL DEFAULT '',
        conversion integer NOT NULL DEFAULT 0,
        opening_balance numeric DEFAULT 0,
        opening_rate numeric DEFAULT 0,
        opening_value numeric DEFAULT 0,
        closing_balance numeric DEFAULT 0,
        closing_rate numeric DEFAULT 0,
        closing_value numeric DEFAULT 0,
        costing_method varchar(32) NOT NULL DEFAULT '',
        gst_type_of_supply varchar(32) DEFAULT '',
        gst_hsn_code varchar(64) DEFAULT '',
        gst_hsn_description varchar(256) DEFAULT '',
        gst_rate numeric DEFAULT 0,
        gst_taxability varchar(32) DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_cost_category (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        allocate_revenue smallint,
        allocate_non_revenue smallint,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_cost_centre (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        category varchar(1024) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_attendance_type (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        uom varchar(32) NOT NULL DEFAULT '',
        attendance_type varchar(64) NOT NULL DEFAULT '',
        attendance_period varchar(64) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_employee (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        id_number varchar(256) NOT NULL DEFAULT '',
        date_of_joining date,
        date_of_release date,
        designation varchar(64) NOT NULL DEFAULT '',
        function_role varchar(64) NOT NULL DEFAULT '',
        location varchar(256) NOT NULL DEFAULT '',
        gender varchar(32) NOT NULL DEFAULT '',
        date_of_birth date,
        blood_group varchar(32) NOT NULL DEFAULT '',
        father_mother_name varchar(256) NOT NULL DEFAULT '',
        spouse_name varchar(256) NOT NULL DEFAULT '',
        address varchar(256) NOT NULL DEFAULT '',
        mobile varchar(32) NOT NULL DEFAULT '',
        email varchar(64) NOT NULL DEFAULT '',
        pan varchar(32) NOT NULL DEFAULT '',
        aadhar varchar(32) NOT NULL DEFAULT '',
        uan varchar(32) NOT NULL DEFAULT '',
        pf_number varchar(32) NOT NULL DEFAULT '',
        pf_joining_date date,
        pf_relieving_date date,
        pr_account_number varchar(32) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_payhead (
        guid varchar(64) NOT NULL PRIMARY KEY,
        name varchar(1024) NOT NULL DEFAULT '',
        parent varchar(1024) NOT NULL DEFAULT '',
        payslip_name varchar(1024) NOT NULL DEFAULT '',
        pay_type varchar(64) NOT NULL DEFAULT '',
        income_type varchar(64) NOT NULL DEFAULT '',
        calculation_type varchar(32) NOT NULL DEFAULT '',
        leave_type varchar(64) NOT NULL DEFAULT '',
        calculation_period varchar(32) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_gst_effective_rate (
        item varchar(1024) NOT NULL DEFAULT '',
        applicable_from date,
        hsn_description varchar(256) NOT NULL DEFAULT '',
        hsn_code varchar(64) NOT NULL DEFAULT '',
        rate numeric DEFAULT 0,
        is_rcm_applicable smallint,
        nature_of_transaction varchar(64) NOT NULL DEFAULT '',
        nature_of_goods varchar(64) NOT NULL DEFAULT '',
        supply_type varchar(64) NOT NULL DEFAULT '',
        taxability varchar(64) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_opening_batch_allocation (
        name varchar(1024) NOT NULL DEFAULT '',
        item varchar(1024) NOT NULL DEFAULT '',
        opening_balance numeric DEFAULT 0,
        opening_rate numeric DEFAULT 0,
        opening_value numeric DEFAULT 0,
        godown varchar(1024) NOT NULL DEFAULT '',
        manufactured_on date,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_opening_bill_allocation (
        ledger varchar(1024) NOT NULL DEFAULT '',
        opening_balance numeric DEFAULT 0,
        bill_date date,
        name varchar(1024) NOT NULL DEFAULT '',
        bill_credit_period integer NOT NULL DEFAULT 0,
        is_advance smallint,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_closingstock_ledger (
        ledger varchar(1024) NOT NULL DEFAULT '',
        stock_date date,
        stock_value numeric NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_stockitem_standard_cost (
        item varchar(1024) NOT NULL DEFAULT '',
        date date,
        rate numeric DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE mst_stockitem_standard_price (
        item varchar(1024) NOT NULL DEFAULT '',
        date date,
        rate numeric DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    -- Transaction Tables
    CREATE TABLE trn_voucher (
        guid varchar(64) NOT NULL PRIMARY KEY,
        date date NOT NULL,
        voucher_type varchar(1024) NOT NULL,
        voucher_number varchar(64) NOT NULL DEFAULT '',
        reference_number varchar(64) NOT NULL DEFAULT '',
        reference_date date,
        narration varchar(4000) NOT NULL DEFAULT '',
        party_name varchar(256) NOT NULL,
        place_of_supply varchar(256) NOT NULL,
        is_invoice smallint,
        is_accounting_voucher smallint,
        is_inventory_voucher smallint,
        is_order_voucher smallint,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_accounting (
        guid varchar(64) NOT NULL DEFAULT '',
        ledger varchar(1024) NOT NULL DEFAULT '',
        amount numeric NOT NULL DEFAULT 0,
        amount_forex numeric NOT NULL DEFAULT 0,
        currency varchar(16) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_inventory (
        guid varchar(64) NOT NULL DEFAULT '',
        item varchar(1024) NOT NULL DEFAULT '',
        quantity numeric NOT NULL DEFAULT 0,
        rate numeric NOT NULL DEFAULT 0,
        amount numeric NOT NULL DEFAULT 0,
        additional_amount numeric NOT NULL DEFAULT 0,
        discount_amount numeric NOT NULL DEFAULT 0,
        godown varchar(1024),
        tracking_number varchar(256),
        order_number varchar(256),
        order_duedate date,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_cost_centre (
        guid varchar(64) NOT NULL DEFAULT '',
        ledger varchar(1024) NOT NULL DEFAULT '',
        costcentre varchar(1024) NOT NULL DEFAULT '',
        amount numeric NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_cost_category_centre (
        guid varchar(64) NOT NULL DEFAULT '',
        ledger varchar(1024) NOT NULL DEFAULT '',
        costcategory varchar(1024) NOT NULL DEFAULT '',
        costcentre varchar(1024) NOT NULL DEFAULT '',
        amount numeric NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_cost_inventory_category_centre (
        guid varchar(64) NOT NULL DEFAULT '',
        ledger varchar(1024) NOT NULL DEFAULT '',
        item varchar(1024) NOT NULL DEFAULT '',
        costcategory varchar(1024) NOT NULL DEFAULT '',
        costcentre varchar(1024) NOT NULL DEFAULT '',
        amount numeric NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_bill (
        guid varchar(64) NOT NULL DEFAULT '',
        ledger varchar(1024) NOT NULL DEFAULT '',
        name varchar(1024) NOT NULL DEFAULT '',
        amount numeric NOT NULL DEFAULT 0,
        billtype varchar(256) NOT NULL DEFAULT '',
        bill_credit_period integer NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_bank (
        guid varchar(64) NOT NULL DEFAULT '',
        ledger varchar(1024) NOT NULL DEFAULT '',
        transaction_type varchar(32) NOT NULL DEFAULT '',
        instrument_date date,
        instrument_number varchar(1024) NOT NULL DEFAULT '',
        bank_name varchar(64) NOT NULL DEFAULT '',
        amount numeric NOT NULL DEFAULT 0,
        bankers_date date,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_batch (
        guid varchar(64) NOT NULL DEFAULT '',
        item varchar(1024) NOT NULL DEFAULT '',
        name varchar(1024) NOT NULL DEFAULT '',
        quantity numeric NOT NULL DEFAULT 0,
        amount numeric NOT NULL DEFAULT 0,
        godown varchar(1024),
        destination_godown varchar(1024),
        tracking_number varchar(1024),
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_inventory_accounting (
        guid varchar(64) NOT NULL DEFAULT '',
        ledger varchar(1024) NOT NULL DEFAULT '',
        amount numeric NOT NULL DEFAULT 0,
        additional_allocation_type varchar(32) NOT NULL DEFAULT '',
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_employee (
        guid varchar(64) NOT NULL DEFAULT '',
        category varchar(1024) NOT NULL DEFAULT '',
        employee_name varchar(1024) NOT NULL DEFAULT '',
        amount numeric NOT NULL DEFAULT 0,
        employee_sort_order integer NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_payhead (
        guid varchar(64) NOT NULL DEFAULT '',
        category varchar(1024) NOT NULL DEFAULT '',
        employee_name varchar(1024) NOT NULL DEFAULT '',
        employee_sort_order integer NOT NULL DEFAULT 0,
        payhead_name varchar(1024) NOT NULL DEFAULT '',
        payhead_sort_order integer NOT NULL DEFAULT 0,
        amount numeric NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );

    CREATE TABLE trn_attendance (
        guid varchar(64) NOT NULL DEFAULT '',
        employee_name varchar(1024) NOT NULL DEFAULT '',
        attendancetype_name varchar(1024) NOT NULL DEFAULT '',
        time_value numeric NOT NULL DEFAULT 0,
        type_value numeric NOT NULL DEFAULT 0,
        user_id varchar(255),
        company_name varchar(255)
    );
    """)

    # Train with comprehensive Tally documentation
    vn_instance.train(documentation="""
    Tally ERP database structure and relationships:

    1. Configuration:
    - config: Stores system configuration parameters

    2. Master Tables:
    - mst_group: Account group hierarchy (Assets, Liabilities, Income, Expenses)
    - mst_ledger: All account ledgers with GST and banking details
    - mst_vouchertype: Different types of transactions
    - mst_uom: Unit of measurement definitions
    - mst_godown: Inventory storage locations
    - mst_stock_group: Stock item grouping
    - mst_stock_item: Inventory items with GST details
    - mst_cost_category: Cost center categories
    - mst_cost_centre: Cost centers
    - mst_attendance_type: Types of attendance records
    - mst_employee: Employee master data
    - mst_payhead: Payroll components
    - mst_gst_effective_rate: GST rates and rules
    - mst_opening_batch_allocation: Opening stock batches
    - mst_opening_bill_allocation: Opening bills
    - mst_stockitem_standard_cost: Standard costs
    - mst_stockitem_standard_price: Standard prices

    3. Transaction Tables:
    - trn_voucher: Main transaction records
    - trn_accounting: Accounting entries
    - trn_inventory: Stock movements
    - trn_cost_centre: Cost center allocations
    - trn_cost_category_centre: Cost category allocations
    - trn_cost_inventory_category_centre: Combined cost allocations
    - trn_bill: Bill tracking
    - trn_bank: Bank transactions
    - trn_batch: Batch tracking
    - trn_inventory_accounting: Inventory accounting entries
    - trn_employee: Employee transactions
    - trn_payhead: Payroll transactions
    - trn_attendance: Attendance records

    Key Relationships:
    - Every transaction in trn_voucher can have multiple entries in trn_accounting
    - Inventory transactions link trn_voucher, trn_inventory, and trn_accounting
    - Cost center allocations can be at ledger, inventory, or combined level
    - Employee transactions link to attendance and payroll
    - GST calculations use multiple tables for rates and transactions
    
    Important Notes:
    - All tables have user_id and company_name columns for multi-tenant support
    - All queries must include user_id filter to ensure data isolation
    - All queries should include company_name filter to ensure correct company data is returned
    - Decimal fields use numeric type instead of decimal
    - String fields use character varying instead of varchar
    """)

    # Train with example business queries
    vn_instance.train(sql="""
    -- Get Trial Balance with Cost Center Details
    SELECT l.name, l.opening_balance, 
           COALESCE(SUM(CASE WHEN a.amount > 0 THEN a.amount ELSE 0 END), 0) as total_debit,
           COALESCE(SUM(CASE WHEN a.amount < 0 THEN ABS(a.amount) ELSE 0 END), 0) as total_credit,
           l.closing_balance,
           cc.costcentre,
           cc.amount as cost_centre_amount
    FROM mst_ledger l
    LEFT JOIN trn_accounting a ON l.name = a.ledger
    LEFT JOIN trn_cost_centre cc ON a.guid = cc.guid
    WHERE l.user_id = :user_id AND l.company_name = :company_name
    GROUP BY l.name, l.opening_balance, l.closing_balance, cc.costcentre, cc.amount
    ORDER BY ABS(l.closing_balance) DESC;
    """)

    vn_instance.train(sql="""
    -- Get Comprehensive Stock Summary
    SELECT s.name, s.opening_balance, s.opening_value,
           SUM(CASE WHEN v.is_inventory_voucher = 1 THEN i.quantity ELSE 0 END) as movement_qty,
           SUM(CASE WHEN v.is_inventory_voucher = 1 THEN i.amount ELSE 0 END) as movement_value,
           s.closing_balance, s.closing_value,
           g.name as godown,
           b.name as batch,
           b.quantity as batch_quantity
    FROM mst_stock_item s
    LEFT JOIN trn_inventory i ON s.name = i.item
    LEFT JOIN trn_voucher v ON i.guid = v.guid
    LEFT JOIN mst_godown g ON i.godown = g.name
    LEFT JOIN trn_batch b ON i.guid = b.guid
    WHERE s.user_id = :user_id AND s.company_name = :company_name
    GROUP BY s.name, s.opening_balance, s.opening_value, s.closing_balance, s.closing_value,
             g.name, b.name, b.quantity;
    """)

    vn_instance.train(sql="""
    -- Get Employee Payroll Summary
    SELECT e.name, e.designation, e.location,
           SUM(CASE WHEN p.payhead_name LIKE '%Basic%' THEN p.amount ELSE 0 END) as basic_pay,
           SUM(CASE WHEN p.payhead_name LIKE '%Allowance%' THEN p.amount ELSE 0 END) as allowances,
           SUM(CASE WHEN p.payhead_name LIKE '%Deduction%' THEN p.amount ELSE 0 END) as deductions,
           SUM(p.amount) as net_pay,
           SUM(a.time_value) as attendance_hours
    FROM mst_employee e
    LEFT JOIN trn_payhead p ON e.name = p.employee_name
    LEFT JOIN trn_attendance a ON e.name = a.employee_name
    WHERE e.user_id = :user_id AND e.company_name = :company_name
    GROUP BY e.name, e.designation, e.location;
    """)


def execute_with_fallback(user_query: str, vanna_instance=None, max_attempts=3):
    """
    Process query with fallback mechanism for handling errors and empty results
    
    Args:
        user_query: The user's original query
        vanna_instance: The Vanna instance to use (if None, uses global vn)
        max_attempts: Maximum number of retry attempts
        
    Returns:
        Tuple containing result DataFrame and status message
    """
    attempts = 0
    last_error = None
    current_query = user_query
    
    # Use provided instance or fall back to global
    if vanna_instance is None:
        _ensure_vn_connected()
    vn_instance = vanna_instance or vn
    logger.info(f"Using Vanna instance with user_id: {vn_instance.user_id}")
    if vn_instance.company_name:
        logger.info(f"Using company_name: {vn_instance.company_name}")
    
    while attempts < max_attempts:
        try:
            logger.info(f"\nAttempt {attempts+1} with query: {current_query}")
            
            # Generate SQL from the current query
            sql = vn_instance.generate_sql(question=current_query, allow_llm_to_see_data=True)
            logger.info(f"Generated SQL: {sql}")
            
            # Execute the SQL
            logger.info("Executing SQL query...")
            result = vn_instance.run_sql(sql)
            logger.info(f"Query result type: {type(result)}")
            if isinstance(result, pd.DataFrame):
                logger.info(f"Result shape: {result.shape}")
                logger.info(f"Result columns: {result.columns.tolist()}")
                logger.info(f"First few rows: {result.head().to_dict()}")
            
            # Handle empty results case
            if isinstance(result, pd.DataFrame) and result.empty:
                logger.warning("Query returned empty results")
                attempts += 1
                
                if attempts >= max_attempts:
                    break
                    
                # Refine the query for empty results
                current_query = vn_instance.refine_user_query(current_query, is_empty_result=True)
                logger.info(f"Refined query: {current_query}")
                continue
            
            # Convert date and decimal columns to string format if result is a DataFrame
            if isinstance(result, pd.DataFrame):
                for col in result.columns:
                    # Handle date columns
                    if 'date' in col.lower():
                        try:
                            result[col] = pd.to_datetime(result[col], format='%Y-%m-%d')
                            result[col] = result[col].dt.strftime('%Y-%m-%d')
                            logger.debug(f"Converted date column: {col}")
                        except:
                            logger.warning(f"Failed to convert date column: {col}")
                            pass
                    
                    # Handle numeric columns (including decimal)
                    if pd.api.types.is_numeric_dtype(result[col]):
                        result[col] = result[col].astype(float)
                        logger.debug(f"Converted numeric column: {col}")
                
            # Success case
            if attempts > 0:
                status_msg = f"✅ Query successful after {attempts+1} attempts with refinements."
            else:
                status_msg = "✅ Query executed successfully."
            logger.info(status_msg)
            return result, status_msg, sql
            
        except Exception as e:
            last_error = str(e)
            logger.error(f"Error executing query: {last_error}")
            attempts += 1
            
            if attempts >= max_attempts:
                break
                
            logger.info(f"Attempt {attempts} failed, trying to refine query...")
            
            # Refine the user query based on the error
            current_query = vn_instance.refine_user_query(current_query, error_message=last_error)
            logger.info(f"Refined query after error: {current_query}")
    
    # If we exit the loop, all attempts failed
    logger.error(f"Failed after {max_attempts} attempts. Last error: {last_error}")
    return None, f"❌ Failed after {max_attempts} attempts. Last error: {last_error}", None


def test_queries():
    print("\nTally Database Query Interface")
    print("Type 'exit' to quit")
    print("-" * 50)

    # Note: user_id is now set in the WebSocket endpoint
    # This test function is kept for development/testing purposes only

    while True:
        try:
            # Get user query
            user_query = input("\nEnter your natural language query (or 'exit' to quit): ").strip()

            if user_query.lower() == 'exit':
                print("Goodbye!")
                break

            print(f"\nProcessing query: {user_query}")
            
            # Execute with fallback mechanism
            result, status_message, sql = execute_with_fallback(user_query)
            print(status_message)
            
            if result is not None:
                print("\nResults:")
                if isinstance(result, pd.DataFrame) and not result.empty:
                    print(tabulate(result, headers='keys', tablefmt='pretty', showindex=False))
                    print(f"\nTotal rows: {len(result)}")
                else:
                    print("No results found.")
            else:
                print("\nCould not complete the query successfully.")

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")


def verify_postgres_connection():
    try:
        _ensure_vn_connected()
        vn.run_sql("SELECT 1")
        db = settings.db_components
        print("\n✅ Successfully connected to PostgreSQL database:")
        print(f"   - Host: {db['host']}")
        print(f"   - Database: {db['dbname']}")
        print(f"   - User: {db['user']}")
        print(f"   - Port: {db['port']}")
        return True
    except Exception as e:
        print("\n❌ Failed to connect to PostgreSQL database:")
        print(f"   Error: {str(e)}")
        return False


if __name__ == "__main__":
    print("Training Vanna with Tally database structure...")
    train_tally_structure()

    # Verify database connection before starting query interface
    if verify_postgres_connection():
        print("\nStarting query interface...")
        test_queries()
    else:
        print("\nExiting due to database connection failure.")