/**
 * MappingRulesPanel Component
 * Side-panel / modal for managing ledger mapping rules.
 *
 * Props:
 * - company: string - Current company name
 * - documentType: 'bank_statement' | 'purchase_invoice' | 'sales_invoice'
 * - availableLedgers: string[] - Union of all selectable ledgers. Used as a
 *   fallback target-ledger source when slot-specific lists aren't passed.
 * - partyLedgers: string[] (optional) - Ledgers eligible for the party
 *   (vendor / customer) slot. When provided, picking "Party" in the rule
 *   editor filters the target-ledger dropdown to this list.
 * - expenseLedgers: string[] (optional) - Ledgers eligible for the
 *   expense / revenue slot. Filters the dropdown when slot=expense.
 * - onRulesChange: function - Callback when rules are modified
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
    Box,
    Button,
    TextField,
    Select,
    MenuItem,
    FormControl,
    InputLabel,
    IconButton,
    Chip,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    List,
    ListItem,
    ListItemText,
    ListItemSecondaryAction,
    Switch,
    Collapse,
    Typography,
    Divider,
    Alert,
    CircularProgress,
    Tabs,
    Tab,
    Tooltip,
} from '@mui/material';
import {
    Plus,
    Pencil,
    Trash2,
    ChevronDown,
    ChevronUp,
    Lightbulb,
    X,
    Eye,
    GitBranch,
    Layers,
} from 'lucide-react';
import { tallyApi } from '../../../../services/api';

// ── Composite rule vocabulary ──────────────────────────────────────────────
// Mirrors the backend's MappingService.COMPOSITE_FIELDS / COMPOSITE_OPERATORS
// vocabulary so we never construct a rule the evaluator would reject.
const FIELD_OPTIONS = [
    { value: 'vendor_name', label: 'Vendor name', ops: ['contains', 'iequals', 'equals', 'starts_with', 'ends_with', 'regex', 'in'] },
    { value: 'vendor_gstin', label: 'Vendor GSTIN', ops: ['equals', 'starts_with', 'in'] },
    { value: 'gstin_prefix', label: 'GSTIN state prefix', ops: ['equals', 'in'] },
    { value: 'amount', label: 'Amount', ops: ['gt', 'gte', 'lt', 'lte', 'between'] },
    { value: 'item_description', label: 'Item description', ops: ['contains', 'iequals', 'regex'] },
    { value: 'hsn_code', label: 'HSN / SAC code', ops: ['equals', 'in'] },
    { value: 'voucher_type', label: 'Voucher type', ops: ['equals', 'in'] },
    { value: 'narration', label: 'Narration', ops: ['contains', 'regex'] },
    { value: 'description', label: 'Description', ops: ['contains', 'regex'] },
    { value: 'any_text', label: 'Any text field', ops: ['contains', 'regex'] },
];

const OP_LABELS = {
    equals: 'equals',
    iequals: 'equals (any case)',
    contains: 'contains',
    starts_with: 'starts with',
    ends_with: 'ends with',
    regex: 'matches regex',
    in: 'is one of',
    between: 'is between',
    gt: '>',
    gte: '≥',
    lt: '<',
    lte: '≤',
};

const NUMERIC_OPS = new Set(['gt', 'gte', 'lt', 'lte', 'between']);

const emptyClause = () => ({ field: 'vendor_name', operator: 'contains', value: '' });
const emptyGroup = (op = 'AND') => ({ op, clauses: [emptyClause()] });

const SUGGESTION_GROUPS = [
    { key: 'correction_pattern', label: 'From your corrections', icon: GitBranch },
    { key: 'vendor_to_ledger', label: 'Frequent vendor patterns', icon: Layers },
    { key: 'gstin_prefix', label: 'GSTIN / state patterns', icon: Layers },
    { key: 'amount_band_voucher', label: 'Amount × voucher patterns', icon: Layers },
];

// Slot labels used by both the editor (radio group) and the list (badge).
// 'either' is the legacy NULL-slot value — kept so users with pre-migration
// rules still see them displayed correctly.
const SLOT_META = {
    party:   { label: 'Party',   chip: 'bg-blue-50 text-blue-700 border-blue-200' },
    expense: { label: 'Expense', chip: 'bg-violet-50 text-violet-700 border-violet-200' },
    either:  { label: 'Either',  chip: 'bg-gray-100 text-gray-700 border-gray-200' },
};

const MappingRulesPanel = ({
    company,
    documentType,
    availableLedgers = [],
    partyLedgers,
    expenseLedgers,
    onRulesChange,
}) => {
    const [rules, setRules] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [expanded, setExpanded] = useState(true);
    const [editingRule, setEditingRule] = useState(null);
    const [showCreateDialog, setShowCreateDialog] = useState(false);
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [suggestions, setSuggestions] = useState([]);
    const [statistics, setStatistics] = useState(null);

    // Fetch rules on mount
    const fetchRules = useCallback(async () => {
        if (!company) return;

        setLoading(true);
        setError(null);

        try {
            const response = await tallyApi.getMappingRules(company, documentType);
            setRules(response.rules || []);

            const stats = await tallyApi.getRuleStatistics(company, documentType);
            setStatistics(stats);
        } catch (err) {
            console.error('Error fetching mapping rules:', err);
            setError('Failed to load mapping rules');
        } finally {
            setLoading(false);
        }
    }, [company, documentType]);

    useEffect(() => {
        fetchRules();
    }, [fetchRules]);

    // Fetch suggestions
    const fetchSuggestions = async () => {
        try {
            const response = await tallyApi.getRuleSuggestions(company, documentType);
            setSuggestions(response.suggestions || []);
            setShowSuggestions(true);
        } catch (err) {
            console.error('Error fetching suggestions:', err);
        }
    };

    // Toggle rule active state
    const handleToggleActive = async (ruleId, currentState) => {
        try {
            await tallyApi.updateMappingRule(ruleId, { is_active: !currentState });
            fetchRules();
            onRulesChange?.();
        } catch (err) {
            console.error('Error toggling rule:', err);
        }
    };

    // Delete rule
    const handleDeleteRule = async (ruleId) => {
        if (!window.confirm('Are you sure you want to delete this rule?')) return;

        try {
            await tallyApi.deleteMappingRule(ruleId);
            fetchRules();
            onRulesChange?.();
        } catch (err) {
            console.error('Error deleting rule:', err);
        }
    };

    // Create one suggestion as a rule. Accepts either the legacy
    // single-keyword shape (suggestion.keywords / .rule_type='keyword') or
    // the new mined shape (suggestion.rule_proposal with conditions for
    // composite rules).
    const handleCreateFromSuggestion = async (suggestion) => {
        try {
            const proposal = suggestion.rule_proposal || suggestion;
            // Best-effort slot inference for mined suggestions: if the
            // proposed target ledger is in the party universe, label as
            // party; if it's in the expense universe, expense; else leave
            // null (legacy "either").
            let slot = proposal.ledger_slot || null;
            if (!slot && proposal.target_ledger) {
                if (partyLedgers?.includes(proposal.target_ledger)) slot = 'party';
                else if (expenseLedgers?.includes(proposal.target_ledger)) slot = 'expense';
            }
            await tallyApi.createMappingRule(company, {
                rule_name: proposal.rule_name,
                rule_type: proposal.rule_type,
                document_type: proposal.document_type || documentType,
                target_ledger: proposal.target_ledger,
                keywords: proposal.keywords,
                vendor_name: proposal.vendor_name,
                conditions: proposal.conditions,
                ledger_slot: slot,
                priority: proposal.priority || proposal.suggested_priority || 5,
            });
            fetchRules();
            setSuggestions((prev) => prev.filter((s) => (s.rule_proposal?.rule_name || s.rule_name) !== proposal.rule_name));
            onRulesChange?.();
        } catch (err) {
            console.error('Error creating rule from suggestion:', err);
        }
    };

    // Bulk-accept every suggestion whose evidence.consistency is at the top
    // tier — covers the "this vendor always maps here" obvious wins.
    const handleAcceptAllStrong = async () => {
        const strong = suggestions.filter((s) => (s.evidence?.consistency || 0) >= 0.95);
        for (const s of strong) {
            // eslint-disable-next-line no-await-in-loop — accept sequentially to keep DB happy
            await handleCreateFromSuggestion(s);
        }
    };

    return (
        <Box className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
            {/* Header */}
            <Box
                className="px-4 py-3 bg-gray-50 border-b border-gray-200 flex items-center justify-between cursor-pointer"
                onClick={() => setExpanded(!expanded)}
            >
                <div className="flex items-center gap-2">
                    <Typography variant="subtitle1" className="font-medium">
                        Mapping Rules
                    </Typography>
                    {statistics && (
                        <Chip
                            size="small"
                            label={`${statistics.active_rules} active`}
                            className="bg-blue-100 text-blue-800"
                        />
                    )}
                </div>
                <IconButton size="small">
                    {expanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
                </IconButton>
            </Box>

            <Collapse in={expanded}>
                <Box className="p-4">
                    {/* Actions */}
                    <Box className="flex gap-2 mb-4">
                        <Button
                            variant="contained"
                            size="small"
                            startIcon={<Plus size={16} />}
                            onClick={() => setShowCreateDialog(true)}
                        >
                            Add Rule
                        </Button>
                        <Button
                            variant="outlined"
                            size="small"
                            startIcon={<Lightbulb size={16} />}
                            onClick={fetchSuggestions}
                        >
                            Suggestions
                        </Button>
                    </Box>

                    {/* Error */}
                    {error && (
                        <Alert severity="error" className="mb-4">
                            {error}
                        </Alert>
                    )}

                    {/* Loading */}
                    {loading && (
                        <Box className="flex justify-center py-4">
                            <CircularProgress size={24} />
                        </Box>
                    )}

                    {/* Rules List */}
                    {!loading && rules.length === 0 && (
                        <Typography variant="body2" className="text-gray-500 text-center py-4">
                            No mapping rules yet. Create one to auto-suggest ledgers.
                        </Typography>
                    )}

                    {!loading && rules.length > 0 && (
                        <List dense disablePadding>
                            {rules.map((rule) => {
                                const slotKey = rule.ledger_slot || 'either';
                                const slotMeta = SLOT_META[slotKey] || SLOT_META.either;
                                return (
                                <ListItem
                                    key={rule.rule_id}
                                    className={`rounded mb-1 ${
                                        rule.is_active ? 'bg-gray-50' : 'bg-gray-100 opacity-60'
                                    }`}
                                >
                                    <ListItemText
                                        primary={
                                            <span className="flex items-center gap-2">
                                                <span
                                                    className={`inline-flex items-center text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${slotMeta.chip}`}
                                                    title={`Applies to ${slotMeta.label} slot`}
                                                >
                                                    {slotMeta.label}
                                                </span>
                                                <span className="text-sm font-medium">
                                                    {rule.rule_name}
                                                </span>
                                            </span>
                                        }
                                        secondary={
                                            <span className="text-xs text-gray-500">
                                                {rule.rule_type} → {rule.target_ledger}
                                                {rule.match_count > 0 && (
                                                    <span className="ml-2 text-blue-600">
                                                        ({rule.match_count} matches)
                                                    </span>
                                                )}
                                            </span>
                                        }
                                    />
                                    <ListItemSecondaryAction>
                                        <Switch
                                            size="small"
                                            checked={rule.is_active}
                                            onChange={() =>
                                                handleToggleActive(rule.rule_id, rule.is_active)
                                            }
                                        />
                                        <IconButton
                                            size="small"
                                            onClick={() => setEditingRule(rule)}
                                        >
                                            <Pencil size={14} />
                                        </IconButton>
                                        <IconButton
                                            size="small"
                                            onClick={() => handleDeleteRule(rule.rule_id)}
                                        >
                                            <Trash2 size={14} />
                                        </IconButton>
                                    </ListItemSecondaryAction>
                                </ListItem>
                                );
                            })}
                        </List>
                    )}

                    {/* Statistics */}
                    {statistics && statistics.total_matches > 0 && (
                        <Box className="mt-4 p-3 bg-blue-50 rounded-lg">
                            <Typography variant="caption" className="text-blue-800">
                                Your rules have auto-matched {statistics.total_matches} transactions
                            </Typography>
                        </Box>
                    )}
                </Box>
            </Collapse>

            {/* Create/Edit Dialog */}
            <RuleDialog
                open={showCreateDialog || editingRule !== null}
                rule={editingRule}
                company={company}
                documentType={documentType}
                availableLedgers={availableLedgers}
                partyLedgers={partyLedgers}
                expenseLedgers={expenseLedgers}
                onClose={() => {
                    setShowCreateDialog(false);
                    setEditingRule(null);
                }}
                onSave={() => {
                    fetchRules();
                    setShowCreateDialog(false);
                    setEditingRule(null);
                    onRulesChange?.();
                }}
            />

            {/* Suggestions Dialog — grouped by mined pattern type, each card
                shows the evidence so the user can judge whether to accept. */}
            <SuggestionsDialog
                open={showSuggestions}
                onClose={() => setShowSuggestions(false)}
                suggestions={suggestions}
                onAccept={handleCreateFromSuggestion}
                onAcceptAllStrong={handleAcceptAllStrong}
                company={company}
                documentType={documentType}
            />
        </Box>
    );
};

