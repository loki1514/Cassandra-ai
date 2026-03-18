import React, { useState } from 'react';
import GrowthOrb from './components/GrowthOrb.legacy';
import CortexPanel from './components/CortexPanel';
import LiveTranscript from './components/LiveTranscript';
import { useAudioPipeline } from './hooks/useAudioPipeline';

function App() {
  const [systemState, setSystemState] = useState('idle');
  const [showTranscript, setShowTranscript] = useState(false);

  // Realtime Live Feed state
  const [transcripts, setTranscripts] = useState([]);
  const [insights, setInsights] = useState([]);

  // Connect Audio Pipeline hook to state managers
  const { startPipeline, stopPipeline } = useAudioPipeline({
    onStateChange: setSystemState,
    onTranscript: (msg) => {
      // Accumulate text stream
      setTranscripts(prev => [...prev, msg].slice(-20)); // Keep last 20
    },
    onInsight: (msg) => {
      // Accumulate insights
      setInsights(prev => [...prev, msg].slice(-10));
    }
  });

  const handleAwaken = () => {
    setShowTranscript(true);
    startPipeline(); // Grabs Mic and connects WebSockets
  };

  const handleSleep = () => {
    stopPipeline(); // Tears down WebSockets
    setShowTranscript(false);
  };

  // Connect Cortex Ingestion to Orb
  const handleCortexIngest = () => {
    setSystemState('thinking');
    setTimeout(() => { if (systemState !== 'speaking') setSystemState('listening') }, 4000);
  };

  return (
    <div className="h-screen w-screen bg-black text-slate-200 overflow-hidden flex" style={{ position: 'relative' }}>
      {/* Video Background */}
      <video
        autoPlay
        loop
        muted
        playsInline
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          zIndex: 0,
          opacity: 0.35,
          pointerEvents: 'none',
        }}
      >
        <source src="/bg-video.mov" type="video/mp4" />
      </video>

      {/* Left Sidebar: The Cortex */}
      <div style={{ position: 'relative', zIndex: 10 }}>
        <CortexPanel onIngest={handleCortexIngest} insights={insights} />
      </div>

      {/* Center: The Orb */}
      <div className="flex-1 relative" style={{ zIndex: 10 }}>
        <GrowthOrb />

        {/* Floating Controls (Bottom Center) */}
        <div className="absolute bottom-10 left-0 right-0 flex justify-center gap-4 z-30">
          <button
            onClick={handleSleep}
            className="px-6 py-2 bg-slate-800/50 border border-slate-600 rounded-full backdrop-blur-sm hover:bg-slate-700 text-xs uppercase tracking-widest cursor-pointer"
          >
            Sleep
          </button>
          <button
            onClick={handleAwaken}
            className="px-6 py-2 bg-cyan-900/30 border border-cyan-500/50 text-cyan-400 rounded-full backdrop-blur-sm hover:bg-cyan-900/50 text-xs uppercase tracking-widest shadow-[0_0_15px_rgba(0,243,255,0.3)] cursor-pointer"
          >
            Awaken
          </button>
        </div>

        {/* Live Transcript (Overlay) */}
        {showTranscript && (
          <div className="absolute bottom-24 left-1/2 transform -translate-x-1/2 w-full max-w-4xl px-6 pointer-events-none">
            <LiveTranscript transcripts={transcripts} />
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
