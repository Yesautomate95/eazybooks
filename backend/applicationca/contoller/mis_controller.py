from fastapi import APIRouter, Depends, HTTPException, Query
from applicationca.rbac import require_permission
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from functools import lru_cache
import logging
from enum import Enum

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Router instance
router = APIRouter(prefix="/api/mis", tags=["MIS"])

# Database configuration
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "tallydb",
    "user": "postgres",
    "password": "admin"
}

class PeriodType(str, Enum):
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"

class LedgerType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    REVENUE = "revenue"
    EXPENSE = "expense"

def get_db_connection():
    """Create and return a database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise HTTPException(status_code=500, detail="Database connection error")

@lru_cache(maxsize=128)
def get_cached_query_result(query: str, params: tuple) -> Dict:
    """Cache and execute database queries"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()

@router.get("/financial-summary", dependencies=[Depends(require_permission("reports.read"))])
async def get_financial_summary(period: PeriodType = Query(..., description="Period type for summary")):
    """
    Get financial summary including revenue, expenses, and profit.
    
    Parameters:
        period (PeriodType): Time period for the summary (week/month/quarter/year)
    """
    try:
        # Calculate date ranges based on period
        if period == PeriodType.WEEK:
            interval = "7 days"
        elif period == PeriodType.MONTH:
            interval = "1 month"
        elif period == PeriodType.QUARTER:
            interval = "3 months"
        else:  # YEAR
            interval = "1 year"
        
        # First, let's check if we have any data
        check_query = """
        SELECT 
            MIN(DATE_TRUNC('day', date)) as earliest_date,
            MAX(DATE_TRUNC('day', date)) as latest_date,
            COUNT(*) as total_transactions
        FROM trn_voucher v
        WHERE date <= CURRENT_DATE
        """
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(check_query)
                date_range = cur.fetchone()
                
                if not date_range or date_range.get('total_transactions', 0) == 0:
                    return {
                        "status": "success",
                        "data": {
                            "message": "No transactions found in the database",
                            "date_range": date_range
                        }
                    }
                
                # Main query with proper date handling
                query = """
                WITH date_range AS (
                    SELECT 
                        CURRENT_DATE as end_date,
                        (CURRENT_DATE - interval %s) as start_date
                ),
                current_period AS (
                    SELECT 
                        -- Revenue calculation
                        COALESCE(SUM(CASE 
                            WHEN g.primary_group IN ('Sales Accounts', 'Direct Incomes', 'Indirect Incomes') 
                                OR (g.affects_gross_profit = 1 AND g.is_revenue = 1) THEN 
                                CASE 
                                    WHEN g.is_deemedpositive = 0 THEN a.amount 
                                    ELSE -a.amount 
                                END
                            ELSE 0 
                        END), 0) as revenue,
                        
                        -- Expenses calculation
                        COALESCE(SUM(CASE 
                            WHEN g.primary_group IN ('Purchase Accounts', 'Direct Expenses', 'Indirect Expenses') 
                                OR (g.affects_gross_profit = 1 AND g.is_revenue = 0) THEN 
                                CASE 
                                    WHEN g.is_deemedpositive = 1 THEN a.amount 
                                    ELSE -a.amount 
                                END
                            ELSE 0 
                        END), 0) as expenses,
                        
                        -- Transaction metrics
                        COUNT(DISTINCT v.guid) as transaction_count,
                        COUNT(DISTINCT l.name) as active_accounts,
                        COUNT(DISTINCT CASE 
                            WHEN g.primary_group IN ('Sales Accounts', 'Direct Incomes', 'Indirect Incomes') 
                            THEN l.name 
                        END) as revenue_accounts,
                        COUNT(DISTINCT CASE 
                            WHEN g.primary_group IN ('Purchase Accounts', 'Direct Expenses', 'Indirect Expenses') 
                            THEN l.name 
                        END) as expense_accounts,
                        
                        -- Period information
                        MIN(v.date) as period_start_date,
                        MAX(v.date) as period_end_date
                    FROM trn_voucher v
                    JOIN trn_accounting a ON v.guid = a.guid
                    JOIN mst_ledger l ON a.ledger = l.name
                    JOIN mst_group g ON l.parent = g.name
                    CROSS JOIN date_range d
                    WHERE v.date >= d.start_date 
                    AND v.date <= d.end_date
                )
                SELECT 
                    revenue,
                    expenses,
                    (revenue - expenses) as net_profit,
                    transaction_count,
                    active_accounts,
                    revenue_accounts,
                    expense_accounts,
                    period_start_date,
                    period_end_date,
                    CASE 
                        WHEN revenue = 0 THEN 0
                        ELSE ROUND((expenses / revenue * 100)::numeric, 2)
                    END as expense_ratio
                FROM current_period;
                """
                
                cur.execute(query, (interval,))
                result = cur.fetchone()
                
                return {
                    "status": "success",
                    "data": {
                        "summary": result or {},
                        "period_type": period,
                        "interval": interval,
                        "database_date_range": date_range
                    }
                }
    except Exception as e:
        logger.error(f"Error in financial summary: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/ledgers", dependencies=[Depends(require_permission("reports.read"))])
