/* AudioWorklet: Mikrofon-Signal auf 16 kHz mono PCM16 bringen und in 100-ms-Blöcken senden.
   Läuft im Audio-Thread des Browsers; der Server bekommt fertige Rohdaten. */
class PCM16Processor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.target = 16000;
    this.ratio = sampleRate / this.target;   // sampleRate = globale Kontextrate (z. B. 48000)
    this.frac = 0;                            // Bruchteil-Position für die Interpolation
    this.chunks = [];
    this.len = 0;
    this.blockIn = Math.round(sampleRate / 10); // ~100 ms Eingangssamples
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    this.chunks.push(new Float32Array(ch));
    this.len += ch.length;
    if (this.len >= this.blockIn) this.flush();
    return true;
  }
  flush() {
    const data = new Float32Array(this.len);
    let o = 0;
    for (const c of this.chunks) { data.set(c, o); o += c.length; }
    const out = [];
    let i = this.frac;
    let sum = 0;
    while (i + 1 < data.length) {
      const i0 = Math.floor(i), t = i - i0;
      const v = data[i0] * (1 - t) + data[i0 + 1] * t;
      out.push(v);
      sum += v * v;
      i += this.ratio;
    }
    const start = Math.floor(i);
    this.frac = i - start;
    const rest = data.subarray(Math.min(start, data.length));
    this.chunks = rest.length ? [new Float32Array(rest)] : [];
    this.len = rest.length;
    if (!out.length) return;
    const pcm = new Int16Array(out.length);
    for (let k = 0; k < out.length; k++) {
      const s = Math.max(-1, Math.min(1, out[k]));
      pcm[k] = s < 0 ? s * 32768 : s * 32767;
    }
    this.port.postMessage({ pcm: pcm.buffer, rms: Math.sqrt(sum / out.length) }, [pcm.buffer]);
  }
}
registerProcessor('pcm16-processor', PCM16Processor);
