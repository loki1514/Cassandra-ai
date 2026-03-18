import { useEffect, useRef, useCallback } from 'react';

const wsUrl = "ws://localhost:8000/ws/boardroom";

export const useAudioPipeline = ({ onStateChange, onTranscript, onInsight }) => {
    const wsRef = useRef(null);
    const audioContextRef = useRef(null);
    const mediaStreamRef = useRef(null);
    const workletNodeRef = useRef(null);

    // Playback state
    const audioQueueRef = useRef([]);
    const isPlayingRef = useRef(false);
    const playbackRMSRef = useRef(0);

    // Global hooks for p5.js
    useEffect(() => {
        window.getAudioEnergy = () => {
            return {
                bass: playbackRMSRef.current * 1500,
                mid: playbackRMSRef.current * 800,
                treble: playbackRMSRef.current * 500
            };
        };
        window.orbState = "idle";
    }, []);

    const updateOrbState = (state) => {
        window.orbState = state;
        onStateChange(state);
    };

    const processAudioQueue = useCallback(() => {
        if (audioQueueRef.current.length === 0) {
            isPlayingRef.current = false;
            playbackRMSRef.current = 0;
            updateOrbState('listening');
            return;
        }

        isPlayingRef.current = true;
        const buffer = audioQueueRef.current.shift();
        const source = audioContextRef.current.createBufferSource();
        source.buffer = buffer;

        const floatData = buffer.getChannelData(0);
        let sumSquares = 0.0;
        for (const amplitude of floatData) { sumSquares += amplitude * amplitude; }
        playbackRMSRef.current = Math.sqrt(sumSquares / floatData.length);

        source.connect(audioContextRef.current.destination);
        source.onended = () => { processAudioQueue(); };
        source.start(0);
    }, [onStateChange]);

    const playAudioDelta = useCallback((base64Data) => {
        const binaryStr = atob(base64Data);
        const len = binaryStr.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
            bytes[i] = binaryStr.charCodeAt(i);
        }
        const pcm16Data = new Int16Array(bytes.buffer);

        const audioBuffer = audioContextRef.current.createBuffer(1, pcm16Data.length, 24000);
        const channelData = audioBuffer.getChannelData(0);

        for (let i = 0; i < pcm16Data.length; i++) {
            channelData[i] = pcm16Data[i] / 32768.0;
        }

        audioQueueRef.current.push(audioBuffer);
        if (!isPlayingRef.current) {
            processAudioQueue();
        }
    }, [processAudioQueue]);

    const connectWebSocket = useCallback(() => {
        wsRef.current = new WebSocket(wsUrl);

        wsRef.current.onopen = () => {
            console.log("Connected to Boardroom AI Proxy");
            updateOrbState('listening');
        };

        wsRef.current.onmessage = async (event) => {
            const msg = JSON.parse(event.data);

            if (msg.type === "audio") {
                updateOrbState('speaking');
                playAudioDelta(msg.audio);
            } else if (msg.type === "interrupt") {
                updateOrbState('listening');
                audioQueueRef.current = [];
                isPlayingRef.current = false;
            } else if (msg.type === "transcript") {
                if (onTranscript) onTranscript(msg);
            } else if (msg.type === "insight") {
                if (onInsight) onInsight(msg);
            }
        };

        wsRef.current.onclose = () => {
            console.log("Disconnected.");
            updateOrbState('idle');
        };
    }, [onStateChange, playAudioDelta, onTranscript, onInsight]);

    const startPipeline = async () => {
        if (audioContextRef.current) return;

        audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });

        try {
            mediaStreamRef.current = await navigator.mediaDevices.getUserMedia({
                audio: { sampleRate: 24000, channelCount: 1, echoCancellation: true, noiseSuppression: true }
            });
            console.log("Mic access granted.");
        } catch (e) {
            console.error("Mic access denied.", e);
            updateOrbState('error');
            return;
        }

        // Load AudioWorklet logically
        if (!window.audioWorkletLoaded) {
            try {
                await audioContextRef.current.audioWorklet.addModule("/processors/recorder.js");
                window.audioWorkletLoaded = true;
            } catch (e) {
                console.error("Failed to load AudioWorklet", e);
            }
        }

        const source = audioContextRef.current.createMediaStreamSource(mediaStreamRef.current);
        workletNodeRef.current = new AudioWorkletNode(audioContextRef.current, 'recorder-processor');

        workletNodeRef.current.port.onmessage = (event) => {
            if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

            const floatData = event.data;
            const pcm16 = new Int16Array(floatData.length);
            for (let i = 0; i < floatData.length; i++) {
                let s = Math.max(-1, Math.min(1, floatData[i]));
                pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }

            const uint8Array = new Uint8Array(pcm16.buffer);
            let binary = '';
            const len = uint8Array.byteLength;
            for (let i = 0; i < len; i++) {
                binary += String.fromCharCode(uint8Array[i]);
            }
            const base64Audio = btoa(binary);

            wsRef.current.send(JSON.stringify({
                type: "input_audio",
                audio: base64Audio
            }));
        };

        source.connect(workletNodeRef.current);
        workletNodeRef.current.connect(audioContextRef.current.destination);

        connectWebSocket();
    };

    const stopPipeline = () => {
        if (wsRef.current) {
            wsRef.current.close();
            wsRef.current = null;
        }
        if (mediaStreamRef.current) {
            mediaStreamRef.current.getTracks().forEach(track => track.stop());
            mediaStreamRef.current = null;
        }
        if (workletNodeRef.current) {
            workletNodeRef.current.disconnect();
            workletNodeRef.current = null;
        }
        if (audioContextRef.current) {
            audioContextRef.current.close();
            audioContextRef.current = null;
        }
        updateOrbState('idle');
    };

    return { startPipeline, stopPipeline };
};
