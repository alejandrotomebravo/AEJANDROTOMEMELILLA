// Pila de deshacer/rehacer basada en instantáneas de la línea de tiempo.
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

// Proyección del tiempo de origen sobre el tiempo de salida (igual que en el servidor).
export function outputDuration(tl) {
  return tl.video.clips.reduce((a, c) => a + (c.out - c.in), 0);
}

export function sourceToOutput(tl, a, b) {
  const res = [];
  let cur = 0;
  for (const c of tl.video.clips) {
    const lo = Math.max(a, c.in); const hi = Math.min(b, c.out);
    if (hi > lo) res.push([cur + lo - c.in, cur + hi - c.in]);
    cur += c.out - c.in;
  }
  return res;
}

export function outputToSource(tl, t) {
  let cur = 0;
  for (let i = 0; i < tl.video.clips.length; i++) {
    const c = tl.video.clips[i];
    const d = c.out - c.in;
    if (t < cur + d || i === tl.video.clips.length - 1) return { index: i, clip: c, source: c.in + Math.min(d, Math.max(0, t - cur)), offset: cur };
    cur += d;
  }
  return null;
}
