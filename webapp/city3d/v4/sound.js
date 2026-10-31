// The sound of the 3D district (docs/PLAN_CITY3D_2.md, phase 8). WebAudio only, no files: rain is filtered noise, the
// engine a saw whose pitch follows the speed, sirens are two tones. Starts after the first touch (the browser's rule).
const KEY = "nc3d.sound";

export class Sound {
  constructor() {
    this.ctx = null; this.on = true; this.nodes = {};
    try { this.on = localStorage.getItem(KEY) !== "off"; } catch (e) {}
  }

  start() {
    if (this.ctx || !this.on) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try { this.ctx = new AC(); } catch (e) { return; }
    const c = this.ctx;
    this.master = c.createGain(); this.master.gain.value = 0.55; this.master.connect(c.destination);
    // a second of white noise, reused by the rain, the steps and the hits
    const buf = c.createBuffer(1, c.sampleRate, c.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    this.noise = buf;
    // rain: band-passed noise, always on (quiet), louder outside
    const rain = c.createBufferSource(); rain.buffer = buf; rain.loop = true;
    const bp = c.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 2400; bp.Q.value = 0.6;
    const rg = c.createGain(); rg.gain.value = 0; rain.connect(bp).connect(rg).connect(this.master); rain.start();
    this.nodes.rain = rg;
    // the engine: a saw through a low-pass, silent until driving
    const eng = c.createOscillator(); eng.type = "sawtooth"; eng.frequency.value = 40;
    const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 500;
    const eg = c.createGain(); eg.gain.value = 0; eng.connect(lp).connect(eg).connect(this.master); eng.start();
    this.nodes.eng = eng; this.nodes.engGain = eg;
    // the siren: a sine swept by a slow square
    const sir = c.createOscillator(); sir.type = "sine"; sir.frequency.value = 700;
    const lfo = c.createOscillator(); lfo.type = "square"; lfo.frequency.value = 1.6;
    const lfoG = c.createGain(); lfoG.gain.value = 180; lfo.connect(lfoG).connect(sir.frequency);
    const sg = c.createGain(); sg.gain.value = 0; sir.connect(sg).connect(this.master); sir.start(); lfo.start();
    this.nodes.siren = sg;
    // the hum of the neon: a low 60 Hz buzz near the doors
    const hum = c.createOscillator(); hum.type = "square"; hum.frequency.value = 60;
    const hl = c.createBiquadFilter(); hl.type = "lowpass"; hl.frequency.value = 180;
    const hg = c.createGain(); hg.gain.value = 0; hum.connect(hl).connect(hg).connect(this.master); hum.start();
    this.nodes.hum = hg;
  }

  toggle() {
    this.on = !this.on;
    try { localStorage.setItem(KEY, this.on ? "on" : "off"); } catch (e) {}
    if (!this.on && this.ctx) { this.ctx.close(); this.ctx = null; this.nodes = {}; }
    else this.start();
    return this.on;
  }

  _set(node, v, t) { if (node && this.ctx) node.gain.setTargetAtTime(v, this.ctx.currentTime, t || 0.2); }

  // called every few frames with the state of the scene
  update(s) {
    if (!this.ctx) return;
    this._set(this.nodes.rain, s.rain ? (s.inside ? 0.04 : 0.16) : 0, 0.5);
    this._set(this.nodes.engGain, s.driving ? 0.05 + Math.min(0.08, Math.abs(s.speed) * 0.003) : 0, 0.15);
    if (this.nodes.eng) this.nodes.eng.frequency.setTargetAtTime(38 + Math.abs(s.speed) * 5, this.ctx.currentTime, 0.1);
    this._set(this.nodes.siren, s.siren ? Math.max(0.015, 0.09 - s.sirenDist * 0.0012) : 0, 0.3);
    this._set(this.nodes.hum, s.hum ? 0.025 : 0, 0.3);
  }

  _burst(dur, freq, vol, type) {
    if (!this.ctx) return;
    const c = this.ctx, src = c.createBufferSource(); src.buffer = this.noise;
    const f = c.createBiquadFilter(); f.type = type || "lowpass"; f.frequency.value = freq;
    const g = c.createGain(); g.gain.setValueAtTime(vol, c.currentTime); g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + dur);
    src.connect(f).connect(g).connect(this.master); src.start(); src.stop(c.currentTime + dur + 0.05);
  }

  step() { this._burst(0.07, 500, 0.12); }
  hit() { this._burst(0.35, 300, 0.6); }
  crash() { this._burst(0.6, 900, 0.8); this._burst(0.25, 120, 0.9); }
  glitch() { this._burst(0.18, 3500, 0.25, "highpass"); }

  tone(freq, dur, vol, type) { // a short tone: doors, abilities
    if (!this.ctx) return;
    const c = this.ctx, o = c.createOscillator(); o.type = type || "square"; o.frequency.value = freq;
    const g = c.createGain(); g.gain.setValueAtTime(vol || 0.08, c.currentTime); g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + dur);
    o.connect(g).connect(this.master); o.start(); o.stop(c.currentTime + dur + 0.05);
  }

  slowmo() { // the Sandevistan: a falling whoosh
    if (!this.ctx) return;
    const c = this.ctx, o = c.createOscillator(); o.type = "sawtooth"; o.frequency.setValueAtTime(900, c.currentTime);
    o.frequency.exponentialRampToValueAtTime(60, c.currentTime + 0.8);
    const g = c.createGain(); g.gain.setValueAtTime(0.12, c.currentTime); g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 0.9);
    o.connect(g).connect(this.master); o.start(); o.stop(c.currentTime + 1);
  }
}
