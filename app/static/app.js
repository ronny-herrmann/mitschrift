/* Protokollant – Oberfläche (reines JavaScript, kein Build-Schritt, keine externen Abhängigkeiten). */
(() => {
  'use strict';

  // ================================================================ Hilfen
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const view = $('#view');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (sec) => {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(s).padStart(2, '0');
  };
  const fmtDur = (sec) => { sec = Math.round(sec || 0); if (sec < 60) return sec + ' s'; const m = Math.round(sec / 60); return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min'; };
  const fmtDate = (iso, opts = { dateStyle: 'medium', timeStyle: 'short' }) => { try { return new Date(iso).toLocaleString('de-DE', opts); } catch { return iso; } };
  const COLORS = ['#0069FF', '#E5484D', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#DB2777', '#65A30D'];
  const initials = (name) => { const m = /^Sprecher\s+(\d+)/i.exec(name || ''); return m ? 'S' + m[1] : (name || '?').split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase(); };

  let toastTimer;
  const toast = (msg, err = false) => {
    const t = $('#toast'); t.textContent = msg; t.className = 'toast show' + (err ? ' err' : '');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = 'toast'), err ? 6500 : 3200);
  };
  const api = async (path, opts = {}) => {
    const r = await fetch(path, { headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) }, ...opts });
    if (r.status === 401) { location.href = '/login'; throw new Error('Bitte anmelden'); }
    if (!r.ok) { let msg = r.statusText; try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch { } const e = new Error(msg); e.status = r.status; throw e; }
    const ct = r.headers.get('content-type') || '';
    return ct.includes('application/json') ? r.json() : r.text();
  };
  const modal = {
    open(html) { $('#modal-card').innerHTML = html; $('#modal').classList.remove('hidden'); return $('#modal-card'); },
    close() { $('#modal').classList.add('hidden'); $('#modal-card').innerHTML = ''; },
  };
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') modal.close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { modal.close(); $$('.menu-list').forEach((m) => m.remove()); } });
  const copy = async (text) => { try { await navigator.clipboard.writeText(text); return true; } catch { return false; } };
  const ICON = {
    spark: '<svg viewBox="0 0 24 24"><path d="M12 3l1.8 4.9L19 9.7l-4.9 1.8L12 16.4l-1.8-4.9L5.3 9.7l4.9-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>',
    doc: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>',
    down: '<svg viewBox="0 0 24 24"><path d="M12 4v12m0 0-4-4m4 4 4-4M4 20h16"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>',
    upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
  };

  // ================================================================ Systemstatus
  let health = { ready: false, load_status: 'lädt' };
  async function loadHealth() {
    try {
      health = await api('/api/health');
      const sys = $('#sys'), txt = $('#sys-txt'), banner = $('#banner');
      const live = (health.models && health.models.live) || health.model || {};
      if (health.ready) {
        sys.className = 'sys ok';
        txt.textContent = `${live.model || live.backend || 'bereit'}${health.llm_configured ? ' · KI: ' + health.llm_model : ''}`;
        sys.title = JSON.stringify(health.models || {}, null, 1);
        banner.classList.add('hidden');
      } else if (health.load_status === 'fehler') {
        sys.className = 'sys err'; txt.textContent = 'Modellfehler';
        banner.innerHTML = `Sprachmodell konnte nicht geladen werden: ${esc(health.load_error)}`; banner.classList.remove('hidden');
      } else {
        sys.className = 'sys loading'; txt.textContent = 'Sprachmodell lädt …';
        banner.innerHTML = '<span class="spin"></span> Das Sprachmodell wird geladen – beim ersten Start bis zu 1–2 Minuten. Die Seite ist gleich einsatzbereit.';
        banner.classList.remove('hidden');
      }
      if (health.auth && !$('#logout')) {
        const a = document.createElement('a'); a.id = 'logout'; a.href = '#'; a.textContent = 'Abmelden';
        a.addEventListener('click', async (e) => { e.preventDefault(); await fetch('/api/logout', { method: 'POST' }); location.href = '/login'; });
        $('#sys').appendChild(a);
      }
      document.dispatchEvent(new CustomEvent('health'));
    } catch {
      $('#sys').className = 'sys err'; $('#sys-txt').textContent = 'Server nicht erreichbar';
    }
    setTimeout(loadHealth, health.ready ? 30000 : 2000);
  }

  // ================================================================ Seitenleiste
  let sideItems = [];
  async function loadSide() {
    try { sideItems = await api('/api/transcripts'); } catch { return; }
    drawSide();
  }
  function groupLabel(iso) {
    const d = new Date(iso), now = new Date(), day = 86400000;
    const diff = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / day);
    return diff === 0 ? 'Heute' : diff === 1 ? 'Gestern' : diff < 7 ? 'Diese Woche' : 'Älter';
  }
  function drawSide() {
    const q = ($('#side-q').value || '').trim().toLowerCase();
    const cur = (location.hash.match(/#\/t\/(\w+)/) || [])[1];
    const items = sideItems.filter((t) => !q || t.title.toLowerCase().includes(q));
    let html = '', last = '';
    for (const t of items) {
      const g = groupLabel(t.created_at);
      if (g !== last) { html += `<div class="side-group">${g}</div>`; last = g; }
      html += `<a class="side-item ${t.id === cur ? 'active' : ''}" href="#/t/${t.id}"><div class="t1"><span class="stat-dot ${t.status}"></span>${esc(t.title)}</div><div class="t2">${fmtDate(t.created_at, { hour: '2-digit', minute: '2-digit' })} · ${fmtDur(t.duration)}${t.source === 'live' ? ' · live' : ''}</div></a>`;
    }
    $('#side-list').innerHTML = html || '<div class="side-group">Noch keine Aufnahmen</div>';
  }
  $('#side-q').addEventListener('input', drawSide);
  setInterval(() => { if (sideItems.some((t) => t.status === 'processing' || t.status === 'refining')) loadSide(); }, 3000);

  // ================================================================ Router
  const routes = { aufnahme: renderAufnahme, hochladen: renderHochladen, transkripte: renderListe, glossar: renderGlossar, faq: () => renderInfo('faq'), infos: renderInfo, t: renderDetail };
  let cleanup = null, recordingActive = false;
  async function route() {
    if (recordingActive && !location.hash.startsWith('#/aufnahme')) {
      if (!confirm('Die Aufnahme läuft noch. Wirklich verlassen? Die Aufnahme wird dann beendet und gespeichert.')) { history.back(); return; }
    }
    if (cleanup) { try { cleanup(); } catch { } cleanup = null; }
    const hash = location.hash.replace(/^#\/?/, '') || 'aufnahme';
    const [name, arg] = hash.split('/');
    $$('[data-nav]').forEach((a) => a.classList.toggle('active', a.dataset.nav === name));
    drawSide();
    view.innerHTML = '';
    window.scrollTo(0, 0);
    try { cleanup = await (routes[name] || renderAufnahme)(arg); } catch (e) { view.innerHTML = `<div class="empty"><h2>Fehler</h2><p>${esc(e.message)}</p></div>`; }
  }
  window.addEventListener('hashchange', route);

  // ================================================================ Aufnahme
  async function renderAufnahme() {
    view.innerHTML = `
      <section class="rec-setup" id="setup">
        <h1>Neue Aufnahme</h1>
        <p class="lead">Der Text erscheint, während gesprochen wird – grau vorläufig, schwarz sobald der Satz steht.</p>
        <div class="rec-fields">
          <label>Titel<input id="r-title" type="text" placeholder="z. B. Dienstbesprechung 10.5" autocomplete="off"></label>
          <label>Mikrofon<select id="r-mic"><option value="">Standard</option></select></label>
        </div>
        <label class="consent"><input type="checkbox" id="r-consent"><span>Alle Teilnehmenden sind über Aufzeichnung und Transkription informiert und einverstanden.</span></label>
        <button class="big-rec" id="r-start" disabled aria-label="Aufnahme starten"><span></span></button>
        <div class="rec-hint" id="r-hint">Bitte zuerst die Information der Teilnehmenden bestätigen.</div>
        <div class="rec-tips">
          <div><strong>Mikrofon nah dran</strong>Ein Konferenzmikrofon in der Tischmitte bringt mehr als jedes Modell.</div>
          <div><strong>Alles bleibt im Haus</strong>Audio und Text werden nur auf diesem Server verarbeitet.</div>
          <div><strong>Nach dem Stopp</strong>Genauer Durchlauf, Sprechererkennung und KI-Bereinigung laufen im Hintergrund.</div>
        </div>
      </section>
      <section class="rec-live hidden" id="live">
        <div class="rec-bar">
          <div class="rec-state"><i></i><span>Aufnahme</span></div>
          <div class="rec-time" id="r-time">00:00</div>
          <canvas class="wave" id="r-wave"></canvas>
          <button class="stop-btn" id="r-stop" aria-label="Aufnahme beenden" title="Aufnahme beenden"><span></span></button>
        </div>
        <div class="rec-sub"><span id="r-status">Hört zu …</span><span id="r-info"></span></div>
        <div class="rec-title-live" id="r-title-live"></div>
        <div class="live-text" id="r-text"><p class="live-empty caret">Sprechen Sie – der Text erscheint hier</p></div>
      </section>`;

    const startBtn = $('#r-start'), consent = $('#r-consent'), hint = $('#r-hint'), micSel = $('#r-mic');
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      hint.textContent = 'Kein Mikrofonzugriff möglich – die Seite muss über HTTPS (oder localhost) geöffnet werden.';
      return;
    }
    try {
      (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audioinput').forEach((d, i) => {
        const o = document.createElement('option'); o.value = d.deviceId; o.textContent = d.label || `Mikrofon ${i + 1}`; micSel.appendChild(o);
      });
    } catch { }
    const updateStart = () => {
      startBtn.disabled = !consent.checked || !health.ready;
      hint.textContent = !health.ready ? 'Sprachmodell lädt noch …' : consent.checked ? 'Zum Starten tippen' : 'Bitte zuerst die Information der Teilnehmenden bestätigen.';
    };
    consent.addEventListener('change', updateStart);
    document.addEventListener('health', updateStart);
    updateStart();

    let ctx, node, analyser, stream, ws, t0, tick, raf, wakeLock = null, finished = false, lastLevels = [];
    const text = $('#r-text');
    const setStatus = (s) => { $('#r-status').textContent = s; };
    const nearBottom = () => window.innerHeight + window.scrollY > document.body.scrollHeight - 160;
    const follow = (was) => { if (was) window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }); };

    async function lockScreen() { try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { } }
    const onVis = () => {
      if (document.visibilityState === 'visible' && recordingActive) { lockScreen(); if (ctx && ctx.state === 'suspended') { ctx.resume(); toast('Die Aufnahme war im Hintergrund pausiert – bitte das Handy während der Aufnahme nicht sperren.', true); } }
    };
    document.addEventListener('visibilitychange', onVis);

    function drawWave() {
      const c = $('#r-wave'); if (!c || !analyser) return;
      const dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
      if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
      const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      const buf = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(buf);
      let sum = 0; for (const v of buf) sum += v * v;
      const lvl = Math.min(1, Math.sqrt(sum / buf.length) * 6);
      lastLevels.push(lvl); const bars = Math.floor(w / 4); if (lastLevels.length > bars) lastLevels = lastLevels.slice(-bars);
      g.fillStyle = '#0069FF';
      lastLevels.forEach((v, i) => { const bh = Math.max(2, v * (h - 4)); const x = w - (lastLevels.length - i) * 4; g.globalAlpha = 0.35 + 0.65 * (i / lastLevels.length); g.fillRect(x, (h - bh) / 2, 2.5, bh); });
      raf = requestAnimationFrame(drawWave);
    }

    let partialEl = null;
    function onMessage(m) {
      const was = nearBottom();
      if (m.type === 'ready') { $('#r-info').textContent = m.ai_clean ? '✨ KI-Bereinigung live aktiv' : ''; }
      else if (m.type === 'partial') {
        $('.live-empty', text)?.remove();
        if (!partialEl) { partialEl = document.createElement('p'); text.appendChild(partialEl); }
        partialEl.innerHTML = `<span class="ts">${fmt(m.start)}</span><span class="partial">${esc(m.text)}</span>`;
        follow(was);
      }
      else if (m.type === 'segment') {
        $('.live-empty', text)?.remove();
        const p = partialEl || document.createElement('p'); partialEl = null;
        p.innerHTML = `<span class="ts">${fmt(m.start)}</span><span class="fin" data-idx="${m.idx}">${esc(m.text)}</span>`;
        if (!p.parentNode) text.appendChild(p);
        follow(was);
      }
      else if (m.type === 'segment_empty') { partialEl?.remove(); partialEl = null; }
      else if (m.type === 'clean') {
        const el = $(`.fin[data-idx="${m.idx}"]`, text);
        if (el) { el.textContent = m.text; el.classList.add('cleaned'); el.title = 'Von der KI bereinigt'; setTimeout(() => el.classList.remove('cleaned'), 1600); }
      }
      else if (m.type === 'status') {
        if (m.state === 'transcribing' && m.pending > 3) setStatus(`Server ausgelastet – ${m.pending} Sätze in der Warteschlange`);
        else if (recordingActive) setStatus('Hört zu …');
      }
      else if (m.type === 'final') { showDone(m); }
      else if (m.type === 'warning' || m.type === 'error') toast(m.message, true);
    }

    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: micSel.value ? { exact: micSel.value } : undefined, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      } catch (e) { toast('Mikrofon nicht freigegeben: ' + e.message, true); return; }
      try { ctx = new AudioContext({ sampleRate: 16000 }); } catch { ctx = new AudioContext(); }
      await ctx.audioWorklet.addModule('/static/worklet.js');
      const src = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser(); analyser.fftSize = 1024; src.connect(analyser);
      node = new AudioWorkletNode(ctx, 'pcm16-processor'); src.connect(node);
      const title = $('#r-title').value.trim();
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws/live?title=${encodeURIComponent(title)}`);
      ws.binaryType = 'arraybuffer';
      const pending = [];
      ws.onopen = () => { pending.forEach((b) => ws.send(b)); pending.length = 0; };
      ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
      ws.onclose = () => { if (recordingActive) { toast('Verbindung getrennt – die Aufnahme bis hierhin ist gespeichert.', true); stopLocal(); setStatus('Verbindung getrennt'); } };
      node.port.onmessage = (ev) => { if (ws.readyState === 1) ws.send(ev.data.pcm); else if (ws.readyState === 0) pending.push(ev.data.pcm); };
      recordingActive = true; finished = false;
      $('#setup').classList.add('hidden'); $('#live').classList.remove('hidden');
      $('#r-title-live').textContent = title || 'Aufnahme ' + fmtDate(new Date().toISOString(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      t0 = Date.now(); tick = setInterval(() => ($('#r-time').textContent = fmt((Date.now() - t0) / 1000)), 250);
      lockScreen(); drawWave();
    }

    function stopLocal() {
      recordingActive = false; clearInterval(tick); cancelAnimationFrame(raf);
      try { node && node.disconnect(); } catch { }
      try { stream && stream.getTracks().forEach((t) => t.stop()); } catch { }
      try { ctx && ctx.close(); } catch { }
      try { wakeLock && wakeLock.release(); } catch { }
    }

    function stop() {
      setStatus('Wird gespeichert …');
      $('#r-stop').disabled = true;
      try { ws.send(JSON.stringify({ type: 'stop' })); } catch { }
      stopLocal();
      partialEl?.classList.add('hidden');
    }

    function showDone(m) {
      if (finished) return; finished = true;
      $('.rec-bar .rec-state').innerHTML = '<span style="color:var(--ok)">✓ Gespeichert</span>';
      setStatus(m.refining ? 'Genauer Durchlauf, Sprechererkennung und Bereinigung laufen im Hintergrund.' : 'Fertig.');
      const card = document.createElement('div'); card.className = 'card done-card';
      card.innerHTML = `<div class="ico">${ICON.check}</div><div style="flex:1"><strong>${fmt(m.duration)} aufgenommen</strong><div class="muted small">${m.segments.length} Sätze${m.refining ? ' · wird gerade verfeinert (Sprecher, genaues Modell)' : ''}</div></div><a class="btn primary" href="#/t/${m.transcript_id}">Transkript öffnen</a><a class="btn" href="#/aufnahme" id="r-new">Neue Aufnahme</a>`;
      text.after(card);
      $('#r-new').addEventListener('click', (e) => { e.preventDefault(); route(); });
      try { ws.close(); } catch { }
      loadSide();
    }

    startBtn.addEventListener('click', start);
    $('#r-stop').addEventListener('click', stop);
    return () => { document.removeEventListener('visibilitychange', onVis); document.removeEventListener('health', updateStart); if (recordingActive) stop(); cancelAnimationFrame(raf); };
  }

  // ================================================================ Hochladen
  async function renderHochladen() {
    view.innerHTML = `
      <div class="page-head"><h1>Datei hochladen</h1><p>Aufnahmen vom Diktiergerät, Handy oder aus Teams – mp3, m4a, wav, mp4, webm … Die Datei bleibt auf diesem Server.</p></div>
      <div class="drop" id="drop" tabindex="0" role="button" aria-label="Datei auswählen">
        <input id="file" type="file" accept="audio/*,video/*,.m4a,.mp3,.wav,.mp4,.webm,.ogg,.opus,.flac,.aac,.wma,.mkv,.mov" multiple hidden>
        <div class="ico">${ICON.upload}</div>
        <div><strong>Datei hierher ziehen</strong> oder klicken</div>
        <div class="muted small" style="margin-top:.3rem">Mehrere Dateien möglich · werden nacheinander verarbeitet</div>
      </div>
      <div style="margin-top:1rem;max-width:480px"><input id="up-title" type="text" placeholder="Titel (optional, sonst Dateiname)" style="width:100%"></div>
      <div class="jobs" id="jobs"></div>`;
    const drop = $('#drop'), input = $('#file'), jobs = $('#jobs');
    drop.addEventListener('click', () => input.click());
    drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') input.click(); });
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => upload([...e.dataTransfer.files]));
    input.addEventListener('change', () => { upload([...input.files]); input.value = ''; });
    const running = new Map();
    async function upload(files) {
      for (const f of files) {
        const fd = new FormData(); fd.append('file', f); fd.append('title', $('#up-title').value.trim());
        const row = document.createElement('div'); row.className = 'job';
        row.innerHTML = `<span class="name">${esc(f.name)}</span><div class="bar"><i></i></div><span class="chip">lädt hoch …</span>`;
        jobs.prepend(row);
        try {
          const r = await fetch('/api/upload', { method: 'POST', body: fd });
          if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
          const j = await r.json(); running.set(j.id, row); row.querySelector('.chip').textContent = 'in Warteschlange'; loadSide();
        } catch (e) { const c = row.querySelector('.chip'); c.textContent = e.message; c.className = 'chip danger'; }
      }
    }
    const poll = setInterval(async () => {
      if (!running.size) return;
      let list; try { list = await api('/api/transcripts'); } catch { return; }
      for (const [id, row] of running) {
        const t = list.find((x) => x.id === id); if (!t) continue;
        const c = row.querySelector('.chip'), bar = row.querySelector('.bar i');
        if (t.status === 'done') { bar.style.width = '100%'; c.className = 'chip ok'; c.innerHTML = `<a href="#/t/${id}">fertig – öffnen</a>`; running.delete(id); loadSide(); }
        else if (t.status === 'error') { c.className = 'chip danger'; c.textContent = 'Fehler: ' + t.error; running.delete(id); }
        else { const p = t.progress; c.textContent = progressText(p); if (p && p.done >= 0) bar.style.width = Math.round(100 * p.done / Math.max(1, p.total)) + '%'; }
      }
    }, 1000);
    return () => clearInterval(poll);
  }
  function progressText(p) {
    if (!p) return 'wartet …';
    if (p.done === -1) return 'Sprecher werden erkannt …';
    if (p.done === -2) return 'KI bereinigt den Text …';
    return `transkribiert … ${p.done}/${p.total}`;
  }

  // ================================================================ Liste (Handy)
  async function renderListe() {
    await loadSide();
    view.innerHTML = `<div class="page-head"><h1>Verlauf</h1></div><input id="l-q" type="search" placeholder="Suchen …" style="width:100%;margin-bottom:.8rem"><div class="list" id="l-list"></div>`;
    const draw = () => {
      const q = $('#l-q').value.trim().toLowerCase();
      const rows = sideItems.filter((t) => !q || t.title.toLowerCase().includes(q));
      $('#l-list').innerHTML = rows.map((t) => `<a class="item" href="#/t/${t.id}"><span class="stat-dot ${t.status}"></span><div class="grow"><div class="t1">${esc(t.title)}</div><div class="t2">${fmtDate(t.created_at)} · ${fmtDur(t.duration)} · ${t.source === 'live' ? 'Live' : 'Upload'}</div></div></a>`).join('') || '<div class="empty">Noch keine Aufnahmen.</div>';
    };
    $('#l-q').addEventListener('input', draw); draw();
  }

  // ================================================================ Glossar
  async function renderGlossar() {
    view.innerHTML = `
      <div class="page-head"><h1>Glossar</h1><p>Begriffe, Namen und Abkürzungen eurer Verwaltung. Sie werden nach der Erkennung automatisch korrigiert und der KI-Bereinigung als Pflicht-Schreibweise mitgegeben – z. B. „CHS“ → „Excel“, „Heilbrunn“ → „Heilbronn“.</p></div>
      <div class="card"><table class="glossar"><thead><tr><th>Wird erkannt als</th><th>Richtig</th><th></th></tr></thead><tbody id="gl"></tbody></table>
      <div style="display:flex;gap:.5rem;margin-top:.8rem"><button class="btn" id="gl-add">+ Eintrag</button><button class="btn primary" id="gl-save">Speichern</button></div></div>`;
    const body = $('#gl');
    const row = (e = {}) => `<tr><td><input type="text" value="${esc(e.von)}" placeholder="z. B. CHS"></td><td><input type="text" value="${esc(e.zu)}" placeholder="z. B. Excel"></td><td><button class="btn ghost sm" data-del>✕</button></td></tr>`;
    body.innerHTML = (await api('/api/glossar')).map(row).join('') || row();
    body.addEventListener('click', (e) => { if (e.target.closest('[data-del]')) e.target.closest('tr').remove(); });
    $('#gl-add').addEventListener('click', () => body.insertAdjacentHTML('beforeend', row()));
    $('#gl-save').addEventListener('click', async () => {
      const eintraege = $$('tr', body).map((tr) => { const [a, b] = $$('input', tr); return { von: a.value, zu: b.value }; });
      const saved = await api('/api/glossar', { method: 'PUT', body: JSON.stringify({ eintraege }) });
      toast(`Glossar gespeichert (${saved.length} Einträge)`);
    });
  }

  // ================================================================ FAQ & interne Infos
  const INFO_TABS = [
    ['faq', 'Häufige Fragen'], ['technik', 'Technische Doku'], ['infrastruktur', 'Infrastruktur'],
    ['datenschutz', 'Datenschutz'], ['sicherheit', 'IT-Sicherheit'], ['personalrat', 'Personalrat'],
  ];
  async function renderInfo(tab) {
    if (!INFO_TABS.some(([k]) => k === tab)) tab = 'faq';
    const intern = tab !== 'faq';
    view.innerHTML = `
      <div class="page-head"><h1>${intern ? 'Infos <span class="pill">intern</span>' : 'Häufige Fragen'}</h1>
        <p>${intern ? 'Unterlagen für Datenschutz, IT-Sicherheit, Personalrat und Betrieb. Stand und Inhalte wachsen mit dem Projekt.' : 'Kurze Antworten für alle, die den Protokollanten nutzen.'}</p></div>
      <nav class="info-tabs" aria-label="Info-Bereiche">${INFO_TABS.map(([k, l]) => `<a href="#/${k === 'faq' ? 'faq' : 'infos/' + k}" class="${k === tab ? 'active' : ''}">${l}</a>`).join('')}</nav>
      <article class="doc card" id="doc"><p class="muted">Lädt …</p></article>`;
    const act = $('.info-tabs a.active'); if (act && act.scrollIntoView) act.scrollIntoView({ inline: 'center', block: 'nearest' });
    const html = await api(`/api/docs/${tab}`);
    const doc = $('#doc');
    doc.innerHTML = html;
    // Inhaltsverzeichnis aus den Überschriften (nur bei längeren Seiten)
    const heads = $$('h2', doc);
    if (heads.length > 3) {
      heads.forEach((h, i) => (h.id = h.id || `a${i}`));
      const lead = $('p.lead', doc) || null;
      (lead ? lead : doc).insertAdjacentHTML(lead ? 'afterend' : 'afterbegin', `<nav class="toc"><strong>Inhalt</strong>${heads.map((h) => `<a href="#" data-goto="${h.id}">${esc(h.textContent)}</a>`).join('')}</nav>`);
      doc.addEventListener('click', (e) => { const a = e.target.closest('[data-goto]'); if (a) { e.preventDefault(); document.getElementById(a.dataset.goto).scrollIntoView({ behavior: 'smooth', block: 'start' }); } });
    }
    // Live-Werte des Servers in die Doku einsetzen (data-live="…")
    $$('[data-live]', doc).forEach((el) => {
      const v = el.dataset.live.split('.').reduce((o, k) => (o == null ? o : o[k]), health);
      if (v != null && v !== '') el.textContent = v;
    });
    const btn = $('#llm-measure', doc);
    if (btn) btn.addEventListener('click', async () => {
      btn.disabled = true; btn.textContent = 'Misst …';
      try {
        const r = await api('/api/llm/test', { method: 'POST' });
        const okN = r.zeilen.filter((z) => z.ok).length;
        $('#llm-result', doc).innerHTML = `5 Sätze bereinigt in <b>${r.sekunden} s</b> · ${okN}/5 bestehen die Treue-Prüfung`
          + `<ul class="small">${r.zeilen.map((z) => `<li>${esc(z.bereinigt || '–')} ${z.ok ? '' : `<span class="tag mittel">${esc(z.grund)}</span>`}</li>`).join('')}</ul>`;
        const cell = $('#llm-infra', doc); if (cell) cell.textContent = `${r.sekunden} s für 5 Sätze`;
      } catch (e) { $('#llm-result', doc).textContent = e.message; }
      btn.disabled = false; btn.textContent = 'Erneut messen';
    });
  }

  // ================================================================ Detail
  async function renderDetail(id) {
    let t = await api(`/api/transcripts/${id}`);
    let showClean = true, mTab = 'tx', editMd = false, busy = '';

    const speakerColor = () => { const m = new Map(); t.segments.forEach((s) => { if (s.speaker && !m.has(s.speaker)) m.set(s.speaker, COLORS[m.size % COLORS.length]); }); return m; };
    const hasClean = () => t.segments.some((s) => s.clean);
    const llm = () => health && health.llm_configured;

    function draw() {
      const rtf = t.duration && t.processing_seconds ? t.duration / t.processing_seconds : 0;
      const speakers = new Set(t.segments.map((s) => s.speaker).filter(Boolean));
      const working = t.status === 'processing' || t.status === 'refining';
      view.innerHTML = `
        <div class="d-head">
          <input class="d-title" id="d-title" value="${esc(t.title)}" aria-label="Titel bearbeiten">
          <div class="menu"><button class="btn" id="d-export">${ICON.down}Export</button></div>
          <div class="menu"><button class="btn ghost" id="d-more" aria-label="Weitere Aktionen">${ICON.more}</button></div>
        </div>
        <div class="d-meta">
          <span class="chip">${fmtDate(t.created_at)}</span>
          <span class="chip">${fmt(t.duration)}</span>
          ${speakers.size ? `<span class="chip">${speakers.size} Sprecher</span>` : ''}
          <span class="chip blue" title="Spracherkennung">${esc(t.model || '')}</span>
          ${rtf ? `<span class="chip ok" title="Rechenzeit ${t.processing_seconds} s">${rtf.toFixed(0)}× schneller als Echtzeit</span>` : ''}
          ${hasClean() ? '<span class="chip ai">✨ KI-bereinigt</span>' : ''}
          ${t.status === 'error' ? `<span class="chip danger">${esc(t.error)}</span>` : (t.error ? `<span class="chip warn" title="${esc(t.error)}">Hinweis</span>` : '')}
        </div>
        ${working ? `<div class="progress-banner"><span class="spin"></span><span>${t.status === 'refining' ? 'Wird verfeinert: ' : ''}${progressText(t.progress)}</span><div class="bar"><i style="width:${t.progress && t.progress.done >= 0 ? Math.round(100 * t.progress.done / Math.max(1, t.progress.total)) : 100}%"></i></div></div>` : ''}
        <div class="ai-bar">
          <button class="btn ai" id="ai-clean" ${busy || !t.segments.length ? 'disabled' : ''}>${busy === 'clean' ? '<span class="spin"></span>' : ICON.spark}Text bereinigen</button>
          <button class="btn ai" id="ai-sum" ${busy || !t.segments.length ? 'disabled' : ''}>${busy === 'sum' ? '<span class="spin"></span>' : ICON.doc}Zusammenfassen &amp; strukturieren</button>
          <span class="note">${llm() ? `KI: ${esc(health.llm_model)} · korrigiert nur, erfindet nichts` : 'Keine KI angebunden – die Knöpfe führen Schritt für Schritt über NOVA.'}</span>
        </div>
        <div class="d-tabs"><div class="seg-toggle"><button data-mtab="tx" class="${mTab === 'tx' ? 'on' : ''}">Transkript</button><button data-mtab="notes" class="${mTab === 'notes' ? 'on' : ''}">Zusammenfassung</button></div></div>
        <div class="d-grid" data-tab="${mTab}">
          <section class="tx-pane">
            <div class="pane-head"><h2>Transkript</h2><span class="sp"></span>
              ${hasClean() ? `<div class="seg-toggle"><button data-view="clean" class="${showClean ? 'on' : ''}">✨ Bereinigt</button><button data-view="orig" class="${showClean ? '' : 'on'}">Original</button></div>` : ''}
            </div>
            <div class="tx ${t.status === 'refining' ? 'provisional' : ''}" id="tx">${drawBlocks()}</div>
          </section>
          <aside class="notes" id="notes">${drawNotes()}</aside>
        </div>
        ${t.has_audio ? `<div class="player"><audio id="audio" controls preload="metadata" src="/api/transcripts/${t.id}/audio"></audio></div>` : ''}`;
      bind();
    }

    function drawBlocks() {
      if (!t.segments.length) return `<div class="empty">${t.status === 'error' ? 'Transkription fehlgeschlagen.' : 'Noch kein Text – die Verarbeitung läuft.'}</div>`;
      const colors = speakerColor();
      const blocks = [];
      for (const s of t.segments) {
        const last = blocks[blocks.length - 1];
        if (last && last.speaker === s.speaker && s.start - last.segs[last.segs.length - 1].end < 30) last.segs.push(s);
        else blocks.push({ speaker: s.speaker, segs: [s] });
      }
      return blocks.map((b) => {
        const col = colors.get(b.speaker);
        return `<div class="block">
          <div class="avatar ${col ? '' : 'none'}" style="${col ? 'background:' + col : ''}">${b.speaker ? esc(initials(b.speaker)) : '?'}</div>
          <div class="bhead"><span class="bname" data-spk="${esc(b.speaker)}" title="Namen ändern">${esc(b.speaker || 'Sprecher zuordnen')}</span><span class="btime" data-seek="${b.segs[0].start}">${fmt(b.segs[0].start)}</span></div>
          <div class="bbody">${b.segs.map((s) => {
            const useClean = showClean && s.clean;
            const mk = useClean && s.clean !== s.text ? `<span class="mk ai" title="Original: ${esc(s.text)}">✨</span>` : (s.clean_note ? `<span class="mk warn" title="KI-Bereinigung verworfen: ${esc(s.clean_note)}">⚠</span>` : '');
            return `<span class="seg" data-idx="${s.idx}" data-start="${s.start}" data-end="${s.end}"><span class="txt" contenteditable="true" spellcheck="true" data-field="${useClean ? 'clean' : 'text'}">${esc(useClean ? s.clean : s.text)}</span>${mk}</span>`;
          }).join('')}</div>
        </div>`;
      }).join('');
    }

    function renderMd(zeilen) {
      return zeilen.map((z) => {
        const raw = z.text.trim(); if (!raw) return '';
        const h = raw.match(/^(#{1,4})\s+(.*)$/);
        if (h) return `<h${h[1].length <= 2 ? 3 : 4}>${esc(h[2])}</h${h[1].length <= 2 ? 3 : 4}>`;
        const li = /^[-*]\s+/.test(raw);
        let html = esc(li ? raw.replace(/^[-*]\s+/, '') : raw);
        html = html.replace(/\[\s*S\s*\d+(?:\s*[,;–-]\s*S?\s*\d+)*\s*\]/g, (ref) => [...ref.matchAll(/\d+/g)].map((x) => +x[0]).map((n) => `<span class="ref ${t.segments.some((s) => s.idx === n) ? '' : 'bad'}" data-ref="${n}" title="Zur Stelle springen">S${n}</span>`).join(''));
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        return `<div class="pl ${li ? 'li' : ''} ${z.status}" ${z.status === 'unbelegt' ? 'title="Keine Belegstelle – bitte prüfen"' : ''}>${html}</div>`;
      }).join('');
    }

    function drawNotes() {
      const p = t.protokoll;
      const styles = (health && health.styles) || { zusammenfassung: 'Zusammenfassung', ergebnis: 'Ergebnisprotokoll', verlauf: 'Verlaufsprotokoll' };
      const sel = `<select id="p-style" style="padding:.35rem .5rem;font-size:.85rem">${Object.entries(styles).map(([k, v]) => `<option value="${k}" ${p && p.style === k ? 'selected' : ''}>${v}</option>`).join('')}</select>`;
      if (!p) return `<div class="pane-head"><h2>Zusammenfassung</h2><span class="sp"></span>${sel}</div><div class="card empty-notes"><div class="big">✨</div>Noch keine Zusammenfassung.<br>Klicke auf <strong>„Zusammenfassen &amp; strukturieren“</strong>. Jede Aussage bekommt eine Belegstelle im Transkript – nichts wird erfunden.</div>`;
      const pr = p.content.pruefung || { zeilen: [], quote_belegt: 1, unbelegt: 0, ungueltige_belege: 0 };
      return `<div class="pane-head"><h2>${esc(styles[p.style] || 'Zusammenfassung')}</h2><span class="sp"></span>${sel}</div>
        <div class="card">
          <div class="pstats">
            <span class="chip ${pr.quote_belegt >= 0.95 ? 'ok' : 'warn'}" title="Anteil der Aussagen mit gültiger Belegstelle">${Math.round(pr.quote_belegt * 100)} % belegt</span>
            ${pr.unbelegt ? `<span class="chip warn">${pr.unbelegt} ohne Beleg</span>` : ''}
            ${pr.ungueltige_belege ? `<span class="chip danger">${pr.ungueltige_belege} falscher Beleg</span>` : ''}
            <span class="chip ${p.status === 'bestaetigt' ? 'ok' : ''}">${p.status === 'bestaetigt' ? '✓ geprüft' : 'Entwurf'}</span>
          </div>
          ${editMd ? `<textarea id="p-md" style="width:100%;min-height:320px;font-family:ui-monospace,Consolas,monospace;font-size:.85rem">${esc(p.content.protokoll_md)}</textarea>` : `<div class="prot">${renderMd(pr.zeilen)}</div>`}
          <div style="display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.9rem">
            ${editMd ? '<button class="btn primary sm" id="p-save">Speichern &amp; prüfen</button>' : '<button class="btn sm" id="p-edit">Bearbeiten</button>'}
            <button class="btn sm" id="p-confirm">${p.status === 'bestaetigt' ? 'Bestätigung aufheben' : ICON.check + 'Als geprüft markieren'}</button>
            <button class="btn ghost sm" id="p-copy">Kopieren</button>
          </div>
        </div>`;
    }

    // ---- Interaktion
    const audio = () => $('#audio');
    const seek = (sec) => { const a = audio(); if (!a) return; a.currentTime = Math.max(0, sec - 0.2); a.play().catch(() => { }); };
    const jump = (idx) => {
      const s = t.segments.find((x) => x.idx === idx); if (!s) { toast(`Segment S${idx} gibt es nicht`, true); return; }
      if (mTab !== 'tx' && innerWidth <= 900) { mTab = 'tx'; draw(); }
      const el = $(`.seg[data-idx="${idx}"]`); if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('playing'); setTimeout(() => el.classList.remove('playing'), 2500); }
      seek(s.start);
    };

    function bind() {
      $('#d-title').addEventListener('change', async (e) => { await api(`/api/transcripts/${t.id}`, { method: 'PATCH', body: JSON.stringify({ title: e.target.value }) }); t.title = e.target.value; toast('Titel gespeichert'); loadSide(); });
      $('#d-export').addEventListener('click', (e) => openMenu(e.currentTarget, `
        <a href="/api/transcripts/${t.id}/export?format=docx">Word (.docx)</a>
        <a href="/api/transcripts/${t.id}/export?format=docx&protokoll=1">Word mit Zusammenfassung</a>
        <a href="/api/transcripts/${t.id}/export?format=txt">Text mit Zeit &amp; Sprecher</a>
        <a href="/api/transcripts/${t.id}/export?format=txt&zeit=false&sprecher=false">Nur Fließtext</a>
        <a href="/api/transcripts/${t.id}/export?format=md">Markdown</a>
        <a href="/api/transcripts/${t.id}/export?format=srt">Untertitel (.srt)</a>`));
      $('#d-more').addEventListener('click', (e) => openMenu(e.currentTarget, `
        ${hasClean() ? '<button data-act="unclean">KI-Bereinigung verwerfen</button>' : ''}
        <button data-act="delaudio" ${t.has_audio ? '' : 'disabled'}>Nur Audio löschen</button><hr>
        <button data-act="delete" class="danger">Aufnahme endgültig löschen</button>`, async (act) => {
        if (act === 'delete') { if (!confirm('Transkript und Audio endgültig löschen?')) return; await api(`/api/transcripts/${t.id}`, { method: 'DELETE' }); toast('Gelöscht'); loadSide(); location.hash = '#/aufnahme'; }
        if (act === 'delaudio') { if (!confirm('Audio endgültig löschen? Das Transkript bleibt erhalten.')) return; await api(`/api/transcripts/${t.id}/audio`, { method: 'DELETE' }); t.has_audio = false; toast('Audio gelöscht'); draw(); }
        if (act === 'unclean') { await api(`/api/transcripts/${t.id}/bereinigen`, { method: 'DELETE' }); await reload(); toast('Bereinigung verworfen – Original wird angezeigt'); }
      }));
      $$('[data-mtab]').forEach((b) => b.addEventListener('click', () => { mTab = b.dataset.mtab; draw(); }));
      $$('[data-view]').forEach((b) => b.addEventListener('click', () => { showClean = b.dataset.view === 'clean'; draw(); }));
      $$('[data-seek]').forEach((el) => el.addEventListener('click', () => seek(+el.dataset.seek)));
      $$('.seg .txt').forEach((el) => {
        el.dataset.orig = el.textContent;
        el.addEventListener('paste', (e) => { e.preventDefault(); document.execCommand('insertText', false, (e.clipboardData || window.clipboardData).getData('text/plain')); });
        el.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); el.blur(); } });
        el.addEventListener('dblclick', () => seek(+el.parentElement.dataset.start));
        el.addEventListener('blur', async () => {
          const val = el.textContent.trim(); if (val === el.dataset.orig) return;
          const idx = +el.parentElement.dataset.idx, field = el.dataset.field;
          await api(`/api/transcripts/${t.id}/segments/${idx}`, { method: 'PATCH', body: JSON.stringify({ [field]: val }) });
          const s = t.segments.find((x) => x.idx === idx); s[field] = val; el.dataset.orig = val; toast('Gespeichert');
        });
      });
      $$('.bname').forEach((el) => el.addEventListener('click', async () => {
        const old = el.dataset.spk, neu = prompt(old ? `Neuer Name für „${old}“ (gilt für alle Stellen):` : 'Sprechername:', old && !old.startsWith('Sprecher ') ? old : '');
        if (neu === null || !neu.trim()) return;
        if (old) { await api(`/api/transcripts/${t.id}/speakers/rename`, { method: 'POST', body: JSON.stringify({ von: old, zu: neu.trim() }) }); t.segments.forEach((s) => { if (s.speaker === old) s.speaker = neu.trim(); }); }
        else { const idx = +el.closest('.block').querySelector('.seg').dataset.idx; await api(`/api/transcripts/${t.id}/segments/${idx}`, { method: 'PATCH', body: JSON.stringify({ speaker: neu.trim() }) }); t.segments.find((s) => s.idx === idx).speaker = neu.trim(); }
        draw();
      }));
      $$('[data-ref]').forEach((el) => el.addEventListener('click', () => jump(+el.dataset.ref)));
      const a = audio();
      if (a) a.ontimeupdate = () => {
        const cur = a.currentTime;
        $$('.seg').forEach((el) => el.classList.toggle('playing', cur >= +el.dataset.start && cur < +el.dataset.end));
      };
      $('#ai-clean').addEventListener('click', aiClean);
      $('#ai-sum').addEventListener('click', aiSum);
      $('#p-edit')?.addEventListener('click', () => { editMd = true; draw(); });
      $('#p-save')?.addEventListener('click', async () => { t.protokoll = await api(`/api/transcripts/${t.id}/protokoll`, { method: 'PATCH', body: JSON.stringify({ protokoll_md: $('#p-md').value }) }); editMd = false; draw(); });
      $('#p-confirm')?.addEventListener('click', async () => { t.protokoll = await api(`/api/transcripts/${t.id}/protokoll`, { method: 'PATCH', body: JSON.stringify({ status: t.protokoll.status === 'bestaetigt' ? 'entwurf' : 'bestaetigt' }) }); draw(); });
      $('#p-copy')?.addEventListener('click', async () => { if (await copy(t.protokoll.content.protokoll_md)) toast('Zusammenfassung kopiert'); });
    }

    function openMenu(anchor, html, onAct) {
      $$('.menu-list').forEach((m) => m.remove());
      const m = document.createElement('div'); m.className = 'menu-list'; m.innerHTML = html;
      anchor.parentElement.appendChild(m);
      setTimeout(() => document.addEventListener('click', function close(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('click', close); } }), 0);
      if (onAct) m.addEventListener('click', (e) => { const b = e.target.closest('[data-act]'); if (b) { m.remove(); onAct(b.dataset.act); } });
    }

    async function reload() { t = await api(`/api/transcripts/${id}`); draw(); }

    // ---- KI: Bereinigen
    async function aiClean() {
      if (llm()) {
        busy = 'clean'; draw();
        try {
          const r = await api(`/api/transcripts/${t.id}/bereinigen`, { method: 'POST' });
          await reload(); showClean = true;
          toast(`${r.uebernommen} Sätze bereinigt${r.verworfen ? ` · ${r.verworfen} verworfen (zu stark verändert – Original bleibt)` : ''}`);
        } catch (e) { toast(e.message, true); } finally { busy = ''; draw(); }
        return;
      }
      const prompt = await api(`/api/transcripts/${t.id}/bereinigen-prompt`);
      const ok = await copy(prompt);
      const card = modal.open(`<h2>✨ Text bereinigen über NOVA</h2>
        <p class="muted">Die KI korrigiert nur Erkennungs- und Schreibfehler – kein Zusammenfassen. Jede Zeile wird danach automatisch gegen das Original geprüft.</p>
        <div class="steps">
          <div class="step"><div><strong>${ok ? 'Anweisung und Transkript sind kopiert.' : 'Anweisung kopieren:'}</strong><br>In NOVA einen neuen Chat öffnen und mit Strg+V einfügen.${ok ? '' : `<textarea readonly>${esc(prompt)}</textarea>`}</div></div>
          <div class="step"><div><strong>Antwort von NOVA komplett kopieren</strong> und hier einfügen:<textarea id="m-in" placeholder="[S0] …\n[S1] …"></textarea></div></div>
        </div>
        <div class="modal-actions"><button class="btn" id="m-copy">Nochmal kopieren</button><button class="btn" id="m-x">Abbrechen</button><button class="btn primary" id="m-ok">Prüfen &amp; übernehmen</button></div>`);
      $('#m-x', card).onclick = modal.close;
      $('#m-copy', card).onclick = async () => { if (await copy(prompt)) toast('Kopiert'); };
      $('#m-ok', card).onclick = async () => {
        try {
          const r = await api(`/api/transcripts/${t.id}/bereinigen/import`, { method: 'POST', body: JSON.stringify({ text: $('#m-in', card).value }) });
          modal.close(); await reload(); showClean = true; draw();
          toast(`${r.uebernommen} Sätze übernommen${r.verworfen ? ` · ${r.verworfen} verworfen (Original bleibt)` : ''}${r.fehlend ? ` · ${r.fehlend} fehlten in der Antwort` : ''}`);
        } catch (e) { toast(e.message, true); }
      };
    }

    // ---- KI: Zusammenfassen
    async function aiSum() {
      const style = ($('#p-style') && $('#p-style').value) || 'zusammenfassung';
      if (llm()) {
        busy = 'sum'; draw();
        try { t.protokoll = await api(`/api/transcripts/${t.id}/protokoll`, { method: 'POST', body: JSON.stringify({ style }) }); mTab = 'notes'; }
        catch (e) { toast(e.message, true); } finally { busy = ''; draw(); }
        return;
      }
      const prompt = await api(`/api/transcripts/${t.id}/nova-prompt?style=${style}`);
      const ok = await copy(prompt);
      const card = modal.open(`<h2>Zusammenfassen &amp; strukturieren über NOVA</h2>
        <p class="muted">Jede Aussage muss eine Belegstelle wie [S12] tragen. Aussagen ohne Beleg werden danach gelb markiert – so fällt sofort auf, wenn die KI etwas hinzudichtet.</p>
        <div class="steps">
          <div class="step"><div><strong>${ok ? 'Anweisung und Transkript sind kopiert.' : 'Anweisung kopieren:'}</strong><br>In NOVA einfügen (Strg+V) und absenden.${ok ? '' : `<textarea readonly>${esc(prompt)}</textarea>`}</div></div>
          <div class="step"><div><strong>Antwort hier einfügen:</strong><textarea id="m-in" placeholder="## Worum ging es …"></textarea></div></div>
        </div>
        <div class="modal-actions"><button class="btn" id="m-copy">Nochmal kopieren</button><button class="btn" id="m-x">Abbrechen</button><button class="btn primary" id="m-ok">Prüfen &amp; übernehmen</button></div>`);
      $('#m-x', card).onclick = modal.close;
      $('#m-copy', card).onclick = async () => { if (await copy(prompt)) toast('Kopiert'); };
      $('#m-ok', card).onclick = async () => {
        try { t.protokoll = await api(`/api/transcripts/${t.id}/protokoll/import`, { method: 'POST', body: JSON.stringify({ style, protokoll_md: $('#m-in', card).value }) }); modal.close(); mTab = 'notes'; draw(); }
        catch (e) { toast(e.message, true); }
      };
    }

    draw();
    const poll = setInterval(async () => {
      if (t.status !== 'processing' && t.status !== 'refining') return;
      if (document.activeElement && (document.activeElement.isContentEditable || document.activeElement.tagName === 'TEXTAREA')) return;
      try {
        const n = await api(`/api/transcripts/${id}`);
        const changed = n.status !== t.status || JSON.stringify(n.progress) !== JSON.stringify(t.progress) || n.segments.length !== t.segments.length;
        const a = audio(), pos = a ? a.currentTime : 0, playing = a && !a.paused;
        t = n;
        if (changed) { draw(); const b = audio(); if (b && pos) { b.currentTime = pos; if (playing) b.play().catch(() => { }); } if (n.status === 'done') { toast('Fertig verfeinert'); loadSide(); } }
      } catch { }
    }, 2000);
    return () => clearInterval(poll);
  }

  // ================================================================ Start
  loadHealth();
  loadSide().then(route);
  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/sw.js').catch(() => { });
})();
