/**
 * The in-place app container: title bar, internal tabs, scrolling body.
 *
 * This is what keeps the springboard metaphor honest. Opening Finanças does
 * not change the route or unmount the home screen — it lays a window over it,
 * and every tab inside Finanças is a segment of that window rather than
 * another destination.
 */

import { ChevronLeft } from 'lucide-react';
import type { ReactNode } from 'react';

export interface Tab {
  id: string;
  label: string;
  render: () => ReactNode;
}

export function AppWindow({
  title,
  tabs,
  activeTab,
  onTabChange,
  onClose,
}: {
  title: string;
  tabs: Tab[];
  activeTab: string;
  onTabChange: (id: string) => void;
  onClose: () => void;
}) {
  const current = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];

  return (
    <div className="oj-window">
      <div className="oj-window-bar">
        <button type="button" className="oj-back" onClick={onClose}>
          <ChevronLeft size={22} />
          <span>Início</span>
        </button>
        <div className="oj-window-title">{title}</div>
      </div>

      {tabs.length > 1 && (
        <div className="oj-segmented" role="tablist" aria-label={`Seções de ${title}`}>
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={tab.id === current.id}
              data-active={tab.id === current.id}
              className="oj-segment"
              onClick={() => onTabChange(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}

      <div className="oj-window-body" role="tabpanel">
        {current.render()}
      </div>
    </div>
  );
}
