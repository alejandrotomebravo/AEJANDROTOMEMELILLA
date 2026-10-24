// Paso 3: selección de escenas. Fotogramas, búsqueda por tiempo, varias escenas y orden.
import { api, assetUrl } from '../api.js';
import { h, field, toast, notice, clear, fmtTime, fmtDuration, checkbox } from '../ui.js';
import { sourceSelect } from '../components.js';
import { parseTime, clipLength, outputDuration } from '../history.js';

const uid = () => Math.random().toString(36).slice(2, 12);

export async function render(root, project) {
  const sources = project.sources.filter(s => s.status === 'ready');
  if (!sources.length) {
    root.append(notice('warn', 'Importe primero algún vídeo desde ', h('a', { href: `#/proyecto/${project.id}/internet` }, 'Internet'), ' o desde ',
      h('a', { href: `#/proyecto/${project.id}/archivos` }, 'sus archivos'), '.'));
    return;
  }
  const byId = Object.fromEntries(project.sources.map(s => [s.id, s]));
  let tl = project.timeline;
  let version = project.timeline_version;
  let current = sources[0];
  const player = h('video', { class: 'player', controls: true, playsinline: true, preload: 'metadata' });
  const strip = h('div', { class: 'sprite-strip' });
  const sceneList = h('div', { class: 'list' });
  const audioList = h('div', { class: 'list' });
  const tlList = h('div', { class: 'list' });
  const info = h('div');

  async function save(msg) {
    try {
      const r = await api(`/api/projects/${project.id}/timeline`, { method: 'PUT', body: { version, timeline: tl } });
      tl = r.timeline; version = r.version;
      if (msg) toast(msg, 'ok');
      drawTimeline();
    } catch (e) {
      toast(e.message, 'error');
      if (e.status === 409) location.reload();
    }
  }

  async function loadSource(s) {
    current = s;
    player.src = await assetUrl(s.review_asset_id || s.proxy_asset_id);
    clear(info).append(h('small', { class: 'muted' }, `${fmtDuration(s.duration)} · ${s.width}×${s.height} · ${Math.round(s.fps || 0)} fps · ${s.processed ? 'versión procesada (con marca de agua)' : 'original'}`));
    inT.value = '0:00'; outT.value = fmtTime(s.duration);
    // Tira de fotogramas (mosaico generado en el servidor)
    clear(strip);
    const sp = s.analysis?.sprite;
    if (s.sprite_asset_id && sp) {
      const url = await assetUrl(s.sprite_asset_id);
      const tw = 96;
      const scale = tw / sp.tile_width;
      const th = Math.round(sp.tile_width * s.height / s.width / 2) * 2; // alto real de cada fotograma del mosaico
      for (let i = 0; i < sp.count; i++) {
        const col = i % sp.cols; const row = Math.floor(i / sp.cols);
        const t = i * sp.interval;
        strip.append(h('button', { class: 'frame', title: fmtTime(t), onclick: () => { player.currentTime = t; },
          style: { backgroundImage: `url("${url}")`, backgroundSize: `${sp.cols * tw}px auto`,
            width: `${tw}px`, height: `${Math.round(th * scale)}px`,
            backgroundPosition: `-${col * tw}px -${Math.round(row * th * scale)}px` } },
        h('span', {}, fmtTime(t).replace(/\.\d$/, ''))));
      }
    }
    // Escenas detectadas
    const cuts = [0, ...(s.analysis?.scenes || []).filter(c => c > 0.3 && c < s.duration - 0.3), s.duration];
    clear(sceneList).append(...cuts.slice(0, -1).map((a, i) => h('div', { class: 'list-item' },
      h('button', { class: 'btn small ghost grow', onclick: () => { player.currentTime = a; } }, `Escena ${i + 1}: ${fmtTime(a)} – ${fmtTime(cuts[i + 1])}`),
      h('button', { class: 'btn small', onclick: () => addClip(a, cuts[i + 1]) }, '+ Añadir'))));
    if (cuts.length === 2) sceneList.append(h('small', { class: 'muted' }, 'No se detectaron cortes de escena en este vídeo.'));
    clear(audioList).append(...(s.analysis?.speech_segments || []).slice(0, 15).map(seg => h('button', {
      class: 'btn small ghost', onclick: () => { player.currentTime = seg.start; player.play(); } }, `${fmtTime(seg.start)}–${fmtTime(seg.end)}`)));
  }

  const go = h('input', { type: 'text', inputmode: 'decimal', placeholder: 'min:seg', style: { maxWidth: '120px' } });
  const inT = h('input', { type: 'text', inputmode: 'decimal' });
  const outT = h('input', { type: 'text', inputmode: 'decimal' });
  let keepAudio = true;
  const keep = checkbox('Conservar el audio original de la escena', true, v => { keepAudio = v; });
  const here = (inp) => h('button', { class: 'btn small ghost', type: 'button', onclick: () => { inp.value = fmtTime(player.currentTime); } }, 'Posición actual');

  function addClip(a, b) {
    if (!(b - a >= 0.1)) return toast('La escena debe durar al menos 0,1 s.', 'warn');
    tl.video.clips.push({ id: uid(), source_id: current.id, in: +a.toFixed(3), out: +Math.min(b, current.duration).toFixed(3), speed: 1,
      volume: keepAudio ? 1 : 0, fade_in: 0, fade_out: 0, color: { brightness: 0, contrast: 1, saturation: 1 } });
    save(`Escena añadida (${fmtTime(a)}–${fmtTime(b)})`);
  }

  function drawTimeline() {
    clear(tlList);
    tl.video.clips.forEach((c, i) => {
      const s = byId[c.source_id];
      tlList.append(h('div', { class: 'list-item' },
        h('span', { class: 'pill' }, i + 1),
        h('div', { class: 'grow' }, h('strong', {}, s?.label || 'Fuente'), h('div', {}, h('small', { class: 'muted' },
          `${fmtTime(c.in)}–${fmtTime(c.out)} · ${fmtDuration(clipLength(c))}${c.volume === 0 ? ' · sin audio original' : ''}${c.speed !== 1 ? ` · ${c.speed}×` : ''}`))),
        h('button', { class: 'btn small ghost', disabled: i === 0, 'aria-label': 'Subir', onclick: () => { tl.video.clips.splice(i - 1, 0, tl.video.clips.splice(i, 1)[0]); save(); } }, '↑'),
        h('button', { class: 'btn small ghost', disabled: i === tl.video.clips.length - 1, 'aria-label': 'Bajar', onclick: () => { tl.video.clips.splice(i + 1, 0, tl.video.clips.splice(i, 1)[0]); save(); } }, '↓'),
        h('button', { class: 'btn small ghost', onclick: () => { c.volume = c.volume === 0 ? 1 : 0; save(c.volume ? 'Audio original activado' : 'Audio original eliminado'); } }, c.volume === 0 ? '🔇' : '🔊'),
        h('button', { class: 'btn small danger', onclick: () => { tl.video.clips.splice(i, 1); save('Escena quitada'); } }, 'Quitar')));
    });
    if (!tl.video.clips.length) tlList.append(notice('info', 'La línea de tiempo está vacía: añada escenas.'));
    totalLabel.textContent = `Duración del montaje: ${fmtDuration(outputDuration(tl))}`;
  }
  const totalLabel = h('strong');

  root.append(h('div', { class: 'cols' },
    h('div', {},
      h('div', { class: 'card' },
        field('Vídeo', sourceSelect(sources, current.id, (id) => loadSource(byId[id]))), info, player,
        h('div', { class: 'row' }, h('span', {}, 'Ir a:'), go, h('button', { class: 'btn small', onclick: () => {
          const t = parseTime(go.value);
          if (Number.isNaN(t) || t < 0 || t > current.duration) return toast('Tiempo fuera del vídeo.', 'warn');
          player.currentTime = t;
        } }, 'Buscar')),
        h('h3', { style: { marginTop: '12px' } }, 'Fotogramas'), strip),
      h('div', { class: 'card' }, h('h2', {}, 'Recortar una escena'),
        h('div', { class: 'cols even' }, field('Inicio (min:seg)', h('div', {}, inT, here(inT))), field('Final (min:seg)', h('div', {}, outT, here(outT)))),
        keep.el,
        h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: () => {
          const a = parseTime(inT.value); const b = parseTime(outT.value);
          if (Number.isNaN(a) || Number.isNaN(b)) return toast('Escriba los tiempos como min:seg.', 'warn');
          addClip(a, b);
        } }, '+ Añadir a la línea de tiempo')))),
    h('div', {},
      h('div', { class: 'card' }, h('h2', {}, 'Línea de tiempo'), totalLabel, tlList,
        h('div', { class: 'row end' }, h('a', { class: 'btn primary', href: `#/proyecto/${project.id}/rostros` }, 'Continuar con rostros →'))),
      h('div', { class: 'card' }, h('h2', {}, 'Escenas detectadas'), sceneList),
      h('div', { class: 'card' }, h('h2', {}, 'Actividad de audio'), h('div', { class: 'row' }, audioList),
        h('small', { class: 'muted' }, 'Detección por energía: incluye voz, música y ruido.')))));
  await loadSource(current);
  drawTimeline();
}
