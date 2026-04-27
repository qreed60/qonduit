import React from 'react';

const ChatPage: React.FC = () => {
  return (
    <div className="p-8">
      <h2 className="text-2xl font-bold mb-4 text-purple-400">Chat</h2>
      <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
        <p className="text-gray-400">
          Chat functionality will be implemented in a future milestone.
        </p>
        <p className="text-sm text-gray-500 mt-2">
          Current settings:
          <ul className="mt-2 space-y-1">
            <li>Gateway: {localStorage.getItem('qonduit-settings') || 'N/A'}</li>
          </ul>
        </p>
      </div>
    </div>
  );
};

export default ChatPage;
