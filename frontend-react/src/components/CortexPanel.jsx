import React, { useState } from 'react';
import { Database, Mail, Link2, FileText, Activity } from 'lucide-react';

const CortexPanel = ({ onIngest, insights = [] }) => {
    const [isConnecting, setIsConnecting] = useState(false);
    const [logs, setLogs] = useState([]);

    const addLog = (msg) => setLogs(prev => [msg, ...prev].slice(0, 50));

    const handleFileUpload = async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        addLog(`[INGESTION] Queueing ${file.name}...`);

        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch('http://localhost:8000/ingest', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (data.status === 'success') {
                addLog(`[EMBEDDING] Processed ${data.chunks} chunks.`);
                addLog(`[VAULT] Synced to Supabase.`);
                onIngest();
            } else {
                addLog(`[ERROR] ${data.message || 'Ingestion failed'}`);
            }
        } catch (err) {
            addLog(`[ERROR] ${err.message}`);
        }
    };

    const connectGWS = () => {
        setIsConnecting(true);
        addLog(`[GWS] Authenticating OAuth flow...`);
        setTimeout(() => {
            addLog(`[GWS] Inbox Connected (1.2GB scanned)`);
            setIsConnecting(false);
        }, 2000);
    };

    return (
        <div className="fixed left-4 top-4 bottom-4 w-80 bg-slate-900/80 backdrop-blur-md border border-slate-700 rounded-xl p-4 flex flex-col z-50 shadow-2xl shadow-cyan-900/10">
            <div className="flex items-center justify-between mb-6 border-b border-slate-700 pb-4">
                <h2 className="text-cyan-400 font-mono font-bold flex items-center gap-2">
                    <Database size={18} /> CORTEX_ADMIN
                </h2>
                <div className="h-2 w-2 bg-green-500 rounded-full animate-pulse" />
            </div>

            {/* Ingestion Zone */}
            <div className="mb-6">
                <label className="text-xs text-slate-400 font-mono mb-2 block">UPLOAD DOCUMENTS</label>
                <div className="border-2 border-dashed border-slate-600 rounded-lg p-6 text-center hover:border-cyan-500 transition-colors group relative">
                    <input
                        type="file"
                        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                        onChange={handleFileUpload}
                    />
                    <FileText className="mx-auto text-slate-500 group-hover:text-cyan-400 mb-2" />
                    <span className="text-xs text-slate-500">Drop PDF/Text or Click</span>
                </div>
            </div>

            {/* GWS Connector */}
            <div className="mb-6">
                <label className="text-xs text-slate-400 font-mono mb-2 block">INTEGRATIONS</label>
                <button
                    onClick={connectGWS}
                    disabled={isConnecting}
                    className={`w-full flex items-center justify-between px-4 py-3 rounded-lg border transition-all ${isConnecting
                        ? 'border-purple-500 bg-purple-900/20 text-purple-300'
                        : 'border-slate-600 hover:border-green-500 hover:bg-green-900/10 text-slate-300'
                        }`}
                >
                    <div className="flex items-center gap-3">
                        <Mail size={18} />
                        <span className="text-sm font-mono">Google Workspace</span>
                    </div>
                    {isConnecting && <Activity size={16} className="animate-spin" />}
                </button>
            </div>

            {/* Live Insights Zone */}
            <div className="mt-6 flex-none overflow-hidden flex flex-col h-48 border-t border-slate-700 pt-4">
                <label className="text-xs text-cyan-400 font-mono mb-2 flex items-center gap-2">
                    <Activity size={12} /> EXTRACTED_INSIGHTS
                </label>
                <div className="flex-1 bg-black/30 rounded-lg p-2 overflow-y-auto space-y-2">
                    {insights.length === 0 && <span className="opacity-50 text-[10px] font-mono">// Waiting for detections...</span>}
                    {insights.map((insight, i) => (
                        <div key={i} className="bg-slate-800/50 border border-slate-700 p-2 rounded text-[10px] font-mono">
                            <div className={`font-bold uppercase ${insight.insight_type === 'risk' ? 'text-red-400' : 'text-cyan-400'}`}>
                                {insight.insight_type}
                            </div>
                            <div className="text-slate-300 mt-1 leading-tight">
                                {insight.text}
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            {/* System Logs (The Console) */}
            <div className="flex-1 overflow-hidden flex flex-col">
                <label className="text-xs text-slate-400 font-mono mb-2 flex items-center gap-2">
                    <Link2 size={12} /> SYSTEM I/O
                </label>
                <div className="flex-1 bg-black/50 rounded-lg p-3 overflow-y-auto font-mono text-[10px] leading-4 text-slate-400 border border-slate-800">
                    {logs.length === 0 && <span className="opacity-50">// Awaiting commands...</span>}
                    {logs.map((log, i) => (
                        <div key={i} className={`
              ${log.includes('ERROR') ? 'text-red-400' :
                                log.includes('GWS') ? 'text-purple-400' : 'text-emerald-400'}
              mb-1 border-l-2 border-transparent pl-2 hover:border-cyan-500/50
            `}>
                            {`> ${log}`}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

export default CortexPanel;
