import { Settings, ModelsResponse, Model } from '../types';

const DEFAULT_SETTINGS: Settings = {
  gatewayBaseUrl: 'http://192.168.5.5:8090',
  directBaseUrl: 'http://192.168.5.5:8080',
  routerBaseUrl: 'http://192.168.5.5:5001',
  apiKey: 'local',
  defaultModel: 'Qwen3-Coder-Next-IQ4_NL.gguf',
  defaultProvider: 'Direct',
};

export function getSettings(): Settings {
  const saved = localStorage.getItem('qonduit-settings');
  if (saved) {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(saved) };
  }
  return DEFAULT_SETTINGS;
}

export function saveSettings(settings: Settings): void {
  localStorage.setItem('qonduit-settings', JSON.stringify(settings));
}

export async function fetchModels(baseUrl: string): Promise<Model[]> {
  try {
    const response = await fetch(`${baseUrl}/v1/models`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data: ModelsResponse = await response.json();
    return data.data;
  } catch (error) {
    console.error('Error fetching models:', error);
    throw error;
  }
}

export async function testConnection(url: string): Promise<boolean> {
  try {
    const response = await fetch(`${url}/health`, { method: 'HEAD' });
    return response.ok;
  } catch {
    return false;
  }
}
