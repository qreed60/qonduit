import React, { useState } from 'react';
import StatusBadge from './StatusBadge';
import {
  CheckCircle2,
  Copy,
  ExternalLink,
  Clock,
} from 'lucide-react';

interface EndpointCardProps {
  name: string;
  icon: string;
  description: string;
  url: string;
  status: 'online' | 'offline' | 'loading' | 'unknown';
  onTest: () => void;
  testLoading: boolean;
  externalUrl?: string; // URL to open in new tab (e.g., WebUI UI)
}

const EndpointCard: React.FC<EndpointCardProps> = ({
  name,
  icon,
  description,
  url,
  status,
  onTest,
  testLoading,
  externalUrl,
}) => {
  const [copied, setCopied] = useState(false);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* ignore */ }
  };

  const handleTest = () => {
    setCheckedAt(new Date());
    onTest();
  };

  // Border color based on status
  const borderClass = status === 'online'
    ? 'border-l-2 border-l-status-success'
    : status === 'offline'
    ? 'border-l-2 border-l-status-error'
    : status === 'loading'
    ? 'border-l-2 border-l-status-warning border-l-dashed'
    : 'border-l-2 border-l-text-tertiary/30';

  const statusLabel = status === 'loading' ? 'Checking...' : status === 'online' ? 'Online' : status === 'offline' ? 'Offline' : 'Pending';

  return (
    <div
      className={`bg-bg-card rounded-xl border border-border-primary border-l-2 p-5 shadow-card hover:shadow-card-hover transition-all duration-200 ${borderClass}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 bg-bg-secondary rounded-lg flex items-center justify-center border border-border-subtle flex-shrink-0">
            <span className="text-base">{icon}</span>
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-text-primary text-sm">{name}</h3>
            <p className="text-xs text-text-tertiary mt-0.5 truncate">{description}</p>
          </div>
        </div>
      </div>

      {/* URL Row */}
      <div className="mb-4 flex items-center gap-2">
        <p className="text-xs font-mono text-text-tertiary truncate flex-1" title={url}>
          {url}
        </p>
        <button
          onClick={handleCopy}
          className="p-1 rounded hover:bg-bg-tertiary text-text-tertiary hover:text-text-primary transition-all duration-200 flex-shrink-0"
          title={copied ? 'Copied!' : 'Copy URL'}
        >
          {copied ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-status-success" />
          ) : (
            <Copy className="w-3.5 h-3.5" />
          )}
        </button>
        {externalUrl && (
          <a
            href={externalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="p-1 rounded hover:bg-bg-tertiary text-text-tertiary hover:text-text-primary transition-all duration-200 flex-shrink-0"
            title="Open in new tab"
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StatusBadge status={status} label={statusLabel} />
          {checkedAt && (
            <span className="flex items-center gap-1 text-[10px] text-text-tertiary">
              <Clock className="w-2.5 h-2.5" />
              {(() => {
                const diff = Math.floor((Date.now() - checkedAt.getTime()) / 1000);
                return diff < 60 ? `${diff}s ago` : `${Math.floor(diff / 60)}m ago`;
              })()}
            </span>
          )}
        </div>
        <button
          onClick={handleTest}
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