async def get_ledgers(type: Optional[LedgerType] = None):
    """
    Get ledger accounts with balances and growth.
    
    Parameters:
        type (LedgerType): Optional filter for ledger type
    """
    try:
        # Debug logging
        logger.info(f"Received ledger type: {type}")
        
        type_conditions = {
            LedgerType.ASSET: "g.primary_group LIKE '%Assets%'",
            LedgerType.LIABILITY: "g.primary_group LIKE '%Liabilities%'",
            LedgerType.REVENUE: "g.is_revenue = 1",
            LedgerType.EXPENSE: "g.is_revenue = 0 AND g.affects_gross_profit = 1"
        }
        
        # Add debug logging for type conditions
        logger.info(f"Available type conditions: {list(type_conditions.keys())}")
        
        where_clause = ""
        if type is not None:
            if type in type_conditions:
                where_clause = f"WHERE {type_conditions[type]}"
                logger.info(f"Applied where clause: {where_clause}")
            else:
                logger.warning(f"Invalid type provided: {type}")
                raise HTTPException(status_code=400, detail=f"Invalid ledger type. Must be one of: {list(type_conditions.keys())}")
        
        query = f"""
        SELECT 
            l.name as ledger_name,
            l.parent as group_name,
            g.primary_group,
            COUNT(DISTINCT a.guid) as transaction_count,
            COALESCE(SUM(CASE 
                WHEN g.is_deemedpositive = 1 THEN a.amount 
                ELSE -a.amount 
            END), 0) as balance,
            MIN(v.date) as first_transaction,
            MAX(v.date) as last_transaction
        FROM mst_ledger l
        JOIN mst_group g ON l.parent = g.name
        LEFT JOIN trn_accounting a ON l.name = a.ledger
        LEFT JOIN trn_voucher v ON a.guid = v.guid
        {where_clause}
        GROUP BY l.name, l.parent, g.primary_group
        ORDER BY g.primary_group, l.parent, l.name;
        """
        
        # Log the final query for debugging
        logger.info(f"Executing query: {query}")
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                result = cur.fetchall()
                
                # Log the result count
                logger.info(f"Found {len(result) if result else 0} ledgers")
                
                return {
                    "status": "success",
                    "data": result or []
                }
                
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error in ledgers: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/monthly-trends", dependencies=[Depends(require_permission("reports.read"))])
async def get_monthly_trends(months: int = Query(12, ge=1, le=60)):
    """
    Get monthly trends for revenue, expenses, and profit.
    
    Parameters:
        months (int): Number of months to return (1-60)
    """
    try:
        # Log the parameters for debugging
        logger.info(f"Fetching monthly trends for last {months} months")
        
        query = f"""
        WITH RECURSIVE months AS (
            SELECT DATE_TRUNC('month', CURRENT_DATE) as month
            UNION ALL
            SELECT DATE_TRUNC('month', month - interval '1 month')
            FROM months
            WHERE DATE_TRUNC('month', month - interval '1 month') >= 
                  DATE_TRUNC('month', CURRENT_DATE - interval '{months} months')
        ),
        monthly_data AS (
            SELECT 
                DATE_TRUNC('month', v.date) as month,
                SUM(CASE 
                    WHEN g.primary_group IN ('Sales Accounts', 'Direct Incomes', 'Indirect Incomes') THEN 
                        CASE WHEN g.is_deemedpositive = 0 THEN a.amount ELSE -a.amount END
                    ELSE 0 
                END) as revenue,
                SUM(CASE 
                    WHEN g.primary_group IN ('Direct Expenses', 'Indirect Expenses') THEN 
                        CASE WHEN g.is_deemedpositive = 1 THEN -a.amount ELSE a.amount END
                    ELSE 0 
                END) as expenses
            FROM trn_accounting a
            JOIN trn_voucher v ON a.guid = v.guid
            JOIN mst_ledger l ON a.ledger = l.name
            JOIN mst_group g ON l.parent = g.name
            GROUP BY DATE_TRUNC('month', v.date)
        )
        SELECT 
            TO_CHAR(m.month, 'YYYY-MM') as period,
            COALESCE(md.revenue, 0) as revenue,
            COALESCE(md.expenses, 0) as expenses,
            COALESCE(md.revenue - md.expenses, 0) as profit
        FROM months m
        LEFT JOIN monthly_data md ON m.month = md.month
        ORDER BY m.month DESC;
        """
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                try:
                    # Execute without parameters since we're using f-string
                    cur.execute(query)
                    result = cur.fetchall()
                    
                    # Log the result count
                    logger.info(f"Found {len(result) if result else 0} monthly records")
                    
                    return {
                        "status": "success",
                        "data": result or []
                    }
                except Exception as e:
                    logger.error(f"Database error in monthly trends: {str(e)}", exc_info=True)
                    raise HTTPException(status_code=500, detail=str(e))
                    
    except Exception as e:
        logger.error(f"Error in monthly trends: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/inventory", dependencies=[Depends(require_permission("reports.read"))])
