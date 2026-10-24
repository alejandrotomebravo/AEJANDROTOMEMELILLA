// Pila de deshacer/rehacer y proyección de tiempos (idéntica a la del servidor).
export class History {
  constructor(initial, limit = 100) {
    this.stack = [JSON.stringify(initial)];
    this.index = 0;
    this.limit = limit;
  }
  push(state) {
    const s = JSON.stringify(state);
    if (s === this.stack[this.index]) return;
    this.stack = this.stack.slice(0, this.index + 1);
    this.stack.push(s);
    if (this.stack.length > this.limit) this.stack.shift();
    this.index = this.stack.length - 1;
  }
  get canUndo() { return this.index > 0; }
  get canRedo() { return this.index < this.stack.length - 1; }
  undo() { if (this.canUndo) this.index--; return JSON.parse(this.stack[this.index]); }
  redo() { if (this.canRedo) this.index++; return JSON.parse(this.stack[this.index]); }
  current() { return JSON.parse(this.stack[this.index]); }
}

export const clipLength = (c) => (c.out - c.in) / (c.speed || 1);

export function outputDuration(tl) {
  return tl.video.clips.reduce((a, c) => a + clipLength(c), 0);
}

// Intervalo [a, b] de la fuente `sid` → lista de intervalos en tiempo de salida.
export function sourceToOutput(tl, sid, a, b) {
  const res = [];
  let cur = 0;
  for (const c of tl.video.clips) {
    const sp = c.speed || 1;
    if (c.source_id === sid) {
      const lo = Math.max(a, c.in); const hi = Math.min(b, c.out);
      if (hi > lo) res.push([cur + (lo - c.in) / sp, cur + (hi - c.in) / sp]);
    }
    cur += clipLength(c);
  }
  return res;
}

// Tiempo de salida → escena, fuente y tiempo dentro de la fuente.
export function outputToSource(tl, t) {
  let cur = 0;
  const clips = tl.video.clips;
  for (let i = 0; i < clips.length; i++) {
    const c = clips[i];
    const d = clipLength(c);
    if (t < cur + d || i === clips.length - 1) {
      const local = Math.min(d, Math.max(0, t - cur));
      return { index: i, clip: c, source_id: c.source_id, source: c.in + local * (c.speed || 1), offset: cur };
    }
    cur += d;
  }
  return null;
}

export function parseTime(v) {
  // Acepta «75», «1:15», «1:15.5» o «0:01:15».
  const s = String(v).trim().replace(',', '.');
  if (!s) return NaN;
  return s.split(':').reduce((acc, p) => acc * 60 + Number(p), 0);
}
