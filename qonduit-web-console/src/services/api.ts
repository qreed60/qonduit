import { Settings, ModelsResponse, Model, ProviderType, ChatMessage } from '../types';
import { getMode, apiPath } from '../config/endpoints';

const DEFAULT_SETTINGS: Settings = {
  apiKey: 'local',
  defaultModel: 'Qwen3-Coder-Next-IQ4_NL.gguf',
  defaultProvider: 'Direct',
  endpointMode: 'local',
};

/**
 * Migrate old localStorage settings that contain the deprecated URL fields.
 * If the stored settings have gatewayBaseUrl/directBaseUrl/routerBaseUrl,
 * strip them out and save the cleaned version with the new endpointMode.
 */
function migrateSettings(): Settings {
  const raw = localStorage.getItem('qonduit-settings');
  if (!raw) return DEFAULT_SETTINGS;

  try {
    const parsed: Record<string, unknown> = JSON.parse(raw);
    const hasOldFields =
      'gatewayBaseUrl' in parsed ||
      'directBaseUrl' in parsed ||
      'routerBaseUrl' in parsed;

    if (hasOldFields) {
      const { gatewayBaseUrl, directBaseUrl, routerBaseUrl, ...clean } = parsed;
      const migrated: Settings = { ...DEFAULT_SETTINGS, ...clean, endpointMode: getMode() };
      localStorage.setItem('qonduit-settings', JSON.stringify(migrated));
      return migrated;
    }
  } catch {
    // If JSON is malformed, fall through to defaults
  }

  const saved: Partial<Settings> = JSON.parse(raw);
  return { ...DEFAULT_SETTINGS, ...saved, endpointMode: getMode() };
}

export function getSettings(): Settings {
  return migrateSettings();
}

export function saveSettings(settings: Settings): void {
  localStorage.setItem('qonduit-settings', JSON.stringify(settings));
}

// ── Internal helpers (kept for backward compatibility) ──────────────────────

/**
 * Fetch models from the Memory Gateway (OpenAI-compatible /v1/models).
 */
export async function fetchGatewayModels(): Promise<Model[]> {
  const response = await fetch(apiPath('gateway', '/v1/models'));
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data: ModelsResponse = await response.json();
  return data.data;
}

/**
 * Fetch models from the direct llama.cpp endpoint (OpenAI-compatible /v1/models).
 */
export async function fetchDirectModels(): Promise<Model[]> {
  const response = await fetch(apiPath('llama', '/v1/models'));
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data: ModelsResponse = await response.json();
  return data.data;
}

/**
 * Fetch the list of GGUF models from the Flask router API.
 * Returns raw shape — not a standard OpenAI /v1/models response.
 */
export async function fetchRouterModels(): Promise<{ models: Array<{ name: string; path: string }>; suggested_ctx: number }> {
  const response = await fetch(apiPath('router', '/api/v1/qonduit-router/models'));
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data = await response.json();
  return { models: data.models ?? [], suggested_ctx: data.suggested_ctx ?? 8192 };
}

// ── Unified provider model fetcher ──────────────────────────────────────────

/**
 * Router models response shape — different from OpenAI /v1/models.
 */
export interface RouterModelsResponse {
  models: Array<{ name: string; path: string }>;
  suggested_ctx: number;
}

/**
 * Fetch models from the correct endpoint based on the provider type.
 *
 * | Provider | Endpoint                              | Response shape       |
 * |----------|---------------------------------------|----------------------|
 * | Gateway  | gatewayBase + /v1/models              | OpenAI ModelsResponse|
 * | Router   | routerBase + /api/v1/qonduit-router/models | RouterModelsResponse |
 * | Direct   | llamaBase + /v1/models                | OpenAI ModelsResponse|
 * | WebUI    | N/A — external link only              | Empty array          |
 */
