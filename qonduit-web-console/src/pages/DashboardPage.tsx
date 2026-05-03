import React, { useState, useEffect, useCallback } from 'react';
import { getSettings } from '../services/api';
import {
  testEndpoint,
  testRouterHealth,
  getRouterStatus,
  fetchRouterModels,
  launchModel as apiLaunchModel,
  stopModel as apiStopModel,
  streamLogs,
} from '../services/api';
import { Settings } from '../types';
import { ENDPOINTS } from '../config/endpoints';
import StatusBar from '../components/StatusBar';
import Toast from '../components/Toast';

// ─── Status Badge ────────────────────────────────────────────────────────────

interface StatusBadgeProps {
  status: 'online' | 'offline' | 'loading' | 'unknown';
  label: string;
}

const StatusBadge: React.FC<StatusBadgeProps> = ({ status, label }) => {
  const colorMap: Record<string, string> = {
    online: 'bg-[var(--status-success)]/10 text-[var(--status-success)] border-[var(--status-success)]/20',
    offline: 'bg-[var(--status-error)]/10 text-[var(--status-error)] border-[var(--status-error)]/20',
    loading: 'bg-[var(--status-warning)]/10 text-[var(--status-warning)] border-[var(--status-warning)]/20',
    unknown: 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border-[var(--border-primary)]',
  };

  const dotMap: Record<string, string> = {
    online: 'bg-[var(--status-success)]',
    offline: 'bg-[var(--status-error)]',
    loading: 'bg-[var(--status-warning)] animate-pulse',
    unknown: 'bg-[var(--text-tertiary)]',
  };

  return (
    <div className={`flex items-center space-x-2 px-3 py-2 rounded-xl border ${colorMap[status]}`}>
      <div className={`w-2 h-2 rounded-full ${dotMap[status]}`} />
      <span className="text-sm font-medium">{label}</span>
    </div>
  );
};

// ─── Endpoint Health Card ────────────────────────────────────────────────────

interface EndpointHealthProps {
  name: string;
  icon: string;
  endpointKey: 'gateway' | 'llama' | 'router';
  healthStatus: boolean | null;
  loading: boolean;
  onTest: () => void;
}

const EndpointHealthCard: React.FC<EndpointHealthProps> = ({
  name,
  icon,
  endpointKey,
  healthStatus,
  loading,
  onTest,
}) => {
  const mode = getSettings().endpointMode;
  const url = ENDPOINTS[endpointKey][mode];

  return (
    <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-5 shadow-lg shadow-black/20 hover:border-[var(--accent-primary)]/30 transition-all duration-200">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 bg-[var(--bg-secondary)] rounded-xl flex items-center justify-center border border-[var(--border-subtle)]">
            <span className="text-lg">{icon}</span>
          </div>
          <div>
            <h3 className="font-semibold text-[var(--text-primary)]">{name}</h3>
            <p className="text-xs font-mono text-[var(--text-tertiary)] truncate max-w-[180px]" title={url}>
              {url}
            </p>
          </div>
        </div>
        <StatusBadge
          status={loading ? 'loading' : healthStatus === true ? 'online' : healthStatus === false ? 'offline' : 'unknown'}
          label={loading ? 'Checking...' : healthStatus === true ? 'Online' : healthStatus === false ? 'Offline' : 'Pending'}
        />
      </div>
      <button
        onClick={onTest}
        disabled={loading}
        className="w-full px-4 py-2 rounded-xl text-sm font-medium border border-[var(--border-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
      >
        {loading ? 'Testing...' : 'Test Connection'}
      </button>
    </div>
  );
};

// ─── Model Control Card ──────────────────────────────────────────────────────

interface ModelControlProps {
  routerStatus: { running: boolean; exists: boolean } | null;
  models: Array<{ name: string; path: string }>;
  selectedModel: string;
  ctxSize: number;
  onSelectModel: (name: string) => void;
  onCtxChange: (size: number) => void;
  onLaunch: () => void;
  onStop: () => void;
  loading: boolean;
  actionStatus: 'idle' | 'launching' | 'stopping' | 'success' | 'error';
  actionMessage: string;
}