async def get_inventory():
    """Get inventory items with quantities and values"""
    try:
        query = """
        WITH latest_inventory AS (
            SELECT DISTINCT ON (i.item)
                i.item,
                i.quantity,
                i.rate,
                v.date as transaction_date
            FROM trn_inventory i
            JOIN trn_voucher v ON i.guid = v.guid
            WHERE v.date <= CURRENT_DATE
            ORDER BY i.item, v.date DESC
        )
            SELECT 
                si.name as item_name,
                si.parent as group_name,
                COALESCE(sg.parent, sg.name) as category,
                COALESCE(li.quantity, si.closing_balance) as quantity,
                COALESCE(li.rate, 0) as unit_rate,
                si.opening_balance as opening_stock,
                si.closing_balance as closing_stock,
                li.transaction_date as last_transaction,
                CASE 
                    WHEN li.transaction_date IS NULL THEN 'No Movement'
                    WHEN li.transaction_date >= CURRENT_DATE - interval '7 days' THEN 'Recent'
                    WHEN li.transaction_date >= CURRENT_DATE - interval '30 days' THEN 'Active'
                    ELSE 'Inactive'
                END as stock_status
            FROM mst_stock_item si
            JOIN mst_stock_group sg ON si.parent = sg.name
            LEFT JOIN latest_inventory li ON li.item = si.name
            WHERE COALESCE(li.quantity, si.closing_balance) != 0
            ORDER BY category, group_name, item_name;
        """
        
        result = get_cached_query_result(query, ())
        
        return {
            "status": "success",
            "data": result or []
        }
    except Exception as e:
        logger.error(f"Error in inventory: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/voucher-distribution", dependencies=[Depends(require_permission("reports.read"))])
