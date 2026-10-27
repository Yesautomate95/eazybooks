/**
 * BulkEditToolbar Component
 * Toolbar for bulk editing operations on selected transactions.
 *
 * Props:
 * - selectedCount: number - Number of selected items
 * - availableLedgers: string[] - List of available ledgers
 * - onBulkAssignLedger: (ledger: string, type: 'debit' | 'credit') => void
 * - onSelectAll: () => void
 * - onDeselectAll: () => void
 * - onDeleteSelected: () => void (optional)
 */

import React, { useState } from 'react';
import {
    Box,
    Button,
    Select,
    MenuItem,
    FormControl,
    InputLabel,
    Chip,
    Popover,
    Typography,
    Divider,
} from '@mui/material';
import { CheckSquare, Square, Trash2, Edit3, ArrowRight, X } from 'lucide-react';

const BulkEditToolbar = ({
    selectedCount = 0,
    totalCount = 0,
    availableLedgers = [],
    onBulkAssignLedger,
    onSelectAll,
    onDeselectAll,
    onDeleteSelected,
    onSkipSelected,
    documentType = 'bank_statement',
}) => {
    const [anchorEl, setAnchorEl] = useState(null);
    const [selectedLedger, setSelectedLedger] = useState('');
    const [ledgerType, setLedgerType] = useState('credit');

    const handleOpenPopover = (event) => {
        setAnchorEl(event.currentTarget);
    };

    const handleClosePopover = () => {
        setAnchorEl(null);
        setSelectedLedger('');
    };

    const handleApplyLedger = () => {
        if (selectedLedger && onBulkAssignLedger) {
            onBulkAssignLedger(selectedLedger, ledgerType);
        }
        handleClosePopover();
    };

    const open = Boolean(anchorEl);

    if (selectedCount === 0) {
        return (
            <Box className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg border border-gray-200">
                <Typography variant="body2" className="text-gray-500">
                    Select transactions to enable bulk editing
                </Typography>
                <Button
                    size="small"
                    variant="text"
                    startIcon={<CheckSquare size={16} />}
                    onClick={onSelectAll}
                >
                    Select All ({totalCount})
                </Button>
            </Box>
        );
    }

    return (
        <Box className="flex items-center gap-3 p-3 bg-blue-50 rounded-lg border border-blue-200">
            {/* Selection info */}
            <Chip
                label={`${selectedCount} selected`}
                color="primary"
                variant="filled"
                size="small"
                onDelete={onDeselectAll}
                deleteIcon={<X size={14} />}
            />

            <Divider orientation="vertical" flexItem />

            {/* Bulk assign ledger */}
            <Button
                size="small"
                variant="contained"
                startIcon={<Edit3 size={16} />}
                onClick={handleOpenPopover}
            >
                Assign Ledger
            </Button>

            <Popover
                open={open}
                anchorEl={anchorEl}
                onClose={handleClosePopover}
                anchorOrigin={{
                    vertical: 'bottom',
                    horizontal: 'left',
                }}
            >
                <Box className="p-4 w-80">
                    <Typography variant="subtitle2" className="mb-3">
                        Assign to {selectedCount} transaction(s)
                    </Typography>

                    {documentType === 'bank_statement' && (
                        <FormControl fullWidth size="small" className="mb-3">
                            <InputLabel>Ledger Type</InputLabel>
                            <Select
                                value={ledgerType}
                                label="Ledger Type"
                                onChange={(e) => setLedgerType(e.target.value)}
                            >
                                <MenuItem value="credit">Credit Ledger (Party)</MenuItem>
                                <MenuItem value="debit">Debit Ledger (Bank)</MenuItem>
                            </Select>
                        </FormControl>
                    )}

                    <FormControl fullWidth size="small" className="mb-3">
                        <InputLabel>Select Ledger</InputLabel>
                        <Select
                            value={selectedLedger}
                            label="Select Ledger"
                            onChange={(e) => setSelectedLedger(e.target.value)}
                            MenuProps={{
                                PaperProps: {
                                    style: { maxHeight: 300 },
                                },
                            }}
                        >
                            {availableLedgers.map((ledger) => (
                                <MenuItem key={ledger} value={ledger}>
                                    {ledger}
                                </MenuItem>
                            ))}
                        </Select>
                    </FormControl>

                    <Box className="flex justify-end gap-2">
                        <Button size="small" onClick={handleClosePopover}>
                            Cancel
                        </Button>
                        <Button
                            size="small"
                            variant="contained"
                            onClick={handleApplyLedger}
                            disabled={!selectedLedger}
                            endIcon={<ArrowRight size={16} />}
                        >
                            Apply
                        </Button>
                    </Box>
                </Box>
            </Popover>

            {/* Skip selected */}
            {onSkipSelected && (
                <Button
                    size="small"
                    variant="outlined"
                    color="warning"
                    onClick={onSkipSelected}
                >
                    Skip Selected
                </Button>
            )}

            {/* Delete selected */}
            {onDeleteSelected && (
                <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    startIcon={<Trash2 size={16} />}
                    onClick={onDeleteSelected}
                >
                    Remove
                </Button>
            )}

            <Box className="flex-grow" />

            {/* Select all / Deselect all */}
            <Button
                size="small"
                variant="text"
                startIcon={<CheckSquare size={16} />}
                onClick={selectedCount === totalCount ? onDeselectAll : onSelectAll}
            >
                {selectedCount === totalCount ? 'Deselect All' : `Select All (${totalCount})`}
            </Button>
        </Box>
    );
};

export default BulkEditToolbar;
