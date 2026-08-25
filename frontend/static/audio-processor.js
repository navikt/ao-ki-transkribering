/**
 * AudioWorklet-processor – sender raw float32 PCM-frames til main thread.
 * Batching (160ms) skjer i main thread.
 */
class AudioProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch && ch.length > 0) {
      const kopi = new Float32Array(ch);
      this.port.postMessage(kopi, [kopi.buffer]);
    }
    return true;
  }
}

registerProcessor("audio-processor", AudioProcessor);
