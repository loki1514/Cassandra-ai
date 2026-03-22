/**
 * App.jsx — CLEAN REBUILD
 * 
 * Key changes from original:
 *   1. All callbacks passed to useAudioPipeline are stable (useCallback with minimal deps)
 *   2. No inline arrow functions passed as hook props
 *   3. Single start guard at both App level and hook level
 *   4. Clean separation of concerns
 */
import React, { useState, useCallback, useRef, useEffect } from 'react';
import CassandraOrb from './components/CassandraOrb';
import CortexPanel from './components/CortexPanel';
import LiveTranscript from './components/LiveTranscript';
import RoleSelector from './components/RoleSelector';
import MicSelector from './components/MicSelector';
import { useAudioPipeline } from './hooks/useAudioPipeline';
import { useOrbStore } from './stores/orbStore';
import './App.css';

const API_URL = import.meta.env.VITE_API_URL || '';

const getCurrentDayName = () => {
  const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  return days[new Date().getDay()];
};

function App() {
  // ── State ──
  const [meetingId, setMeetingId] = useState(null);
  const [systemState, setSystemState] = useState('idle');
  const [currentRole, setCurrentRole] = useState('GENERAL');
  const [roleConfig, setRoleConfig] = useState(null);
  const [availableRoles, setAvailableRoles] = useState([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState(null);
  const [transcripts, setTranscripts] = useState([]);
  const [insights, setInsights] = useState([]);
  const [isConnected, setIsConnected] = useState(false);

  const setOrbState = useOrbStore((s) => s.setState);

  // Guard against double-clicks
  const isStartingRef = useRef(false);

  // ── Stable Callbacks ──
  // These MUST be wrapped in useCallback with minimal deps.
  // Passing inline arrows to useAudioPipeline caused the original bug:
  // inline arrow → new reference every render → hook deps change →
  // cleanup effect fires → pipeline destroyed.

  const handleStateChange = useCallback((state) => {
    setSystemState(state);
    // setOrbState is stable (from zustand), safe to call directly
    useOrbStore.getState().setState(state);
  }, []);

  const handleTranscript = useCallback((msg) => {
    setTranscripts((prev) => [...prev, msg].slice(-100));
  }, []);

  const handleInsight = useCallback((msg) => {
    setInsights((prev) => [...prev, msg].slice(-50));
  }, []);

  const handleConnected = useCallback(() => {
    setIsConnected(true);
  }, []);

  const handleDisconnected = useCallback(() => {
    setIsConnected(false);
  }, []);

  const handleRoleUpdate = useCallback((role, config) => {
    setCurrentRole(role);
    setRoleConfig(config);
  }, []);

  // ── Audio Pipeline ──
  const {
    startPipeline,
    stopPipeline,
    switchRole: wsSwitchRole,
    isConnecting,
  } = useAudioPipeline({
    onStateChange: handleStateChange,
    onTranscript: handleTranscript,
    onInsight: handleInsight,
    onConnected: handleConnected,
    onDisconnected: handleDisconnected,
    onRoleUpdate: handleRoleUpdate,
    selectedDeviceId,
  });

  // ── Fetch roles on mount ──
  useEffect(() => {
    fetch(`${API_URL}/api/roles`)
      .then((r) => r.json())
      .then((data) => setAvailableRoles(data.roles || []))
      .catch(console.error);
  }, []);

  // ── Start Meeting ──
  const handleStartMeeting = useCallback(async () => {
    // Double-click guard
    if (isStartingRef.current) return;
    isStartingRef.current = true;

    try {
      const res = await fetch(`${API_URL}/api/meetings/new`, { method: 'POST' });
      if (!res.ok) throw new Error(`Failed to create meeting: ${res.status}`);

      const data = await res.json();
      const id = data.meeting_id;

      setMeetingId(id);
      await startPipeline(id);
    } catch (err) {
      console.error('Failed to start meeting:', err);
      isStartingRef.current = false;
    }
  }, [startPipeline]);

  // ── End Meeting ──
  const handleEndMeeting = useCallback(() => {
    stopPipeline();
    setMeetingId(null);
    setTranscripts([]);
    setInsights([]);
    setIsConnected(false);
    setSystemState('idle');
    isStartingRef.current = false;
  }, [stopPipeline]);

  // ── Switch Role ──
  const handleSwitchRole = useCallback((role) => {
    if (!meetingId) return;
    wsSwitchRole(role);
  }, [meetingId, wsSwitchRole]);

  // ── Mic Change ──
  const handleMicChange = useCallback((device) => {
    setSelectedDeviceId(device.deviceId);
    console.log('Selected microphone:', device.label, device.deviceId);
  }, []);

  // ── Render ──
  return (
    <div className="app-container">
      <div className="bg-grid" />

      {/* Left Sidebar */}
      <CortexPanel
        meetingId={meetingId}
        transcripts={transcripts}
        insights={insights}
        isConnected={isConnected}
        apiUrl={API_URL}
      />

      {/* Main Content */}
      <div className="main-content">
        {/* Header */}
        <header className="app-header">
          <div className="logo">
            <span className="logo-icon">◉</span>
            <span>CASSANDRA</span>
          </div>

          <MicSelector onMicChange={handleMicChange} disabled={!!meetingId} />

          {meetingId && (
            <div className="meeting-info">
              <span className="meeting-id">
                Meeting: {meetingId.slice(0, 8)}...
              </span>
              <span
                className={`connection-status ${
                  isConnected ? 'connected' : 'disconnected'
                }`}
              >
                {isConnected ? '● Live' : '○ Offline'}
              </span>
            </div>
          )}
        </header>

        {/* The Orb */}
        <div className="orb-container">
          <OrbErrorBoundary>
            <CassandraOrb />
          </OrbErrorBoundary>

          <div className="state-indicator">
            <div className={`state-pill ${systemState}`}>
              {systemState.toUpperCase()}
            </div>
            {currentRole !== 'GENERAL' && (
              <div
                className="role-pill"
                style={{
                  background: roleConfig?.color_scheme?.primary || '#00FFFF',
                }}
              >
                {currentRole}
              </div>
            )}
          </div>
        </div>

        {/* Bottom Controls */}
        <div className="bottom-controls">
          {!meetingId ? (
            <button
              onClick={handleStartMeeting}
              disabled={isConnecting}
              className="btn-awaken"
            >
              {isConnecting
                ? 'Initializing...'
                : `Start Meeting - ${getCurrentDayName()}`}
            </button>
          ) : (
            <>
              <RoleSelector
                currentRole={currentRole}
                roles={availableRoles}
                onSwitch={handleSwitchRole}
              />
              <button onClick={handleEndMeeting} className="btn-end">
                End Meeting
              </button>
            </>
          )}
        </div>

        {/* Live Transcript */}
        {meetingId && (
          <LiveTranscript transcripts={transcripts} insights={insights} />
        )}
      </div>
    </div>
  );
}

// ── Error Boundary for the Orb ──
// Prevents a THREE.js crash from killing the entire app.
class OrbErrorBoundary extends React.Component {
  state = { hasError: false, error: null };

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('[OrbErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            width: '100%',
            height: '400px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#00FFFF',
            fontFamily: 'monospace',
            fontSize: '14px',
            opacity: 0.5,
          }}
        >
          Orb render failed — check console
        </div>
      );
    }
    return this.props.children;
  }
}

export default App;
