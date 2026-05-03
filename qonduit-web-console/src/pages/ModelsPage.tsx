import React, { useState, useEffect } from 'react';
import { Model } from '../types';
import { fetchGatewayModels, fetchDirectModels } from '../services/api';
import Toast from '../components/Toast';
import {
  RefreshCw,
  Copy,
  CheckCircle2,
  Globe,
  Zap,
  AlertCircle,
  Server,
} from 'lucide-react';

const ModelsPage: React.FC = () => {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    loadModels();
  }, []);

  const loadModels = async () => {
    setLoading(true);
    setError(null);
    setModels([]);

    try {
      const allModels: Model[] = [];

      // Fetch from gateway
      try {
        const gatewayModels = await fetchGatewayModels();
        allModels.push(...gatewayModels.map((m) => ({ ...m, id: `gateway:${m.id}` })));
      } catch (err) {
        console.log('Gateway models unavailable:', err);
      }

      // Fetch from direct
      try {
        const directModels = await fetchDirectModels();
        allModels.push(...directModels.map((m) => ({ ...m, id: `direct:${m.id}` })));
      } catch (err) {
        console.log('Direct models unavailable:', err);
      }

      setModels(allModels);
      if (allModels.length === 0) {
        setError('No models found from either gateway or direct endpoint');
      }
    } catch (err) {
      setError('Failed to load models. Check your network connection.');
    } finally {
      setLoading(false);
    }
  };

  const handleCopyId = async (id: string) => {
    try {
      await navigator.clipboard.writeText(id);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    } catch { /* ignore */ }
  };

  const getProvider = (id: string) => {
    return id.startsWith('gateway:') ? 'Gateway' : 'Direct';
  };

  const getProviderColor = (provider: string) => {
    return provider === 'Gateway'
      ? 'bg-accent-primary/10 text-accent-primary'
      : 'bg-accent-secondary/10 text-accent-secondary';
  };

  const getProviderIcon = (provider: string) => {
    return provider === 'Gateway' ? <Globe className="w-3.5 h-3.5" /> : <Zap className="w-3.5 h-3.5" />;
  };

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold bg-gradient-to-r from-accent-primary to-accent-tertiary bg-clip-text text-transparent">
            Available Models
          </h2>
          <p className="text-sm text-text-secondary mt-0.5">
            Models from Gateway and Direct endpoints
          </p>
        </div>
        <button
          onClick={loadModels}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 border border-border-primary text-text-secondary hover:bg-bg-tertiary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Models Grid */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 space-y-4">
            <div className="w-12 h-12 rounded-full border-4 border-accent-primary/20 border-t-accent-primary animate-spin" />
            <p className="text-text-secondary text-sm">Loading models...</p>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center h-64 text-center">
            <div className="w-14 h-14 bg-status-error/10 rounded-full flex items-center justify-center mb-4">
              <AlertCircle className="w-7 h-7 text-status-error" />
            </div>
            <p className="text-status-error font-medium mb-2">Unable to Load Models</p>
            <p className="text-text-secondary max-w-md text-sm">{error}</p>
            <button
              onClick={loadModels}
              className="mt-4 px-4 py-2 rounded-lg text-sm font-medium bg-accent-primary/10 text-accent-primary border border-accent-primary/20 hover:bg-accent-primary/20 transition-colors"
            >
              Try Again
            </button>
          </div>
        ) : models.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-center">
            <div className="w-14 h-14 bg-bg-tertiary rounded-full flex items-center justify-center mb-4">
              <Server className="w-7 h-7 text-text-tertiary" />
            </div>
            <p className="text-text-secondary">No models available</p>
            <button
              onClick={loadModels}
              className="mt-4 text-accent-primary hover:text-accent-primary-hover font-medium text-sm"
            >
              Try refreshing
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {models.map((model) => {
              const provider = getProvider(model.id);
              const cleanId = model.id.replace(/^gateway:|^direct:/, '');
              return (
                <div
                  key={model.id}
                  className="bg-bg-card border border-border-primary rounded-xl p-4 hover:border-accent-primary/30 transition-all duration-200 group"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <div className={`px-2 py-0.5 rounded text-xs font-medium flex items-center gap-1 ${getProviderColor(provider)}`}>
                        {getProviderIcon(provider)}
                        {provider}
                      </div>
                      <span className="text-[10px] text-text-tertiary">
                        {new Date(model.created * 1000).toLocaleDateString()}
                      </span>
                    </div>
                  </div>

                  <div className="bg-bg-secondary/50 rounded-lg p-2.5 border border-border-subtle mb-3">
                    <p className="text-xs font-mono text-text-primary truncate" title={cleanId}>
                      {cleanId}
                    </p>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 text-xs text-text-tertiary">
                      <span>{model.object}</span>
                      <span>·</span>
                      <span>{model.owned_by}</span>
                    </div>
                    <button
                      onClick={() => handleCopyId(model.id)}
                      className="p-1 rounded hover:bg-bg-tertiary text-text-tertiary hover:text-text-primary transition-all duration-200 opacity-0 group-hover:opacity-100"
                      title="Copy ID"
                    >
                      {copiedId === model.id ? (
                        <CheckCircle2 className="w-3.5 h-3.5 text-status-success" />
                      ) : (
                        <Copy className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {toastMessage && (
        <Toast
          message={toastMessage}
          type="info"
          onClose={() => setToastMessage(null)}
        />
      )}
    </div>
  );
};

export default ModelsPage;
