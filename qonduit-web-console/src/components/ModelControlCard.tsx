import React from 'react';
import StatusBadge from './StatusBadge';

interface ModelControlCardProps {
  routerStatus: { running: boolean; exists: boolean } | null;
  models: Array<{ name: string; path: string }>;
  selectedModel: string;
  ctxSize: number;
  suggestedCtx: number | null;
  onSelectModel: (name: string) => void;
  onCtxChange: (size: number) => void;
  onLaunch: () => void;
  onStop: () => void;
  loading: boolean;
  actionStatus: 'idle' | 'launching' | 'stopping' | 'success' | 'error';
  actionMessage: string;
}

const ModelControlCard: React.FC<ModelControlCardProps> = ({
  routerStatus,
  models,
  selectedModel,
  ctxSize,
  suggestedCtx,
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
    <div className="bg-bg-card rounded-xl border border-border-primary p-6 shadow-card">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-lg font-semibold text-text-primary">Model Control</h3>
          <p className="text-sm text-text-secondary mt-1">
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
          <label className="block text-sm font-medium text-text-secondary mb-2">
            Select Model
          </label>
          <select
            value={selectedModel}
            onChange={(e) => onSelectModel(e.target.value)}
            disabled={loading || isRunning}
            className="w-full px-4 py-3 bg-bg-secondary border border-border-primary rounded-lg text-text-primary focus:outline-none focus:border-accent-primary/50 focus:ring-1 focus:ring-accent-primary/50 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
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
          <label className="block text-sm font-medium text-text-secondary mb-2">
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
            className="w-full h-2 bg-bg-secondary rounded-lg appearance-none cursor-pointer accent-accent-primary disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <div className="flex justify-between text-xs text-text-tertiary mt-1">
            <span>512</span>
            {suggestedCtx && (
              <span className="text-accent-primary">
                Suggested: {suggestedCtx}
              </span>
            )}
            <span>8192</span>
          </div>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex gap-4 mt-6">
        <button
          onClick={onLaunch}
          disabled={!canLaunch}
          className={`flex-1 px-6 py-3 rounded-lg font-medium transition-all duration-200 ${
            canLaunch
              ? 'bg-gradient-to-r from-accent-primary to-accent-tertiary hover:from-accent-primary-hover hover:to-accent-tertiary text-white shadow-lg shadow-accent-primary/20 hover:shadow-accent-primary/30'
              : 'bg-bg-tertiary text-text-secondary cursor-not-allowed'
          }`}
        >
          {loading && actionStatus === 'launching' ? (
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
          {loading && actionStatus === 'stopping' ? (
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
        <div className={`mt-4 px-4 py-3 rounded-lg text-sm ${
          actionStatus === 'success'
            ? 'bg-status-success/10 text-status-success border border-status-success/20'
            : actionStatus === 'error'
            ? 'bg-status-error/10 text-status-error border border-status-error/20'
            : 'bg-bg-secondary/50 text-text-secondary border border-border-subtle'
        }`}>
          {actionMessage}
        </div>
      )}

      {/* Empty State */}
      {models.length === 0 && !loading && (
        <div className="mt-4 text-center py-4">
          <p className="text-text-tertiary text-sm">No models available</p>
          <p className="text-text-tertiary/60 text-xs mt-1">Ensure the router API is running</p>
        </div>
      )}
    </div>
  );
};

export default ModelControlCard;
