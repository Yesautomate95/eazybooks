-- Migration 042: Tally loader tables (mst_* / trn_*) for the local Postgres.
--
-- These tables previously lived only in the old Aiven database (created by the
-- desktop tally-loader). A fresh deployment has none, so Tally Chat / Analytics
-- fail with 'relation "mst_ledger" does not exist'. Migrations 031/032 skipped
-- them (tables absent) so their tenant-column and RLS steps are repeated here.

CREATE TABLE IF NOT EXISTS _diff
(
 guid varchar(64) not null,
 alterid int not null
);

CREATE TABLE IF NOT EXISTS _delete
(
 guid varchar(64) not null
);

CREATE TABLE IF NOT EXISTS _vchnumber
(
 guid varchar(64) not null,
 voucher_number varchar(256) not null
);

CREATE TABLE IF NOT EXISTS config
(
 name varchar(64) not null primary key,
 value varchar(1024)
);

CREATE TABLE IF NOT EXISTS mst_group
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 primary_group varchar(1024) not null default '',
 is_revenue smallint,
 is_deemedpositive smallint,
 is_reserved smallint,
 affects_gross_profit smallint,
 sort_position int
);

CREATE TABLE IF NOT EXISTS mst_ledger
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 alias varchar(256) not null default '',
 description varchar(64) not null default '',
 notes varchar(64) not null default '',
 is_revenue smallint,
 is_deemedpositive smallint,
 opening_balance decimal(17,2) default 0,
 closing_balance decimal(17,2) default 0,
 mailing_name varchar(256) not null default '',
 mailing_address varchar(1024) not null default '',
 mailing_state varchar(256) not null default '',
 mailing_country varchar(256) not null default '',
 mailing_pincode varchar(64) not null default '',
 email varchar(256) not null default '',
 it_pan varchar(64) not null default '',
 gstn varchar(64) not null default '',
 gst_registration_type varchar(64) not null default '',
 gst_supply_type varchar(64) not null default '',
 gst_duty_head varchar(16) not null default '',
 tax_rate decimal(9,4) default 0,
 bank_account_holder varchar(256) not null default '',
 bank_account_number varchar(64) not null default '',
 bank_ifsc varchar(64) not null default '',
 bank_swift varchar(64) not null default '',
 bank_name varchar(64) not null default '',
 bank_branch varchar(64) not null default '',
 bill_credit_period int not null default 0
);

CREATE TABLE IF NOT EXISTS mst_vouchertype
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 numbering_method varchar(64) not null default '',
 is_deemedpositive smallint,
 affects_stock smallint
);

CREATE TABLE IF NOT EXISTS mst_uom
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 formalname varchar(256) not null default '',
 is_simple_unit smallint not null,
 base_units varchar(1024) not null,
 additional_units varchar(1024) not null,
 conversion int not null
);

CREATE TABLE IF NOT EXISTS mst_godown
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 address varchar(1024) not null default ''
);

CREATE TABLE IF NOT EXISTS mst_stock_group
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default ''
);

CREATE TABLE IF NOT EXISTS mst_stock_item
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 alias varchar(256) not null default '',
 description varchar(64) not null default '',
 notes varchar(64) not null default '',
 part_number varchar(256) not null default '',
 uom varchar(32) not null default '',
 _uom varchar(64) not null default '',
 alternate_uom varchar(32) not null default '',
 _alternate_uom varchar(64) not null default '',
 conversion int not null default 0,
 opening_balance decimal(15,4) default 0,
 opening_rate decimal(15,4) default 0,
 opening_value decimal(17,2) default 0,
 closing_balance decimal(15,4) default 0,
 closing_rate decimal(15,4) default 0,
 closing_value decimal(17,2) default 0,
 costing_method varchar(32) not null default '',
 gst_type_of_supply varchar(32) default '',
 gst_hsn_code varchar(64) default '',
 gst_hsn_description varchar(256) default '',
 gst_rate int default 0,
 gst_taxability varchar(32) default ''
);

