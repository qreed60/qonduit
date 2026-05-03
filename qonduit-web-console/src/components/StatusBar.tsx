import React from 'react';
import { Settings } from '../types';
import { ENDPOINTS } from '../config/endpoints';

interface StatusBarProps {
  settings: Settings;
}

const StatusBar: React.FC<StatusBarProps> = ({ settings }) => {
  const mode = settings.endpointMode;

  const modeLabel = mode === 'public' ? 'Public' : 'Local';
  const modeColor = mode === 'public'
    ? 'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border-[var(--accent-primary)]/20'
    : 'bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] border-[var(--border-primary)]';

  return (
    <header className="h-16 bg-[var(--bg-secondary)] border-b border-[var(--border-subtle)] flex items-center px-6 space-x-4 overflow-x-auto">
      {/* Mode Badge */}
      <div className="flex items-center space-x-2 flex-shrink-0">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Mode</span>
        <span className={`px-2 py-1 rounded-lg text-xs font-medium border ${modeColor}`}>
          {modeLabel}
        </span>
      </div>

      {/* Gateway */}
      <div className="flex items-center space-x-2 flex-shrink-0">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Gateway</span>
        <span className="px-2 py-1 rounded-lg text-xs font-mono border border-[var(--border-primary)]/50 text-[var(--text-secondary)] truncate max-w-[140px]">
          {ENDPOINTS.gateway[mode]}
        </span>
      </div>

      {/* Direct (llama.cpp) */}
      <div className="flex items-center space-x-2 flex-shrink-0">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Direct</span>
        <span className="px-2 py-1 rounded-lg text-xs font-mono border border-[var(--border-primary)]/50 text-[var(--text-secondary)] truncate max-w-[140px]">
          {ENDPOINTS.llama[mode]}
        </span>
      </div>

      {/* Router */}
      <div className="flex items-center space-x-2 flex-shrink-0">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Router</span>
        <span className="px-2 py-1 rounded-lg text-xs font-mono border border-[var(--border-primary)]/50 text-[var(--text-secondary)] truncate max-w-[140px]">
          {ENDPOINTS.router[mode]}
        </span>
      </div>

      {/* Provider Pill */}
      <div className="flex items-center space-x-2 flex-shrink-0">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Provider</span>
        <div className="px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20">
          {settings.defaultProvider}
        </div>
      </div>

      {/* Model Pill */}
      <div className="flex items-center space-x-2 flex-shrink-0">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Model</span>
        <div className="px-2 py-1 rounded-lg text-xs font-medium bg-[var(--bg-tertiary)] text-[var(--text-primary)] border border-[var(--border-primary)] truncate max-w-[180px]">
          {settings.defaultModel}
        </div>
      </div>
    </header>
  );
};

export default StatusBar;
