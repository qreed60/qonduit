import React from 'react';

interface StatusBadgeProps {
  status: 'online' | 'offline' | 'loading' | 'unknown';
  label: string;
  size?: 'sm' | 'md';
}

const StatusBadge: React.FC<StatusBadgeProps> = ({ status, label, size = 'sm' }) => {
  const sizeClasses = size === 'md'
    ? 'px-4 py-2 text-sm'
    : 'px-3 py-1.5 text-xs';

  const colorMap: Record<string, string> = {
    online: 'bg-status-success/10 text-status-success border-status-success/20',
    offline: 'bg-status-error/10 text-status-error border-status-error/20',
    loading: 'bg-status-warning/10 text-status-warning border-status-warning/20',
    unknown: 'bg-bg-tertiary text-text-tertiary border-border-primary',
  };

  const dotMap: Record<string, string> = {
    online: 'bg-status-success',
    offline: 'bg-status-error',
    loading: 'bg-status-warning animate-pulse-dot',
    unknown: 'bg-text-tertiary',
  };

  return (
    <div className={`flex items-center gap-2 rounded-lg border ${colorMap[status]} ${sizeClasses}`}>
      <div className={`w-2 h-2 rounded-full flex-shrink-0 ${dotMap[status]}`} />
      <span className="font-medium truncate">{label}</span>
    </div>
  );
};

export default StatusBadge;
