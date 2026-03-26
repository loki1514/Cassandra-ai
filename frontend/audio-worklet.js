/**
 * audio-worklet.js — AudioWorklet Processor
 * Runs off the main thread. Sends raw Float32 PCM frames via postMessage.
 */
class AudioCaptureProcessor extends AudioWorkletProcessor {
    process(inputs) {
        const ch = inputs[0]?.[0];
        if (ch?.length) {
            // Clone before posting — transferring would be faster but less safe
            this.port.postMessage(new Float32Array(ch));
        }
        return true; // keep alive
    }
}

registerProcessor('audio-capture-processor', AudioCaptureProcessor);
