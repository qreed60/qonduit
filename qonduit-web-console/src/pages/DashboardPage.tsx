import React, { useState, useEffect } from 'react';
import { getSettings, fetchProviderModels, launchModel as apiLaunchModel, stopModel as apiStopModel, testEndpoint, testRouterHealth, testWebuiEndpoint, getRouterStatus } from '../services/api';
import { Settings, ProviderType, SelectableModel, Model } from '../types';
import { ENDPOINTS } from '../config/endpoints';
import StatusBar from '../components/StatusBar';
import Toast from '../components/Toast';
import EndpointCard from '../components/EndpointCard';
import ModelControlCard from '../components/ModelControlCard';
import LogsPanel from '../components/LogsPanel';
import SystemOverview from '../components/SystemOverview';
import ComingSoon from '../components/ComingSoon';
import {
  BookOpen,
  Settings2,
  Wrench,
  BarChart3,
  Shield,
  Database,
} from 'lucide-react';

const DashboardPage: React.FC = () => {
  const [settings] = useState<Settings>(getSettings());
  const [endpointHealth, setEndpointHealth] = useState<{
    gateway: boolean | null;
    llama: boolean | null;
    router: boolean | null;
    webui: boolean | null;
  }>({ gateway: null, llama: null, router: null, webui: null });
  const [healthLoading, setHealthLoading] = useState(false);
  const [routerStatus, setRouterStatus] = useState<{
    running: boolean;
    exists: boolean;
  } | null>(null);
  const [routerModels, setRouterModels] = useState<Array<{ name: string; path: string }>>([]);
  const [providerModels, setProviderModels] = useState<SelectableModel[]>([]);
  const [providerModelsError, setProviderModelsError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState('');
  const [ctxSize, setCtxSize] = useState(4096);
  const [suggestedCtx, setSuggestedCtx] = useState<number | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionStatus, setActionStatus] = useState<'idle' | 'launching' | 'stopping' | 'success' | 'error'>('idle');
  const [actionMessage, setActionMessage] = useState('');
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Fetch initial data
  useEffect(() => {
    fetchDashboardData();
  }, []);

  const fetchDashboardData = async () => {
    // Fetch router status (always, for health card and logs)
    try {
      const status = await getRouterStatus();
      setRouterStatus({ running: status.running, exists: status.exists });
    } catch {
      // Router might not be available
    }

    // Fetch router models (for launch/stop when provider is Router)
    try {
      const data = await fetchProviderModels('Router');
      if (Array.isArray(data)) {
        // Gateway/Direct returned Model[] — not expected here
        setRouterModels([]);
      } else {
        setRouterModels(data.models || []);
        if (data.suggested_ctx) {
          setSuggestedCtx(data.suggested_ctx);
          if (!selectedModel) {
            setCtxSize(data.suggested_ctx);
          }
        }
      }
    } catch (err) {
      // Router models might not be available
    }

    // Fetch provider models based on selected provider
    await fetchProviderModelsList(settings.defaultProvider);

    // Test endpoint health
    await testAllEndpoints();
  };

  const fetchProviderModelsList = async (provider: ProviderType) => {
      setProviderModelsError(null);
      try {
        const data = await fetchProviderModels(provider);
  
        if (provider === 'Router') {
          // Router returns { models, suggested_ctx }
          if (Array.isArray(data)) {
            // Unexpected: Router returned array instead of object
            setProviderModels([]);
          } else {
            setProviderModels(data.models.map((m) => ({ name: m.name, path: m.path })));
            if (data.suggested_ctx) {
              setSuggestedCtx(data.suggested_ctx);
            }
          }
        } else if (provider === 'WebUI') {
          setProviderModels([]);
        } else {
          // Gateway/Direct return Model[]
          const models = data as Model[];
          setProviderModels(models.map((m) => ({ name: m.id })));
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load models';
        setProviderModelsError(`${provider} models unavailable: ${msg}`);
        setProviderModels([]);
      }
    };

  // Re-fetch provider models when provider changes
  useEffect(() => {
    fetchProviderModelsList(settings.defaultProvider);
  }, [settings.defaultProvider]);

  const testAllEndpoints = async () => {
     setHealthLoading(true);
     try {
       const [gateway, llama, router, webui] = await Promise.all([
         testEndpoint('gateway').catch(() => false),
         testEndpoint('llama').catch(() => false),
         testRouterHealth().catch(() => false),
         testWebuiEndpoint().catch(() => false),
       ]);
       setEndpointHealth({ gateway, llama, router, webui });
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
    if (!selectedModel && providerModels.length > 0) {
      setSelectedModel(providerModels[0].name);
    }
  }, [providerModels, selectedModel]);

  const mode = settings.endpointMode;

  // Determine which models to show in ModelControlCard
  // Router provider → router models (launch/stop enabled)
  // Other providers → provider models (launch/stop disabled)
  const isRouterProvider = settings.defaultProvider === 'Router';
  const displayModels = isRouterProvider ? routerModels : providerModels;

  const comingSoonItems = [
     {
       icon: <BookOpen className="w-4 h-4" />,
       title: 'RAG Collections',
       description: 'Manage document collections for retrieval-augmented generation',
       category: 'ai' as const,
     },
     {
       icon: <Settings2 className="w-4 h-4" />,
       title: 'Gateway Settings',
       description: 'Configure memory gateway parameters and behavior',
       category: 'infra' as const,
     },
     {
       icon: <Wrench className="w-4 h-4" />,
       title: 'Tool Toggles',
       description: 'Enable tools and function calling for models',
       category: 'tools' as const,
     },
     {
       icon: <BarChart3 className="w-4 h-4" />,
       title: 'Usage Analytics',
       description: 'Track model usage, costs, and performance metrics',
       category: 'tools' as const,
     },
     {
       icon: <Shield className="w-4 h-4" />,
       title: 'Access Control',
       description: 'Manage user permissions and API key rotation',
       category: 'infra' as const,
     },
     {
       icon: <Database className="w-4 h-4" />,
       title: 'Vector Store',
       description: 'Configure and manage vector embeddings storage',
       category: 'ai' as const,
     },
   ];

  return (
    <div className="flex flex-col h-full bg-bg-primary">
      {/* Status Bar */}
      <StatusBar settings={settings} />

      {/* Dashboard Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Page Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold bg-gradient-to-r from-accent-primary to-accent-tertiary bg-clip-text text-transparent">
            Qonduit Control Center
          </h1>
          <p className="text-text-secondary mt-1">
            Monitor and manage your AI infrastructure
          </p>
        </div>

        {/* System Overview */}
         <div className="mb-6">
           <SystemOverview
             endpointHealth={endpointHealth}
             healthLoading={healthLoading}
             routerStatus={routerStatus}
             selectedModel={selectedModel}
             onRefresh={testAllEndpoints}
           />
         </div>

        {/* Endpoint Cards */}
         <div className="mb-6">
           <h2 className="text-lg font-semibold text-text-primary mb-4">Endpoints</h2>
           <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
             <EndpointCard
               name="Memory Gateway"
               icon="🌐"
               description="Chat completions & memory"
               url={ENDPOINTS.gateway[mode]}
               status={endpointHealth.gateway === true ? 'online' : endpointHealth.gateway === false ? 'offline' : healthLoading ? 'loading' : 'unknown'}
               onTest={() => {
                 setHealthLoading(true);
                 testEndpoint('gateway')
                   .then((result) => setEndpointHealth((prev) => ({ ...prev, gateway: result })))
                   .finally(() => setHealthLoading(false));
               }}
               testLoading={healthLoading}
             />
             <EndpointCard
                name="Direct (llama.cpp)"
                icon="⚡"
                description="Direct inference server"
                url={ENDPOINTS.llama[mode]}
                status={endpointHealth.llama === true ? 'online' : endpointHealth.llama === false ? 'offline' : healthLoading ? 'loading' : 'unknown'}
                externalUrl={ENDPOINTS.llama[mode]}
                onTest={() => {
                  setHealthLoading(true);
                  testEndpoint('llama')
                    .then((result) => setEndpointHealth((prev) => ({ ...prev, llama: result })))
                    .finally(() => setHealthLoading(false));
                }}
                testLoading={healthLoading}
              />
              <EndpointCard
                name="Router API"
                icon="🔀"
                description="Model router & container manager"
                url={ENDPOINTS.router[mode]}
                status={endpointHealth.router === true ? 'online' : endpointHealth.router === false ? 'offline' : healthLoading ? 'loading' : 'unknown'}
                onTest={() => {
                  setHealthLoading(true);
                  testRouterHealth()
                    .then((result) => setEndpointHealth((prev) => ({ ...prev, router: result })))
                    .finally(() => setHealthLoading(false));
                }}
                testLoading={healthLoading}
              />
              <EndpointCard
                name="Open WebUI"
                icon="💬"
                description="Web-based chat interface"
                url={ENDPOINTS.webui[mode]}
                status={endpointHealth.webui === true ? 'online' : endpointHealth.webui === false ? 'offline' : healthLoading ? 'loading' : 'unknown'}
                externalUrl={ENDPOINTS.webui[mode]}
                onTest={() => {
                  setHealthLoading(true);
                  testWebuiEndpoint()
                    .then((result) => setEndpointHealth((prev) => ({ ...prev, webui: result })))
                    .finally(() => setHealthLoading(false));
                }}
                testLoading={healthLoading}
              />
           </div>
         </div>

        {/* Model Control */}
        <div className="mb-6">
          <ModelControlCard
            routerStatus={routerStatus}
            models={displayModels}
            selectedModel={selectedModel}
            ctxSize={ctxSize}
            suggestedCtx={suggestedCtx}
            onSelectModel={setSelectedModel}
            onCtxChange={setCtxSize}
            onLaunch={handleLaunch}
            onStop={handleStop}
            loading={actionLoading}
            actionStatus={actionStatus}
            actionMessage={actionMessage}
            provider={settings.defaultProvider}
            providerModelsError={providerModelsError}
          />
        </div>

        {/* Live Logs */}
        <div className="mb-6">
          <LogsPanel routerStatus={routerStatus} />
        </div>

        {/* Coming Soon */}
        <div>
          <ComingSoon items={comingSoonItems} />
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