async def get_voucher_distribution():
    """Get voucher type distribution"""
    try:
        query = """
        WITH voucher_stats AS (
            SELECT 
                v.voucher_type,
                COUNT(DISTINCT v.guid) as voucher_count,
                COUNT(DISTINCT a.guid) as entry_count,
                COUNT(DISTINCT a.ledger) as unique_ledgers,
                COALESCE(SUM(CASE 
                    WHEN g.is_deemedpositive = 1 THEN a.amount 
                    ELSE -a.amount 
                END), 0) as total_value,
                MIN(v.date) as first_transaction,
                MAX(v.date) as last_transaction
            FROM trn_voucher v
            LEFT JOIN trn_accounting a ON v.guid = a.guid
            LEFT JOIN mst_ledger l ON a.ledger = l.name
            LEFT JOIN mst_group g ON l.parent = g.name
            WHERE v.date <= CURRENT_DATE
            GROUP BY v.voucher_type
        )
        SELECT 
            vt.name as voucher_type,
            vt.parent as voucher_category,
            vt.affects_stock,
            vs.voucher_count,
            vs.entry_count,
            vs.unique_ledgers,
            vs.total_value,
            ROUND(vs.total_value / NULLIF(vs.voucher_count, 0), 2) as avg_value_per_voucher,
            vs.first_transaction,
            vs.last_transaction,
            CASE 
                WHEN vs.last_transaction IS NULL THEN 'Never Used'
                WHEN vs.last_transaction >= CURRENT_DATE - interval '7 days' THEN 'Very Active'
                WHEN vs.last_transaction >= CURRENT_DATE - interval '30 days' THEN 'Active'
                ELSE 'Inactive'
            END as usage_status
        FROM mst_vouchertype vt
        LEFT JOIN voucher_stats vs ON vt.name = vs.voucher_type
        ORDER BY vs.voucher_count DESC NULLS LAST, vt.name;
        """
        
        result = get_cached_query_result(query, ())
        
        return {
            "status": "success",
            "data": result or []
        }
    except Exception as e:
        logger.error(f"Error in voucher distribution: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/gst-summary", dependencies=[Depends(require_permission("reports.read"))])