export async function fetchProviderModels(provider: ProviderType): Promise<Model[] | RouterModelsResponse> {
  switch (provider) {
    case 'Gateway':
      return fetchGatewayModels();
    case 'Direct':
      return fetchDirectModels();
    case 'Router':
      return fetchRouterModels();
    case 'WebUI':
      return { models: [], suggested_ctx: 8192 };
    default:
      return { models: [], suggested_ctx: 8192 };
  }
}

// ── Chat completions ────────────────────────────────────────────────────────

/**
 * Send a chat request to the Gateway's /v1/chat/completions endpoint.
 *
 * @param model   — Model ID to use for completion
 * @param messages — Array of chat messages
 * @param ctxSize — Context size (optional, defaults to 8192)
 */
export async function fetchChatCompletions(
  model: string,
  messages: ChatMessage[],
  ctxSize: number = 8192,
): Promise<{ choices: Array<{ message: ChatMessage; finish_reason: string | null }> }> {
  const response = await fetch(apiPath('gateway', '/v1/chat/completions'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      messages,
      temperature: 0.7,
      max_tokens: 4096,
      stream: false,
      // Pass context size if the backend supports it
      ...(ctxSize !== 8192 && { context_size: ctxSize }),
    }),
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`HTTP ${response.status}: ${body}`);
  }
  return response.json();
}

// ── Health checks ───────────────────────────────────────────────────────────

/**
 * Test whether an endpoint is reachable by hitting its /health path.
 */
export async function testEndpoint(key: 'gateway' | 'llama' | 'router' | 'webui'): Promise<boolean> {
  try {
    const response = await fetch(apiPath(key, '/health'), { method: 'HEAD' });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Test the WebUI health endpoint (Open WebUI typically uses /health).
 */
export async function testWebuiEndpoint(): Promise<boolean> {
  try {
    const response = await fetch(apiPath('webui', '/health'), { method: 'HEAD' });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Test the router health endpoint (POST /health on the Flask router API).
 */
export async function testRouterHealth(): Promise<boolean> {
  try {
    const response = await fetch(apiPath('router', '/api/v1/qonduit-router/health'), { method: 'GET' });
    return response.ok;
  } catch {
    return false;
  }
}

// ── Router control ──────────────────────────────────────────────────────────

/**
 * Launch a model via the Flask router API.
 */
export async function launchModel(modelName: string, ctxSize: number): Promise<{ ok: boolean; message: string }> {
  const response = await fetch(apiPath('router', '/api/v1/qonduit-router/launch'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: modelName, context_size: ctxSize }),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * Stop the llama_server container.
 */
export async function stopModel(): Promise<{ ok: boolean; message: string }> {
  const response = await fetch(apiPath('router', '/api/v1/qonduit-router/stop'), { method: 'POST' });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * Get the router status (container running/exists).
 */
export async function getRouterStatus(): Promise<{
  running: boolean;
  exists: boolean;
}> {
  const response = await fetch(apiPath('router', '/api/v1/qonduit-router/status'));
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * Get the suggested context size for a model.
 */
export async function suggestContext(modelName: string): Promise<{ suggested_ctx: number }> {
  const response = await fetch(`${apiPath('router', '/api/v1/qonduit-router/context/suggest')}?model=${encodeURIComponent(modelName)}`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * Probe the llama.cpp /health endpoint directly.
 */
export async function llamaReady(): Promise<boolean> {
  try {
    const response = await fetch(apiPath('llama', '/health'));
    return response.ok;
  } catch {
    return false;
  }
}

// ── Logs streaming ──────────────────────────────────────────────────────────

/**
 * Stream logs from the router API (SSE via fetch ReadableStream).
 */
export async function* streamLogs(onMessage: (line: string) => void): AsyncIterable<void> {
  const response = await fetch(apiPath('router', '/api/v1/qonduit-router/logs'));
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (line.trim()) onMessage(line.trim());
      }
    }
  } finally {
    reader.releaseLock();
  }
}