// ── Suggestions Dialog ────────────────────────────────────────────────────
// Groups mined suggestions by kind (correction, vendor, gstin, composite),
// shows evidence stats, and exposes a one-click Preview action that hits the
// backend's /mapping-rules/preview endpoint to count matches against recent
// drafts.
const SuggestionsDialog = ({
    open, onClose, suggestions, onAccept, onAcceptAllStrong, company, documentType,
}) => {
    const [previewMap, setPreviewMap] = useState({});

    const runPreview = async (suggestion) => {
        const proposal = suggestion.rule_proposal || suggestion;
        const key = proposal.rule_name;
        setPreviewMap((p) => ({ ...p, [key]: { loading: true } }));
        try {
            const result = await tallyApi.previewMappingRule({
                company,
                ruleType: proposal.rule_type,
                documentType: proposal.document_type || documentType,
                conditions: proposal.conditions,
                keywords: proposal.keywords,
                pattern: proposal.pattern,
                vendorName: proposal.vendor_name,
                amountMin: proposal.amount_min,
                amountMax: proposal.amount_max,
            });
            setPreviewMap((p) => ({ ...p, [key]: { loading: false, result } }));
        } catch (e) {
            setPreviewMap((p) => ({ ...p, [key]: { loading: false, error: 'Preview failed' } }));
        }
    };

    const grouped = SUGGESTION_GROUPS.map((g) => ({
        ...g,
        items: suggestions.filter((s) => (s.kind || 'vendor_to_ledger') === g.key),
    })).filter((g) => g.items.length > 0);

    const strongCount = suggestions.filter((s) => (s.evidence?.consistency || 0) >= 0.95).length;

    return (
        <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
            <DialogTitle className="flex items-center justify-between">
                <span>Rule suggestions from your past invoices</span>
                <IconButton onClick={onClose}>
                    <X size={20} />
                </IconButton>
            </DialogTitle>
            <DialogContent>
                {suggestions.length === 0 ? (
                    <Typography className="text-gray-500 text-center py-6">
                        No suggestions yet. Push a few invoices or correct some AI picks —
                        we'll learn the patterns and propose rules here.
                    </Typography>
                ) : (
                    <>
                        {strongCount > 0 && (
                            <Box className="mb-3 p-2 bg-emerald-50 border border-emerald-100 rounded flex items-center justify-between">
                                <span className="text-sm text-emerald-900">
                                    {strongCount} high-confidence pattern{strongCount > 1 ? 's' : ''} ready to lock in.
                                </span>
                                <Button size="small" variant="contained" color="success" onClick={onAcceptAllStrong}>
                                    Accept all strong
                                </Button>
                            </Box>
                        )}
                        {grouped.map((group) => {
                            const Icon = group.icon;
                            return (
                                <Box key={group.key} className="mb-4">
                                    <div className="flex items-center gap-2 mb-1 text-xs uppercase tracking-wide text-gray-500">
                                        <Icon size={14} />
                                        <span>{group.label}</span>
                                        <span className="ml-auto">{group.items.length}</span>
                                    </div>
                                    <div className="space-y-2">
                                        {group.items.map((s, idx) => {
                                            const proposal = s.rule_proposal || s;
                                            const evidence = s.evidence || {};
                                            const preview = previewMap[proposal.rule_name];
                                            return (
                                                <div key={idx} className="p-3 border border-gray-200 rounded-md hover:border-indigo-300">
                                                    <div className="flex items-start justify-between gap-3">
                                                        <div className="min-w-0 flex-1">
                                                            <div className="text-sm font-medium text-gray-900 truncate">
                                                                {proposal.rule_name}
                                                            </div>
                                                            <div className="text-xs text-gray-600 mt-0.5">
                                                                {s.explanation}
                                                            </div>
                                                            <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-gray-500">
                                                                {evidence.occurrences != null && (
                                                                    <span>n = {evidence.occurrences}</span>
                                                                )}
                                                                {evidence.consistency != null && (
                                                                    <span>consistency {Math.round((evidence.consistency || 0) * 100)}%</span>
                                                                )}
                                                                {evidence.distinct_vendors != null && (
                                                                    <span>{evidence.distinct_vendors} vendors</span>
                                                                )}
                                                                {evidence.last_used_at && (
                                                                    <span>last seen {evidence.last_used_at.slice(0, 10)}</span>
                                                                )}
                                                            </div>
                                                            {preview && (
                                                                <div className="mt-2 text-[11px] text-indigo-700">
                                                                    {preview.loading && 'Checking past invoices…'}
                                                                    {preview.error && <span className="text-red-600">{preview.error}</span>}
                                                                    {preview.result && (
                                                                        <span>
                                                                            Matches {preview.result.match_count} of {preview.result.scanned_count} recent invoices.
                                                                        </span>
                                                                    )}
                                                                </div>
                                                            )}
                                                        </div>
                                                        <div className="flex items-center gap-1 flex-shrink-0">
                                                            <Tooltip title="Preview matches">
                                                                <IconButton size="small" onClick={() => runPreview(s)}>
                                                                    <Eye size={14} />
                                                                </IconButton>
                                                            </Tooltip>
                                                            <Button size="small" variant="outlined" onClick={() => onAccept(s)}>
                                                                Accept
                                                            </Button>
                                                        </div>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </Box>
                            );
                        })}
                    </>
                )}
            </DialogContent>
        </Dialog>
    );
};

// ── RuleDialog ────────────────────────────────────────────────────────────
// Two-tab editor:
//   Simple    — the legacy single-condition forms (keyword/pattern/vendor/amount)
//   Advanced  — composite AND/OR tree builder for arbitrary multi-condition rules
//
// A live preview at the bottom hits POST /ca/mapping-rules/preview so the user
// can see exactly how many past invoices the draft rule would match before
// saving it.
const initialFormState = {
    rule_name: '',
    rule_type: 'keyword',
    keywords: [],
    pattern: '',
    vendor_name: '',
    amount_min: '',
    amount_max: '',
    target_ledger: '',
    target_voucher_type: '',
    // Default new rules to 'party' since the most common first-rule pattern
    // is "vendor X always maps to party ledger Y". Users can switch to
    // 'expense' to make a rule that fills the expense/revenue slot instead.
    ledger_slot: 'party',
    priority: 5,
    conditions: null,
};

const RuleDialog = ({
    open, rule, company, documentType,
    availableLedgers, partyLedgers, expenseLedgers,
    onClose, onSave,
}) => {
    const [tab, setTab] = useState(0);
    const [formData, setFormData] = useState(initialFormState);
    const [keywordInput, setKeywordInput] = useState('');
    const [saving, setSaving] = useState(false);
    const [previewState, setPreviewState] = useState(null);
    const [previewLoading, setPreviewLoading] = useState(false);

    useEffect(() => {
        if (rule) {
            const isComposite = rule.rule_type === 'composite';
            setFormData({
                rule_name: rule.rule_name || '',
                rule_type: rule.rule_type || 'keyword',
                keywords: rule.keywords || [],
                pattern: rule.pattern || '',
                vendor_name: rule.vendor_name || '',
                amount_min: rule.amount_min ?? '',
                amount_max: rule.amount_max ?? '',
                target_ledger: rule.target_ledger || '',
                target_voucher_type: rule.target_voucher_type || '',
                // Legacy rules (created before slot column existed) come
                // back with null. Surface them as 'either' in the editor
                // so the user can choose to migrate or leave them.
                ledger_slot: rule.ledger_slot || 'either',
                priority: rule.priority || 5,
                conditions: rule.conditions || (isComposite ? emptyGroup('AND') : null),
            });
            setTab(isComposite ? 1 : 0);
        } else {
            setFormData(initialFormState);
            setTab(0);
        }
        setPreviewState(null);
    }, [rule, open]);

    const switchTab = (_, idx) => {
        setTab(idx);
        setPreviewState(null);
        if (idx === 1 && (!formData.conditions || formData.rule_type !== 'composite')) {
            // Entering Advanced — seed an empty group so the editor has something to show.
            setFormData((f) => ({
                ...f,
                rule_type: 'composite',
                conditions: f.conditions || emptyGroup('AND'),
            }));
        } else if (idx === 0 && formData.rule_type === 'composite') {
            // Leaving Advanced — revert to a benign legacy type so the Simple tab makes sense.
            setFormData((f) => ({ ...f, rule_type: 'keyword' }));
        }
    };

    const handleAddKeyword = () => {
        if (keywordInput.trim() && !formData.keywords.includes(keywordInput.trim())) {
            setFormData({ ...formData, keywords: [...formData.keywords, keywordInput.trim()] });
            setKeywordInput('');
        }
    };
    const handleRemoveKeyword = (keyword) => {
        setFormData({ ...formData, keywords: formData.keywords.filter((k) => k !== keyword) });
    };

    const buildPayload = () => {
        const data = {
            rule_name: formData.rule_name,
            rule_type: formData.rule_type,
            target_ledger: formData.target_ledger,
            // 'either' is the editor-only label for NULL — send through as
            // null so the backend stores it as a slot-agnostic rule (legacy
            // behavior). 'party' / 'expense' are stored verbatim.
            ledger_slot: formData.ledger_slot === 'either' ? null : formData.ledger_slot,
            priority: parseInt(formData.priority) || 0,
        };
        if (formData.rule_type === 'composite') {
            data.conditions = formData.conditions;
        } else {
            if (formData.keywords?.length) data.keywords = formData.keywords;
            if (formData.pattern?.trim()) data.pattern = formData.pattern.trim();
            if (formData.vendor_name?.trim()) data.vendor_name = formData.vendor_name.trim();
            if (formData.amount_min !== '' && formData.amount_min !== null) data.amount_min = parseFloat(formData.amount_min);
            if (formData.amount_max !== '' && formData.amount_max !== null) data.amount_max = parseFloat(formData.amount_max);
            if (formData.target_voucher_type?.trim()) data.target_voucher_type = formData.target_voucher_type.trim();
        }
        return data;
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const payload = buildPayload();
            if (rule) {
                await tallyApi.updateMappingRule(rule.rule_id, payload);
            } else {
                await tallyApi.createMappingRule(company, { ...payload, document_type: documentType });
            }
            onSave();
        } catch (err) {
            console.error('Error saving rule:', err);
        } finally {
            setSaving(false);
        }
    };

    const handlePreview = async () => {
        setPreviewLoading(true);
        setPreviewState(null);
        try {
            const payload = buildPayload();
            const result = await tallyApi.previewMappingRule({
                company,
                ruleType: payload.rule_type,
                documentType,
                conditions: payload.conditions,
                keywords: payload.keywords,
                pattern: payload.pattern,
                vendorName: payload.vendor_name,
                amountMin: payload.amount_min,
                amountMax: payload.amount_max,
            });
            setPreviewState(result);
        } catch (err) {
            setPreviewState({ error: err?.response?.data?.detail || 'Preview failed' });
        } finally {
            setPreviewLoading(false);
        }
    };

    return (
        <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
            <DialogTitle>{rule ? 'Edit rule' : 'Create mapping rule'}</DialogTitle>
            <DialogContent>
                <Box className="pt-2">
                    <Tabs value={tab} onChange={switchTab} className="mb-2">
                        <Tab label="Simple" />
                        <Tab label="Advanced (composite)" />
                    </Tabs>

                    <Box className="space-y-3 pt-1">
                        <TextField
                            fullWidth
                            size="small"
                            label="Rule name"
                            value={formData.rule_name}
                            onChange={(e) => setFormData({ ...formData, rule_name: e.target.value })}
                        />

                        {tab === 0 && (
                            <SimpleRuleFields
                                formData={formData}
                                setFormData={setFormData}
                                keywordInput={keywordInput}
                                setKeywordInput={setKeywordInput}
                                onAddKeyword={handleAddKeyword}
                                onRemoveKeyword={handleRemoveKeyword}
                            />
                        )}

                        {tab === 1 && (
                            <AdvancedRuleFields
                                conditions={formData.conditions || emptyGroup('AND')}
                                onChange={(next) => setFormData((f) => ({ ...f, conditions: next, rule_type: 'composite' }))}
                            />
                        )}

                        <Divider />

                        {/* Slot selector — controls which suggestion slot this
                            rule fills. Party = vendor / customer ledger.
                            Expense = expense / revenue ledger. Either =
                            legacy, fires for whichever universe contains the
                            target ledger (matches pre-migration behavior). */}
                        <FormControl fullWidth size="small">
                            <InputLabel>Applies to slot</InputLabel>
                            <Select
                                value={formData.ledger_slot}
                                label="Applies to slot"
                                onChange={(e) => setFormData({
                                    ...formData,
                                    ledger_slot: e.target.value,
                                    // Reset target if switching slots — the
                                    // previously-picked ledger likely isn't
                                    // valid in the new slot's universe.
                                    target_ledger: '',
                                })}
                            >
                                <MenuItem value="party">Party (Vendor / Customer)</MenuItem>
                                <MenuItem value="expense">Expense / Revenue</MenuItem>
                                <MenuItem value="either">Either (legacy)</MenuItem>
                            </Select>
                        </FormControl>

                        <FormControl fullWidth size="small">
                            <InputLabel>Target ledger</InputLabel>
                            <Select
                                value={formData.target_ledger}
                                label="Target ledger"
                                onChange={(e) => setFormData({ ...formData, target_ledger: e.target.value })}
                            >
                                {/* Filter the dropdown by the chosen slot when
                                    slot-specific lists were passed in. Falls
                                    back to the union (availableLedgers) when
                                    the host view didn't separate them — e.g.,
                                    BankEntriesView still passes a flat list. */}
                                {(() => {
                                    let opts = availableLedgers;
                                    if (formData.ledger_slot === 'party' && partyLedgers) {
                                        opts = partyLedgers;
                                    } else if (formData.ledger_slot === 'expense' && expenseLedgers) {
                                        opts = expenseLedgers;
                                    }
                                    return (opts || []).map((ledger) => (
                                        <MenuItem key={ledger} value={ledger}>{ledger}</MenuItem>
                                    ));
                                })()}
                            </Select>
                        </FormControl>

                        <TextField
                            fullWidth
                            size="small"
                            label="Priority (1-10)"
                            type="number"
                            value={formData.priority}
                            onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) || 5 })}
                            inputProps={{ min: 1, max: 10 }}
                            helperText="Higher priority rules are evaluated first."
                        />

                        <Box className="mt-2 p-3 bg-gray-50 border border-gray-200 rounded">
                            <div className="flex items-center justify-between mb-1">
                                <Typography variant="caption" className="text-gray-600 font-medium">
                                    Preview against your recent invoices
                                </Typography>
                                <Button
                                    size="small"
                                    variant="text"
                                    startIcon={<Eye size={14} />}
                                    onClick={handlePreview}
                                    disabled={previewLoading}
                                >
                                    {previewLoading ? 'Checking…' : 'Run preview'}
                                </Button>
                            </div>
                            {previewState?.error && (
                                <Typography variant="caption" className="text-red-700">{previewState.error}</Typography>
                            )}
                            {previewState && !previewState.error && (
                                <>
                                    <Typography variant="caption" className="text-gray-700">
                                        Matches <strong>{previewState.match_count}</strong> of {previewState.scanned_count} recent transactions.
                                    </Typography>
                                    {previewState.matches?.length > 0 && (
                                        <ul className="mt-1 text-[11px] text-gray-600 list-disc list-inside max-h-32 overflow-y-auto">
                                            {previewState.matches.slice(0, 5).map((m, i) => (
                                                <li key={i} className="truncate">
                                                    {m.vendor_name || m.draft_title || 'Untitled'}
                                                    {m.amount != null && ` · ₹${m.amount}`}
                                                    {m.voucher_type && ` · ${m.voucher_type}`}
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                </>
                            )}
                        </Box>
                    </Box>
                </Box>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>Cancel</Button>
                <Button
                    variant="contained"
                    onClick={handleSave}
                    disabled={saving || !formData.rule_name || !formData.target_ledger}
                >
                    {saving ? <CircularProgress size={20} /> : rule ? 'Save' : 'Create'}
                </Button>
            </DialogActions>
        </Dialog>
    );
};

// ── Simple (legacy) rule fields ───────────────────────────────────────────
const SimpleRuleFields = ({ formData, setFormData, keywordInput, setKeywordInput, onAddKeyword, onRemoveKeyword }) => (
    <>
        <FormControl fullWidth size="small">
            <InputLabel>Rule type</InputLabel>
            <Select
                value={formData.rule_type === 'composite' ? 'keyword' : formData.rule_type}
                label="Rule type"
                onChange={(e) => setFormData({ ...formData, rule_type: e.target.value })}
            >
                <MenuItem value="keyword">Keyword match</MenuItem>
                <MenuItem value="pattern">Pattern (regex)</MenuItem>
                <MenuItem value="vendor">Vendor match</MenuItem>
                <MenuItem value="amount_range">Amount range</MenuItem>
            </Select>
        </FormControl>

        {formData.rule_type === 'keyword' && (
            <Box>
                <Box className="flex gap-2 mb-2">
                    <TextField
                        fullWidth
                        size="small"
                        label="Add keyword"
                        value={keywordInput}
                        onChange={(e) => setKeywordInput(e.target.value)}
                        onKeyPress={(e) => e.key === 'Enter' && onAddKeyword()}
                    />
                    <Button variant="outlined" onClick={onAddKeyword}>Add</Button>
                </Box>
                <Box className="flex flex-wrap gap-1">
                    {formData.keywords.map((kw) => (
                        <Chip key={kw} label={kw} onDelete={() => onRemoveKeyword(kw)} size="small" />
                    ))}
                </Box>
            </Box>
        )}

        {formData.rule_type === 'pattern' && (
            <TextField
                fullWidth
                size="small"
                label="Regex pattern"
                value={formData.pattern}
                onChange={(e) => setFormData({ ...formData, pattern: e.target.value })}
                helperText="e.g. NEFT-.*-John"
            />
        )}

        {formData.rule_type === 'vendor' && (
            <TextField
                fullWidth
                size="small"
                label="Vendor name"
                value={formData.vendor_name}
                onChange={(e) => setFormData({ ...formData, vendor_name: e.target.value })}
            />
        )}

        {formData.rule_type === 'amount_range' && (
            <Box className="flex gap-2">
                <TextField fullWidth size="small" label="Min amount" type="number"
                    value={formData.amount_min}
                    onChange={(e) => setFormData({ ...formData, amount_min: e.target.value })} />
                <TextField fullWidth size="small" label="Max amount" type="number"
                    value={formData.amount_max}
                    onChange={(e) => setFormData({ ...formData, amount_max: e.target.value })} />
            </Box>
        )}
    </>
);

// ── Advanced (composite) rule fields ──────────────────────────────────────
// Renders a recursive condition tree. Each ConditionGroup contains a list of
// children that are themselves either ConditionGroups or ConditionClauses.
const AdvancedRuleFields = ({ conditions, onChange }) => {
    return (
        <Box>
            <Typography variant="caption" className="text-gray-600 block mb-1">
                Combine multiple conditions with AND/OR. Click "+ group" to nest sub-groups
                (e.g. <code>A AND (B OR C)</code>).
            </Typography>
            <ConditionGroup
                node={conditions}
                onChange={onChange}
                onRemove={null}
                depth={0}
            />
        </Box>
    );
};

const ConditionGroup = ({ node, onChange, onRemove, depth }) => {
    const op = node.op || 'AND';
    const clauses = node.clauses || [];

    const update = (next) => onChange(next);
    const setOp = (newOp) => update({ ...node, op: newOp });
    const setChild = (idx, child) => update({ ...node, clauses: clauses.map((c, i) => (i === idx ? child : c)) });
    const addClause = () => update({ ...node, clauses: [...clauses, emptyClause()] });
    const addGroup = () => update({ ...node, clauses: [...clauses, emptyGroup('OR')] });
    const removeChild = (idx) => update({ ...node, clauses: clauses.filter((_, i) => i !== idx) });

    return (
        <Box className={`border-l-4 pl-3 py-2 ${op === 'AND' ? 'border-indigo-300' : 'border-amber-300'} bg-gray-50 rounded-r mb-2`}>
            <Box className="flex items-center gap-2 mb-2">
                <FormControl size="small" sx={{ minWidth: 80 }}>
                    <Select value={op} onChange={(e) => setOp(e.target.value)}>
                        <MenuItem value="AND">AND</MenuItem>
                        <MenuItem value="OR">OR</MenuItem>
                    </Select>
                </FormControl>
                <Typography variant="caption" className="text-gray-500">
                    {op === 'AND' ? 'all of the following must match' : 'any of the following must match'}
                </Typography>
                {onRemove && (
                    <IconButton size="small" onClick={onRemove} className="ml-auto">
                        <Trash2 size={14} />
                    </IconButton>
                )}
            </Box>

            {clauses.length === 0 && (
                <Typography variant="caption" className="text-gray-400 block ml-1 mb-1">
                    Empty group — add at least one clause.
                </Typography>
            )}

            {clauses.map((child, idx) => {
                const isGroup = child && typeof child === 'object' && 'op' in child;
                if (isGroup) {
                    return (
                        <ConditionGroup
                            key={idx}
                            node={child}
                            onChange={(next) => setChild(idx, next)}
                            onRemove={() => removeChild(idx)}
                            depth={depth + 1}
                        />
                    );
                }
                return (
                    <ConditionClause
                        key={idx}
                        clause={child}
                        onChange={(next) => setChild(idx, next)}
                        onRemove={() => removeChild(idx)}
                    />
                );
            })}

            <Box className="flex gap-1 mt-1">
                <Button size="small" startIcon={<Plus size={14} />} onClick={addClause}>
                    clause
                </Button>
                {depth < 2 && (
                    <Button size="small" startIcon={<Plus size={14} />} onClick={addGroup}>
                        group
                    </Button>
                )}
            </Box>
        </Box>
    );
};

const ConditionClause = ({ clause, onChange, onRemove }) => {
    const fieldMeta = FIELD_OPTIONS.find((f) => f.value === clause.field) || FIELD_OPTIONS[0];
    const allowedOps = fieldMeta.ops || [];
    const op = allowedOps.includes(clause.operator) ? clause.operator : allowedOps[0];

    const onFieldChange = (e) => {
        const nextField = e.target.value;
        const nextMeta = FIELD_OPTIONS.find((f) => f.value === nextField);
        onChange({
            field: nextField,
            operator: nextMeta?.ops?.[0] || 'equals',
            value: '',
        });
    };
    const onOpChange = (e) => {
        const nextOp = e.target.value;
        // For 'between' we store [lo, hi]; for 'in' we store a CSV string and let the backend split.
        let nextValue = clause.value;
        if (nextOp === 'between' && !Array.isArray(nextValue)) nextValue = ['', ''];
        if (nextOp !== 'between' && Array.isArray(nextValue)) nextValue = '';
        onChange({ ...clause, operator: nextOp, value: nextValue });
    };

    const renderValueInput = () => {
        if (op === 'between') {
            const [lo, hi] = Array.isArray(clause.value) ? clause.value : ['', ''];
            return (
                <Box className="flex gap-1 items-center">
                    <TextField size="small" type="number" placeholder="min"
                        value={lo}
                        onChange={(e) => onChange({ ...clause, value: [e.target.value, hi] })}
                        sx={{ width: 100 }} />
                    <span className="text-gray-400 text-xs">to</span>
                    <TextField size="small" type="number" placeholder="max"
                        value={hi}
                        onChange={(e) => onChange({ ...clause, value: [lo, e.target.value] })}
                        sx={{ width: 100 }} />
                </Box>
            );
        }
        if (op === 'in') {
            const csv = Array.isArray(clause.value) ? clause.value.join(', ') : (clause.value || '');
            return (
                <TextField
                    size="small"
                    fullWidth
                    placeholder="comma-separated"
                    value={csv}
                    onChange={(e) => onChange({
                        ...clause,
                        value: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })}
                />
            );
        }
        return (
            <TextField
                size="small"
                fullWidth
                type={NUMERIC_OPS.has(op) ? 'number' : 'text'}
                value={clause.value || ''}
                onChange={(e) => onChange({ ...clause, value: e.target.value })}
            />
        );
    };

    return (
        <Box className="flex items-center gap-1 mb-1 bg-white p-1.5 rounded border border-gray-200">
            <FormControl size="small" sx={{ minWidth: 150 }}>
                <Select value={clause.field || 'vendor_name'} onChange={onFieldChange}>
                    {FIELD_OPTIONS.map((f) => (
                        <MenuItem key={f.value} value={f.value}>{f.label}</MenuItem>
                    ))}
                </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 130 }}>
                <Select value={op} onChange={onOpChange}>
                    {allowedOps.map((o) => (
                        <MenuItem key={o} value={o}>{OP_LABELS[o] || o}</MenuItem>
                    ))}
                </Select>
            </FormControl>
            <Box className="flex-1 min-w-0">
                {renderValueInput()}
            </Box>
            <IconButton size="small" onClick={onRemove}>
                <X size={14} />
            </IconButton>
        </Box>
    );
};

export default MappingRulesPanel;
