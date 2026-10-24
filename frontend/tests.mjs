// Pruebas del cliente: node --test frontend/tests.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { History, outputDuration, sourceToOutput, outputToSource } from './js/history.js';

const tl = { video: { clips: [{ id: 'a', in: 5, out: 8 }, { id: 'b', in: 0, out: 2 }] } };

test('duración y proyección de tiempos coinciden con el servidor', () => {
  assert.equal(outputDuration(tl), 5);
  assert.deepEqual(sourceToOutput(tl, 6, 7), [[1, 2]]);
  assert.deepEqual(sourceToOutput(tl, 1, 6), [[0, 1], [4, 5]]);
  const m = outputToSource(tl, 3.5);
  assert.equal(m.clip.id, 'b');
  assert.equal(m.source, 0.5);
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