CREATE TABLE IF NOT EXISTS mst_cost_category
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 allocate_revenue smallint,
 allocate_non_revenue smallint
);

CREATE TABLE IF NOT EXISTS mst_cost_centre
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 category varchar(1024) not null default ''
);

CREATE TABLE IF NOT EXISTS mst_attendance_type
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 uom varchar(32) not null default '',
 _uom varchar(64) not null default '',
 attendance_type varchar(64) not null default '',
 attendance_period varchar(64) not null default ''
);

CREATE TABLE IF NOT EXISTS mst_employee
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 id_number varchar(256) not null default '',
 date_of_joining date,
 date_of_release date,
 designation varchar(64) not null default '',
 function_role varchar(64) not null default '',
 location varchar(256) not null default '',
 gender varchar(32) not null default '',
 date_of_birth date,
 blood_group varchar(32) not null default '',
 father_mother_name varchar(256) not null default '',
 spouse_name varchar(256) not null default '',
 address varchar(256) not null default '',
 mobile varchar(32) not null default '',
 email varchar(64) not null default '',
 pan varchar(32) not null default '',
 aadhar varchar(32) not null default '',
 uan varchar(32) not null default '',
 pf_number varchar(32) not null default '',
 pf_joining_date date,
 pf_relieving_date date,
 pr_account_number varchar(32) not null default ''
);

CREATE TABLE IF NOT EXISTS mst_payhead
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 name varchar(1024) not null default '',
 parent varchar(1024) not null default '',
 _parent varchar(64) not null default '',
 payslip_name varchar(1024) not null default '',
 pay_type varchar(64) not null default '',
 income_type varchar(64) not null default '',
 calculation_type varchar(32) not null default '',
 leave_type varchar(64) not null default '',
 calculation_period varchar(32) not null default ''
);

CREATE TABLE IF NOT EXISTS mst_gst_effective_rate
(
 item varchar(1024) not null default '',
 _item varchar(64) not null default '',
 applicable_from date,
 hsn_description varchar(256) not null default '',
 hsn_code varchar(64) not null default '',
 rate decimal(9,4) default 0,
 is_rcm_applicable smallint,
 nature_of_transaction varchar(64) not null default '',
 nature_of_goods varchar(64) not null default '',
 supply_type varchar(64) not null default '',
 taxability varchar(64) not null default ''
);

CREATE TABLE IF NOT EXISTS mst_opening_batch_allocation
(
 name varchar(1024) not null default '',
 item varchar(1024) not null default '',
 _item varchar(64) not null default '',
 opening_balance decimal(15,4) default 0,
 opening_rate decimal(15,4) default 0,
 opening_value decimal(17,2) default 0,
 godown varchar(1024) not null default '',
 _godown varchar(64) not null default '',
 manufactured_on date
);

CREATE TABLE IF NOT EXISTS mst_opening_bill_allocation
(
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 opening_balance decimal(17,4) default 0,
 bill_date date,
 name varchar(1024) not null default '',
 bill_credit_period int not null default 0,
 is_advance smallint
);

CREATE TABLE IF NOT EXISTS trn_closingstock_ledger
(
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 stock_date date,
 stock_value decimal(17,2) not null default 0
);

CREATE TABLE IF NOT EXISTS mst_stockitem_standard_cost
(
 item varchar(1024) not null default '',
 _item varchar(64) not null default '',
 date date,
 rate decimal(15,4) default 0
);

CREATE TABLE IF NOT EXISTS mst_stockitem_standard_price
(
 item varchar(1024) not null default '',
 _item varchar(64) not null default '',
 date date,
 rate decimal(15,4) default 0
);

