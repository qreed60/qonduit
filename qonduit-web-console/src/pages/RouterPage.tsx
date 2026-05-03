import React, { useState, useEffect } from 'react';
import { getSettings } from '../services/api';
import { getRouterStatus, fetchRouterModels } from '../services/api';
import { ENDPOINTS } from '../config/endpoints';
import {
  Cpu,
  AlertCircle,
  CheckCircle2,
  Loader2,
  ExternalLink,
} from 'lucide-react';

const RouterPage: React.FC = () => {
  const settings = getSettings();
  const mode = settings.endpointMode;
  const [routerStatus, setRouterStatus] = useState<{ running: boolean; exists: boolean } | null>(null);
  const [routerModels, setRouterModels] = useState<Array<{ name: string; path: string }>>([]);
  const [suggestedCtx, setSuggestedCtx] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const status = await getRouterStatus();
        setRouterStatus({ running: status.running, exists: status.exists });

        try {
          const data = await fetchRouterModels();
          setRouterModels(data.models || []);
          if (data.suggested_ctx) setSuggestedCtx(data.suggested_ctx);
        } catch { /* models may not be available */ }
      } catch {
        // Router may not be available
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    // Refresh every 10 seconds
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-xl font-bold bg-gradient-to-r from-accent-primary to-accent-tertiary bg-clip-text text-transparent">
          Router
        </h2>
        <p className="text-sm text-text-secondary mt-1">
          Launch and manage local GGUF models via the Qonduit Router
        </p>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto space-y-4">
        {/* Router Status */}
        <div className="bg-bg-card rounded-xl border border-border-primary p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-text-primary">Router Status</h3>
            {loading ? (
              <Loader2 className="w-4 h-4 text-text-tertiary animate-spin" />
            ) : routerStatus ? (
              <div className="flex items-center gap-2">
                {routerStatus.running ? (
                  <>
                    <CheckCircle2 className="w-4 h-4 text-status-success" />
                    <span className="text-xs font-medium text-status-success">Running</span>
                  </>
                ) : routerStatus.exists ? (
                  <>
                    <AlertCircle className="w-4 h-4 text-status-warning" />
                    <span className="text-xs font-medium text-status-warning">Stopped</span>
                  </>
                ) : (
                  <>
                    <AlertCircle className="w-4 h-4 text-text-tertiary" />
                    <span className="text-xs font-medium text-text-tertiary">Not Found</span>
                  </>
                )}
              </div>
            ) : (
              <span className="text-xs text-text-tertiary">Unknown</span>
            )}
          </div>
          <div className="bg-bg-secondary/50 rounded-lg p-3 border border-border-subtle">
            <p className="text-xs text-text-secondary mb-1">Router Endpoint</p>
            <p className="text-xs font-mono text-text-primary break-all">{ENDPOINTS.router[mode]}</p>
          </div>
        </div>

        {/* Model Info */}
        <div className="bg-bg-card rounded-xl border border-border-primary p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-text-primary">Launchable Models</h3>
            {routerStatus?.running && (
              <span className="flex items-center gap-1 text-[10px] font-medium text-status-success bg-status-success/10 px-2 py-0.5 rounded-full">
                <Cpu className="w-3 h-3" />
                Model Active
              </span>
            )}
          </div>
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 text-text-tertiary animate-spin" />
            </div>
          ) : routerModels.length > 0 ? (
            <div className="space-y-2">
              {routerModels.map((model) => (
                <div
                  key={model.name}
                  className="flex items-center justify-between bg-bg-secondary/50 rounded-lg p-3 border border-border-subtle"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-mono text-text-primary truncate">{model.name}</p>
                    <p className="text-[10px] text-text-tertiary truncate mt-0.5">{model.path}</p>
                  </div>
                  <div className="flex items-center gap-2 ml-3 flex-shrink-0">
                    {suggestedCtx && (
                      <span className="flex items-center gap-1 text-[10px] text-accent-primary">
                        <Cpu className="w-3 h-3" />
                        ctx: {suggestedCtx}
                      </span>
                    )}
                    {routerStatus?.running && (
                      <span className="flex items-center gap-1 text-[10px] text-status-success bg-status-success/10 px-2 py-0.5 rounded-full">
                        <CheckCircle2 className="w-3 h-3" />
                        Running
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8">
              <p className="text-text-tertiary text-sm">No models available</p>
              <p className="text-text-tertiary/60 text-xs mt-1">Add GGUF files to the Router's model directory</p>
            </div>
          )}
        </div>

        {/* Launch Instructions */}
        <div className="bg-bg-card rounded-xl border border-border-primary p-5">
          <h3 className="text-sm font-semibold text-text-primary mb-3">How to Launch a Model</h3>
          <div className="space-y-2">
            <p className="text-xs text-text-secondary leading-relaxed">
              Router models are local GGUF files that can be launched and stopped via the Router API.
              They are separate from Gateway and Direct models which are used for chat inference.
            </p>
            <ol className="text-xs text-text-secondary space-y-1.5 list-decimal list-inside">
              <li>Go to the <span className="text-accent-primary font-medium">Dashboard</span></li>
              <li>Set <span className="text-accent-primary font-medium">Default Provider</span> to "Router" in Settings</li>
              <li>Select a model from the dropdown and click <span className="text-accent-primary font-medium">Launch Model</span></li>
            </ol>
            <a
              href="#/dashboard"
              className="inline-flex items-center gap-1.5 mt-3 px-3 py-1.5 bg-accent-primary/10 text-accent-primary border border-accent-primary/20 rounded-lg text-xs font-medium hover:bg-accent-primary/20 transition-colors"
            >
              <ExternalLink className="w-3 h-3" />
              Go to Dashboard
            </a>
          </div>
        </div>

        {/* Info */}
        <div className="bg-bg-card rounded-xl border border-border-primary p-5">
          <h3 className="text-sm font-semibold text-text-primary mb-3">About the Router</h3>
          <p className="text-xs text-text-secondary leading-relaxed">
            The Qonduit Router manages model lifecycle, handles intelligent request routing,
            and provides a unified API for chat completions. It runs as a containerized service
            on your local network. Router models are GGUF files that are launched/stopped via the
            Flask API, while Gateway and Direct models are used for inference through their respective endpoints.
          </p>
        </div>
      </div>
    </div>
  );
};

export default RouterPage;
