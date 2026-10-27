/**
 * DuplicateWarning Component
 * Displays a warning indicator for duplicate or potentially duplicate transactions.
 *
 * Props:
 * - isDuplicate: boolean - Is this an exact duplicate?
 * - isPotentialDuplicate: boolean - Is this a potential/fuzzy match?
 * - duplicateInfo: object - Info about the existing duplicate transaction
 * - fuzzyMatches: array - List of similar transactions
 * - onViewDuplicate: function - Callback to view the duplicate
 * - onIgnoreDuplicate: function - Callback to ignore/override duplicate warning
 */

import React, { useState } from 'react';
import {
    Box,
    Chip,
    Tooltip,
    IconButton,
    Popover,
    Typography,
    List,
    ListItem,
    ListItemText,
    Button,
    Divider,
} from '@mui/material';
import { AlertTriangle, AlertCircle, Copy, Eye, X, Check } from 'lucide-react';

const DuplicateWarning = ({
    isDuplicate = false,
    isPotentialDuplicate = false,
    duplicateInfo = null,
    fuzzyMatches = [],
    onViewDuplicate,
    onIgnoreDuplicate,
}) => {
    const [anchorEl, setAnchorEl] = useState(null);

    const handleClick = (event) => {
        event.stopPropagation();
        setAnchorEl(event.currentTarget);
    };

    const handleClose = () => {
        setAnchorEl(null);
    };

    const open = Boolean(anchorEl);

    if (!isDuplicate && !isPotentialDuplicate) {
        return null;
    }

    const formatDate = (date) => {
        if (!date) return 'N/A';
        return new Date(date).toLocaleDateString();
    };

    const formatAmount = (amount) => {
        if (amount === undefined || amount === null) return 'N/A';
        return new Intl.NumberFormat('en-IN', {
            style: 'currency',
            currency: 'INR',
        }).format(amount);
    };

    return (
        <>
            {isDuplicate ? (
                <Tooltip title="Exact duplicate found - click for details">
                    <Chip
                        icon={<Copy size={14} />}
                        label="Duplicate"
                        size="small"
                        color="error"
                        variant="outlined"
                        onClick={handleClick}
                        className="cursor-pointer"
                    />
                </Tooltip>
            ) : (
                <Tooltip title="Similar transaction found - click to review">
                    <Chip
                        icon={<AlertTriangle size={14} />}
                        label="Similar"
                        size="small"
                        color="warning"
                        variant="outlined"
                        onClick={handleClick}
                        className="cursor-pointer"
                    />
                </Tooltip>
            )}

            <Popover
                open={open}
                anchorEl={anchorEl}
                onClose={handleClose}
                anchorOrigin={{
                    vertical: 'bottom',
                    horizontal: 'left',
                }}
                onClick={(e) => e.stopPropagation()}
            >
                <Box className="p-4 w-96">
                    {/* Header */}
                    <Box className="flex items-center justify-between mb-3">
                        <Typography variant="subtitle1" className="font-medium flex items-center gap-2">
                            {isDuplicate ? (
                                <>
                                    <AlertCircle size={18} className="text-red-500" />
                                    Duplicate Transaction
                                </>
                            ) : (
                                <>
                                    <AlertTriangle size={18} className="text-yellow-500" />
                                    Similar Transactions Found
                                </>
                            )}
                        </Typography>
                        <IconButton size="small" onClick={handleClose}>
                            <X size={16} />
                        </IconButton>
                    </Box>

                    {/* Exact Duplicate Info */}
                    {isDuplicate && duplicateInfo && (
                        <Box className="bg-red-50 p-3 rounded-lg mb-3">
                            <Typography variant="caption" className="text-red-800 font-medium">
                                This transaction already exists:
                            </Typography>
                            <Box className="mt-2 text-sm">
                                <p>
                                    <strong>Date:</strong> {formatDate(duplicateInfo.transaction_date)}
                                </p>
                                <p>
                                    <strong>Amount:</strong> {formatAmount(duplicateInfo.amount)}
                                </p>
                                <p>
                                    <strong>Party:</strong> {duplicateInfo.party_name || 'N/A'}
                                </p>
                                <p>
                                    <strong>Status:</strong>{' '}
                                    <Chip
                                        size="small"
                                        label={duplicateInfo.status}
                                        color={duplicateInfo.status === 'pushed' ? 'success' : 'default'}
                                    />
                                </p>
                            </Box>
                        </Box>
                    )}

                    {/* Fuzzy Matches */}
                    {!isDuplicate && fuzzyMatches.length > 0 && (
                        <Box className="bg-yellow-50 p-3 rounded-lg mb-3">
                            <Typography variant="caption" className="text-yellow-800 font-medium">
                                {fuzzyMatches.length} similar transaction(s) found:
                            </Typography>
                            <List dense disablePadding className="mt-1">
                                {fuzzyMatches.slice(0, 3).map((match, idx) => (
                                    <ListItem key={idx} disablePadding className="py-1">
                                        <ListItemText
                                            primary={
                                                <span className="text-sm">
                                                    {formatDate(match.transaction_date)} -{' '}
                                                    {formatAmount(match.amount)}
                                                </span>
                                            }
                                            secondary={
                                                <span className="text-xs text-gray-600">
                                                    {match.party_name || match.description || 'No description'}
                                                </span>
                                            }
                                        />
                                    </ListItem>
                                ))}
                                {fuzzyMatches.length > 3 && (
                                    <Typography variant="caption" className="text-gray-500">
                                        +{fuzzyMatches.length - 3} more...
                                    </Typography>
                                )}
                            </List>
                        </Box>
                    )}

                    <Divider className="my-3" />

                    {/* Actions */}
                    <Box className="flex gap-2 justify-end">
                        {onViewDuplicate && (duplicateInfo || fuzzyMatches.length > 0) && (
                            <Button
                                size="small"
                                variant="outlined"
                                startIcon={<Eye size={16} />}
                                onClick={() => {
                                    onViewDuplicate(duplicateInfo || fuzzyMatches[0]);
                                    handleClose();
                                }}
                            >
                                View Original
                            </Button>
                        )}
                        {onIgnoreDuplicate && (
                            <Button
                                size="small"
                                variant="contained"
                                color={isDuplicate ? 'error' : 'warning'}
                                startIcon={<Check size={16} />}
                                onClick={() => {
                                    onIgnoreDuplicate();
                                    handleClose();
                                }}
                            >
                                {isDuplicate ? 'Import Anyway' : 'Proceed'}
                            </Button>
                        )}
                    </Box>
                </Box>
            </Popover>
        </>
    );
};

export default DuplicateWarning;
