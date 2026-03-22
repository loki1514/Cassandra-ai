/**
 * recorder-processor.js
 * AudioWorklet processor — runs on a dedicated audio thread.
 * 
 * Place at: /public/processors/recorder.js
 * 
 * Buffers incoming mic samples and posts Float32Array chunks
 * to the main thread. Supports mute/unmute for echo prevention.
 */
class RecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.bufferSize = 2400;  // 100ms at 24kHz — matches send interval
    this.buffer = new Float32Array(this.bufferSize);
    this.bufferIndex = 0;
    this.isMuted = false;

    this.port.onmessage = (event) => {
      if (event.data.type === 'mute') this.isMuted = true;
      if (event.data.type === 'unmute') this.isMuted = false;
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channelData = input[0];
    for (let i = 0; i < channelData.length; i++) {
      this.buffer[this.bufferIndex++] = this.isMuted ? 0 : channelData[i];

      if (this.bufferIndex >= this.bufferSize) {
        // Send a copy, not the reference
        this.port.postMessage(this.buffer.slice(0));
        this.bufferIndex = 0;
      }
    }
    return true;
  }
}

registerProcessor('recorder-processor', RecorderProcessor);