async def get_gst_summary():
    """Get GST summary including input, output, and payable GST"""
    try:
        # First, let's check if we have any GST transactions
        check_query = """
        SELECT 
            COUNT(DISTINCT a.guid) as transaction_count,
            COUNT(DISTINCT l.name) as ledger_count
        FROM mst_ledger l
        LEFT JOIN trn_accounting a ON l.name = a.ledger
        LEFT JOIN trn_voucher v ON a.guid = v.guid AND v.date <= CURRENT_DATE
        WHERE (l.name ILIKE '%GST%' OR l.parent ILIKE '%GST%');
        """
        
        empty_result = {
            'input_gst': 0,
            'output_gst': 0,
            'igst_amount': 0,
            'cgst_amount': 0,
            'sgst_amount': 0,
            'input_transaction_days': 0,
            'output_transaction_days': 0,
            'total_transaction_days': 0,
            'total_ledgers': 0,
            'first_gst_transaction': None,
            'last_gst_transaction': None,
            'active_months': 0
        }
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(check_query)
                    check_result = cur.fetchone()
                    if not check_result or check_result['transaction_count'] == 0:
                        logger.info("No GST transactions found in the given period")
                        empty_result['total_ledgers'] = check_result['ledger_count'] if check_result else 0
                        return {
                            "status": "success",
                            "data": empty_result
                        }
                except Exception as e:
                    logger.error(f"Error in check query: {str(e)}")
                    return {
                        "status": "success",
                        "data": empty_result
                    }

                # If we have transactions, run the main query
                query = """
                WITH gst_ledgers AS (
                    -- First identify GST-related ledgers and their types
                    SELECT 
                        l.name,
                        l.parent,
                        g.primary_group,
                        g.is_revenue,
                        g.affects_gross_profit,
                        CASE 
                            WHEN l.name ILIKE '%IGST%' THEN 'IGST'
                            WHEN l.name ILIKE '%CGST%' THEN 'CGST'
                            WHEN l.name ILIKE '%SGST%' THEN 'SGST'
                            ELSE 'OTHER'
                        END as gst_type,
                        CASE 
                            WHEN l.name ILIKE '%Input%' OR l.name ILIKE '%ITC%' OR g.primary_group ILIKE '%Input%' THEN 'INPUT'
                            WHEN l.name ILIKE '%Output%' OR l.name ILIKE '%Payable%' OR g.primary_group ILIKE '%Output%' THEN 'OUTPUT'
                            ELSE 'OTHER'
                        END as tax_direction
                    FROM mst_ledger l
                    JOIN mst_group g ON l.parent = g.name
                    WHERE l.name ILIKE '%GST%' OR l.parent ILIKE '%GST%'
                ),
                gst_transactions AS (
                    -- Calculate GST transactions
                    SELECT 
                        gl.*,
                        v.date,
                        CASE WHEN g.is_deemedpositive = 1 THEN a.amount ELSE -a.amount END as amount
                    FROM trn_voucher v
                    JOIN trn_accounting a ON v.guid = a.guid
                    JOIN gst_ledgers gl ON a.ledger = gl.name
                    JOIN mst_group g ON gl.parent = g.name
                    WHERE v.date <= CURRENT_DATE
                )
                SELECT 
                    -- Input GST
                    COALESCE(SUM(CASE WHEN tax_direction = 'INPUT' THEN amount ELSE 0 END), 0) as input_gst,
                    
                    -- Output GST
                    COALESCE(SUM(CASE WHEN tax_direction = 'OUTPUT' THEN amount ELSE 0 END), 0) as output_gst,
                    
                    -- GST Type Breakdown
                    COALESCE(SUM(CASE WHEN gst_type = 'IGST' THEN amount ELSE 0 END), 0) as igst_amount,
                    COALESCE(SUM(CASE WHEN gst_type = 'CGST' THEN amount ELSE 0 END), 0) as cgst_amount,
                    COALESCE(SUM(CASE WHEN gst_type = 'SGST' THEN amount ELSE 0 END), 0) as sgst_amount,
                    
                    -- Transaction Days
                    COUNT(DISTINCT CASE WHEN tax_direction = 'INPUT' THEN date END) as input_transaction_days,
                    COUNT(DISTINCT CASE WHEN tax_direction = 'OUTPUT' THEN date END) as output_transaction_days,
                    
                    -- Overall Stats
                    COUNT(DISTINCT date) as total_transaction_days,
                    COUNT(DISTINCT name) as total_ledgers,
                    
                    -- Date Range
                    MIN(date) as first_gst_transaction,
                    MAX(date) as last_gst_transaction,
                    COUNT(DISTINCT DATE_TRUNC('month', date)) as active_months
                FROM gst_transactions;
                """
                
                try:
                    cur.execute(query)
                    result = cur.fetchone()
                    
                    if result:
                        # Convert Decimal to float for JSON serialization
                        summary = {
                            'input_gst': float(result['input_gst']) if result['input_gst'] is not None else 0,
                            'output_gst': float(result['output_gst']) if result['output_gst'] is not None else 0,
                            'igst_amount': float(result['igst_amount']) if result['igst_amount'] is not None else 0,
                            'cgst_amount': float(result['cgst_amount']) if result['cgst_amount'] is not None else 0,
                            'sgst_amount': float(result['sgst_amount']) if result['sgst_amount'] is not None else 0,
                            'input_transaction_days': result['input_transaction_days'] or 0,
                            'output_transaction_days': result['output_transaction_days'] or 0,
                            'total_transaction_days': result['total_transaction_days'] or 0,
                            'total_ledgers': result['total_ledgers'] or 0,
                            'first_gst_transaction': result['first_gst_transaction'],
                            'last_gst_transaction': result['last_gst_transaction'],
                            'active_months': result['active_months'] or 0
                        }
                        return {
                            "status": "success",
                            "data": summary
                        }
                    else:
                        logger.info("No GST transactions found in main query")
                        return {
                            "status": "success",
                            "data": empty_result
                        }
                except Exception as e:
                    logger.error(f"Error in main query: {str(e)}")
        return {
            "status": "success",
                        "data": empty_result
        }
                    
    except Exception as e:
        logger.error(f"Error in GST summary: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cost-center", dependencies=[Depends(require_permission("reports.read"))])
