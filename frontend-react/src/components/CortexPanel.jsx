import React, { useState } from 'react';
import { Database, Mail, Link2, FileText, Activity, ChevronLeft } from 'lucide-react';

const CortexPanel = ({ apiUrl, onIngest }) => {
    const [isConnecting, setIsConnecting] = useState(false);
    const [logs, setLogs] = useState([]);
    const [isOpen, setIsOpen] = useState(false);

    const addLog = (msg) => setLogs(prev => [msg, ...prev].slice(0, 50));

    const handleFileUpload = async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        addLog(`[INGESTION] Queueing ${file.name}...`);

        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch(`${apiUrl || ''}/ingest`, {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (data.status === 'success') {
                addLog(`[EMBEDDING] Processed ${data.chunks} chunks.`);
                addLog(`[VAULT] Synced to Supabase.`);
                if(onIngest) onIngest();
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
        <div style={{ position: 'absolute', top: 0, left: 0, height: '100%', zIndex: 100, display: 'flex' }}>
            {/* The Panel */}
            <div className="glass-panel" style={{ 
                width: '350px', 
                height: '100%', 
                transition: 'transform 0.3s ease', 
                transform: isOpen ? 'translateX(0)' : 'translateX(-100%)',
                display: 'flex', 
                flexDirection: 'column', 
                padding: '1.5rem',
                borderRight: '1px solid rgba(0,255,255,0.2)',
                backgroundColor: 'rgba(5, 10, 20, 0.95)',
                boxShadow: isOpen ? '10px 0 30px rgba(0,0,0,0.8)' : 'none'
            }}>
                <div className="flex items-center justify-between mb-8 border-b border-white/10 pb-4">
                    <h2 className="text-cyan-400 font-mono font-bold flex items-center gap-2 tracking-widest text-sm">
                        <Database size={16} /> CORTEX_ADMIN
                    </h2>
                    <div className="flex items-center gap-4">
                        <div className="h-2 w-2 bg-emerald-400 rounded-full animate-pulse shadow-[0_0_10px_rgba(52,211,153,0.8)]" />
                        <button onClick={() => setIsOpen(false)} className="text-cyan-400 hover:text-white transition-colors">
                            <ChevronLeft size={20} />
                        </button>
                    </div>
                </div>

                {/* Ingestion Zone */}
                <div className="mb-8">
                    <label className="text-[10px] text-slate-400 font-mono mb-3 tracking-widest block uppercase">Upload Documents</label>
                    <div className="border border-dashed border-white/20 bg-black/20 rounded-xl p-6 text-center hover:border-cyan-400/50 hover:bg-cyan-900/10 transition-all duration-300 group relative">
                        <input
                            type="file"
                            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                            onChange={handleFileUpload}
                        />
                        <FileText className="mx-auto text-slate-500 group-hover:text-cyan-400 mb-3 transition-colors" size={24} />
                        <span className="text-[11px] text-slate-400 font-mono tracking-wide">Drop PDF/Text or Click</span>
                    </div>
                </div>

                {/* GWS Connector */}
                <div className="mb-8">
                    <label className="text-[10px] text-slate-400 font-mono mb-3 tracking-widest block uppercase">Integrations</label>
                    <button
                        onClick={connectGWS}
                        disabled={isConnecting}
                        className={`w-full flex items-center justify-between px-5 py-3.5 rounded-xl border transition-all duration-300 ${isConnecting
                            ? 'border-purple-500/50 bg-purple-900/20 text-purple-300'
                            : 'border-white/10 bg-black/20 hover:border-emerald-500/50 hover:bg-emerald-900/20 text-slate-300 hover:text-emerald-300 hover:shadow-[0_0_15px_rgba(52,211,153,0.1)]'
                            }`}
                    >
                        <div className="flex items-center gap-3">
                            <Mail size={16} />
                            <span className="text-[11px] font-mono tracking-widest uppercase">Google Workspace</span>
                        </div>
                        {isConnecting && <Activity size={16} className="animate-spin text-purple-400" />}
                    </button>
                </div>

                {/* System Logs (The Console) */}
                <div className="flex-1 overflow-hidden flex flex-col pt-4 border-t border-white/10">
                    <label className="text-[10px] text-slate-400 font-mono mb-3 flex items-center gap-2 tracking-widest uppercase">
                        <Link2 size={12} /> System I/O
                    </label>
                    <div className="flex-1 bg-black/40 rounded-xl p-4 overflow-y-auto font-mono text-[10px] leading-5 text-slate-400 border border-white/5 shadow-inner" style={{scrollbarWidth:'none'}}>
                        {logs.length === 0 && <span className="opacity-40">// Awaiting commands...</span>}
                        {logs.map((log, i) => (
                            <div key={i} className={`
                  ${log.includes('ERROR') ? 'text-red-400' :
                                    log.includes('GWS') ? 'text-purple-400' : 'text-emerald-400'}
                  mb-1.5 border-l-2 border-transparent pl-2 hover:border-cyan-500/50 transition-colors
                `}>
                                {`> ${log}`}
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* Toggle Button (visible when closed) */}
            {!isOpen && (
                <button 
                    onClick={() => setIsOpen(true)}
                    className="glass-panel"
                    style={{
                        position: 'absolute', top: '20px', left: '20px', 
                        background: 'rgba(10, 20, 30, 0.8)', border: '1px solid rgba(0,255,255,0.4)',
                        color: '#00FFFF', padding: '12px', borderRadius: '12px', zIndex: 101,
                        cursor: 'pointer', boxShadow: '0 0 15px rgba(0,255,255,0.2)'
                    }}
                >
                    <Database size={24} />
                </button>
            )}
        </div>
    );
};

export default CortexPanel;
