// Paso 6: editor multipista con deshacer/rehacer y guardado automático.
//
// La reproducción en el navegador es una aproximación (salta entre escenas y
// sincroniza los audios). La vista previa exacta, con subtítulos y marca de
// agua renderizados, se genera en el servidor en el paso «Vista previa».
import { api, upload, assetUrl, ApiError } from '../api.js';
import { h, field, toast, notice, fmtTime, clear, debounce, modal } from '../ui.js';
import { History, outputDuration, sourceToOutput, outputToSource } from '../history.js';

const uid = () => Math.random().toString(36).slice(2, 12);

export async function render(root, project) {
  if (!project.proxy_asset_id) {
    root.append(notice('warn', 'Importe primero un vídeo.'));
    return;
  }
  const media = await api(`/api/projects/${project.id}/media`);
  const mediaById = Object.fromEntries(media.map(m => [m.id, m]));
  let tl = project.timeline;
  let version = project.timeline_version;
  const hist = new History(tl);
  let selected = null; // {kind, id}
  let pps = 40; // píxeles por segundo
  let outTime = 0;
  let showOriginal = false;
  const srcDur = project.source.duration;

  // ---------------------------------------------------------- reproductor
  const video = h('video', { class: 'player', playsinline: true, preload: 'auto' });
  const urls = { review: project.review_asset_id ? await assetUrl(project.review_asset_id) : null, original: await assetUrl(project.proxy_asset_id) };
  video.src = urls.review || urls.original;
  const audioEls = new Map(); // asset_id -> HTMLAudioElement
  async function audioFor(assetId) {
    if (!audioEls.has(assetId)) {
      const a = new Audio();
      a.preload = 'auto';
      a.src = await assetUrl(assetId);
      audioEls.set(assetId, a);
    }
    return audioEls.get(assetId);
  }
  for (const it of tl.dialogue.items) audioFor(it.asset_id);
  for (const t of tl.tracks) for (const c of t.clips) audioFor(c.asset_id);

  let playing = false;
  let raf = null;
  const timeLabel = h('span', { class: 'cost' }, '0:00.0');
  const playBtn = h('button', { class: 'btn primary small', onclick: () => (playing ? pause() : play()) }, '▶ Reproducir');

  function seek(t) {
    outTime = Math.max(0, Math.min(outputDuration(tl), t));
    const m = outputToSource(tl, outTime);
    if (m) video.currentTime = m.source;
    syncAudio(true);
    drawPlayhead();
  }
  function play() {
    if (outTime >= outputDuration(tl) - 0.05) seek(0);
    playing = true; playBtn.textContent = '❚❚ Pausa';
    seek(outTime);
    video.play().catch(() => {});
    loop();
  }
  function pause() {
    playing = false; playBtn.textContent = '▶ Reproducir';
    video.pause();
    for (const a of audioEls.values()) a.pause();
    cancelAnimationFrame(raf);
  }
  function loop() {
    raf = requestAnimationFrame(() => {
      if (!playing) return;
      const m = outputToSource(tl, outTime);
      if (!m) return pause();
      const src = video.currentTime;
      if (src >= m.clip.out - 0.04) {
        const next = tl.video.clips[m.index + 1];
        if (!next) { pause(); outTime = outputDuration(tl); drawPlayhead(); return; }
        outTime = m.offset + (m.clip.out - m.clip.in) + 0.001;
        video.currentTime = next.in;
      } else {
        outTime = m.offset + Math.max(0, src - m.clip.in);
      }
      syncAudio(false);
      drawPlayhead();
      loop();
    });
  }
  function syncAudio(force) {
    const m = outputToSource(tl, outTime);
    const srcT = m ? m.source : 0;
    const muted = tl.source_audio.mutes.some(x => srcT >= x.start && srcT < x.end);
    video.muted = muted;
    video.volume = Math.min(1, tl.source_audio.volume);
    const want = new Map();
    for (const it of tl.dialogue.items) {
      const local = srcT - it.source_start;
      const dur = mediaById[it.asset_id]?.duration || 0;
      if (local >= 0 && local < dur) want.set(it.asset_id, { local, vol: it.volume * tl.dialogue.volume });
    }
    for (const t of tl.tracks) {
      if (t.muted) continue;
      for (const c of t.clips) {
        const local = outTime - c.start + c.in;
        if (outTime >= c.start && local < c.out) want.set(c.asset_id + ':' + c.id, { asset: c.asset_id, local, vol: c.volume * t.volume });
      }
    }
    for (const [key, a] of audioEls) {
      if (![...want.entries()].some(([k, w]) => (w.asset || k) === key)) a.pause();
    }
    for (const [k, w] of want) {
      const a = audioEls.get(w.asset || k);
      if (!a) continue;
      a.volume = Math.min(1, w.vol);
      if (force || Math.abs(a.currentTime - w.local) > 0.25) a.currentTime = w.local;
      if (playing && a.paused) a.play().catch(() => {});
      if (!playing) a.pause();
    }
    timeLabel.textContent = `${fmtTime(outTime)} / ${fmtTime(outputDuration(tl))}`;
  }

  // ---------------------------------------------------------- guardado
  const saveState = h('small', { class: 'muted' }, 'Guardado');
  const save = debounce(async () => {
    saveState.textContent = 'Guardando…';
    try {
      const r = await api(`/api/projects/${project.id}/timeline`, { method: 'PUT', body: { version, timeline: tl } });
      version = r.version;
      saveState.textContent = 'Guardado ✓';
    } catch (e) {
      saveState.textContent = 'Sin guardar';
      if (e instanceof ApiError && e.status === 409) {
        const ok = await modal({ title: 'Cambios en otro lugar', body: h('p', {}, 'El proyecto se modificó desde otra ventana o por un procesamiento (por ejemplo, una sincronización labial). ¿Cargar la versión más reciente? Se perderán los cambios locales no guardados.'),
          actions: [{ label: 'Mantener aquí', value: false }, { label: 'Cargar', value: true, kind: 'primary' }] });
        if (ok) location.reload();
      } else toast(e.message, 'error');
    }
  }, 1200);
  function commit() {
    hist.push(tl);
    updateUndo();
    save();
    draw();
  }

  // ---------------------------------------------------------- dibujo de pistas
  const tlBox = h('div', { class: 'timeline' });
  const inner = h('div', { class: 'tl-inner' });
  const playhead = h('div', { class: 'tl-playhead' });
  tlBox.append(inner);
  const inspector = h('div', { class: 'inspector card flat' });

  function drawPlayhead() { playhead.style.left = `${outTime * pps}px`; }

  function clipEl(kind, cls, id, left, width, label, opts = {}) {
    const el = h('div', { class: `tl-clip ${cls} ${selected?.id === id ? 'sel' : ''}`, style: { left: `${left}px`, width: `${Math.max(6, width)}px` }, title: label }, label);
    el.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      selected = { kind, id };
      const handle = e.target.classList.contains('handle') ? (e.target.classList.contains('l') ? 'l' : 'r') : null;
      const x0 = e.clientX;
      let moved = false;
      const before = JSON.stringify(tl);
      const move = (ev) => {
        const dt = (ev.clientX - x0) / pps;
        if (Math.abs(ev.clientX - x0) > 3) moved = true;
        if (!moved || !opts.onDrag) return;
        tl = JSON.parse(before);
        opts.onDrag(dt, handle);
        draw(false);
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        if (moved && opts.onDrag) commit(); else { draw(); drawInspector(); }
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    });
    if (opts.handles) el.append(h('span', { class: 'handle l' }), h('span', { class: 'handle r' }));
    return el;
  }

  function track(label, children) {
    return h('div', { class: 'tl-track' }, h('span', { class: 'tl-label' }, label), ...children);
  }

  function draw(withInspector = true) {
    const dur = Math.max(outputDuration(tl), 1);
    const width = Math.max(tlBox.clientWidth || 600, (dur + 4) * pps);
    inner.style.width = `${width}px`;
    const ruler = h('div', { class: 'tl-ruler', onpointerdown: (e) => { const r = inner.getBoundingClientRect(); seek((e.clientX - r.left) / pps); } });
    const step = pps > 60 ? 1 : pps > 25 ? 2 : 5;
    for (let s = 0; s <= dur + 4; s += step) ruler.append(h('span', { style: { left: `${s * pps}px` } }, fmtTime(s).replace(/\.0$/, '')));

    let cur = 0;
    const vclips = tl.video.clips.map((c, i) => {
      const el = clipEl('video', 'video', c.id, cur * pps, (c.out - c.in) * pps, `Escena ${i + 1} · ${fmtTime(c.in)}–${fmtTime(c.out)}`, {
        handles: true,
        onDrag: (dt, handle) => {
          const cc = tl.video.clips[i];
          if (handle === 'l') cc.in = round(Math.min(cc.out - 0.2, Math.max(0, cc.in + dt)));
          else if (handle === 'r') cc.out = round(Math.max(cc.in + 0.2, Math.min(srcDur, cc.out + dt)));
        },
      });
      cur += c.out - c.in;
      return el;
    });
    const mutes = tl.source_audio.mutes.flatMap((m, i) => sourceToOutput(tl, m.start, m.end).map(([a, b]) =>
      clipEl('mute', 'mute', `mute${i}`, a * pps, (b - a) * pps, 'silenciado')));
    const dlg = tl.dialogue.items.flatMap(it => {
      const dur2 = mediaById[it.asset_id]?.duration || 1;
      return sourceToOutput(tl, it.source_start, it.source_start + dur2).map(([a, b]) =>
        clipEl('dialogue', 'dialogue', it.id, a * pps, (b - a) * pps, `Diálogo · ${Math.round(it.volume * 100)} %`, {
          onDrag: (dt) => {
            const x = tl.dialogue.items.find(y => y.id === it.id);
            x.source_start = round(Math.max(0, Math.min(srcDur - dur2, x.source_start + dt)));
          },
        }));
    });
    const subs = tl.subtitles.items.flatMap(s => {
      const spans = s.base === 'source' ? sourceToOutput(tl, s.start, s.end) : [[s.start, s.end]];
      return spans.map(([a, b]) => clipEl('sub', 'sub', s.id, a * pps, (b - a) * pps, s.text, {
        onDrag: s.base === 'source' ? null : (dt) => {
          const x = tl.subtitles.items.find(y => y.id === s.id);
          const d = x.end - x.start;
          x.start = round(Math.max(0, x.start + dt)); x.end = round(x.start + d);
        },
      }));
    });
    const gfx = tl.graphics.map(g => clipEl('gfx', 'gfx', g.id, g.start * pps, (g.end - g.start) * pps, `T · ${g.text}`, {
      handles: true,
      onDrag: (dt, handle) => {
        const x = tl.graphics.find(y => y.id === g.id);
        if (handle === 'l') x.start = round(Math.min(x.end - 0.2, Math.max(0, x.start + dt)));
        else if (handle === 'r') x.end = round(Math.max(x.start + 0.2, x.end + dt));
        else { const d = x.end - x.start; x.start = round(Math.max(0, x.start + dt)); x.end = round(x.start + d); }
      },
    }));
    const audioTracks = tl.tracks.map((t, ti) => track(`${t.name}${t.muted ? ' (silenciada)' : ''}`, t.clips.map(c =>
      clipEl('track', t.kind === 'music' ? 'music' : 'audio', c.id, c.start * pps, (c.out - c.in) * pps,
        mediaById[c.asset_id]?.meta?.filename || 'audio', {
          handles: true,
          onDrag: (dt, handle) => {
            const x = tl.tracks[ti].clips.find(y => y.id === c.id);
            const maxLen = mediaById[c.asset_id]?.duration || x.out;
            if (handle === 'l') { const n = Math.min(x.out - 0.2, Math.max(0, x.in + dt)); x.start = round(x.start + (n - x.in)); x.in = round(n); }
            else if (handle === 'r') x.out = round(Math.max(x.in + 0.2, Math.min(maxLen, x.out + dt)));
            else x.start = round(Math.max(0, x.start + dt));
          },
        }))));
    clear(inner).append(ruler, track('Vídeo', vclips), track('Audio original', mutes), track('Diálogos', dlg), ...audioTracks,
      track('Subtítulos', subs), track('Gráficos', gfx), playhead);
    drawPlayhead();
    timeLabel.textContent = `${fmtTime(outTime)} / ${fmtTime(outputDuration(tl))}`;
    if (withInspector) drawInspector();
  }
  const round = (v) => Math.round(v * 1000) / 1000;

  // ---------------------------------------------------------- inspector
  function num(label, value, onchange, step = 0.1, min = 0) {
    const i = h('input', { type: 'number', step, min, value, onchange: () => { onchange(Number(i.value)); commit(); } });
    return field(label, i);
  }
  function vol(label, value, onchange) {
    const out = h('output', {}, `${Math.round(value * 100)} %`);
    const i = h('input', { type: 'range', min: 0, max: 2, step: 0.05, value,
      oninput: () => { out.textContent = `${Math.round(i.value * 100)} %`; onchange(Number(i.value)); syncAudio(false); },
      onchange: () => commit() });
    return field(label, h('div', { class: 'range-row' }, i, out));
  }
  function drawInspector() {
    clear(inspector);
    const s = selected;
    const general = h('div', {}, h('h3', {}, 'Mezcla general'),
      vol('Audio original', tl.source_audio.volume, v => { tl.source_audio.volume = v; }),
      vol('Diálogos', tl.dialogue.volume, v => { tl.dialogue.volume = v; }),
      ...tl.tracks.map(t => h('div', {}, vol(t.name, t.volume, v => { t.volume = v; }),
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: t.muted, onchange: (e) => { t.muted = e.target.checked; commit(); } }), `Silenciar ${t.name.toLowerCase()}`))),
      field('Encaje del vídeo en el formato', h('select', { onchange: (e) => { tl.fit = e.target.value; commit(); } },
        [['contain_blur', 'Completo con fondo difuminado'], ['contain', 'Completo con bandas negras'], ['cover', 'Recortar para llenar']].map(([v, l]) => h('option', { value: v, selected: tl.fit === v }, l)))),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: tl.subtitles.enabled, onchange: (e) => { tl.subtitles.enabled = e.target.checked; commit(); } }), 'Mostrar subtítulos en el vídeo'));
    if (!s) { inspector.append(h('p', { class: 'muted' }, 'Seleccione un elemento de la línea de tiempo para editarlo.'), general); return; }
    const del = (fn) => h('button', { class: 'btn small danger', onclick: () => { fn(); selected = null; commit(); } }, 'Eliminar');
    if (s.kind === 'video') {
      const i = tl.video.clips.findIndex(c => c.id === s.id);
      const c = tl.video.clips[i];
      if (!c) return;
      inspector.append(h('h3', {}, `Escena ${i + 1}`),
        h('div', { class: 'cols even' }, num('Inicio en origen (s)', c.in, v => { c.in = Math.min(v, c.out - 0.2); }), num('Fin en origen (s)', c.out, v => { c.out = Math.min(srcDur, Math.max(v, c.in + 0.2)); })),
        h('div', { class: 'row' },
          h('button', { class: 'btn small', disabled: i === 0, onclick: () => { tl.video.clips.splice(i - 1, 0, tl.video.clips.splice(i, 1)[0]); commit(); } }, '← Mover antes'),
          h('button', { class: 'btn small', disabled: i === tl.video.clips.length - 1, onclick: () => { tl.video.clips.splice(i + 1, 0, tl.video.clips.splice(i, 1)[0]); commit(); } }, 'Mover después →'),
          h('button', { class: 'btn small', onclick: () => {
            const m = outputToSource(tl, outTime);
            if (!m || m.clip.id !== c.id || m.source - c.in < 0.2 || c.out - m.source < 0.2) return toast('Coloque el cursor dentro de esta escena para dividirla.', 'warn');
            tl.video.clips.splice(i + 1, 0, { id: uid(), in: round(m.source), out: c.out });
            c.out = round(m.source); commit();
          } }, 'Dividir en el cursor'),
          h('button', { class: 'btn small', onclick: () => { tl.video.clips.splice(i + 1, 0, { ...c, id: uid() }); commit(); } }, 'Duplicar'),
          tl.video.clips.length > 1 ? del(() => tl.video.clips.splice(i, 1)) : null));
    } else if (s.kind === 'dialogue') {
      const it = tl.dialogue.items.find(x => x.id === s.id);
      if (!it) return;
      inspector.append(h('h3', {}, 'Diálogo sincronizado'),
        notice('info', 'El diálogo está ligado a la imagen: si mueve su inicio, los labios sincronizados no lo acompañarán. Para cambiar el momento, repita la sincronización.'),
        num('Inicio en el vídeo de origen (s)', it.source_start, v => { it.source_start = v; }), vol('Volumen', it.volume, v => { it.volume = v; }),
        del(() => { tl.dialogue.items = tl.dialogue.items.filter(x => x.id !== it.id); }));
    } else if (s.kind === 'track') {
      const t = tl.tracks.find(tt => tt.clips.some(c => c.id === s.id));
      const c = t?.clips.find(x => x.id === s.id);
      if (!c) return;
      inspector.append(h('h3', {}, `${t.name}: ${mediaById[c.asset_id]?.meta?.filename || 'audio'}`),
        h('div', { class: 'cols even' }, num('Posición (s)', c.start, v => { c.start = v; }), num('Recorte inicial (s)', c.in, v => { c.in = Math.min(v, c.out - 0.2); })),
        num('Recorte final (s)', c.out, v => { c.out = Math.max(v, c.in + 0.2); }), vol('Volumen', c.volume, v => { c.volume = v; }),
        del(() => { t.clips = t.clips.filter(x => x.id !== c.id); }));
    } else if (s.kind === 'sub') {
      const it = tl.subtitles.items.find(x => x.id === s.id);
      if (!it) return;
      const text = h('textarea', { style: { minHeight: '60px' }, onchange: () => { it.text = text.value; commit(); } }, it.text);
      inspector.append(h('h3', {}, 'Subtítulo'), field('Texto', text),
        h('div', { class: 'cols even' }, num(`Inicio (${it.base === 'source' ? 'origen' : 'salida'}) (s)`, it.start, v => { it.start = Math.min(v, it.end - 0.1); }), num('Fin (s)', it.end, v => { it.end = Math.max(v, it.start + 0.1); })),
        del(() => { tl.subtitles.items = tl.subtitles.items.filter(x => x.id !== it.id); }));
    } else if (s.kind === 'gfx') {
      const g = tl.graphics.find(x => x.id === s.id);
      if (!g) return;
      const text = h('input', { type: 'text', value: g.text, maxlength: 120, onchange: () => { g.text = text.value; commit(); } });
      const color = h('select', { onchange: () => { g.color = color.value; commit(); } }, [['white', 'Blanco'], ['yellow', 'Amarillo'], ['black', 'Negro'], ['red', 'Rojo'], ['cyan', 'Cian']].map(([v, l]) => h('option', { value: v, selected: g.color === v }, l)));
      inspector.append(h('h3', {}, 'Texto en pantalla'), field('Texto', text), field('Color', color),
        h('div', { class: 'cols even' }, num('Inicio (s)', g.start, v => { g.start = v; }), num('Fin (s)', g.end, v => { g.end = Math.max(v, g.start + 0.2); })),
        h('div', { class: 'cols even' }, num('Posición horizontal (0-1)', g.x, v => { g.x = Math.min(1, Math.max(0, v)); }, 0.05), num('Posición vertical (0-1)', g.y, v => { g.y = Math.min(1, Math.max(0, v)); }, 0.05)),
        num('Tamaño (fracción de la altura)', g.size, v => { g.size = Math.min(0.2, Math.max(0.02, v)); }, 0.01),
        notice('info', 'La marca de agua de IA siempre se dibuja por encima de los gráficos.'),
        del(() => { tl.graphics = tl.graphics.filter(x => x.id !== g.id); }));
    } else if (s.kind === 'mute') {
      const i = Number(s.id.replace('mute', ''));
      const m = tl.source_audio.mutes[i];
      if (!m) return;
      inspector.append(h('h3', {}, 'Silencio del audio original'),
        h('div', { class: 'cols even' }, num('Inicio en origen (s)', m.start, v => { m.start = v; }), num('Fin (s)', m.end, v => { m.end = Math.max(v, m.start + 0.1); })),
        del(() => tl.source_audio.mutes.splice(i, 1)));
    }
    inspector.append(h('hr', { style: { borderColor: 'var(--line)', margin: '16px 0' } }), general);
  }

  // ---------------------------------------------------------- barra de herramientas
  const undoBtn = h('button', { class: 'btn small', title: 'Deshacer (Ctrl+Z)', onclick: () => { tl = hist.undo(); updateUndo(); save(); draw(); } }, '↶ Deshacer');
  const redoBtn = h('button', { class: 'btn small', title: 'Rehacer (Ctrl+Mayús+Z)', onclick: () => { tl = hist.redo(); updateUndo(); save(); draw(); } }, '↷ Rehacer');
  function updateUndo() { undoBtn.disabled = !hist.canUndo; redoBtn.disabled = !hist.canRedo; }
  updateUndo();
  const fileIn = h('input', { type: 'file', accept: 'audio/*', hidden: true });
  const addAudio = (kind) => {
    fileIn.onchange = async () => {
      const f = fileIn.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append('file', f);
      try {
        toast('Subiendo audio…');
        const r = await upload(`/api/projects/${project.id}/media?kind=${kind}`, fd);
        mediaById[r.asset_id] = { id: r.asset_id, duration: r.duration, meta: { filename: r.name } };
        await audioFor(r.asset_id);
        const t = tl.tracks.find(x => x.kind === kind) || tl.tracks[0];
        t.clips.push({ id: uid(), asset_id: r.asset_id, start: round(outTime), in: 0, out: round(Math.min(r.duration, outputDuration(tl) - outTime || r.duration)), volume: 1 });
        commit();
      } catch (e) { toast(e.message, 'error'); }
      fileIn.value = '';
    };
    fileIn.click();
  };
  const toolbar = h('div', { class: 'tl-toolbar' }, playBtn, timeLabel, undoBtn, redoBtn,
    h('button', { class: 'btn small', onclick: () => addAudio('music') }, '+ Música'),
    h('button', { class: 'btn small', onclick: () => addAudio('audio') }, '+ Audio'),
    h('button', { class: 'btn small', onclick: () => {
      const id = uid();
      tl.subtitles.items.push({ id, start: round(outTime), end: round(outTime + 2), text: 'Nuevo subtítulo', base: 'output' });
      selected = { kind: 'sub', id }; commit();
    } }, '+ Subtítulo'),
    h('button', { class: 'btn small', onclick: () => {
      const id = uid();
      tl.graphics.push({ id, text: 'Título', start: round(outTime), end: round(outTime + 3), x: 0.5, y: 0.15, size: 0.07, color: 'white' });
      selected = { kind: 'gfx', id }; commit();
    } }, '+ Texto'),
    h('button', { class: 'btn small ghost', onclick: () => { pps = Math.max(8, pps / 1.5); draw(false); } }, '−'),
    h('button', { class: 'btn small ghost', onclick: () => { pps = Math.min(240, pps * 1.5); draw(false); } }, '+'),
    h('span', { class: 'spacer' }), saveState, fileIn);
  const compareBtn = h('button', { class: 'btn small', disabled: !urls.review, onclick: () => {
    showOriginal = !showOriginal;
    const t = video.currentTime;
    video.src = showOriginal ? urls.original : urls.review;
    video.currentTime = t;
    compareBtn.textContent = showOriginal ? 'Ver resultado' : 'Ver original';
    compareLabel.textContent = showOriginal ? 'Mostrando: ORIGINAL' : 'Mostrando: RESULTADO (con marca de agua)';
  } }, 'Ver original');
  const compareLabel = h('small', { class: 'muted' }, urls.review ? 'Mostrando: RESULTADO (con marca de agua)' : 'Vídeo sin procesar');

  const onKey = (e) => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); (e.shiftKey ? redoBtn : undoBtn).click(); }
    if (e.code === 'Space') { e.preventDefault(); playBtn.click(); }
  };
  window.addEventListener('keydown', onKey);
  tlBox.addEventListener('pointerdown', () => { selected = null; draw(); });

  root.append(h('div', { class: 'cols' },
    h('div', { class: 'card' }, video, h('div', { class: 'row' }, compareBtn, compareLabel),
      h('small', { class: 'muted' }, 'Reproducción aproximada en el navegador. La vista previa exacta (subtítulos, gráficos y marca de agua) se genera en el paso siguiente.')),
    inspector),
  h('div', { class: 'card' }, toolbar, tlBox),
  h('div', { class: 'row end' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/vista` }, 'Vista previa →')));
  draw();
  return () => { pause(); window.removeEventListener('keydown', onKey); };
}