CREATE TABLE IF NOT EXISTS trn_voucher
(
 guid varchar(64) not null primary key,
 alterid int not null default 0,
 date date not null,
 voucher_type varchar(1024) not null,
 _voucher_type varchar(64) not null default '',
 voucher_number varchar(64) not null default '',
 reference_number varchar(64) not null default '',
 reference_date date,
 narration varchar(4000) not null default '',
 party_name varchar(256) not null,
 _party_name varchar(64) not null default '',
 place_of_supply varchar(256) not null,
 is_invoice smallint,
 is_accounting_voucher smallint,
 is_inventory_voucher smallint,
 is_order_voucher smallint
);

CREATE TABLE IF NOT EXISTS trn_accounting
(
 guid varchar(64) not null default '',
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 amount decimal(17,2) not null default 0,
 amount_forex decimal(17,2) not null default 0,
 currency varchar(16) not null default ''
);

CREATE TABLE IF NOT EXISTS trn_inventory
(
 guid varchar(64) not null default '',
 item varchar(1024) not null default '',
 _item varchar(64) not null default '',
 quantity decimal(15,4) not null default 0,
 rate decimal(15,4) not null default 0,
 amount decimal(17,2) not null default 0,
 additional_amount decimal(17,2) not null default 0,
 discount_amount decimal(17,2) not null default 0,
 godown varchar(1024),
 _godown varchar(64) not null default '',
 tracking_number varchar(256),
 order_number varchar(256),
 order_duedate date
);

CREATE TABLE IF NOT EXISTS trn_cost_centre
(
 guid varchar(64) not null default '',
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 costcentre varchar(1024) not null default '',
 _costcentre varchar(64) not null default '',
 amount decimal(17,2) not null default 0
);

CREATE TABLE IF NOT EXISTS trn_cost_category_centre
(
 guid varchar(64) not null default '',
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 costcategory varchar(1024) not null default '',
 _costcategory varchar(64) not null default '',
 costcentre varchar(1024) not null default '',
 _costcentre varchar(64) not null default '',
 amount decimal(17,2) not null default 0
);

CREATE TABLE IF NOT EXISTS trn_cost_inventory_category_centre
(
 guid varchar(64) not null default '',
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 item varchar(1024) not null default '',
 _item varchar(64) not null default '',
 costcategory varchar(1024) not null default '',
 _costcategory varchar(64) not null default '',
 costcentre varchar(1024) not null default '',
 _costcentre varchar(64) not null default '',
 amount decimal(17,2) not null default 0
);

CREATE TABLE IF NOT EXISTS trn_bill
(
 guid varchar(64) not null default '',
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 name varchar(1024) not null default '',
 amount decimal(17,2) not null default 0,
 billtype varchar(256) not null default '',
 bill_credit_period int not null default 0
);

CREATE TABLE IF NOT EXISTS trn_bank
(
 guid varchar(64) not null default '',
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 transaction_type varchar(32) not null default '',
 instrument_date date,
 instrument_number varchar(1024) not null default '',
 bank_name varchar(64) not null default '',
 amount decimal(17,2) not null default 0,
 bankers_date date
);

CREATE TABLE IF NOT EXISTS trn_batch
(
 guid varchar(64) not null default '',
 item varchar(1024) not null default '',
 _item varchar(64) not null default '',
 name varchar(1024) not null default '',
 quantity decimal(15,4) not null default 0,
 amount decimal(17,2) not null default 0,
 godown varchar(1024),
 _godown varchar(64) not null default '',
 destination_godown varchar(1024),
 _destination_godown varchar(64) not null default '',
 tracking_number varchar(1024)
);

CREATE TABLE IF NOT EXISTS trn_inventory_accounting
(
 guid varchar(64) not null default '',
 ledger varchar(1024) not null default '',
 _ledger varchar(64) not null default '',
 amount decimal(17,2) not null default 0,
 additional_allocation_type varchar(32) not null default ''
);

CREATE TABLE IF NOT EXISTS trn_employee
(
 guid varchar(64) not null default '',
 category varchar(1024) not null default '',
 _category varchar(64) not null default '',
 employee_name varchar(1024) not null default '',
 _employee_name varchar(64) not null default '',
 amount decimal(17,2) not null default 0,
 employee_sort_order int not null default 0
);

