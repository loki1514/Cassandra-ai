const wsUrl = "ws://localhost:8000/ws/boardroom";
let ws;
let audioContext;
let audioQueue = [];
let isPlaying = false;
let mediaStream;
let audioProcessor;

async function initAudio() {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: { sampleRate: 24000, channelCount: 1, echoCancellation: true, noiseSuppression: true }
        });
        console.log("Mic access granted.");
    } catch (e) {
        console.error("Mic access denied.", e);
        document.getElementById('status-text').innerText = "SYSTEM ERROR: MIC";
        return;
    }

    const source = audioContext.createMediaStreamSource(mediaStream);
    audioProcessor = audioContext.createScriptProcessor(4096, 1, 1);
    source.connect(audioProcessor);
    audioProcessor.connect(audioContext.destination);

    audioProcessor.onaudioprocess = function (e) {
        // Send audio buffer to backend payload
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        const floatData = e.inputBuffer.getChannelData(0);
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

        ws.send(JSON.stringify({
            type: "input_audio",
            audio: base64Audio
        }));
    };
}

function connectWebSocket() {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("Connected to Boardroom AI Proxy");
        window.setOrbState("LISTENING");
        document.getElementById('sub-status').innerText = "GENERAL PROTOCOL ACTIVE";
    };

    ws.onmessage = async (event) => {
        const msg = JSON.parse(event.data);

        if (msg.type === "audio") {
            window.setOrbState("SPEAKING");
            playAudioDelta(msg.audio);
        } else if (msg.type === "interrupt") {
            window.setOrbState("LISTENING");
            audioQueue = [];
            isPlaying = false;
        } else if (msg.type === "transcript") {
            appendTranscript(msg.speaker, msg.text);
        } else if (msg.type === "insight") {
            appendInsight(msg.insight_type, msg.text, msg.confidence || '0.9');
        } else if (msg.type === "memory_match") {
            window.setOrbState("MEMORY_RECALL");
            appendMemory(msg.text);
            setTimeout(() => { if (orbState === 'MEMORY_RECALL') window.setOrbState("LISTENING"); }, 2500);
        }
    };

    ws.onclose = () => {
        console.log("Disconnected.");
        window.setOrbState("DORMANT");
        document.getElementById('sub-status').innerText = "CONNECTION LOST - RECONNECTING...";
        setTimeout(connectWebSocket, 3000);
    };
}

// Playback queue mechanism
async function playAudioDelta(base64Data) {
    const pcm16Data = base64ToInt16Array(base64Data);
    const audioBuffer = audioContext.createBuffer(1, pcm16Data.length, 24000);
    const channelData = audioBuffer.getChannelData(0);

    for (let i = 0; i < pcm16Data.length; i++) {
        channelData[i] = pcm16Data[i] / 32768.0;
    }

    audioQueue.push(audioBuffer);
    if (!isPlaying) {
        processAudioQueue();
    }
}

function base64ToInt16Array(base64) {
    const binaryStr = atob(base64);
    const len = binaryStr.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        bytes[i] = binaryStr.charCodeAt(i);
    }
    return new Int16Array(bytes.buffer);
}

let playbackRMS = 0;

function processAudioQueue() {
    if (audioQueue.length === 0) {
        isPlaying = false;
        window.setOrbState("LISTENING");
        playbackRMS = 0;
        return;
    }

    isPlaying = true;
    const buffer = audioQueue.shift();
    const source = audioContext.createBufferSource();
    source.buffer = buffer;

    const floatData = buffer.getChannelData(0);
    let sumSquares = 0.0;
    for (const amplitude of floatData) { sumSquares += amplitude * amplitude; }
    playbackRMS = Math.sqrt(sumSquares / floatData.length);

    source.connect(audioContext.destination);
    source.onended = () => { processAudioQueue(); };
    source.start(0);
}

window.getAudioEnergy = function () {
    return { bass: playbackRMS * 1500, mid: playbackRMS * 800, treble: playbackRMS * 500 };
};

window.initBackendPipeline = async function () {
    if (!audioContext) {
        await initAudio();
        connectWebSocket();
        document.getElementById('status-text').innerText = "SYSTEM INITIATING...";
    }
};

// UI DOM MANIPULATION
function appendTranscript(speaker, text) {
    const p = document.getElementById('transcript-content');
    const div = document.createElement('div');
    div.className = 'transcript-line';
    const isAI = speaker.toLowerCase() === 'ai' || speaker.toLowerCase() === 'assistant';
    div.innerHTML = `<span class="transcript-speaker ${isAI ? 'transcript-ai' : ''}">${speaker}:</span> <span style="opacity:0.9;">${text}</span>`;
    p.appendChild(div);
    p.scrollTop = p.scrollHeight;
}

function appendInsight(type, text, conf) {
    // type: decision, risk, topic
    let target = `${type}s-content`;
    const container = document.getElementById(target);
    if (!container) {
        // Fallback for topics if it comes through
        target = `topics-content`;
    }
    const realContainer = document.getElementById(target);

    const div = document.createElement('div');
    div.className = `card ${type}`;
    div.innerHTML = `
        <div class="card-title">${type.toUpperCase()} / CONF: ${conf}</div>
        <div class="card-content">${text}</div>
    `;
    realContainer.appendChild(div);
    realContainer.scrollTop = realContainer.scrollHeight;
}

function appendMemory(text) {
    const p = document.getElementById('memory-content');
    const div = document.createElement('div');
    div.className = 'card memory';
    div.innerHTML = `
        <div class="card-title">HISTORICAL MATCH</div>
        <div class="card-content">${text}</div>
    `;
    p.appendChild(div);
    p.scrollTop = p.scrollHeight;
}

// SETUP UI LISTENER
document.addEventListener("DOMContentLoaded", () => {
    // Agent Mode Switing Hook
    document.getElementById("agent-mode-select").addEventListener("change", (e) => {
        window.setAgentMode(e.target.value);
    });

    document.getElementById("recall-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "inject_context",
                query: "What were previous decisions or risks?"
            }));
            window.setOrbState("MEMORY_RECALL");
            console.log("Triggered Memory Recall");
        }
    });
});