const ModelControlCard: React.FC<ModelControlProps> = ({
  routerStatus,
  models,
  selectedModel,
  ctxSize,
  onSelectModel,
  onCtxChange,
  onLaunch,
  onStop,
  loading,
  actionStatus,
  actionMessage,
}) => {
  const isRunning = routerStatus?.running;
  const canLaunch = !isRunning && !loading && models.length > 0 && !!selectedModel;
  const canStop = isRunning && !loading;

  return (
    <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Model Control</h3>
          <p className="text-sm text-[var(--text-secondary)] mt-1">
            {isRunning ? 'Model is currently running' : 'Select and launch a model'}
          </p>
        </div>
        <StatusBadge
          status={isRunning ? 'online' : !routerStatus ? 'unknown' : 'offline'}
          label={isRunning ? 'Running' : !routerStatus ? 'Checking...' : 'Stopped'}
        />
      </div>

      {/* Model Selection */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
            Select Model
          </label>
          <select
            value={selectedModel}
            onChange={(e) => onSelectModel(e.target.value)}
            disabled={loading || isRunning}
            className="w-full px-4 py-3 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]/50 focus:ring-1 focus:ring-[var(--accent-primary)]/50 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
          >
            <option value="">Choose a model...</option>
            {models.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">
            Context Size: {ctxSize}
          </label>
          <input
            type="range"
            min="512"
            max="8192"
            step="512"
            value={ctxSize}
            onChange={(e) => onCtxChange(Number(e.target.value))}
            disabled={loading || isRunning}
            className="w-full h-2 bg-[var(--bg-secondary)] rounded-lg appearance-none cursor-pointer accent-[var(--accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <div className="flex justify-between text-xs text-[var(--text-tertiary)] mt-1">
            <span>512</span>
            <span>8192</span>
          </div>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex space-x-4 mt-6">
        <button
          onClick={onLaunch}
          disabled={!canLaunch}
          className={`flex-1 px-6 py-3 rounded-xl font-medium transition-all duration-200 ${
            canLaunch
              ? 'bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] hover:from-[var(--accent-primary-hover)] hover:to-[var(--accent-tertiary)] text-white shadow-lg shadow-[var(--accent-primary)]/20 hover:shadow-[var(--accent-primary)]/30'
              : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] cursor-not-allowed'
          }`}
        >
          {loading && actionStatus === 'launching' ? (
            <span className="flex items-center justify-center space-x-2">
              <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.825 3 7.938l3-2.647z" />
              </svg>
              <span>Launching...</span>
            </span>
          ) : (
            'Launch Model'
          )}
        </button>
        <button
          onClick={onStop}
          disabled={!canStop}
          className={`flex-1 px-6 py-3 rounded-xl font-medium transition-all duration-200 ${
            canStop
              ? 'bg-[var(--status-error)]/10 text-[var(--status-error)] border border-[var(--status-error)]/20 hover:bg-[var(--status-error)]/20'
              : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border-primary)] cursor-not-allowed'
          }`}
        >
          {loading && actionStatus === 'stopping' ? (
            <span className="flex items-center justify-center space-x-2">
              <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.825 3 7.938l3-2.647z" />
              </svg>
              <span>Stopping...</span>
            </span>
          ) : (
            'Stop Model'
          )}
        </button>
      </div>

      {/* Action Message */}
      {actionMessage && (
        <div className={`mt-4 px-4 py-3 rounded-xl text-sm ${
          actionStatus === 'success'
            ? 'bg-[var(--status-success)]/10 text-[var(--status-success)] border border-[var(--status-success)]/20'
            : actionStatus === 'error'
            ? 'bg-[var(--status-error)]/10 text-[var(--status-error)] border border-[var(--status-error)]/20'
            : 'bg-[var(--bg-secondary)]/50 text-[var(--text-secondary)] border border-[var(--border-subtle)]'
        }`}>
          {actionMessage}
        </div>
      )}
    </div>
  );
};

// ─── Logs Panel ──────────────────────────────────────────────────────────────

interface LogsPanelProps {
  routerStatus: { running: boolean } | null;
}

const LogsPanel: React.FC<LogsPanelProps> = ({ routerStatus }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logsEndRef = React.useRef<HTMLDivElement>(null);
  const abortRef = React.useRef(false);

  const startStreaming = useCallback(async () => {
    setIsStreaming(true);
    setError(null);
    abortRef.current = false;

    try {
      let lineCount = 0;
      const maxLines = 500;

      for await (const _ of streamLogs((line: string) => {
        if (abortRef.current) return;
        setLogs((prev) => {
          const next = [...prev, line];
          return next.length > maxLines ? next.slice(-maxLines) : next;
        });
        lineCount++;
      })) {
        // This loop just keeps the generator running
      }
    } catch (err) {
      if (!abortRef.current) {
        setError(err instanceof Error ? err.message : 'Failed to connect to log stream');
      }
    } finally {
      if (!abortRef.current) {
        setIsStreaming(false);
      }
    }
  }, []);

  const stopStreaming = useCallback(() => {
    abortRef.current = true;
    setIsStreaming(false);
  }, []);

  useEffect(() => {
    if (routerStatus?.running && isStreaming) {
      startStreaming();
    }
    return () => {
      abortRef.current = true;
    };
  }, [routerStatus?.running, startStreaming]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div className="bg-[var(--bg-card)] rounded-2xl border border-[var(--border-primary)] p-6 shadow-lg shadow-black/20">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Live Logs</h3>
          <p className="text-sm text-[var(--text-secondary)] mt-1">
            {routerStatus?.running ? (
              isStreaming ? (
                <span className="flex items-center space-x-2">
                  <div className="w-2 h-2 rounded-full bg-[var(--status-success)] animate-pulse" />
                  <span>Streaming...</span>
                </span>
              ) : (
                <span className="text-[var(--status-warning)]">Reconnecting...</span>
              )
            ) : (
              <span className="text-[var(--text-tertiary)]">Start a model to view logs</span>
            )}
          </p>
        </div>
        {routerStatus?.running && (
          <button
            onClick={isStreaming ? stopStreaming : startStreaming}
            className={`px-4 py-2 rounded-xl text-sm font-medium border transition-all duration-200 ${
              isStreaming
                ? 'border-[var(--status-warning)]/30 text-[var(--status-warning)] hover:bg-[var(--status-warning)]/10'
                : 'border-[var(--accent-primary)]/30 text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/10'
            }`}
          >
            {isStreaming ? 'Pause' : 'Resume'}
          </button>
        )}
      </div>

      {/* Logs Display */}
      <div className="bg-[var(--bg-secondary)]/50 rounded-xl border border-[var(--border-subtle)] p-4 h-64 overflow-y-auto font-mono text-xs">
        {logs.length === 0 ? (
          <div className="flex items-center justify-center h-full text-[var(--text-tertiary)]">
            {error ? (
              <div className="text-center">
                <p className="text-[var(--status-error)] mb-2">Connection Error</p>
                <p className="text-xs">{error}</p>
              </div>
            ) : (
              <p>No logs to display</p>
            )}
          </div>
        ) : (
          <div className="space-y-1">
            {logs.map((log, idx) => (
              <div key={idx} className="text-[var(--text-secondary)] break-all">
                <span className="text-[var(--text-tertiary)] select-none">{String(idx + 1).padStart(4, ' ')} │ </span>
                {log}
              </div>
            ))}
            <div ref={logsEndRef} />
          </div>
        )}
      </div>
    </div>
  );
};

// ─── Dashboard Page ──────────────────────────────────────────────────────────

const DashboardPage: React.FC = () => {
  const [settings] = useState<Settings>(getSettings());
  const [endpointHealth, setEndpointHealth] = useState<{
    gateway: boolean | null;
    llama: boolean | null;
    router: boolean | null;
  }>({ gateway: null, llama: null, router: null });
  const [healthLoading, setHealthLoading] = useState(false);
  const [routerStatus, setRouterStatus] = useState<{
    running: boolean;
    exists: boolean;
    webui: string;
    llama: string;
  } | null>(null);
  const [routerModels, setRouterModels] = useState<Array<{ name: string; path: string }>>([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [ctxSize, setCtxSize] = useState(4096);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionStatus, setActionStatus] = useState<'idle' | 'launching' | 'stopping' | 'success' | 'error'>('idle');
  const [actionMessage, setActionMessage] = useState('');
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Fetch initial data
  useEffect(() => {
    fetchDashboardData();
  }, []);

  const fetchDashboardData = async () => {
    setFetchError(null);

    // Fetch router status
    try {
      const status = await getRouterStatus();
      setRouterStatus(status);
    } catch {
      // Router might not be available
    }

    // Fetch router models
    try {
      const data = await fetchRouterModels();
      setRouterModels(data.models || []);
      if (data.suggested_ctx && !selectedModel) {
        setCtxSize(data.suggested_ctx);
      }
    } catch {
      // Models might not be available
    }

    // Test endpoint health
    await testAllEndpoints();
  };

  const testAllEndpoints = async () => {
    setHealthLoading(true);
    try {
      const [gateway, llama, router] = await Promise.all([
        testEndpoint('gateway').catch(() => false),
        testEndpoint('llama').catch(() => false),
        testRouterHealth().catch(() => false),
      ]);
      setEndpointHealth({ gateway, llama, router });
    } finally {
      setHealthLoading(false);
    }
  };

  const handleLaunch = async () => {
    if (!selectedModel) return;

    setActionLoading(true);
    setActionStatus('launching');
    setActionMessage('');

    try {
      const result = await apiLaunchModel(selectedModel, ctxSize);
      if (result.ok) {
        setActionStatus('success');
        setActionMessage(result.message || 'Model launched successfully');
        setToastMessage('Model launched successfully');
        setRouterStatus((prev) => (prev ? { ...prev, running: true } : null));
      } else {
        setActionStatus('error');
        setActionMessage(result.message || 'Failed to launch model');
      }
    } catch (err) {
      setActionStatus('error');
      setActionMessage(err instanceof Error ? err.message : 'Failed to launch model');
    } finally {
      setActionLoading(false);
      setTimeout(() => {
        setActionMessage('');
        setActionStatus('idle');
      }, 5000);
    }
  };

  const handleStop = async () => {
    setActionLoading(true);
    setActionStatus('stopping');
    setActionMessage('');

    try {
      const result = await apiStopModel();
      if (result.ok) {
        setActionStatus('success');
        setActionMessage(result.message || 'Model stopped successfully');
        setToastMessage('Model stopped successfully');
        setRouterStatus((prev) => (prev ? { ...prev, running: false } : null));
      } else {
        setActionStatus('error');
        setActionMessage(result.message || 'Failed to stop model');
      }
    } catch (err) {
      setActionStatus('error');
      setActionMessage(err instanceof Error ? err.message : 'Failed to stop model');
    } finally {
      setActionLoading(false);
      setTimeout(() => {
        setActionMessage('');
        setActionStatus('idle');
      }, 5000);
    }
  };

  // Auto-select first model if none selected and models available
  useEffect(() => {
    if (!selectedModel && routerModels.length > 0) {
      setSelectedModel(routerModels[0].name);
    }
  }, [routerModels, selectedModel]);

  const isRunning = routerStatus?.running;

  return (
    <div className="flex flex-col h-full bg-[var(--bg-primary)]">
      {/* Status Bar */}
      <StatusBar settings={settings} />

      {/* Dashboard Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Page Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-tertiary)] bg-clip-text text-transparent">
            Control Center
          </h1>
          <p className="text-[var(--text-secondary)] mt-1">
            Monitor and manage your AI infrastructure
          </p>
        </div>

        {/* System Health Overview */}
        <div className="mb-6">
          <h2 className="text-lg font-semibold text-[var(--text-primary)] mb-3">System Health</h2>
          <div className="flex flex-wrap gap-3">
            <StatusBadge
              status={endpointHealth.gateway === true ? 'online' : endpointHealth.gateway === false ? 'offline' : 'unknown'}
              label={endpointHealth.gateway === null && healthLoading ? 'Checking Gateway...' : endpointHealth.gateway === true ? 'Gateway Online' : endpointHealth.gateway === false ? 'Gateway Offline' : 'Gateway Unknown'}
            />
            <StatusBadge
              status={endpointHealth.llama === true ? 'online' : endpointHealth.llama === false ? 'offline' : 'unknown'}
              label={endpointHealth.llama === null && healthLoading ? 'Checking Direct...' : endpointHealth.llama === true ? 'Direct Online' : endpointHealth.llama === false ? 'Direct Offline' : 'Direct Unknown'}
            />
            <StatusBadge
              status={endpointHealth.router === true ? 'online' : endpointHealth.router === false ? 'offline' : 'unknown'}
              label={endpointHealth.router === null && healthLoading ? 'Checking Router...' : endpointHealth.router === true ? 'Router Online' : endpointHealth.router === false ? 'Router Offline' : 'Router Unknown'}
            />
            <StatusBadge
              status={isRunning ? 'online' : !routerStatus ? 'unknown' : 'offline'}
              label={routerStatus === null ? 'Checking Model...' : isRunning ? 'Model Running' : 'Model Stopped'}
            />
          </div>
          {fetchError && (
            <div className="mt-3 px-4 py-2 rounded-xl bg-[var(--status-error)]/10 border border-[var(--status-error)]/20 text-sm text-[var(--status-error)]">
              {fetchError}
            </div>
          )}
        </div>

        {/* Endpoint Health Cards */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Endpoint Health</h2>
            <button
              onClick={testAllEndpoints}
              disabled={healthLoading}
              className="px-4 py-2 rounded-xl text-sm font-medium border border-[var(--border-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
            >
              {healthLoading ? 'Testing...' : 'Refresh'}
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <EndpointHealthCard
              name="Memory Gateway"
              icon="🌐"
              endpointKey="gateway"
              healthStatus={endpointHealth.gateway}
              loading={healthLoading}
              onTest={() => {
                setHealthLoading(true);
                testEndpoint('gateway')
                  .then((result) => setEndpointHealth((prev) => ({ ...prev, gateway: result })))
                  .finally(() => setHealthLoading(false));
              }}
            />
            <EndpointHealthCard
              name="Direct (llama.cpp)"
              icon="⚡"
              endpointKey="llama"
              healthStatus={endpointHealth.llama}
              loading={healthLoading}
              onTest={() => {
                setHealthLoading(true);
                testEndpoint('llama')
                  .then((result) => setEndpointHealth((prev) => ({ ...prev, llama: result })))
                  .finally(() => setHealthLoading(false));
              }}
            />
            <EndpointHealthCard
              name="Router API"
              icon="🔀"
              endpointKey="router"
              healthStatus={endpointHealth.router}
              loading={healthLoading}
              onTest={() => {
                setHealthLoading(true);
                testRouterHealth()
                  .then((result) => setEndpointHealth((prev) => ({ ...prev, router: result })))
                  .finally(() => setHealthLoading(false));
              }}
            />
          </div>
        </div>

        {/* Model Control */}
        <div className="mb-6">
          <ModelControlCard
            routerStatus={routerStatus}
            models={routerModels}
            selectedModel={selectedModel}
            ctxSize={ctxSize}
            onSelectModel={setSelectedModel}
            onCtxChange={setCtxSize}
            onLaunch={handleLaunch}
            onStop={handleStop}
            loading={actionLoading}
            actionStatus={actionStatus}
            actionMessage={actionMessage}
          />
        </div>

        {/* Logs Panel */}
        <div>
          <LogsPanel routerStatus={routerStatus} />
        </div>
      </div>

      {/* Toast */}
      {toastMessage && (
        <Toast message={toastMessage} type="success" onClose={() => setToastMessage(null)} />
      )}
    </div>
  );
};

export default DashboardPage;
