import React from 'react';
import { Outlet, Link } from 'react-router-dom';
import { Key } from 'lucide-react';

export default function ConsoleLayout() {

  return (
    <div className="min-h-screen bg-slate-950 flex">
      {/* Sidebar */}
      <div className="w-64 bg-black/30 border-r border-white/5 flex flex-col">
        <div className="p-6 border-b border-white/5">
          <div className="flex items-center gap-2 text-cyan-400 font-mono font-bold tracking-widest text-sm">
            <span className="text-lg">◉</span> CASSANDRA
          </div>
          <div className="text-slate-500 text-[10px] font-mono mt-1">Console</div>
        </div>

        <div className="flex-1 p-4 space-y-2">
          <Link
            to="/console/dashboard"
            className="flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:text-cyan-400 hover:bg-cyan-400/5 transition-all text-sm font-mono"
          >
            <Key size={16} /> API Keys
          </Link>
        </div>

        <div className="p-4 border-t border-white/5">
          <Link
            to="/"
            className="flex items-center gap-2 w-full px-4 py-2 rounded-xl text-slate-600 hover:text-slate-400 transition-all text-xs font-mono mt-1"
          >
            ← Back to App
          </Link>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 p-8 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  );
}
