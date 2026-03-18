import React, { useEffect, useRef } from 'react';

const LiveTranscript = ({ transcripts = [] }) => {
    const scrollRef = useRef(null);

    // Auto-scroll on new message
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [transcripts]);

    return (
        <div className="text-center text-cyan-100/80 font-mono text-sm">
            <div className="mb-2 animate-pulse text-cyan-400 font-bold tracking-widest bg-black/50 inline-block px-4 py-1 rounded-full border border-cyan-900/50">
                MIC ACTIVE / FULL DUPLEX STREAM
            </div>
            <div
                ref={scrollRef}
                className="bg-slate-900/60 backdrop-blur-md border border-cyan-900/50 rounded p-4 h-48 overflow-y-auto w-full mx-auto shadow-2xl pointer-events-auto"
            >
                {transcripts.length === 0 ? (
                    <div className="text-slate-500 italic mt-6 text-center">
                        Waiting for voice input...
                    </div>
                ) : (
                    transcripts.map((t, idx) => {
                        const isAI = t.speaker.toLowerCase() === 'ai' || t.speaker.toLowerCase() === 'assistant';
                        return (
                            <div key={idx} className="text-left mb-2">
                                <span className={`${isAI ? 'text-purple-400' : 'text-cyan-400'} font-bold uppercase mr-2`}>
                                    {t.speaker}:
                                </span>
                                <span className={isAI ? 'text-purple-100/90' : 'text-slate-100/90'}>
                                    {t.text}
                                </span>
                            </div>
                        );
                    })
                )}
            </div>
        </div>
    );
};

export default LiveTranscript;
