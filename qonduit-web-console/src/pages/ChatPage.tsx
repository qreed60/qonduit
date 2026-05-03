import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ENDPOINTS } from '../config/endpoints';
import { getSettings } from '../services/api';
import {
  MessageSquare,
  ArrowRight,
  Globe,
  Zap,
  Router,
} from 'lucide-react';

const ChatPage: React.FC = () => {
  const navigate = useNavigate();
  const settings = getSettings();
  const mode = settings.endpointMode;

  const endpointInfo = [
    { name: 'Gateway', url: ENDPOINTS.gateway[mode], icon: Globe, color: 'text-status-success' },
    { name: 'Direct', url: ENDPOINTS.llama[mode], icon: Zap, color: 'text-status-success' },
    { name: 'Router', url: ENDPOINTS.router[mode], icon: Router, color: 'text-status-success' },
  ];

  return (
    <div className="flex flex-col h-full p-6">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-xl font-bold bg-gradient-to-r from-accent-primary to-accent-tertiary bg-clip-text text-transparent">
          Chat
        </h2>
        <p className="text-sm text-text-secondary mt-1">
          Interact with your AI models through intelligent routing
        </p>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto">
          {/* Hero Section */}
          <div className="text-center mb-8">
            <div className="w-16 h-16 mx-auto mb-4 bg-accent-primary/10 rounded-2xl flex items-center justify-center border border-accent-primary/20">
              <MessageSquare className="w-8 h-8 text-accent-primary" />
            </div>
            <h3 className="text-lg font-semibold text-text-primary mb-2">
              Chat Interface Coming Soon
            </h3>
            <p className="text-sm text-text-secondary max-w-md mx-auto">
              A full chat experience with intelligent model routing is in development.
              Use the Dashboard to manage your models in the meantime.
            </p>
          </div>

          {/* Endpoint Info */}
          <div className="bg-bg-card rounded-xl border border-border-primary p-4 mb-6">
            <p className="text-xs font-medium text-text-secondary mb-3">Active Endpoints</p>
            <div className="grid grid-cols-3 gap-3">
              {endpointInfo.map(({ name, url, icon: Icon, color }) => (
                <div
                  key={name}
                  className="bg-bg-secondary/50 rounded-lg p-3 border border-border-subtle"
                >
                  <Icon className={`w-4 h-4 mb-1 ${color}`} />
                  <p className="text-xs font-medium text-text-primary">{name}</p>
                  <p className="text-[10px] font-mono text-text-tertiary truncate" title={url}>
                    {url}
                  </p>
                </div>
              ))}
            </div>
          </div>

          {/* CTA */}
          <button
            onClick={() => navigate('/')}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-medium bg-gradient-to-r from-accent-primary to-accent-tertiary hover:from-accent-primary-hover hover:to-accent-tertiary text-white shadow-lg shadow-accent-primary/20 transition-all duration-200"
          >
            <ArrowRight className="w-4 h-4" />
            Go to Dashboard
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChatPage;
