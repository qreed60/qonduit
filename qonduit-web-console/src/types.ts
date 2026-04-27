export interface Settings {
  gatewayBaseUrl: string;
  directBaseUrl: string;
  routerBaseUrl: string;
  apiKey: string;
  defaultModel: string;
  defaultProvider: 'Direct' | 'Gateway';
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

export type Page = 'chat' | 'models' | 'router' | 'diagnostics' | 'settings';