async def get_cost_center_allocations():
    """Get cost center allocations and percentages"""
    try:
        query = """
        WITH RECURSIVE cost_hierarchy AS (
            SELECT 
                cc.guid,
                cc.name,
                cc.parent,
                1 as level,
                cc.name::varchar as path
            FROM mst_cost_centre cc
            WHERE NOT EXISTS (
                SELECT 1 FROM mst_cost_centre child WHERE child.parent = cc.name
            )
            
            UNION ALL
            
            SELECT 
                p.guid,
                p.name,
                p.parent,
                ch.level + 1,
                (p.name || ' > ' || ch.path)::varchar
            FROM mst_cost_centre p
            JOIN cost_hierarchy ch ON ch.parent = p.name
        ),
        cost_transactions AS (
            SELECT 
                cc.guid,
                cc.name as cost_center,
                cc.parent as category,
                ch.level as hierarchy_level,
                ch.path as full_path,
                COALESCE(SUM(tcc.amount), 0) as allocated_amount,
                COUNT(DISTINCT tcc.guid) as transaction_count,
                COUNT(DISTINCT tcc.ledger) as unique_ledgers,
                MIN(v.date) as first_allocation,
                MAX(v.date) as last_allocation
            FROM mst_cost_centre cc
            JOIN cost_hierarchy ch ON cc.guid = ch.guid
            LEFT JOIN trn_cost_centre tcc ON cc.name = tcc.costcentre
            LEFT JOIN trn_voucher v ON tcc.guid = v.guid
            WHERE v.date <= CURRENT_DATE OR v.date IS NULL
            GROUP BY cc.guid, cc.name, cc.parent, ch.level, ch.path
        ),
        category_totals AS (
            SELECT 
                category,
                SUM(allocated_amount) as total_amount,
                SUM(transaction_count) as total_transactions
            FROM cost_transactions
            GROUP BY category
        )
        SELECT 
            ct.cost_center,
            ct.category,
            ct.hierarchy_level,
            ct.full_path,
            ct.allocated_amount,
            ct.transaction_count,
            ct.unique_ledgers,
            COALESCE(cat.total_amount, 0) as category_total,
            COALESCE(cat.total_transactions, 0) as category_transactions,
            CASE 
                WHEN COALESCE(cat.total_amount, 0) = 0 THEN 0
                ELSE ROUND((ct.allocated_amount / cat.total_amount * 100)::numeric, 2)
            END as allocation_percentage,
            ct.first_allocation,
            ct.last_allocation,
            CASE 
                WHEN ct.last_allocation IS NULL THEN 'Never Used'
                WHEN ct.last_allocation >= CURRENT_DATE - interval '7 days' THEN 'Active'
                WHEN ct.last_allocation >= CURRENT_DATE - interval '30 days' THEN 'Recent'
                ELSE 'Inactive'
            END as usage_status
        FROM cost_transactions ct
        LEFT JOIN category_totals cat ON ct.category = cat.category
        ORDER BY ct.category, ct.hierarchy_level DESC, ct.allocated_amount DESC;
        """
        
        result = get_cached_query_result(query, ())
        
        return {
            "status": "success",
            "data": result or []
        }
    except Exception as e:
        logger.error(f"Error in cost center allocations: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) 