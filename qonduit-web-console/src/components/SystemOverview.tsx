import React from 'react';
import StatusBadge from './StatusBadge';

interface SystemOverviewProps {
  endpointHealth: {
    gateway: boolean | null;
    llama: boolean | null;
    router: boolean | null;
  };
  healthLoading: boolean;
  routerStatus: { running: boolean; exists: boolean } | null;
  selectedModel: string;
  onRefresh: () => void;
  onLaunch: () => void;
  onStop: () => void;
  actionLoading: boolean;
  actionStatus: 'idle' | 'launching' | 'stopping' | 'success' | 'error';
  actionMessage: string;
}

const SystemOverview: React.FC<SystemOverviewProps> = ({
  endpointHealth,
  healthLoading,
  routerStatus,
  selectedModel,
  onRefresh,
  onLaunch,
  onStop,
  actionLoading,
  actionStatus,
  actionMessage,
}) => {
  const isRunning = routerStatus?.running;
  const canLaunch = !isRunning && !actionLoading && !!selectedModel;
  const canStop = isRunning && !actionLoading;

  return (
    <div className="bg-bg-card rounded-xl border border-border-primary p-6 shadow-card">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-text-primary">System Overview</h2>
        <button
          onClick={onRefresh}
          disabled={healthLoading}
          className="px-4 py-2 rounded-lg text-xs font-medium border border-border-primary text-text-secondary hover:bg-bg-tertiary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
        >
          {healthLoading ? 'Checking...' : 'Refresh'}
        </button>
      </div>

      {/* Health Badges */}
      <div className="flex flex-wrap gap-2 mb-4">
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
        {selectedModel && (
          <StatusBadge
            status="online"
            label={selectedModel}
          />
        )}
      </div>

      {/* Primary Action Button */}
      <div className="flex gap-3">
        <button
          onClick={onLaunch}
          disabled={!canLaunch}
          className={`flex-1 px-6 py-3 rounded-lg font-medium transition-all duration-200 ${
            canLaunch
              ? 'bg-gradient-to-r from-accent-primary to-accent-tertiary hover:from-accent-primary-hover hover:to-accent-tertiary text-white shadow-lg shadow-accent-primary/20 hover:shadow-accent-primary/30'
              : 'bg-bg-tertiary text-text-secondary cursor-not-allowed'
          }`}
        >
          {actionLoading && actionStatus === 'launching' ? (
            <span className="flex items-center justify-center gap-2">
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
          className={`flex-1 px-6 py-3 rounded-lg font-medium transition-all duration-200 ${
            canStop
              ? 'bg-status-error/10 text-status-error border border-status-error/20 hover:bg-status-error/20'
              : 'bg-bg-tertiary text-text-secondary border border-border-primary cursor-not-allowed'
          }`}
        >
          {actionLoading && actionStatus === 'stopping' ? (
            <span className="flex items-center justify-center gap-2">
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
        <div className={`mt-3 px-4 py-2 rounded-lg text-sm ${
          actionStatus === 'success'
            ? 'bg-status-success/10 text-status-success border border-status-success/20'
            : actionStatus === 'error'
            ? 'bg-status-error/10 text-status-error border border-status-error/20'
            : 'bg-bg-secondary/50 text-text-secondary border border-border-subtle'
        }`}>
          {actionMessage}
        </div>
      )}
    </div>
  );
};

export default SystemOverview;
