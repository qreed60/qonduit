import React from 'react';
import StatusBadge from './StatusBadge';

interface EndpointCardProps {
  name: string;
  icon: string;
  description: string;
  url: string;
  status: 'online' | 'offline' | 'loading' | 'unknown';
  onTest: () => void;
  testLoading: boolean;
}

const EndpointCard: React.FC<EndpointCardProps> = ({
  name,
  icon,
  description,
  url,
  status,
  onTest,
  testLoading,
}) => {
  return (
    <div className="bg-bg-card rounded-xl border border-border-primary p-5 shadow-card hover:shadow-card-hover hover:border-accent-primary/30 transition-all duration-200">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-bg-secondary rounded-lg flex items-center justify-center border border-border-subtle">
            <span className="text-lg">{icon}</span>
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-text-primary text-sm">{name}</h3>
            <p className="text-xs text-text-tertiary mt-0.5">{description}</p>
          </div>
        </div>
      </div>

      <div className="mb-4">
        <p className="text-xs font-mono text-text-tertiary truncate" title={url}>
          {url}
        </p>
      </div>

      <div className="flex items-center justify-between">
        <StatusBadge status={status} label={status === 'loading' ? 'Checking...' : status === 'online' ? 'Online' : status === 'offline' ? 'Offline' : 'Pending'} />
        <button
          onClick={onTest}
          disabled={testLoading}
          className="px-3 py-1.5 rounded-lg text-xs font-medium border border-border-primary text-text-secondary hover:bg-bg-tertiary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
        >
          {testLoading ? 'Testing...' : 'Test'}
        </button>
      </div>
    </div>
  );
};

export default EndpointCard;
