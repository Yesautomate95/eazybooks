/**
 * ConfidenceBadge Component
 * Displays a visual confidence indicator for AI-extracted data.
 *
 * Props:
 * - confidence: number (0-1) - The confidence score
 * - showPercentage: boolean - Whether to show the percentage value
 * - size: 'sm' | 'md' | 'lg' - Badge size
 * - tooltip: string - Optional custom tooltip text
 */

import React from 'react';
import { Tooltip } from '@mui/material';

const getConfidenceLevel = (confidence) => {
    if (confidence >= 0.8) return 'high';
    if (confidence >= 0.5) return 'medium';
    return 'low';
};

const getConfidenceColor = (level) => {
    switch (level) {
        case 'high':
            return {
                bg: 'bg-transparent',
                text: 'text-gilt-credit',
                border: 'border-gilt-credit/40',
                ring: 'ring-gilt-credit',
            };
        case 'medium':
            return {
                bg: 'bg-transparent',
                text: 'text-gilt-brass',
                border: 'border-gilt-brass/40',
                ring: 'ring-gilt-brass',
            };
        case 'low':
        default:
            return {
                bg: 'bg-transparent',
                text: 'text-gilt-debit',
                border: 'border-gilt-debit/40',
                ring: 'ring-gilt-debit',
            };
    }
};

const getSizeClasses = (size) => {
    switch (size) {
        case 'sm':
            return 'px-1.5 py-0.5 text-[10px]';
        case 'lg':
            return 'px-3 py-1.5 text-xs';
        case 'md':
        default:
            return 'px-2 py-1 text-[10px]';
    }
};

const ConfidenceBadge = ({
    confidence,
    showPercentage = true,
    size = 'md',
    tooltip = null,
}) => {
    const level = getConfidenceLevel(confidence);
    const colors = getConfidenceColor(level);
    const sizeClasses = getSizeClasses(size);

    const percentage = Math.round(confidence * 100);

    const defaultTooltip = `Confidence: ${percentage}% (${level})\n${
        level === 'high'
            ? 'This value is likely correct'
            : level === 'medium'
            ? 'This value may need verification'
            : 'This value likely needs manual review'
    }`;

    const badge = (
        <span
            className={`inline-flex items-center rounded-full font-mono tabular-nums uppercase tracking-[0.14em] ${colors.bg} ${colors.text} ${colors.border} border ${sizeClasses}`}
        >
            <span
                className={`w-2 h-2 rounded-full mr-1.5 ${
                    level === 'high'
                        ? 'bg-gilt-credit'
                        : level === 'medium'
                        ? 'bg-gilt-brass'
                        : 'bg-gilt-debit'
                }`}
            />
            {showPercentage && `${percentage}%`}
        </span>
    );

    return (
        <Tooltip title={tooltip || defaultTooltip} arrow placement="top">
            {badge}
        </Tooltip>
    );
};

/**
 * ConfidenceIndicator - A simpler dot indicator without percentage
 */
export const ConfidenceIndicator = ({ confidence, size = 'md' }) => {
    const level = getConfidenceLevel(confidence);

    const dotSize = {
        sm: 'w-2 h-2',
        md: 'w-3 h-3',
        lg: 'w-4 h-4',
    }[size];

    const dotColor = {
        high: 'bg-gilt-credit',
        medium: 'bg-gilt-brass',
        low: 'bg-gilt-debit',
    }[level];

    return (
        <Tooltip title={`Confidence: ${Math.round(confidence * 100)}%`}>
            <span className={`inline-block rounded-full ${dotSize} ${dotColor}`} />
        </Tooltip>
    );
};

/**
 * ConfidenceBar - A horizontal bar showing confidence level
 */
export const ConfidenceBar = ({ confidence, width = 60 }) => {
    const level = getConfidenceLevel(confidence);
    const percentage = Math.round(confidence * 100);

    const barColor = {
        high: 'bg-gilt-credit',
        medium: 'bg-gilt-brass',
        low: 'bg-gilt-debit',
    }[level];

    return (
        <Tooltip title={`Confidence: ${percentage}%`}>
            <div
                className="h-2 bg-gilt-sunken rounded-full overflow-hidden"
                style={{ width }}
            >
                <div
                    className={`h-full ${barColor} transition-all duration-300`}
                    style={{ width: `${percentage}%` }}
                />
            </div>
        </Tooltip>
    );
};

export default ConfidenceBadge;
