import React, { useState, useEffect } from 'react';
import { testConnection, getSettings } from '../services/api';

const DiagnosticsPage: React.FC = () => {
  const settings = getSettings();
  const [connectionStatus, setConnectionStatus] = useState<{
    gateway: boolean | null;
    direct: boolean | null;
    router: boolean | null;
  }>({ gateway: null, direct: null, router: null });
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    testConnections();
  }, [settings]);

  const testConnections = async () => {
    setTesting(true);
    try {
      const gateway = await testConnection(settings.gatewayBaseUrl);
      const direct = await testConnection(settings.directBaseUrl);
      const router = await testConnection(settings.routerBaseUrl);

      setConnectionStatus({ gateway, direct, router });
    } finally {
      setTesting(false);
    }
  };

  const getStatusColor = (status: boolean | null) => {
    switch (status) {
      case true:
        return 'border-[var(--status-success)]/30 bg-[var(--status-success)]/5 text-[var(--status-success)]';
      case false:
        return 'border-[var(--status-error)]/30 bg-[var(--status-error)]/5 text-[var(--status-error)]';
      default:
        return 'border-[var(--border-primary)]/30 bg-[var(--bg-secondary)]/30 text-[var(--text-secondary)]';
    }
  };

  const getStatusIcon = (status: boolean | null) => {
    switch (status) {
      case true:
        return (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        );
      case false:
        return (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        );
      default:
        return (
          <svg className="w-5 h-5 animate-pulse-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        );
    }
  };

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
          Diagnostics
        </h2>
        <p className="text-[var(--text-secondary)] mt-2">
          Test connectivity and monitor service health
        </p>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        {/* Connectivity Test Card */}
        <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 mb-6 shadow-lg shadow-black/20">
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-lg font-semibold text-[var(--text-primary)]">Service Connectivity</h3>
            <button
              onClick={testConnections}
              disabled={testing}
              className={`px-4 py-2 rounded-lg font-medium transition-all duration-200 ${
                testing
                  ? 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] cursor-not-allowed'
                  : 'bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] hover:from-[var(--accent-primary-hover)] hover:to-[var(--accent-tertiary)] text-white shadow-lg shadow-[var(--accent-primary)]/20'
              }`}
            >
              {testing ? (
                <span className="flex items-center space-x-2">
                  <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.825 3 7.938l3-2.647z"></path>
                  </svg>
                  <span>Testing...</span>
                </span>
              ) : (
                <span className="flex items-center space-x-2">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                  </svg>
                  <span>Test Connections</span>
                </span>
              )}
            </button>
          </div>

          <div className="space-y-3">
            {/* Gateway Status */}
            <div className={`flex items-center justify-between p-4 rounded-xl border ${getStatusColor(connectionStatus.gateway)} transition-all duration-300`}>
              <div className="flex items-center space-x-4">
                <div className="w-10 h-10 bg-[var(--bg-primary)] rounded-lg flex items-center justify-center">
                  <span className="text-xl">🌐</span>
                </div>
                <div>
                  <p className="font-medium text-[var(--text-primary)]">Gateway</p>
                  <p className="text-xs text-[var(--text-secondary)] truncate max-w-[200px]" title={settings.gatewayBaseUrl}>
                    {settings.gatewayBaseUrl}
                  </p>
                </div>
              </div>
              <div className="flex items-center space-x-3">
                {getStatusIcon(connectionStatus.gateway)}
                <span className="text-sm font-medium">
                  {connectionStatus.gateway === true
                    ? 'Connected'
                    : connectionStatus.gateway === false
                    ? 'Disconnected'
                    : 'Pending'}
                </span>
              </div>
            </div>

            {/* Direct Status */}
            <div className={`flex items-center justify-between p-4 rounded-xl border ${getStatusColor(connectionStatus.direct)} transition-all duration-300`}>
              <div className="flex items-center space-x-4">
                <div className="w-10 h-10 bg-[var(--bg-primary)] rounded-lg flex items-center justify-center">
                  <span className="text-xl">⚡</span>
                </div>
                <div>
                  <p className="font-medium text-[var(--text-primary)]">Direct</p>
                  <p className="text-xs text-[var(--text-secondary)] truncate max-w-[200px]" title={settings.directBaseUrl}>
                    {settings.directBaseUrl}
                  </p>
                </div>
              </div>
              <div className="flex items-center space-x-3">
                {getStatusIcon(connectionStatus.direct)}
                <span className="text-sm font-medium">
                  {connectionStatus.direct === true
                    ? 'Connected'
                    : connectionStatus.direct === false
                    ? 'Disconnected'
                    : 'Pending'}
                </span>
              </div>
            </div>

            {/* Router Status */}
            <div className={`flex items-center justify-between p-4 rounded-xl border ${getStatusColor(connectionStatus.router)} transition-all duration-300`}>
              <div className="flex items-center space-x-4">
                <div className="w-10 h-10 bg-[var(--bg-primary)] rounded-lg flex items-center justify-center">
                  <span className="text-xl">Routing</span>
                </div>
                <div>
                  <p className="font-medium text-[var(--text-primary)]">Router</p>
                  <p className="text-xs text-[var(--text-secondary)] truncate max-w-[200px]" title={settings.routerBaseUrl}>
                    {settings.routerBaseUrl}
                  </p>
                </div>
              </div>
              <div className="flex items-center space-x-3">
                {getStatusIcon(connectionStatus.router)}
                <span className="text-sm font-medium">
                  {connectionStatus.router === true
                    ? 'Connected'
                    : connectionStatus.router === false
                    ? 'Disconnected'
                    : 'Pending'}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Recent Results Card */}
        <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
          <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-4">Recent Results</h3>
          <div className="bg-[var(--bg-secondary)]/50 rounded-xl p-4 border border-[var(--border-subtle)]">
            <p className="text-sm text-[var(--text-secondary)] mb-2">
              Last updated: {new Date().toLocaleString()}
            </p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {['Gateway', 'Direct', 'Router'].map((service, idx) => {
                const status = idx === 0 ? connectionStatus.gateway : idx === 1 ? connectionStatus.direct : connectionStatus.router;
                return (
                  <div key={service} className="flex flex-col items-center justify-center p-3 bg-[var(--bg-primary)]/50 rounded-lg border border-[var(--border-subtle)]">
                    <span className="text-lg mb-2">
                      {idx === 0 ? '🌐' : idx === 1 ? '⚡' : 'Routing'}
                    </span>
                    <span className="text-sm font-medium text-[var(--text-primary)]">{service}</span>
                    <span className={`text-xs mt-1 px-2 py-0.5 rounded-full ${
                      status === true
                        ? 'bg-[var(--status-success)]/10 text-[var(--status-success)]'
                        : status === false
                        ? 'bg-[var(--status-error)]/10 text-[var(--status-error)]'
                        : 'bg-[var(--bg-tertiary)]/50 text-[var(--text-tertiary)]'
                    }`}>
                      {status === true
                        ? 'Online'
                        : status === false
                        ? 'Offline'
                        : 'Unknown'}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DiagnosticsPage;
