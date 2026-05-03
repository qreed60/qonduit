/**
 * Centralized endpoint configuration for Qonduit services.
 *
 * Each service defines local and public base URLs. The active mode
 * (local | public) is resolved at runtime via getEndpoint().
 */

export type EndpointMode = 'local' | 'public';

export const ENDPOINTS = {
  router: {
    local: 'http://127.0.0.1:5001',
    public: 'https://llmapi.qneural.org',
  },
  gateway: {
    local: 'http://127.0.0.1:8090',
    public: 'https://llmapi.qneural.org',
  },
  llama: {
    local: 'http://127.0.0.1:8080',
    public: 'https://llama.qneural.org',
  },
} as const;

export type EndpointKey = keyof typeof ENDPOINTS;

/**
 * Resolve the active base URL for a given endpoint based on the current mode.
 * Falls back to 'local' when no mode is set in localStorage.
 */
export function getEndpoint(key: EndpointKey): string {
  const mode = getMode();
  const urls = ENDPOINTS[key];
  return mode === 'public' ? urls.public : urls.local;
}

/**
 * Return the current endpoint mode from localStorage.
 */
export function getMode(): EndpointMode {
  const stored = localStorage.getItem('qonduit-endpoint-mode');
  return stored === 'public' ? 'public' : 'local';
}

/**
 * Persist the endpoint mode to localStorage.
 */
export function setMode(mode: EndpointMode): void {
  localStorage.setItem('qonduit-endpoint-mode', mode);
}

/**
 * Get the full API path for a service.
 */
export function apiPath(key: EndpointKey, path: string): string {
  const base = getEndpoint(key);
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${cleanPath}`;
}
