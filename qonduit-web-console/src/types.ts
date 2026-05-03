export type EndpointMode = 'local' | 'public';

export interface Settings {
  apiKey: string;
  defaultModel: string;
  defaultProvider: 'Direct' | 'Gateway';
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
