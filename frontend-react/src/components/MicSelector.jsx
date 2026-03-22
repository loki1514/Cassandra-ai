import React, { useEffect, useState } from 'react';

export default function MicSelector({ onMicChange, disabled }) {
  const [devices, setDevices] = useState([]);
  
  useEffect(() => {
    // Request permissions first to get accurate labels
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(stream => {
        navigator.mediaDevices.enumerateDevices().then(allDevices => {
          const mics = allDevices.filter(d => d.kind === 'audioinput');
          setDevices(mics);
          if (mics.length > 0) onMicChange(mics[0]);
        }).catch(console.error);
        
        // Stop all tracks to release the mic
        stream.getTracks().forEach(track => track.stop());
      })
      .catch(console.error);
  }, [onMicChange]);

  if (devices.length === 0) {
    return <span className="mic-selector-fallback" style={{fontSize: '12px', color: '#999'}}>No mics found</span>;
  }

  return (
    <select 
      disabled={disabled} 
      onChange={e => {
        const dev = devices.find(d => d.deviceId === e.target.value);
        if (dev) onMicChange(dev);
      }} 
      className="mic-selector"
      style={{
        background: 'transparent',
        border: '1px solid rgba(0, 255, 255, 0.3)',
        color: '#00FFFF',
        padding: '4px 8px',
        borderRadius: '4px',
        fontSize: '12px',
        marginLeft: '10px'
      }}
    >
      {devices.map((d, i) => (
        <option key={d.deviceId} value={d.deviceId} style={{ background: '#000' }}>
          {d.label || `Microphone ${i+1}`}
        </option>
      ))}
    </select>
  );
}
