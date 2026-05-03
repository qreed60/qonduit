/**
 * Centralized endpoint configuration for Qonduit services.
 *
 * Each service defines local and public base URLs. The active mode
 * (local | public) is resolved at runtime via getEndpoint().
 *
 * Local LAN mode uses 192.168.5.5 (the LLM server).
 * Public mode uses the deployed qneural.org domains.
 *
 * Vite env overrides (VITE_QONDUIT_*_BASE) take precedence over defaults.
 */

export type EndpointMode = 'local' | 'public';

// ── Local LAN defaults ──────────────────────────────────────────────────────

const LOCAL_DEFAULTS = {
  gatewayBase: 'http://192.168.5.5:8090',
  routerBase: 'http://192.168.5.5:5001',
  llamaBase: 'http://192.168.5.5:8080',
  webuiBase: 'http://192.168.5.5:3000',
};

// ── Public / reverse-proxy defaults ─────────────────────────────────────────

const PUBLIC_DEFAULTS = {
  gatewayBase: 'https://memory.qneural.org',
  routerBase: 'https://router.qneural.org',
  llamaBase: 'https://llama.qneural.org',
  webuiBase: 'https://openai.qneural.org',
};

// ── Vite env overrides (set at build time) ──────────────────────────────────

function envOverride(key: string): string | undefined {
  return import.meta.env[key] as string | undefined;
}

// ── Resolved endpoint sets ──────────────────────────────────────────────────

const LOCAL_ENDPOINTS = {
  gatewayBase: envOverride('VITE_QONDUIT_GATEWAY_BASE') ?? LOCAL_DEFAULTS.gatewayBase,
  routerBase: envOverride('VITE_QONDUIT_ROUTER_BASE') ?? LOCAL_DEFAULTS.routerBase,
  llamaBase: envOverride('VITE_QONDUIT_LLAMA_BASE') ?? LOCAL_DEFAULTS.llamaBase,
  webuiBase: envOverride('VITE_QONDUIT_WEBUI_BASE') ?? LOCAL_DEFAULTS.webuiBase,
};

const PUBLIC_ENDPOINTS = {
  gatewayBase: envOverride('VITE_QONDUIT_GATEWAY_BASE') ?? PUBLIC_DEFAULTS.gatewayBase,
  routerBase: envOverride('VITE_QONDUIT_ROUTER_BASE') ?? PUBLIC_DEFAULTS.routerBase,
  llamaBase: envOverride('VITE_QONDUIT_LLAMA_BASE') ?? PUBLIC_DEFAULTS.llamaBase,
  webuiBase: envOverride('VITE_QONDUIT_WEBUI_BASE') ?? PUBLIC_DEFAULTS.webuiBase,
};

// ── ENDPOINTS map (backward-compatible keys) ────────────────────────────────

export const ENDPOINTS = {
  gateway: {
    local: LOCAL_ENDPOINTS.gatewayBase,
    public: PUBLIC_ENDPOINTS.gatewayBase,
  },
  router: {
    local: LOCAL_ENDPOINTS.routerBase,
    public: PUBLIC_ENDPOINTS.routerBase,
  },
  llama: {
    local: LOCAL_ENDPOINTS.llamaBase,
    public: PUBLIC_ENDPOINTS.llamaBase,
  },
  webui: {
    local: LOCAL_ENDPOINTS.webuiBase,
    public: PUBLIC_ENDPOINTS.webuiBase,
  },
} as const;

export type EndpointKey = keyof typeof ENDPOINTS;

// ── Named accessors (preferred for new code) ────────────────────────────────

export const GATEWAY_BASE = ENDPOINTS.gateway;
export const ROUTER_BASE = ENDPOINTS.router;
export const LLAMA_BASE = ENDPOINTS.llama;
export const WEBUI_BASE = ENDPOINTS.webui;

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
