import React from 'react';
import StatusBadge from './StatusBadge';
import {
  Globe,
  Zap,
  Router,
  Server,
  RefreshCw,
} from 'lucide-react';

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
}

const SystemOverview: React.FC<SystemOverviewProps> = ({
  endpointHealth,
  healthLoading,
  routerStatus,
  selectedModel,
  onRefresh,
}) => {
  const isRunning = routerStatus?.running;
  const lastChecked = Date.now();

  const healthCards = [
    {
      key: 'gateway' as const,
      label: 'Gateway',
      icon: Globe,
      status: endpointHealth.gateway,
      loadingLabel: 'Checking Gateway...',
      onlineLabel: 'Gateway Online',
      offlineLabel: 'Gateway Offline',
      unknownLabel: 'Gateway Unknown',
    },
    {
      key: 'llama' as const,
      label: 'Direct',
      icon: Zap,
      status: endpointHealth.llama,
      loadingLabel: 'Checking Direct...',
      onlineLabel: 'Direct Online',
      offlineLabel: 'Direct Offline',
      unknownLabel: 'Direct Unknown',
    },
    {
      key: 'router' as const,
      label: 'Router',
      icon: Router,
      status: endpointHealth.router,
      loadingLabel: 'Checking Router...',
      onlineLabel: 'Router Online',
      offlineLabel: 'Router Offline',
      unknownLabel: 'Router Unknown',
    },
    {
      key: 'model' as const,
      label: 'Model',
      icon: Server,
      status: isRunning ? true : !routerStatus ? null : false,
      loadingLabel: 'Checking Model...',
      onlineLabel: 'Model Running',
      offlineLabel: 'Model Stopped',
      unknownLabel: 'Model Unknown',
    },
  ];

  const getStatus = (status: boolean | null): 'online' | 'offline' | 'loading' | 'unknown' => {
    if (status === true) return 'online';
    if (status === false) return 'offline';
    return 'unknown';
  };

  return (
    <div className="bg-bg-card rounded-xl border border-border-primary p-5 shadow-card">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-base font-semibold text-text-primary">System Health</h2>
          {selectedModel && (
            <StatusBadge status="online" label={selectedModel} />
          )}
        </div>
        <button
          onClick={onRefresh}
          disabled={healthLoading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border-primary text-text-secondary hover:bg-bg-tertiary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${healthLoading ? 'animate-spin' : ''}`} />
          {healthLoading ? 'Checking...' : 'Refresh'}
        </button>
      </div>

      {/* Health Cards Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        {healthCards.map(({ label, icon: Icon, status, loadingLabel, onlineLabel, offlineLabel, unknownLabel }) => (
          <div
            key={label}
            className="flex items-center gap-3 px-3 py-2.5 bg-bg-secondary rounded-lg border border-border-subtle"
          >
            <Icon className="w-4 h-4 text-text-tertiary flex-shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-medium text-text-tertiary uppercase tracking-wider">{label}</p>
              <StatusBadge
                status={getStatus(status)}
                label={
                  status === null && healthLoading ? loadingLabel :
                  status === true ? onlineLabel :
                  status === false ? offlineLabel :
                  unknownLabel
                }
                size="sm"
              />
            </div>
          </div>
        ))}
      </div>

      {/* Model Info Bar */}
      {selectedModel && (
        <div className="flex items-center justify-between px-3 py-2 bg-bg-secondary/50 rounded-lg border border-border-subtle">
          <div className="flex items-center gap-2">
            <Server className="w-3.5 h-3.5 text-text-tertiary" />
            <span className="text-xs text-text-secondary">Selected:</span>
            <span className="text-xs font-mono text-text-primary truncate max-w-[200px]" title={selectedModel}>
              {selectedModel}
            </span>
          </div>
          <span className="text-[10px] text-text-tertiary">
            Last checked: {new Date(lastChecked).toLocaleTimeString()}
          </span>
        </div>
      )}
    </div>
  );
};

export default SystemOverview;
