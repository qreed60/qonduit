import React from 'react';
import { Settings } from '../types';

interface StatusBarProps {
  settings: Settings;
}

const StatusBar: React.FC<StatusBarProps> = ({ settings }) => {
  const getStatusColor = (url: string) => {
    try {
      const urlObj = new URL(url);
      return urlObj.hostname === '192.168.5.5' ? 'success' : 'warning';
    } catch {
      return 'error';
    }
  };

  const statusColorMap = {
    success: 'bg-[var(--status-success)]/10 text-[var(--status-success)] border-[var(--status-success)]/20',
    warning: 'bg-[var(--status-warning)]/10 text-[var(--status-warning)] border-[var(--status-warning)]/20',
    error: 'bg-[var(--status-error)]/10 text-[var(--status-error)] border-[var(--status-error)]/20',
  };

  return (
    <header className="h-16 bg-[var(--bg-secondary)] border-b border-[var(--border-subtle)] flex items-center px-6 space-x-6">
      {/* Gateway Status */}
      <div className="flex items-center space-x-3">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Gateway</span>
        <div className={`px-3 py-1.5 rounded-lg text-xs font-mono border ${statusColorMap[getStatusColor(settings.gatewayBaseUrl)]} transition-colors`}>
          {settings.gatewayBaseUrl}
        </div>
      </div>

      {/* Direct Status */}
      <div className="flex items-center space-x-3">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Direct</span>
        <div className={`px-3 py-1.5 rounded-lg text-xs font-mono border ${statusColorMap[getStatusColor(settings.directBaseUrl)]} transition-colors`}>
          {settings.directBaseUrl}
        </div>
      </div>

      {/* Router Status */}
      <div className="flex items-center space-x-3">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Router</span>
        <div className={`px-3 py-1.5 rounded-lg text-xs font-mono border ${statusColorMap[getStatusColor(settings.routerBaseUrl)]} transition-colors`}>
          {settings.routerBaseUrl}
        </div>
      </div>

      {/* Provider Pill */}
      <div className="flex items-center space-x-3">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Provider</span>
        <div className="px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20">
          {settings.defaultProvider}
        </div>
      </div>

      {/* Model Pill */}
      <div className="flex items-center space-x-3">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Model</span>
        <div className="px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--bg-tertiary)] text-[var(--text-primary)] border border-[var(--border-primary)] truncate max-w-[250px]">
          {settings.defaultModel}
        </div>
      </div>
    </header>
  );
};

export default StatusBar;
