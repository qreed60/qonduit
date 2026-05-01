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

  const getStatusIcon = (status: 'success' | 'warning' | 'error') => {
    const dotStyle: React.CSSProperties = {
      width: '8px',
      height: '8px',
      borderRadius: '50%',
      flexShrink: 0,
    };
    
    switch (status) {
      case 'success':
        return <div style={{ ...dotStyle, backgroundColor: 'var(--status-success)' }} />;
      case 'warning':
        return <div style={{ ...dotStyle, backgroundColor: 'var(--status-warning)' }} />;
      case 'error':
        return <div style={{ ...dotStyle, backgroundColor: 'var(--status-error)' }} />;
    }
  };

  const statusColorMap = {
    success: 'border-[var(--status-success)]/30 text-[var(--status-success)]',
    warning: 'border-[var(--status-warning)]/30 text-[var(--status-warning)]',
    error: 'border-[var(--status-error)]/30 text-[var(--status-error)]',
  };

  return (
    <header className="h-16 bg-[var(--bg-secondary)] border-b border-[var(--border-subtle)] flex items-center px-6 space-x-6">
      {/* Gateway Status */}
      <div className="flex items-center space-x-3">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Gateway</span>
        <div className={`px-2 py-1 rounded-lg text-xs font-mono border flex items-center space-x-2 ${statusColorMap[getStatusColor(settings.gatewayBaseUrl)]} transition-colors`}>
          {getStatusIcon(getStatusColor(settings.gatewayBaseUrl))}
          <span className="truncate max-w-[120px]">{settings.gatewayBaseUrl}</span>
        </div>
      </div>

      {/* Direct Status */}
      <div className="flex items-center space-x-3">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Direct</span>
        <div className={`px-2 py-1 rounded-lg text-xs font-mono border flex items-center space-x-2 ${statusColorMap[getStatusColor(settings.directBaseUrl)]} transition-colors`}>
          {getStatusIcon(getStatusColor(settings.directBaseUrl))}
          <span className="truncate max-w-[120px]">{settings.directBaseUrl}</span>
        </div>
      </div>

      {/* Router Status */}
      <div className="flex items-center space-x-3">
        <span className="text-sm font-medium text-[var(--text-secondary)]">Router</span>
        <div className={`px-2 py-1 rounded-lg text-xs font-mono border flex items-center space-x-2 ${statusColorMap[getStatusColor(settings.routerBaseUrl)]} transition-colors`}>
          {getStatusIcon(getStatusColor(settings.routerBaseUrl))}
          <span className="truncate max-w-[120px]">{settings.routerBaseUrl}</span>
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
        <div className="px-2 py-1 rounded-lg text-xs font-medium bg-[var(--bg-tertiary)] text-[var(--text-primary)] border border-[var(--border-primary)] truncate max-w-[200px]">
          {settings.defaultModel}
        </div>
      </div>
    </header>
  );
};

export default StatusBar;