CREATE TABLE IF NOT EXISTS trn_payhead
(
 guid varchar(64) not null default '',
 category varchar(1024) not null default '',
 _category varchar(64) not null default '',
 employee_name varchar(1024) not null default '',
 _employee_name varchar(64) not null default '',
 employee_sort_order int not null default 0,
 payhead_name varchar(1024) not null default '',
 _payhead_name varchar(64) not null default '',
 payhead_sort_order int not null default 0,
 amount decimal(17,2) not null default 0
);

CREATE TABLE IF NOT EXISTS trn_attendance
(
 guid varchar(64) not null default '',
 employee_name varchar(1024) not null default '',
 _employee_name varchar(64) not null default '',
 attendancetype_name varchar(1024) not null default '',
 _attendancetype_name varchar(64) not null default '',
 time_value decimal(17,2) not null default 0,
 type_value decimal(17,2) not null default 0
);

-- Tenant columns + indexes (from 031)
DO $$
DECLARE
    t TEXT;
    read_tables TEXT[] := ARRAY[
        'mst_group', 'mst_ledger', 'mst_stock_item', 'mst_stock_group',
        'mst_vouchertype', 'mst_uom', 'mst_godown',
        'trn_voucher', 'trn_accounting', 'trn_inventory',
        'trn_bill', 'trn_bank', 'trn_batch', 'trn_cost_centre'
    ];
BEGIN
    FOREACH t IN ARRAY read_tables LOOP
        -- Skip tables that don't exist in this deployment yet (loader creates
        -- them lazily); the loader will add them with the right shape.
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS user_id varchar(255)', t);
            EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS company_name varchar(255)', t);
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON %I (user_id, company_name)',
                'idx_' || t || '_tenant', t
            );
        END IF;
    END LOOP;

    -- Time-series reads (cash flow, monthly P&L) scan trn_voucher by date
    -- within a tenant — give them a covering composite.
    IF to_regclass('trn_voucher') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_trn_voucher_tenant_date
            ON trn_voucher (user_id, company_name, date);
    END IF;

    -- Ledger-amount rollups join trn_accounting on guid then group by ledger.
    IF to_regclass('trn_accounting') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_trn_accounting_tenant_ledger
            ON trn_accounting (user_id, company_name, ledger);
    END IF;
END $$;


-- RLS for TallyChat read role (from 032)
DO $$
DECLARE
    t TEXT;
BEGIN
    -- 1. Least-privilege read role + the membership that lets the owner
    --    (this migration's CURRENT_USER, == the backend's runtime role) SET ROLE
    --    into it. NOLOGIN: it is only ever entered via SET LOCAL ROLE, never
    --    connected to directly.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tally_chat_ro') THEN
        CREATE ROLE tally_chat_ro NOLOGIN;
    END IF;
    GRANT USAGE ON SCHEMA public TO tally_chat_ro;
    EXECUTE format('GRANT tally_chat_ro TO %I', CURRENT_USER);

    -- 2. For every public mst_/trn_ table that has BOTH tenant columns: grant
    --    SELECT to the read role, enable RLS, and (re)create the tenant policy.
    FOR t IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind = 'r'
           AND (c.relname LIKE 'mst_%' OR c.relname LIKE 'trn_%')
           AND EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = c.relname
                  AND column_name = 'user_id')
           AND EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = c.relname
                  AND column_name = 'company_name')
    LOOP
        EXECUTE format('GRANT SELECT ON %I TO tally_chat_ro', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tally_tenant_read ON %I', t);
        EXECUTE format($p$
            CREATE POLICY tally_tenant_read ON %I
                FOR SELECT TO tally_chat_ro
                USING (
                    user_id = current_setting('app.user_id', true)
                    AND company_name = current_setting('app.company_name', true)
                )
        $p$, t);
        RAISE NOTICE 'RLS tenant policy applied to %', t;
    END LOOP;
END $$;
