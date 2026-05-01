import React, { useState, useEffect, useRef, useCallback } from 'react';
import { testConnection, getSettings } from '../services/api';
import StatusBar from '../components/StatusBar';

const DiagnosticsPage: React.FC = () => {
  const settings = getSettings();
  const [connectionStatus, setConnectionStatus] = useState<{
    gateway: boolean | null;
    direct: boolean | null;
    router: boolean | null;
  }>({ gateway: null, direct: null, router: null });
  const [isCheckingConnectivity, setIsCheckingConnectivity] = useState(false);
  const [consecutiveFailures, setConsecutiveFailures] = useState<{
    gateway: number;
    direct: number;
    router: number;
  }>({ gateway: 0, direct: 0, router: 0 });
  const [lastSuccessfulCheck, setLastSuccessfulCheck] = useState<Date | null>(null);
  const [debounceTimer, setDebounceTimer] = useState<number | null>(null);

  const initialLoadRef = useRef(true);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (debounceTimer) {
        clearTimeout(debounceTimer);
      }
    };
  }, [debounceTimer]);

  const performConnectionTest = useCallback(async () => {
    setIsCheckingConnectivity(true);
    try {
      const gateway = await testConnection(settings.gatewayBaseUrl);
      const direct = await testConnection(settings.directBaseUrl);
      const router = await testConnection(settings.routerBaseUrl);

      // Update status with new results
      setConnectionStatus({ gateway, direct, router });
      setLastSuccessfulCheck(new Date());

      // Track consecutive failures for each service
      setConsecutiveFailures(prev => ({
        gateway: gateway ? 0 : prev.gateway + 1,
        direct: direct ? 0 : prev.direct + 1,
        router: router ? 0 : prev.router + 1,
      }));
    } finally {
      setIsCheckingConnectivity(false);
    }
  }, [settings]);

  // Initial test on mount
  useEffect(() => {
    if (initialLoadRef.current) {
      initialLoadRef.current = false;
      performConnectionTest();
    }
  }, [performConnectionTest]);

  // Debounced re-test on settings change (prevents rapid re-testing)
  useEffect(() => {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }

    const timer = setTimeout(() => {
      performConnectionTest();
    }, 1000);

    setDebounceTimer(timer);

    return () => {
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [settings, performConnectionTest, debounceTimer]);

  const getStatusColor = (status: boolean | null, hasFailures: number) => {
    if (status === true) {
      return 'border-[var(--status-success)]/30 bg-[var(--status-success)]/5 text-[var(--status-success)]';
    }
    if (status === false || hasFailures > 2) {
      return 'border-[var(--status-error)]/30 bg-[var(--status-error)]/5 text-[var(--status-error)]';
    }
    return 'border-[var(--border-primary)]/30 bg-[var(--bg-secondary)]/30 text-[var(--text-secondary)]';
  };

  const getStatusIcon = (status: boolean | null, hasFailures: number, checking: boolean) => {
    const iconStyle: React.CSSProperties = {
      width: '16px',
      height: '16px',
      flexShrink: 0,
    };

    // Show success icon if connected or checking (preserves stability)
    if (status === true || (checking && status !== false)) {
      return (
        <svg style={{ ...iconStyle, animation: checking ? 'pulse-subtle 2s cubic-bezier(0.4, 0, 0.6, 1) infinite' : 'none' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
        </svg>
      );
    }
    // Show error icon only if definitely disconnected
    if (status === false || hasFailures > 2) {
      return (
        <svg style={iconStyle} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
        </svg>
      );
    }
    // Default: pending/checking
    return (
      <svg style={{ ...iconStyle, animation: checking ? 'spin 1s linear infinite' : 'pulse-subtle 2s cubic-bezier(0.4, 0, 0.6, 1) infinite' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    );
  };

  const getStatusText = (status: boolean | null, hasFailures: number, checking: boolean) => {
    // If connected and checking, show "Connected" (stable state)
    if (status === true) {
      return 'Connected';
    }
    // If definitely disconnected (failed > 2 times or false)
    if (status === false || hasFailures > 2) {
      return 'Disconnected';
    }
    // If checking and status is null (first time or no previous result)
    if (checking && status === null) {
      return 'Checking...';
    }
    // If checking and we have a previous result but it's not confirmed yet
    if (checking && status !== null) {
      return 'Connected';
    }
    // Default: pending (never tested)
    return 'Pending';
  };

  return (
    <div className="flex flex-col h-screen bg-[var(--bg-primary)] text-[var(--text-primary)]">
      <StatusBar settings={settings} />
      <div className="flex-1 overflow-y-auto p-6">
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
              onClick={performConnectionTest}
              disabled={isCheckingConnectivity}
              className={`px-4 py-2 rounded-lg font-medium transition-all duration-200 ${
                isCheckingConnectivity
                  ? 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] cursor-not-allowed'
                  : 'bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] hover:from-[var(--accent-primary-hover)] hover:to-[var(--accent-tertiary)] text-white shadow-lg shadow-[var(--accent-primary)]/20'
              }`}
            >
              {isCheckingConnectivity ? (
                <span className="flex items-center space-x-2">
                  <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.825 3 7.938l3-2.647z"></path>
                  </svg>
                  <span>Checking...</span>
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
            <div className={`flex items-center justify-between p-4 rounded-xl border ${getStatusColor(connectionStatus.gateway, consecutiveFailures.gateway)} transition-all duration-300`}>
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
                {getStatusIcon(connectionStatus.gateway, consecutiveFailures.gateway, isCheckingConnectivity)}
                <span className="text-sm font-medium">
                  {getStatusText(connectionStatus.gateway, consecutiveFailures.gateway, isCheckingConnectivity)}
                </span>
              </div>
            </div>

            {/* Direct Status */}
            <div className={`flex items-center justify-between p-4 rounded-xl border ${getStatusColor(connectionStatus.direct, consecutiveFailures.direct)} transition-all duration-300`}>
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
                {getStatusIcon(connectionStatus.direct, consecutiveFailures.direct, isCheckingConnectivity)}
                <span className="text-sm font-medium">
                  {getStatusText(connectionStatus.direct, consecutiveFailures.direct, isCheckingConnectivity)}
                </span>
              </div>
            </div>

            {/* Router Status */}
            <div className={`flex items-center justify-between p-4 rounded-xl border ${getStatusColor(connectionStatus.router, consecutiveFailures.router)} transition-all duration-300`}>
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
                {getStatusIcon(connectionStatus.router, consecutiveFailures.router, isCheckingConnectivity)}
                <span className="text-sm font-medium">
                  {getStatusText(connectionStatus.router, consecutiveFailures.router, isCheckingConnectivity)}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Recent Results Card */}
        <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-[var(--text-primary)]">Recent Results</h3>
            <span className="text-xs text-[var(--text-secondary)]">
              {lastSuccessfulCheck ? `Last check: ${lastSuccessfulCheck.toLocaleTimeString()}` : 'No checks yet'}
              {isCheckingConnectivity && <span className="ml-2 text-[var(--accent-primary)] animate-pulse">• Checking...</span>}
            </span>
          </div>
          <div className="bg-[var(--bg-secondary)]/50 rounded-xl p-4 border border-[var(--border-subtle)]">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {['Gateway', 'Direct', 'Router'].map((service, idx) => {
                const status = idx === 0 ? connectionStatus.gateway : idx === 1 ? connectionStatus.direct : connectionStatus.router;
                const failures = idx === 0 ? consecutiveFailures.gateway : idx === 1 ? consecutiveFailures.direct : consecutiveFailures.router;
                const isDisconnected = status === false || failures > 2;
                
                return (
                  <div key={service} className="flex flex-col items-center justify-center p-3 bg-[var(--bg-primary)]/50 rounded-lg border border-[var(--border-subtle)]">
                    <span className="text-lg mb-2">
                      {idx === 0 ? '🌐' : idx === 1 ? '⚡' : 'Routing'}
                    </span>
                    <span className="text-sm font-medium text-[var(--text-primary)]">{service}</span>
                    <span className={`text-xs mt-1 px-2 py-0.5 rounded-full ${
                      status === true
                        ? 'bg-[var(--status-success)]/10 text-[var(--status-success)]'
                        : isDisconnected
                        ? 'bg-[var(--status-error)]/10 text-[var(--status-error)]'
                        : 'bg-[var(--bg-tertiary)]/50 text-[var(--text-tertiary)]'
                    }`}>
                      {status === true
                        ? 'Online'
                        : isDisconnected
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
    </div>
  );
};

export default DiagnosticsPage;
