import React from 'react';

interface ComingSoonCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  category: 'ai' | 'infra' | 'tools';
}

const CATEGORY_COLORS: Record<string, string> = {
  ai: 'text-accent-primary',
  infra: 'text-accent-secondary',
  tools: 'text-status-warning',
};

const CATEGORY_LABELS: Record<string, string> = {
  ai: 'AI Features',
  infra: 'Infrastructure',
  tools: 'Tools',
};

const ComingSoonCard: React.FC<ComingSoonCardProps> = ({ icon, title, description, category }) => {
  return (
    <div className="bg-bg-card rounded-xl border border-border-primary p-4 opacity-75 hover:opacity-100 transition-opacity duration-200 group">
      <div className="flex items-start gap-3">
        <div className={`w-9 h-9 bg-bg-secondary rounded-lg flex items-center justify-center border border-border-subtle flex-shrink-0 ${CATEGORY_COLORS[category]}`}>
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-semibold text-text-primary text-sm">{title}</h3>
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-accent-primary/10 text-accent-primary">
              Soon
            </span>
          </div>
          <p className="text-xs text-text-tertiary leading-relaxed">{description}</p>
        </div>
      </div>
    </div>
  );
};

interface ComingSoonProps {
  items: ComingSoonCardProps[];
}

const ComingSoon: React.FC<ComingSoonProps> = ({ items }) => {
  if (items.length === 0) return null;

  // Group by category
  const grouped = items.reduce<Record<string, ComingSoonCardProps[]>>((acc, item) => {
    const cat = item.category;
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(item);
    return acc;
  }, {});

  return (
    <div>
      <h2 className="text-base font-semibold text-text-primary mb-4">Upcoming Features</h2>
      <div className="space-y-5">
        {Object.entries(grouped).map(([category, categoryItems]) => (
          <div key={category}>
            <p className="text-xs font-medium text-text-tertiary uppercase tracking-wider mb-3">
              {CATEGORY_LABELS[category] || category}
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {categoryItems.map((item, idx) => (
                <ComingSoonCard key={idx} {...item} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ComingSoon;
