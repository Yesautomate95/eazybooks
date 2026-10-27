import { app } from 'electron';
import * as path from 'path';
import * as fs from 'fs';

// Types for Tally data structures
export interface TallyGroup {
    guid: string;
    name: string;
    parent: string;
    is_deemed_positive: number;
}

export interface TallyLedger {
    guid: string;
    name: string;
    parent: string;
    opening_balance: number;
    is_deemed_positive: number;
}

export interface TallyVoucher {
    guid: string;
    date: string;
    voucher_number: string;
    voucher_type: string;
    is_order_voucher: number;
    is_inventory_voucher: number;
}

export interface TallyAccounting {
    guid: string;
    ledger: string;
    amount: number;
}

export interface TallyInventory {
    guid: string;
    item: string;
    quantity: number;
    tracking_number: string;
}

export interface TallyBill {
    guid: string;
    name: string;
    amount: number;
    billtype: string;
}

export class TallyDataExtractor {
    private tallyDataPath: string;

    constructor() {
        // Default Tally data path - can be configured
        this.tallyDataPath = path.join(app.getPath('userData'), 'tally_data');
        this.ensureDirectoryExists();
    }

    private ensureDirectoryExists() {
        if (!fs.existsSync(this.tallyDataPath)) {
            fs.mkdirSync(this.tallyDataPath, { recursive: true });
        }
    }

    /**
     * Extracts master data from Tally
     * @returns Promise containing extracted master data
     */
    public async extractMasterData(): Promise<{
        groups: TallyGroup[];
        ledgers: TallyLedger[];
    }> {
        // TODO: Implement actual Tally data extraction
        // This is a placeholder that will be replaced with actual implementation
        return {
            groups: [],
            ledgers: []
        };
    }

    /**
     * Extracts transaction data from Tally
     * @returns Promise containing extracted transaction data
     */
    public async extractTransactionData(): Promise<{
        vouchers: TallyVoucher[];
        accounting: TallyAccounting[];
        inventory: TallyInventory[];
        bills: TallyBill[];
    }> {
        // TODO: Implement actual Tally data extraction
        // This is a placeholder that will be replaced with actual implementation
        return {
            vouchers: [],
            accounting: [],
            inventory: [],
            bills: []
        };
    }

    /**
     * Saves extracted data to JSON files
     * @param data The data to save
     */
    public async saveData(data: any, filename: string): Promise<void> {
        const filePath = path.join(this.tallyDataPath, filename);
        await fs.promises.writeFile(filePath, JSON.stringify(data, null, 2));
    }

    /**
     * Loads data from JSON files
     * @param filename The name of the file to load
     */
    public async loadData(filename: string): Promise<any> {
        const filePath = path.join(this.tallyDataPath, filename);
        const data = await fs.promises.readFile(filePath, 'utf-8');
        return JSON.parse(data);
    }
} 