import React, { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Activity, MessageSquare, ChevronDown, ChevronUp } from 'lucide-react';

const LiveTranscript = ({ transcripts = [] }) => {
    const scrollRef = useRef(null);
    const [isExpanded, setIsExpanded] = useState(false);

    // Auto-scroll on new message
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [transcripts]);

    const latestTranscript = transcripts.length > 0 ? transcripts[transcripts.length - 1] : null;

    return (
        <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-2 pointer-events-auto" style={{
            width: isExpanded ? '400px' : 'auto',
            transition: 'width 0.3s ease'
        }}>
            {/* Header / Toggle Button */}
            <button 
                onClick={() => setIsExpanded(!isExpanded)}
                className="glass-panel flex items-center justify-between px-4 py-2 hover:bg-white/5 transition-all text-sm font-mono cursor-pointer rounded-full shadow-[0_0_20px_rgba(0,0,0,0.8)] border border-white/10"
                style={{ background: 'rgba(5, 10, 20, 0.95)' }}
            >
                <div className="flex items-center gap-3">
                    <div className="h-2 w-2 bg-emerald-400 rounded-full animate-pulse shadow-[0_0_8px_rgba(52,211,153,0.8)]" />
                    <span className="text-emerald-400 font-bold tracking-[0.2em] text-[10px] uppercase">
                        {isExpanded ? 'Live Transcript' : 'Show Transcript'}
                    </span>
                </div>
                {isExpanded ? <ChevronDown size={14} className="ml-4 text-cyan-500/80" /> : <ChevronUp size={14} className="ml-4 text-cyan-500/80" />}
            </button>

            {/* Expandable Transcript Body */}
            <AnimatePresence>
                {isExpanded && (
                    <motion.div
                        initial={{ opacity: 0, height: 0, y: 20 }}
                        animate={{ opacity: 1, height: '350px', y: 0 }}
                        exit={{ opacity: 0, height: 0, y: 20 }}
                        className="glass-panel w-full flex flex-col font-mono text-sm overflow-hidden rounded-2xl border border-white/10 shadow-[0_0_30px_rgba(0,0,0,0.8)]"
                        style={{ background: 'rgba(5, 10, 20, 0.95)' }}
                    >
                        <div
                            ref={scrollRef}
                            className="flex-1 overflow-y-auto px-6 py-4 w-full"
                            style={{ 
                                maskImage: 'linear-gradient(to bottom, transparent, black 5%, black 95%, transparent)',
                                WebkitMaskImage: 'linear-gradient(to bottom, transparent, black 5%, black 95%, transparent)',
                                scrollbarWidth: 'none'
                            }}
                        >
                            {transcripts.length === 0 ? (
                                <div className="text-slate-500/50 tracking-widest text-xs uppercase h-full flex items-center justify-center">
                                    Awaiting voice input...
                                </div>
                            ) : (
                                <AnimatePresence>
                                    {transcripts.map((t, idx) => {
                                        const isAI = t.speaker.toLowerCase() === 'ai' || t.speaker.toLowerCase() === 'assistant';
                                        return (
                                            <motion.div 
                                                initial={{ opacity: 0, x: 20 }}
                                                animate={{ opacity: 1, x: 0 }}
                                                key={idx} 
                                                className="text-left mb-4 flex items-start gap-3"
                                            >
                                                <div className={`flex-shrink-0 mt-1 h-5 w-5 rounded border flex items-center justify-center ${isAI ? 'border-amber-400/30 bg-amber-400/10' : 'border-cyan-400/30 bg-cyan-400/10'}`}>
                                                    <span className={`font-bold tracking-tighter text-[8px] ${isAI ? 'text-amber-400' : 'text-cyan-400'}`}>
                                                        {isAI ? 'SYS' : 'USR'}
                                                    </span>
                                                </div>
                                                <span className={`${isAI ? 'text-amber-100/90' : 'text-slate-200'} text-[12px] leading-relaxed tracking-wide`}>
                                                    {t.text}
                                                </span>
                                            </motion.div>
                                        );
                                    })}
                                </AnimatePresence>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Floating Recent Message preview (when collapsed) */}
            <AnimatePresence>
                {!isExpanded && latestTranscript && (
                    <motion.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="glass-panel px-4 py-2 rounded-xl border border-white/5 max-w-[300px] truncate"
                        style={{ background: 'rgba(5, 10, 20, 0.8)' }}
                    >
                        <span className={`text-[11px] ${
                            (latestTranscript.speaker.toLowerCase() === 'ai' || latestTranscript.speaker.toLowerCase() === 'assistant') 
                                ? 'text-amber-100/90' 
                                : 'text-slate-200'} tracking-wide`}
                        >
                            <span className="opacity-50 mr-2 font-mono">
                                {(latestTranscript.speaker.toLowerCase() === 'ai' || latestTranscript.speaker.toLowerCase() === 'assistant') ? 'SYS >' : 'USR >'}
                            </span>
                            {latestTranscript.text}
                        </span>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};

export default LiveTranscript;
