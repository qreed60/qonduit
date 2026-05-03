import React, { useState } from 'react';
import StatusBadge from './StatusBadge';
import { ProviderType, SelectableModel } from '../types';
import {
  Play,
  Square,
  Cpu,
  AlertCircle,
  CheckCircle2,
  Info,
} from 'lucide-react';

interface ModelControlCardProps {
  routerStatus: { running: boolean; exists: boolean } | null;
  models: SelectableModel[];
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
  /** Which provider is currently selected */
  provider?: ProviderType;
  /** Error message from provider model loading */
  providerModelsError?: string | null;
}

/**
 * Context size presets supporting up to 262k context models.
 */
const PRESET_CTX = [4096, 8192, 16384, 32768, 65536, 131072, 262144];
const SLIDER_MIN = 512;
const SLIDER_MAX = 262144;
const SLIDER_STEP = 512;

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
  provider = 'Router',
  providerModelsError,
}) => {
  const isRouterProvider = provider === 'Router';
  const isRunning = routerStatus?.running;
  // Launch/stop only works for Router provider
  const canLaunch = isRouterProvider && !isRunning && !loading && models.length > 0 && !!selectedModel;
  const canStop = isRouterProvider && isRunning && !loading;

  const [customCtxInput, setCustomCtxInput] = useState('');

  const handlePresetClick = (ctx: number) => {
    onCtxChange(ctx);
    setCustomCtxInput('');
  };

  const handleCustomCtxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setCustomCtxInput(e.target.value);
  };

  const handleCustomCtxSubmit = () => {
    const val = parseInt(customCtxInput, 10);
    if (!isNaN(val) && val >= SLIDER_MIN && val <= SLIDER_MAX && val % SLIDER_STEP === 0) {
      onCtxChange(val);
      setCustomCtxInput('');
    }
  };

  const handleCustomCtxKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      handleCustomCtxSubmit();
    }
  };

  const isPreset = PRESET_CTX.includes(ctxSize);

  // Determine why launch might be disabled
  const launchDisabledReason = () => {
    if (!isRouterProvider) return 'Launch/stop is only available for Router provider';
    if (!routerStatus) return 'Router not available';
    if (isRunning) return 'Model is already running';
    if (loading) return 'Action in progress';
    if (models.length === 0) return 'No models available';
    if (!selectedModel) return 'No model selected';
    return '';
  };

  return (
    <div className="bg-bg-card rounded-xl border border-border-primary p-5 shadow-card">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h3 className="text-base font-semibold text-text-primary">
            {isRouterProvider ? 'Router Model Control' : `${provider} Model Selection`}
          </h3>
          <p className="text-xs text-text-secondary mt-0.5">
            {isRouterProvider
              ? (isRunning ? 'Model is currently running' : models.length > 0 ? 'Select and launch a model' : 'No models available')
              : (models.length > 0 ? `${models.length} model(s) available` : 'No models available')
            }
          </p>
        </div>
        <StatusBadge
          status={isRouterProvider ? (isRunning ? 'online' : !routerStatus ? 'unknown' : 'offline') : (models.length > 0 ? 'online' : 'offline')}
          label={isRouterProvider
            ? (isRunning ? 'Running' : !routerStatus ? 'Checking...' : 'Stopped')
            : (models.length > 0 ? 'Available' : 'Unavailable')
          }
        />
      </div>

      {/* Model Selection */}
      <div className="mb-4">
        <label className="block text-xs font-medium text-text-secondary mb-1.5">
          Select Model
        </label>
        <select
          value={selectedModel}
          onChange={(e) => onSelectModel(e.target.value)}
          disabled={loading || isRunning || !isRouterProvider}
          className="w-full px-4 py-2.5 bg-bg-secondary border border-border-primary rounded-lg text-text-primary text-sm focus:outline-none focus:border-accent-primary/50 focus:ring-1 focus:ring-accent-primary/50 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
        >
          <option value="">Choose a model...</option>
          {models.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}{m.path ? ` (${m.path.split('/').pop()})` : ''}
            </option>
          ))}
        </select>
        {models.length === 0 && !loading && (
          <p className="text-xs text-text-tertiary mt-1.5 flex items-center gap-1">
            <AlertCircle className="w-3 h-3" />
            {providerModelsError || (isRouterProvider ? 'Router API unavailable — check connection' : `${provider} models unavailable`)}
          </p>
        )}
      </div>

      {/* Context Size */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label className="block text-xs font-medium text-text-secondary">
            Context Size
          </label>
          <span className="text-xs font-mono text-accent-primary font-semibold">{ctxSize.toLocaleString()}</span>
        </div>

        {/* Preset Buttons */}
        <div className="flex gap-1.5 mb-2">
          {PRESET_CTX.map((ctx) => (
            <button
              key={ctx}
              onClick={() => handlePresetClick(ctx)}
              disabled={loading || isRunning}
              className={`flex-1 px-2 py-1.5 rounded-md text-xs font-medium transition-all duration-200 ${
                ctxSize === ctx
                  ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/30'
                  : 'bg-bg-secondary border border-border-subtle text-text-tertiary hover:text-text-primary hover:border-border-primary disabled:opacity-50 disabled:cursor-not-allowed'
              }`}
            >
              {ctx >= 1000 ? `${ctx / 1000}k` : ctx}
            </button>
          ))}
        </div>

        {/* Custom Input */}
        <div className="flex gap-2 mb-2">
          <input
            type="number"
            min={SLIDER_MIN}
            max={SLIDER_MAX}
            step={SLIDER_STEP}
            value={customCtxInput}
            onChange={handleCustomCtxChange}
            onKeyDown={handleCustomCtxKeyDown}
            placeholder="Custom (multiple of 512)"
            disabled={loading || isRunning}
            className="flex-1 px-3 py-1.5 bg-bg-secondary border border-border-primary rounded-md text-xs text-text-primary focus:outline-none focus:border-accent-primary/50 disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <button
            onClick={handleCustomCtxSubmit}
            disabled={loading || isRunning || !customCtxInput}
            className="px-3 py-1.5 bg-bg-secondary border border-border-primary rounded-md text-xs font-medium text-text-secondary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200"
          >
            Apply
          </button>
        </div>

        {/* Slider */}
        <div className="relative">
          <input
            type="range"
            min={String(SLIDER_MIN)}
            max={String(SLIDER_MAX)}
            step={String(SLIDER_STEP)}
            value={ctxSize}
            onChange={(e) => onCtxChange(Number(e.target.value))}
            disabled={loading || isRunning}
            className="w-full h-1.5 bg-bg-secondary rounded-lg appearance-none cursor-pointer accent-accent-primary disabled:opacity-50 disabled:cursor-not-allowed"
          />
          {suggestedCtx && !isPreset && (
            <div className="flex items-center gap-1 mt-1">
              <Info className="w-3 h-3 text-accent-primary flex-shrink-0" />
              <span className="text-[10px] text-accent-primary">
                Suggested: {suggestedCtx}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex gap-3 mb-4">
        <button
          onClick={onLaunch}
          disabled={!canLaunch}
          className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg font-medium text-sm transition-all duration-200 ${
            canLaunch
              ? 'bg-gradient-to-r from-accent-primary to-accent-tertiary hover:from-accent-primary-hover hover:to-accent-tertiary text-white shadow-lg shadow-accent-primary/20 hover:shadow-accent-primary/30'
              : 'bg-bg-tertiary text-text-secondary border border-border-primary cursor-not-allowed'
          }`}
          title={launchDisabledReason()}
        >
          {loading && actionStatus === 'launching' ? (
            <>
              <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.825 3 7.938l3-2.647z" />
              </svg>
              <span>Launching...</span>
            </>
          ) : isRunning ? (
            <>
              <CheckCircle2 className="w-4 h-4" />
              <span>Running</span>
            </>
          ) : (
            <>
              <Play className="w-4 h-4" />
              <span>Launch Model</span>
            </>
          )}
        </button>
        <button
          onClick={onStop}
          disabled={!canStop}
          className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg font-medium text-sm transition-all duration-200 ${
            canStop
              ? 'bg-status-error/10 text-status-error border border-status-error/20 hover:bg-status-error/20'
              : 'bg-bg-tertiary text-text-secondary border border-border-primary cursor-not-allowed'
          }`}
          title={canStop ? 'Stop the running model' : 'Model is not running'}
        >
          {loading && actionStatus === 'stopping' ? (
            <>
              <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.825 3 7.938l3-2.647z" />
              </svg>
              <span>Stopping...</span>
            </>
          ) : (
            <>
              <Square className="w-4 h-4" />
              <span>Stop Model</span>
            </>
          )}
        </button>
      </div>

      {/* Action Message */}
      {actionMessage && (
        <div className={`flex items-start gap-2 px-3 py-2.5 rounded-lg text-xs ${
          actionStatus === 'success'
            ? 'bg-status-success/10 text-status-success border border-status-success/20'
            : actionStatus === 'error'
            ? 'bg-status-error/10 text-status-error border border-status-error/20'
            : 'bg-bg-secondary/50 text-text-secondary border border-border-subtle'
        }`}>
          {actionStatus === 'success' ? (
            <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          ) : actionStatus === 'error' ? (
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          ) : (
            <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          )}
          <span>{actionMessage}</span>
        </div>
      )}

      {/* Running State Display */}
      {isRunning && selectedModel && (
        <div className="mt-4 flex items-center gap-3 px-3 py-2.5 bg-status-success/5 rounded-lg border border-status-success/10">
          <Cpu className="w-4 h-4 text-status-success flex-shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-status-success">Model Running</p>
            <p className="text-[10px] text-text-tertiary truncate">{selectedModel} · ctx: {ctxSize.toLocaleString()}</p>
          </div>
        </div>
      )}
    </div>
  );
};

export default ModelControlCard;
