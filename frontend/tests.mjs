// Pruebas del cliente: node --test frontend/tests.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { History, outputDuration, sourceToOutput, outputToSource, parseTime } from './js/history.js';

const tl = { video: { clips: [
  { id: 'a', source_id: 'A', in: 5, out: 8, speed: 1 },
  { id: 'b', source_id: 'B', in: 0, out: 2, speed: 2 },
  { id: 'c', source_id: 'A', in: 0, out: 2, speed: 1 },
] } };

test('proyección de tiempos con varias fuentes y velocidad (igual que el servidor)', () => {
  assert.equal(outputDuration(tl), 6);
  assert.deepEqual(sourceToOutput(tl, 'A', 6, 7), [[1, 2]]);
  assert.deepEqual(sourceToOutput(tl, 'B', 0, 2), [[3, 4]]);
  assert.deepEqual(sourceToOutput(tl, 'A', 1, 6), [[0, 1], [5, 6]]);
  const m = outputToSource(tl, 3.5);
  assert.equal(m.clip.id, 'b');
  assert.equal(m.source, 1);
  assert.equal(outputToSource(tl, 4.5).source_id, 'A');
});

test('lectura de tiempos mm:ss', () => {
  assert.equal(parseTime('75'), 75);
  assert.equal(parseTime('1:15'), 75);
  assert.equal(parseTime('1:15,5'), 75.5);
  assert.equal(parseTime('0:01:15'), 75);
  assert.ok(Number.isNaN(parseTime('')));
});

test('deshacer y rehacer', () => {
  const hst = new History({ v: 1 });
  hst.push({ v: 2 }); hst.push({ v: 3 });
  assert.deepEqual(hst.undo(), { v: 2 });
  assert.deepEqual(hst.undo(), { v: 1 });
  assert.equal(hst.canUndo, false);
  assert.deepEqual(hst.redo(), { v: 2 });
  hst.push({ v: 9 });
  assert.equal(hst.canRedo, false);
});

test('todos los módulos de pantalla se cargan sin errores de sintaxis', async () => {
  const { readdirSync } = await import('node:fs');
  const files = readdirSync(new URL('./js/screens/', import.meta.url)).filter(f => f.endsWith('.js'));
  for (const f of ['api.js', 'ui.js', 'components.js', ...files.map(x => `screens/${x}`)]) {
    const mod = await import(`./js/${f}`);
    if (f.startsWith('screens/')) assert.equal(typeof mod.render, 'function', f);
  }
});
