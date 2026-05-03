export type EndpointMode = 'local' | 'public';

/**
 * The four providers the app can route requests through.
 * Each has a distinct responsibility:
 *   Gateway  — OpenAI-compatible chat & model listing
 *   Router   — Launch/stop local GGUF models via Flask API
 *   Direct   — Raw llama.cpp inference
 *   WebUI    — External Open WebUI link (no API calls)
 */
export type ProviderType = 'Gateway' | 'Router' | 'Direct' | 'WebUI';

export interface EndpointOverrides {
  gateway?: string;
  router?: string;
  llama?: string;
  webui?: string;
}

export interface Settings {
  apiKey: string;
  defaultModel: string;
  defaultProvider: ProviderType;
  endpointMode: EndpointMode;
}

export interface Model {
  id: string;
  object: string;
  created: number;
  owned_by: string;
}

export interface ModelsResponse {
  object: string;
  data: Model[];
}

export interface ApiError {
  message: string;
  type: string;
  code?: number;
}

export type Page = 'dashboard' | 'chat' | 'models' | 'router' | 'diagnostics' | 'settings';

/**
 * Chat message used for the /v1/chat/completions API.
 */
export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

/**
 * Shared provider state for tracking loading, errors, and health
 * across all four providers.
 */
export interface ProviderState {
  gateway: {
    models: Model[];
    loading: boolean;
    error: string | null;
    health: boolean | null;
  };
  router: {
    models: Array<{ name: string; path: string }>;
    status: { running: boolean; exists: boolean } | null;
    loading: boolean;
    error: string | null;
    health: boolean | null;
  };
  direct: {
    models: Model[];
    loading: boolean;
    error: string | null;
    health: boolean | null;
  };
}

/**
 * Default provider state — all providers start unloaded.
 */
export const DEFAULT_PROVIDER_STATE: ProviderState = {
  gateway: { models: [], loading: false, error: null, health: null },
  router:  { models: [], status: null, loading: false, error: null, health: null },
  direct:  { models: [], loading: false, error: null, health: null },
};

/**
 * A model that can be selected in the UI, regardless of provider.
 * Router models carry a `path`; Gateway/Direct models do not.
 */
export interface SelectableModel {
  name: string;
  path?: string;
}
