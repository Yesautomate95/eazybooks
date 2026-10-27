import pyodbc
import requests
import json
from typing import List, Dict
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TallySchemaExtractor:
    def __init__(self, tally_url: str = "http://localhost:9000"):
        self.tally_url = tally_url
        self.tables: List[str] = []
        self.schemas: Dict[str, List[Dict]] = {}
        
    def connect_to_tally(self) -> pyodbc.Connection:
        """Connect to Tally using ODBC."""
        try:
            # Assuming Tally ODBC driver is installed and configured
            conn_str = (
                "DRIVER={Tally ODBC Driver};"
                f"SERVER={self.tally_url};"
                "DATABASE=Tally;"
            )
            return pyodbc.connect(conn_str)
        except pyodbc.Error as e:
            logger.error(f"Failed to connect to Tally: {e}")
            raise

    def get_all_tables(self) -> List[str]:
        """Get all table names from Tally."""
        try:
            conn = self.connect_to_tally()
            cursor = conn.cursor()
            
            # Execute query to get all table names
            cursor.execute("SELECT $Name FROM ODBC Tables")
            
            # Fetch all table names
            self.tables = [row[0] for row in cursor.fetchall()]
            logger.info(f"Found {len(self.tables)} tables in Tally")
            
            cursor.close()
            conn.close()
            return self.tables
            
        except Exception as e:
            logger.error(f"Error getting tables: {e}")
            raise

    def get_table_schema(self, table_name: str) -> List[Dict]:
        """Get schema for a specific table."""
        try:
            conn = self.connect_to_tally()
            cursor = conn.cursor()
            
            # Execute SELECT * to get column information
            cursor.execute(f"SELECT * FROM [{table_name}] WHERE 1=0")
            
            # Get column descriptions
            columns = cursor.description
            schema = []
            
            for col in columns:
                column_info = {
                    "name": col[0],
                    "type": str(col[1]),
                    "size": col[3],
                    "nullable": bool(col[6])
                }
                schema.append(column_info)
            
            cursor.close()
            conn.close()
            
            self.schemas[table_name] = schema
            return schema
            
        except Exception as e:
            logger.error(f"Error getting schema for table {table_name}: {e}")
            return []

    def generate_markdown(self) -> str:
        """Generate markdown documentation of the schema."""
        md_content = "# Tally Database Structure\n\n"
        md_content += "This document contains the schema of all tables in Tally.\n\n"
        
        for table_name in sorted(self.schemas.keys()):
            md_content += f"## {table_name}\n\n"
            md_content += "| Column Name | Data Type | Size | Nullable |\n"
            md_content += "|------------|-----------|------|----------|\n"
            
            for column in self.schemas[table_name]:
                md_content += f"| {column['name']} | {column['type']} | {column['size'] or 'N/A'} | "
                md_content += f"{'Yes' if column['nullable'] else 'No'} |\n"
            
            md_content += "\n"
        
        return md_content

    def extract_and_save_schemas(self):
        """Main method to extract all schemas and save to README."""
        try:
            # Get all tables
            logger.info("Getting all tables from Tally...")
            self.get_all_tables()
            
            # Get schema for each table
            logger.info("Extracting schema for each table...")
            for table in self.tables:
                logger.info(f"Processing table: {table}")
                self.get_table_schema(table)
            
            # Generate and save markdown
            logger.info("Generating markdown documentation...")
            md_content = self.generate_markdown()
            
            # Save to file
            readme_path = Path("TALLY-structure.md")
            readme_path.write_text(md_content, encoding='utf-8')
            logger.info(f"Schema documentation saved to {readme_path}")
            
        except Exception as e:
            logger.error(f"Error in extraction process: {e}")
            raise

def main():
    extractor = TallySchemaExtractor()
    extractor.extract_and_save_schemas()

if __name__ == "__main__":
    main() 