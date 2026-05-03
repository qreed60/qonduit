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
 * Runtime overrides (stored in localStorage) take precedence over everything.
 */

export type EndpointMode = 'local' | 'public';
export type EndpointKey = 'gateway' | 'router' | 'llama' | 'webui';

// ── Local LAN defaults ──────────────────────────────────────────────────────

const LOCAL_DEFAULTS: Record<EndpointKey, string> = {
  gateway: 'http://192.168.5.5:8090',
  router: 'http://192.168.5.5:5001',
  llama: 'http://192.168.5.5:8080',
  webui: 'http://192.168.5.5:3000',
};

// ── Public / reverse-proxy defaults ─────────────────────────────────────────

const PUBLIC_DEFAULTS: Record<EndpointKey, string> = {
  gateway: 'https://memory.qneural.org',
  router: 'https://router.qneural.org',
  llama: 'https://llama.qneural.org',
  webui: 'https://openai.qneural.org',
};

// ── URL Validation ──────────────────────────────────────────────────────────

/**
 * Validate that a URL string is well-formed and has a proper protocol.
 */
export function validateEndpoint(url: string): { valid: boolean; error?: string } {
  if (!url || typeof url !== 'string') {
    return { valid: false, error: 'URL is required' };
  }

  // Check for malformed protocol (e.g., "https:/" instead of "https://")
  if (/^https?:\/[^/]/.test(url)) {
    return { valid: false, error: 'Malformed URL — missing slash in protocol' };
  }

  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return { valid: false, error: 'Protocol must be http:// or https://' };
    }
    if (!parsed.hostname) {
      return { valid: false, error: 'Missing hostname' };
    }
    return { valid: true };
  } catch {
    return { valid: false, error: 'Invalid URL format' };
  }
}

// ── Runtime Overrides (localStorage) ────────────────────────────────────────

const OVERRIDES_KEY = 'qonduit-endpoint-overrides';

interface EndpointOverrides {
  gateway?: string;
  router?: string;
  llama?: string;
  webui?: string;
}

function getOverrides(): EndpointOverrides {
  try {
    const raw = localStorage.getItem(OVERRIDES_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveOverrides(overrides: EndpointOverrides): void {
  localStorage.setItem(OVERRIDES_KEY, JSON.stringify(overrides));
}

/**
 * Set a runtime override for a specific endpoint.
 * Overrides persist in localStorage and survive mode switches.
 */
export function setEndpointOverride(key: EndpointKey, url: string): void {
  const overrides = getOverrides();
  if (url) {
    overrides[key] = url;
  } else {
    delete overrides[key];
  }
  saveOverrides(overrides);
}

/**
 * Clear all runtime endpoint overrides.
 */
export function clearEndpointOverrides(): void {
  localStorage.removeItem(OVERRIDES_KEY);
}

/**
 * Check if a specific endpoint has a runtime override.
 */
export function hasEndpointOverride(key: EndpointKey): boolean {
  return !!getOverrides()[key];
}

// ── Vite env overrides (set at build time) ──────────────────────────────────

function envOverride(key: string): string | undefined {
  return import.meta.env[key] as string | undefined;
}

// ── Resolved endpoint sets ──────────────────────────────────────────────────

function resolveEndpoints(): Record<EndpointKey, { local: string; public: string }> {
  const overrides = getOverrides();

  const local: Record<EndpointKey, string> = {} as Record<EndpointKey, string>;
  const pub: Record<EndpointKey, string> = {} as Record<EndpointKey, string>;

  for (const key of Object.keys(LOCAL_DEFAULTS) as EndpointKey[]) {
    local[key] = envOverride(`VITE_QONDUIT_${key.toUpperCase()}_BASE`)
      ?? overrides[key]
      ?? LOCAL_DEFAULTS[key];
    pub[key] = envOverride(`VITE_QONDUIT_${key.toUpperCase()}_BASE`)
      ?? overrides[key]
      ?? PUBLIC_DEFAULTS[key];
  }

  return { gateway: local.gateway, router: local.router, llama: local.llama, webui: local.webui } as unknown as Record<EndpointKey, { local: string; public: string }>;
}

const RESOLVED = resolveEndpoints();

// ── ENDPOINTS map (backward-compatible keys) ────────────────────────────────

export const ENDPOINTS: Record<EndpointKey, { local: string; public: string }> = RESOLVED;

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
  return ENDPOINTS[key][mode];
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
