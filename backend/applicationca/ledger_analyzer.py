import pyodbc
import pandas as pd
import logging
from typing import Dict, List, Optional
import warnings
from functools import lru_cache

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LedgerAnalyzer:
    _instance = None
    _ledgers_df = None  # Cache for the ledger data
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LedgerAnalyzer, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.server = 'localhost,9000'
        self.conn_str = f'DRIVER={{Tally ODBC Driver64}};SERVER={self.server};Trusted_Connection=yes;'
        self._initialized = True
    
    def connect_to_tally(self) -> Optional[pyodbc.Connection]:
        """Establish connection to Tally database."""
        try:
            warnings.filterwarnings('ignore')
            pyodbc.drivers()
            return pyodbc.connect(self.conn_str)
        except Exception as e:
            logger.error(f"Failed to connect to Tally: {str(e)}")
            return None

    @lru_cache(maxsize=1)
    def get_detailed_ledgers(self) -> pd.DataFrame:
        """Get detailed ledger information including their properties and capabilities.
        This method is cached to avoid multiple SQL calls."""
        if self._ledgers_df is not None:
            logger.info("Using cached ledger data")
            return self._ledgers_df
            
        try:
            conn = self.connect_to_tally()
            if not conn:
                return pd.DataFrame()

            logger.info("Making SQL call to fetch ledger details")
            # Enhanced query to get more ledger details
            query = """
            SELECT 
                $Name,
                $Parent,
                $_PrimaryGroup,
                $_GroupName,
                $_IsRevenue,
                $_IsExpense,
                $_IsDeemedPositive
            FROM Ledger
            """
            
            self._ledgers_df = pd.read_sql(query, conn)
            conn.close()
            logger.info(f"Successfully fetched {len(self._ledgers_df)} ledgers from Tally")
            return self._ledgers_df
            
        except Exception as e:
            logger.error(f"Error fetching ledger details: {str(e)}")
            return pd.DataFrame()

    def categorize_ledgers(self) -> Dict[str, List[str]]:
        """Categorize ledgers based on their transaction capabilities."""
        df = self.get_detailed_ledgers()  # This will use cached data if available
        if df.empty:
            return {}

        categories = {
            'sales_capable': [],
            'purchase_capable': [],
            'payment_capable': [],
            'general': []
        }

        for _, row in df.iterrows():
            ledger_name = row['$Name'].strip()
            parent_group = row['$Parent'].strip() if pd.notna(row['$Parent']) else ""
            is_revenue = bool(row['$_IsRevenue']) if pd.notna(row['$_IsRevenue']) else False
            is_expense = bool(row['$_IsExpense']) if pd.notna(row['$_IsExpense']) else False
            
            # Sales-capable ledgers
            if any(group in parent_group.lower() for group in ['sundry debtors', 'sales accounts', 'income']) or is_revenue:
                categories['sales_capable'].append(ledger_name)
            
            # Purchase-capable ledgers
            if any(group in parent_group.lower() for group in ['sundry creditors', 'purchase accounts', 'expense']) or is_expense:
                categories['purchase_capable'].append(ledger_name)
            
            # Payment-capable ledgers (Bank and Cash related)
            if any(group in parent_group.lower() for group in ['bank accounts', 'cash in hand', 'bank od']):
                categories['payment_capable'].append(ledger_name)
            
            # General ledgers (those not falling in above categories)
            if ledger_name not in categories['sales_capable'] and \
               ledger_name not in categories['purchase_capable'] and \
               ledger_name not in categories['payment_capable']:
                categories['general'].append(ledger_name)

        return categories

    def analyze_ledger_properties(self) -> Dict[str, Dict]:
        """Analyze detailed properties of each ledger."""
        df = self.get_detailed_ledgers()  # This will use cached data if available
        if df.empty:
            return {}

        ledger_properties = {}
        
        for _, row in df.iterrows():
            ledger_name = row['$Name'].strip()
            ledger_properties[ledger_name] = {
                'parent_group': row['$Parent'].strip() if pd.notna(row['$Parent']) else "",
                'primary_group': row['$_PrimaryGroup'].strip() if pd.notna(row['$_PrimaryGroup']) else "",
                'is_revenue': bool(row['$_IsRevenue']) if pd.notna(row['$_IsRevenue']) else False,
                'is_expense': bool(row['$_IsExpense']) if pd.notna(row['$_IsExpense']) else False,
                'is_deemed_positive': bool(row['$_IsDeemedPositive']) if pd.notna(row['$_IsDeemedPositive']) else False
            }

        return ledger_properties

    def clear_cache(self):
        """Clear the cached ledger data."""
        self._ledgers_df = None
        self.get_detailed_ledgers.cache_clear()
        logger.info("Ledger cache cleared")

def main():
    """Main function to demonstrate the ledger analyzer functionality."""
    analyzer = LedgerAnalyzer()
    
    # First call will make SQL query
    print("\nFirst call - will query SQL:")
    categories = analyzer.categorize_ledgers()
    for category, ledgers in categories.items():
        print(f"\n{category.replace('_', ' ').title()} ({len(ledgers)} ledgers):")
        for ledger in ledgers:
            print(f"  - {ledger}")
    
    # Second call will use cached data
    print("\nSecond call - will use cached data:")
    properties = analyzer.analyze_ledger_properties()
    print(f"\nAnalyzed {len(properties)} ledgers")
    
    # Clear cache if needed
    analyzer.clear_cache()
    print("\nCache cleared - next call will query SQL again")

if __name__ == "__main__":
    main() 