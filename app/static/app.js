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
  const COLORS = ['#006EB7', '#E73039', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#DB2777', '#65A30D'];
  const initials = (name) => { const m = /^Sprecher\s+(\d+)/i.exec(name || ''); return m ? 'S' + m[1] : (name || '?').split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase(); };

  let toastTimer;
  const toast = (msg, err = false) => {
    const t = $('#toast'); t.textContent = msg; t.className = 'toast show' + (err ? ' err' : '');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = 'toast'), err ? 6500 : 3200);
  };
  const toastAction = (msg, label, fn) => {
    const t = $('#toast'); t.innerHTML = `<span>${esc(msg)}</span><button class="toast-btn">${esc(label)}</button>`; t.className = 'toast show action';
    $('.toast-btn', t).onclick = () => { t.className = 'toast'; fn(); };
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = 'toast'), 12000);
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
        const kiAus = health.llm_configured && health.llm_status && health.llm_status !== 'bereit';
        sys.className = 'sys ' + (kiAus ? 'loading' : 'ok');
        txt.textContent = `Spracherkennung bereit${health.llm_configured ? ' · KI ' + (health.llm_status || 'bereit') : ''}`;
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

      document.dispatchEvent(new CustomEvent('health'));
    } catch {
      $('#sys').className = 'sys err'; $('#sys-txt').textContent = 'Server nicht erreichbar';
    }
    setTimeout(loadHealth, health.ready ? 30000 : 2000);
  }

  // ================================================================ Benutzer-Menü
  const userName = () => { try { return localStorage.getItem('protokollant-name') || ''; } catch { return ''; } };
  function drawUser() {
    const n = userName();
    $('#user-av').textContent = n ? n.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() : '?';
    $('#user-name').textContent = n || 'Testzugang';
  }
  $('#user-btn').addEventListener('click', (e) => {
    $$('.menu-list').forEach((m) => m.remove());
    const m = document.createElement('div'); m.className = 'menu-list up';
    m.innerHTML = `<a href="#/faq">Häufige Fragen</a><a href="#/infos">Infos <span class="side-badge">intern</span></a><hr>
      <button data-act="name">${userName() ? 'Namen ändern' : 'Namen eintragen'}</button>${health.auth ? '<button data-act="logout">Abmelden</button>' : ''}`;
    e.currentTarget.parentElement.appendChild(m);
    setTimeout(() => document.addEventListener('click', function close(ev) { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener('click', close); } }), 0);
    m.addEventListener('click', async (ev) => {
      const b = ev.target.closest('[data-act]'); m.remove(); if (!b) return;
      if (b.dataset.act === 'name') {
        const n = prompt('Ihr Name (nur für die Anzeige hier, wird nicht gespeichert):', userName());
        if (n !== null) { try { localStorage.setItem('protokollant-name', n.trim()); } catch { } drawUser(); }
      }
      if (b.dataset.act === 'logout') { await fetch('/api/logout', { method: 'POST' }); location.href = '/login'; }
    });
  });
  drawUser();

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
        <div class="page-head"><h1>Neue Aufnahme</h1></div>
        <div class="card rec-card">
          <div class="rec-fields three">
            <label>Titel<input id="r-title" type="text" placeholder="z. B. Dienstbesprechung Amt 10.5" autocomplete="off"></label>
            <label>Mikrofon<select id="r-mic"><option value="">Standard</option></select></label>
            <label>Glossar<select id="r-glossar"><option value="alle">Alle Ämter</option></select></label>
          </div>
          ${sitzungsFelder('r')}
          <label class="consent"><input type="checkbox" id="r-consent"><span>Alle Teilnehmenden sind über Aufzeichnung und Transkription informiert und einverstanden.</span></label>
          <div class="rec-start">
            <button class="big-rec" id="r-start" disabled aria-label="Aufnahme starten"><span></span></button>
            <div class="rec-start-text"><strong>Aufnahme starten</strong><div class="rec-hint" id="r-hint">Bitte zuerst die Information der Teilnehmenden bestätigen.</div></div>
          </div>
        </div>
        <div class="rec-tips">
          <div><strong>Mikrofon nah dran</strong>Ein Konferenzmikrofon in der Tischmitte bringt mehr als jedes Modell.</div>
          <div><strong>Alles bleibt im Haus</strong>Audio und Text werden nur auf diesem Server verarbeitet.</div>
          <div><strong>Nach dem Stopp</strong>Mit „Text bereinigen“ und „Protokoll erstellen“ verbessern Sie das Ergebnis per Klick.</div>
        </div>
      </section>
      <section class="rec-live hidden" id="live">
        <div class="rec-bar" id="r-bar">
          <div class="rec-state" id="r-state"><i></i><span>Aufnahme</span></div>
          <div class="rec-time" id="r-time">00:00</div>
          <canvas class="wave" id="r-wave"></canvas>
          <button class="pause-btn" id="r-pause" aria-label="Aufnahme pausieren" title="Pause"><span></span></button>
          <button class="stop-btn" id="r-stop" aria-label="Aufnahme beenden" title="Aufnahme beenden"><span></span></button>
        </div>
        <div class="rec-sub"><span id="r-status">Hört zu …</span><span id="r-info"></span></div>
        <input class="rec-title-live" id="r-title-live" aria-label="Titel der Aufnahme" autocomplete="off">
        <div id="r-done"></div>
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
      hint.textContent = !health.ready ? 'Sprachmodell lädt noch …' : consent.checked ? 'Bereit – zum Starten auf den roten Knopf tippen.' : 'Bitte zuerst die Information der Teilnehmenden bestätigen.';
    };
    fillGlossarSelect($('#r-glossar')); bindSitzung('r');
    consent.addEventListener('change', updateStart);
    document.addEventListener('health', updateStart);
    updateStart();

    let ctx, node, analyser, stream, ws, tick, raf, wakeLock = null, finished = false, lastLevels = [];
    let paused = false, elapsed = 0, runStart = 0, tid = null;
    const text = $('#r-text');
    const setStatus = (s) => { $('#r-status').textContent = s; };
    const nearBottom = () => window.innerHeight + window.scrollY > document.body.scrollHeight - 160;
    const follow = (was) => { if (was) window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }); };
    const seconds = () => elapsed + (paused || !runStart ? 0 : (Date.now() - runStart) / 1000);

    async function lockScreen() { try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { } }
    const onVis = () => {
      if (document.visibilityState === 'visible' && recordingActive) { lockScreen(); if (ctx && ctx.state === 'suspended' && !paused) { ctx.resume(); toast('Die Aufnahme war im Hintergrund pausiert – bitte das Handy während der Aufnahme nicht sperren.', true); } }
    };
    document.addEventListener('visibilitychange', onVis);

    function drawWave() {
      const c = $('#r-wave'); if (!c || !analyser) return;
      const dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
      if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
      const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      const buf = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(buf);
      let sum = 0; for (const v of buf) sum += v * v;
      const lvl = paused ? 0 : Math.min(1, Math.sqrt(sum / buf.length) * 6);
      lastLevels.push(lvl); const bars = Math.floor(w / 4); if (lastLevels.length > bars) lastLevels = lastLevels.slice(-bars);
      g.fillStyle = paused ? '#9AA4B2' : '#006EB7';
      lastLevels.forEach((v, i) => { const bh = Math.max(2, v * (h - 4)); const x = w - (lastLevels.length - i) * 4; g.globalAlpha = 0.35 + 0.65 * (i / lastLevels.length); g.fillRect(x, (h - bh) / 2, 2.5, bh); });
      raf = requestAnimationFrame(drawWave);
    }

    let partialEl = null;
    function onMessage(m) {
      const was = nearBottom();
      if (m.type === 'ready') {
        tid = m.transcript_id; $('#r-info').textContent = m.ai_clean ? 'KI-Bereinigung live aktiv' : '';
        const sd = sitzungsDaten('r');
        if (sd.tagesordnung.length) api(`/api/transcripts/${tid}/meta`, { method: 'PUT', body: JSON.stringify({ tagesordnung: sd.tagesordnung }) }).catch(() => { });
      }
      else if (m.type === 'partial') {
        if (paused) return;
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
        if (paused) return;
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
      const title = $('#r-title').value.trim() || 'Aufnahme ' + fmtDate(new Date().toISOString(), { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const sd = sitzungsDaten('r');
      ws = new WebSocket(`${proto}://${location.host}/ws/live?title=${encodeURIComponent(title)}&glossar=${encodeURIComponent($('#r-glossar').value)}&teilnehmende=${encodeURIComponent(sd.teilnehmende.join(','))}`);
      ws.binaryType = 'arraybuffer';
      const pending = [];
      ws.onopen = () => { pending.forEach((b) => ws.send(b)); pending.length = 0; };
      ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
      ws.onclose = () => { if (recordingActive) { toast('Verbindung getrennt – die Aufnahme bis hierhin ist gespeichert.', true); stopLocal(); setStatus('Verbindung getrennt'); } };
      node.port.onmessage = (ev) => { if (paused) return; if (ws.readyState === 1) ws.send(ev.data.pcm); else if (ws.readyState === 0) pending.push(ev.data.pcm); };
      recordingActive = true; finished = false; paused = false; elapsed = 0; runStart = Date.now();
      $('#setup').classList.add('hidden'); $('#live').classList.remove('hidden');
      $('#r-title-live').value = title;
      tick = setInterval(() => ($('#r-time').textContent = fmt(seconds())), 250);
      lockScreen(); drawWave();
    }

    function togglePause() {
      if (!recordingActive) return;
      const btn = $('#r-pause');
      if (!paused) {
        elapsed = seconds(); paused = true;
        try { ws.send(JSON.stringify({ type: 'pause' })); } catch { }
        partialEl?.remove(); partialEl = null;
        btn.classList.add('resume'); btn.title = 'Weiter aufnehmen'; btn.setAttribute('aria-label', 'Weiter aufnehmen');
        $('#r-state').classList.add('paused'); $('#r-state span').textContent = 'Pausiert';
        setStatus('Pausiert – es wird nichts aufgenommen. Mit ▶ geht es weiter, mit ■ ist die Aufnahme beendet.');
      } else {
        paused = false; runStart = Date.now();
        btn.classList.remove('resume'); btn.title = 'Pause'; btn.setAttribute('aria-label', 'Aufnahme pausieren');
        $('#r-state').classList.remove('paused'); $('#r-state span').textContent = 'Aufnahme';
        setStatus('Hört zu …');
      }
    }

    function stopLocal() {
      recordingActive = false; clearInterval(tick); cancelAnimationFrame(raf);
      try { node && node.disconnect(); } catch { }
      try { stream && stream.getTracks().forEach((t) => t.stop()); } catch { }
      try { ctx && ctx.close(); } catch { }
      try { wakeLock && wakeLock.release(); } catch { }
    }

    function stop() {
      if (!recordingActive) return;
      setStatus('Wird gespeichert …');
      $('#r-stop').disabled = true; $('#r-pause').disabled = true;
      try { ws.send(JSON.stringify({ type: 'stop' })); } catch { }
      stopLocal();
      partialEl?.classList.add('hidden');
    }

    function showDone(m) {
      if (finished) return; finished = true;
      $('#r-bar').classList.add('done');
      $('#r-state').innerHTML = '<span class="ok-txt">✓ Aufnahme beendet</span>';
      $('#r-pause').remove(); $('#r-stop').remove(); $('#r-wave').remove();
      setStatus(`${fmt(m.duration)} aufgenommen · ${m.segments.length} Sätze`);
      $('#r-info').textContent = '';
      $('#r-done').innerHTML = `
        <div class="card done-card">
          <div class="ico">${ICON.check}</div>
          <div class="grow">
            <strong>Die Aufnahme ist gespeichert.</strong>
            <div class="muted small">${m.refining ? 'Jetzt laufen automatisch: genaue Erkennung → Sprechererkennung. Sie können das Transkript sofort öffnen – es aktualisiert sich von selbst. Bereinigen und Protokoll starten Sie dort per Klick.' : 'Das Transkript ist fertig. Bereinigen und Protokoll starten Sie dort per Klick.'}</div>
          </div>
          <div class="done-actions"><a class="btn primary" href="#/t/${m.transcript_id}">Transkript öffnen</a><a class="btn" href="#/aufnahme" id="r-new">Neue Aufnahme</a></div>
        </div>`;
      $('#r-new').addEventListener('click', (e) => { e.preventDefault(); route(); });
      text.classList.add('finished');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      try { ws.close(); } catch { }
      loadSide();
    }

    // Titel auch während der Aufnahme änderbar
    $('#r-title-live').addEventListener('change', async (e) => {
      const v = e.target.value.trim(); if (!v || !tid) return;
      try { await api(`/api/transcripts/${tid}`, { method: 'PATCH', body: JSON.stringify({ title: v }) }); toast('Titel gespeichert'); loadSide(); } catch (err) { toast(err.message, true); }
    });
    $('#r-title-live').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
    startBtn.addEventListener('click', start);
    $('#r-pause').addEventListener('click', togglePause);
    $('#r-stop').addEventListener('click', stop);
    return () => { document.removeEventListener('visibilitychange', onVis); document.removeEventListener('health', updateStart); if (recordingActive) stop(); cancelAnimationFrame(raf); };
  }

  // ================================================================ Sitzungsdaten (Teilnehmende, Tagesordnung)
  function sitzungsFelder(p, m = {}) {
    const open = (m.teilnehmende && m.teilnehmende.length) || (m.tagesordnung && m.tagesordnung.length);
    return `<details class="sitzung" ${open ? 'open' : ''}><summary>Teilnehmende und Tagesordnung <span class="muted">(optional – verbessert Sprechernamen und Protokoll)</span></summary>
      <div class="sitzung-grid">
        <label>Teilnehmende<textarea id="${p}-tn" rows="4" placeholder="Frau Müller&#10;Herr Maier">${esc((m.teilnehmende || []).join('\n'))}</textarea><span class="hint">Anrede und Nachname, eine Person je Zeile. Keine Vornamen nötig.</span></label>
        <label>Tagesordnung<textarea id="${p}-to" rows="4" placeholder="Begrüßung&#10;Haushalt 2027&#10;Verschiedenes">${esc((m.tagesordnung || []).join('\n'))}</textarea>
          <span class="hint">Ein Punkt je Zeile – oder <a href="#" id="${p}-to-file">aus Datei übernehmen</a> (Word, PDF, Text).<input type="file" id="${p}-to-input" accept=".docx,.pdf,.txt,.md" hidden></span></label>
      </div></details>`;
  }
  function sitzungsDaten(p) {
    const lines = (id) => ($('#' + id)?.value || '').split(/\n/).map((x) => x.trim()).filter(Boolean);
    return { teilnehmende: lines(`${p}-tn`).flatMap((x) => x.split(/[,;]/)).map((x) => x.trim()).filter(Boolean), tagesordnung: lines(`${p}-to`) };
  }
  function bindSitzung(p) {
    const link = $(`#${p}-to-file`), input = $(`#${p}-to-input`);
    if (!link) return;
    link.addEventListener('click', (e) => { e.preventDefault(); input.click(); });
    input.addEventListener('change', async () => {
      const f = input.files[0]; if (!f) return;
      const fd = new FormData(); fd.append('file', f);
      try {
        const r = await fetch('/api/tagesordnung/lesen', { method: 'POST', body: fd });
        const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.statusText);
        $(`#${p}-to`).value = j.join('\n'); toast(`${j.length} Tagesordnungspunkte übernommen – bitte kurz prüfen`);
      } catch (e) { toast(e.message, true); }
      input.value = '';
    });
  }
  async function fillGlossarSelect(sel, cur = 'alle') {
    try {
      const aemter = await api('/api/glossar/aemter');
      sel.innerHTML = '<option value="alle">Alle Ämter</option>' + aemter.map((a) => `<option value="${esc(a)}">Amt ${esc(a)} + allgemein</option>`).join('');
      sel.value = cur;
    } catch { }
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
      <div class="card up-card">
        <div class="rec-fields"><label>Titel<input id="up-title" type="text" placeholder="optional – sonst Dateiname"></label><label>Glossar<select id="up-glossar"><option value="alle">Alle Ämter</option></select></label></div>
        ${sitzungsFelder('u')}
      </div>
      <div class="jobs" id="jobs"></div>`;
    const drop = $('#drop'), input = $('#file'), jobs = $('#jobs');
    fillGlossarSelect($('#up-glossar')); bindSitzung('u');
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
        const sd = sitzungsDaten('u');
        fd.append('glossar', $('#up-glossar').value); fd.append('teilnehmende', sd.teilnehmende.join('\n')); fd.append('tagesordnung', sd.tagesordnung.join('\n'));
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
        else { const p = t.progress; c.textContent = progressText(p); bar.style.width = progressPct(p) + '%'; }
      }
    }, 1000);
    return () => clearInterval(poll);
  }
  function progressText(p) {
    if (!p) return 'wartet …';
    const n = health && health.auto_ai_clean ? 3 : 2;
    if (p.done === -1) return `Schritt 2 von ${n}: Sprecher werden erkannt …`;
    if (p.done === -2) return `Schritt 3 von ${n}: KI bereinigt den Text …`;
    return `Schritt 1 von ${n}: genaue Erkennung … ${p.done}/${p.total} Abschnitte`;
  }
  const progressPct = (p) => (!p ? 0 : p.done === -1 ? 70 : p.done === -2 ? 85 : Math.round(60 * p.done / Math.max(1, p.total)));

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
      <div class="page-head"><h1>Glossar</h1><p>Begriffe, Namen und Abkürzungen der Verwaltung. Sie werden nach der Erkennung automatisch korrigiert und der KI als Pflicht-Schreibweise mitgegeben – z. B. „CHS“ → „Excel“. Einträge ohne Amt gelten für alle.</p></div>
      <div class="card">
        <div class="gl-tools">
          <label class="field-label inline">Anzeigen<select id="gl-filter"><option value="">Alle Einträge</option></select></label>
          <span class="sp"></span>
          <button class="btn" id="gl-import">Aus Excel übernehmen</button><input type="file" id="gl-file" accept=".xlsx,.csv" hidden>
        </div>
        <table class="glossar"><thead><tr><th>Wird erkannt als</th><th>Richtig</th><th>Amt</th><th></th></tr></thead><tbody id="gl"></tbody></table>
        <div class="gl-actions"><button class="btn" id="gl-add">+ Eintrag</button><button class="btn primary" id="gl-save">Speichern</button>
          <span class="muted small">Excel: Spalten „Erkannt als“, „Richtig“ und optional „Amt“ – oder einfach die ersten beiden Spalten.</span></div>
      </div>`;
    const body = $('#gl'), filter = $('#gl-filter');
    let all = await api('/api/glossar');
    const row = (e = {}) => `<tr data-amt="${esc(e.amt || '')}"><td><input type="text" value="${esc(e.von)}" placeholder="z. B. CHS"></td><td><input type="text" value="${esc(e.zu)}" placeholder="z. B. Excel"></td><td><input type="text" class="amt" value="${esc(e.amt || '')}" placeholder="alle"></td><td><button class="btn ghost sm" data-del>✕</button></td></tr>`;
    const collect = () => $$('tr', body).map((tr) => { const [a, b, c] = $$('input', tr); return { von: a.value, zu: b.value, amt: c.value }; });
    const fillFilter = () => {
      const aemter = [...new Set(all.map((e) => e.amt).filter(Boolean))].sort();
      const cur = filter.value;
      filter.innerHTML = '<option value="">Alle Einträge</option>' + aemter.map((a) => `<option value="${esc(a)}">Amt ${esc(a)} + allgemein</option>`).join('');
      filter.value = aemter.includes(cur) ? cur : '';
    };
    const drawRows = () => {
      const f = filter.value;
      body.innerHTML = all.map((e) => row(e)).join('') || row({ amt: f });
      $$('tr', body).forEach((tr) => { tr.hidden = !!f && !['', f].includes(tr.dataset.amt); });
    };
    fillFilter(); drawRows();
    filter.addEventListener('change', () => { all = collect(); drawRows(); });
    body.addEventListener('click', (e) => { if (e.target.closest('[data-del]')) e.target.closest('tr').remove(); });
    $('#gl-add').addEventListener('click', () => body.insertAdjacentHTML('beforeend', row({ amt: filter.value })));
    $('#gl-save').addEventListener('click', async () => {
      all = await api('/api/glossar', { method: 'PUT', body: JSON.stringify({ eintraege: collect() }) });
      fillFilter(); drawRows(); toast(`Glossar gespeichert (${all.length} Einträge)`);
    });
    $('#gl-import').addEventListener('click', () => $('#gl-file').click());
    $('#gl-file').addEventListener('change', async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const amt = prompt('Für welches Amt gelten die Einträge? (leer lassen = für alle; Angaben in der Datei haben Vorrang)', filter.value || '');
      if (amt === null) { e.target.value = ''; return; }
      const fd = new FormData(); fd.append('file', f); fd.append('amt', amt.trim());
      try {
        const r = await fetch('/api/glossar/import', { method: 'POST', body: fd }); const j = await r.json();
        if (!r.ok) throw new Error(j.detail || r.statusText);
        all = [...collect(), ...j]; fillFilter(); drawRows();
        toast(`${j.length} Einträge übernommen – zum Übernehmen „Speichern“ klicken`);
      } catch (err) { toast(err.message, true); }
      e.target.value = '';
    });
  }

  // ================================================================ FAQ & interne Infos
  const INFO_TABS = [
    ['infrastruktur', 'Infrastruktur'], ['technik', 'Technische Doku'],
    ['datenschutz', 'Datenschutz'], ['sicherheit', 'IT-Sicherheit'], ['personalrat', 'Personalrat'],
  ];
  async function renderInfo(tab) {
    const faq = tab === 'faq';
    if (!faq && !INFO_TABS.some(([k]) => k === tab)) tab = 'infrastruktur';
    view.innerHTML = faq ? `
      <div class="page-head"><h1>Häufige Fragen</h1><p>Kurze Antworten für alle, die den Protokollanten nutzen.</p></div>
      <article class="doc card" id="doc"><p class="muted">Lädt …</p></article>` : `
      <div class="page-head"><h1>Infos <span class="pill">intern</span></h1>
        <p>Unterlagen für Betrieb, Datenschutz, IT-Sicherheit und Personalrat. Stand und Inhalte wachsen mit dem Projekt.</p></div>
      <nav class="info-tabs" aria-label="Info-Bereiche">${INFO_TABS.map(([k, l]) => `<a href="#/infos/${k}" class="${k === tab ? 'active' : ''}">${l}</a>`).join('')}</nav>
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
  const STYLE_INFO = {
    zusammenfassung: ['Zusammenfassung', 'Kurz: worum ging es, das Wichtigste, Entscheidungen, Aufgaben.'],
    ergebnis: ['Ergebnisprotokoll', 'Nach Themen gegliedert: Ergebnisse, Beschlüsse, Aufgaben, offene Punkte.'],
    verlauf: ['Verlaufsprotokoll', 'Chronologisch mit Zeitabschnitten: wer hat was gesagt.'],
  };
  async function renderDetail(id) {
    let t = await api(`/api/transcripts/${id}`);
    let showClean = true, mTab = 'tx', editMd = false, task = null, taskPoll = null, tick = null;
    let curStyle = t.protokoll ? t.protokoll.style : null;
    let q = '', hit = 0, aufgaben = null;
    const prot = () => (curStyle && t.protokolle && t.protokolle[curStyle]) || null;

    const speakerColor = () => { const m = new Map(); t.segments.forEach((s) => { if (s.speaker && !m.has(s.speaker)) m.set(s.speaker, COLORS[m.size % COLORS.length]); }); return m; };
    const hasClean = () => t.segments.some((s) => s.clean);
    const llm = () => health && health.llm_configured;
    const working = () => t.status === 'processing' || t.status === 'refining';
    const busy = () => task && task.status === 'running';

    function taskEstimate() {
      const el = (task.elapsed || 0) + (task._since ? (Date.now() - task._since) / 1000 : 0);
      const tot = task.total || 0, done = task.done || 0;
      let eta = task.eta || 0;
      if (tot && done) eta = eta ? 0.5 * eta + 0.5 * (el * tot / done) : el * tot / done;
      const byWork = tot ? done / tot : 0, byTime = eta ? el / eta : 0;
      const pct = Math.max(byWork, Math.min(0.97, byTime));
      return { pct: Math.round(pct * 100), rest: eta ? Math.max(0, eta - el) : null };
    }
    function taskBar() {
      if (!busy()) return '';
      const label = task.kind === 'bereinigen' ? 'KI bereinigt den Text' : `KI erstellt ${STYLE_INFO[task.style || 'zusammenfassung'][0]}`;
      const e = taskEstimate();
      const rest = e.rest == null ? 'startet …' : e.rest < 8 ? 'gleich fertig' : `noch ca. ${e.rest < 90 ? Math.round(e.rest / 5) * 5 + ' s' : Math.round(e.rest / 60) + ' Min.'}`;
      return `<div class="progress-banner task"><span class="spin"></span><span class="pb-label">${label}</span><span class="pb-pct">${e.pct} %</span><div class="bar"><i style="width:${Math.max(2, e.pct)}%"></i></div><span class="pb-rest">${rest}</span></div>`;
    }
    function refreshTaskBar() { const bar = $('.progress-banner.task'); if (bar && busy()) bar.outerHTML = taskBar(); }

    function draw() {
      const rtf = t.duration && t.processing_seconds ? t.duration / t.processing_seconds : 0;
      const speakers = new Set(t.segments.map((s) => s.speaker).filter(Boolean));

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
          <span class="chip blue" title="Spracherkennung">Modell: ${esc(t.model || '–')}</span>
          ${rtf ? `<span class="chip ok" title="So lange hat der Server für die Spracherkennung gebraucht">Rechenzeit ${fmtDur(t.processing_seconds)} · ${rtf.toFixed(0)}× schneller als die Aufnahme</span>` : ''}
          ${hasClean() ? '<span class="chip ai">KI-bereinigt</span>' : ''}
          <button class="chip chip-btn" id="d-meta" title="Teilnehmende, Tagesordnung und Glossar dieser Sitzung">${metaLabel()}</button>
          ${t.status === 'error' ? `<span class="chip danger">${esc(t.error)}</span>` : (t.error ? `<span class="chip warn" title="${esc(t.error)}">Hinweis</span>` : '')}
        </div>
        ${working() ? `<div class="progress-banner"><span class="spin"></span><span>${t.status === 'refining' ? 'Wird verfeinert – ' : ''}${progressText(t.progress)}</span><div class="bar"><i style="width:${progressPct(t.progress)}%"></i></div></div>` : ''}
        ${taskBar()}
        <div class="ai-bar">
          <button class="btn ai" id="ai-clean" ${busy() || working() || !t.segments.length ? 'disabled' : ''}>${ICON.spark}${hasClean() ? 'Erneut bereinigen' : 'Text bereinigen'}</button>
          <div class="menu"><button class="btn ai" id="ai-sum" ${busy() || working() || !t.segments.length ? 'disabled' : ''}>${ICON.doc}Protokoll erstellen ▾</button></div>
          <span class="note">${llm() ? `KI: ${esc(health.llm_model)} · korrigiert nur, erfindet nichts · Ihre eigenen Korrekturen haben Vorrang` : 'Keine KI angebunden – die Knöpfe führen Schritt für Schritt über NOVA.'}</span>
        </div>
        <div class="d-tabs"><div class="seg-toggle"><button data-mtab="tx" class="${mTab === 'tx' ? 'on' : ''}">Transkript</button><button data-mtab="notes" class="${mTab === 'notes' ? 'on' : ''}">Protokoll</button></div></div>
        <div class="d-grid" data-tab="${mTab}">
          <section class="tx-pane">
            <div class="pane-head"><h2>Transkript</h2><span class="sp"></span>
              <div class="tx-search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg><input id="tx-q" type="search" placeholder="Im Transkript suchen" value="${esc(q)}"><span class="tx-hits" id="tx-hits"></span></div>
              ${hasClean() ? `<div class="seg-toggle"><button data-view="clean" class="${showClean ? 'on' : ''}">✨ Bereinigt</button><button data-view="orig" class="${showClean ? '' : 'on'}">Original</button></div>` : ''}
            </div>
            <div class="tx ${t.status === 'refining' ? 'provisional' : ''}" id="tx">${drawBlocks()}</div>
          </section>
          <aside class="notes" id="notes">${drawNotes()}</aside>
        </div>
        ${t.has_audio ? `<div class="player"><audio id="audio" controls preload="metadata" src="/api/transcripts/${t.id}/audio"></audio></div>` : ''}`;
      bind();
    }

    function metaLabel() {
      const m = t.meta || {}; const tn = (m.teilnehmende || []).length, to = (m.tagesordnung || []).length;
      const parts = [tn ? `${tn} Teilnehmende` : '', to ? `Tagesordnung: ${to} Punkte` : '', m.glossar && m.glossar !== 'alle' ? `Glossar Amt ${m.glossar}` : ''].filter(Boolean);
      return parts.length ? '✎ ' + parts.join(' · ') : '+ Teilnehmende und Tagesordnung';
    }
    const hl = (html) => {
      if (!q) return html;
      const rx = new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
      return html.replace(rx, (m) => `<mark>${m}</mark>`);
    };
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
            const mk = s.edited ? '<span class="mk edit" title="Von Ihnen korrigiert – die KI ändert diesen Satz nicht mehr">✎</span>'
              : useClean && s.clean !== s.text ? `<span class="mk ai" title="Original: ${esc(s.text)}">✨</span>`
              : (s.clean_note ? `<span class="mk warn" title="KI-Bereinigung verworfen: ${esc(s.clean_note)}">⚠</span>` : '');
            return `<span class="seg" data-idx="${s.idx}" data-start="${s.start}" data-end="${s.end}"><span class="txt" contenteditable="true" spellcheck="true" data-field="${useClean ? 'clean' : 'text'}">${hl(esc(useClean ? s.clean : s.text))}</span>${mk}</span>`;
          }).join('')}</div>
        </div>`;
      }).join('');
    }

    const TIPS = { unbelegt: 'Keine Belegstelle im Transkript – bitte prüfen', ungueltig: 'Die Belegstelle gibt es nicht – bitte prüfen', schwach: 'Passt inhaltlich kaum zur Stelle im Transkript – bitte prüfen' };
    function inlineMd(text) { return esc(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); }
    function renderMd(zeilen) {
      return zeilen.map((z) => {
        const raw = z.text.trim(); if (!raw) return '';
        const h = raw.match(/^(#{1,4})\s+(.*)$/);
        if (h) return h[1].length <= 2 ? `<h3>${esc(h[2])}</h3>` : `<h4>${esc(h[2])}</h4>`;
        const li = /^[-*]\s+/.test(raw);
        const body = (li ? raw.replace(/^[-*]\s+/, '') : raw).replace(/\s*\[\s*S\s*\d+(?:\s*[,;–-]\s*S?\s*\d+)*\s*\]/g, '').trim();
        const refs = (z.refs || []).join(',');
        const tip = TIPS[z.status] || (refs ? 'Klicken: zur Stelle im Transkript springen' : '');
        return `<div class="pl ${li ? 'li' : ''} ${z.status}" data-refs="${refs}" ${tip ? `title="${tip}"` : ''}>${inlineMd(body)}</div>`;
      }).join('');
    }
    function toMarkdown(root) {
      const out = [];
      [...root.children].forEach((el) => {
        const txt = (node) => {
          const c = node.cloneNode(true);
          c.querySelectorAll('strong,b').forEach((b) => b.replaceWith('**' + b.textContent + '**'));
          return c.textContent.replace(/\s+/g, ' ').trim();
        };
        const text = txt(el); if (!text) return;
        if (el.tagName === 'H3') { out.push('', '## ' + text); return; }
        if (el.tagName === 'H4') { out.push('', '### ' + text); return; }
        const refs = (el.dataset.refs || '').split(',').filter(Boolean);
        out.push((el.classList.contains('li') ? '- ' : '') + text + (refs.length ? ' [' + refs.map((r) => 'S' + r).join(', ') + ']' : ''));
      });
      return out.join('\n').trim() + '\n';
    }

    function styleButtons(cur) {
      return Object.entries(STYLE_INFO).map(([k, [name, desc]]) => `<button class="style-card ${k === cur ? 'on' : ''}" data-style="${k}" ${busy() || working() ? 'disabled' : ''}><strong>${name}</strong><span>${desc}</span></button>`).join('');
    }
    function styleMenuHtml() {
      return Object.entries(STYLE_INFO).map(([k, [n, d]]) => {
        const have = t.protokolle && t.protokolle[k];
        return have ? `<button data-act="${k}" class="${k === curStyle ? 'current' : ''}"><strong>${n}</strong><small>${k === curStyle ? 'wird angezeigt' : 'anzeigen'}</small></button>`
          : `<span class="disabled"><strong>${n}</strong><small>noch nicht erstellt – oben über „Protokoll erstellen“</small></span>`;
      }).join('');
    }

    function drawAufgaben() {
      if (!aufgaben || !aufgaben.length) return '';
      return `<div class="tasks"><div class="tasks-head"><h4>Aufgaben (${aufgaben.length})</h4><button class="btn ghost sm" id="a-copy" title="Als Tabelle kopieren – zum Einfügen in Teams, Outlook oder Excel">Kopieren</button></div>
        <table><thead><tr><th>Wer</th><th>Was</th><th>Bis wann</th></tr></thead><tbody>${aufgaben.map((a) => `<tr data-jump="${a.refs[0] ?? ''}"><td>${esc(a.wer || '–')}</td><td>${esc(a.was)}</td><td>${esc(a.bis || '–')}</td></tr>`).join('')}</tbody></table></div>`;
    }
    async function loadAufgaben() {
      if (!prot()) return;
      try { aufgaben = await api(`/api/transcripts/${t.id}/aufgaben`); } catch { aufgaben = []; }
      const box = $('#aufgaben'); if (box) { box.innerHTML = drawAufgaben(); bindAufgaben(); }
    }
    function bindAufgaben() {
      $('#a-copy')?.addEventListener('click', async () => {
        const rows = aufgaben.map((a) => [a.wer || '–', a.was, a.bis || '–'].join('\t'));
        if (await copy(['Wer\tWas\tBis wann', ...rows].join('\n'))) toast('Aufgaben kopiert – in Teams, Outlook oder Excel einfügen');
      });
      $$('.tasks tr[data-jump]').forEach((tr) => { if (tr.dataset.jump !== '') tr.addEventListener('click', () => jump(+tr.dataset.jump)); });
    }

    function qualityLine(p) {
      const pr = p.content.pruefung || { zeilen_inhalt: 0, unbelegt: 0, ungueltige_belege: 0, schwach_belegt: 0 };
      const n = pr.zeilen_inhalt || 0, bad = (pr.unbelegt || 0) + (pr.ungueltige_belege || 0) + (pr.schwach_belegt || 0);
      const weg = p.content.verworfen || 0;
      const ok = n - bad;
      let html = `<span class="q-ico ${bad ? 'warn' : 'ok'}">${bad ? '!' : '✓'}</span><span><strong>${ok} von ${n} Aussagen</strong> stehen so im Transkript.`;
      if (bad) html += ` ${bad} ${bad === 1 ? 'Stelle ist' : 'Stellen sind'} gelb markiert – bitte prüfen.`;
      if (weg) html += ` <span class="muted" title="${esc((p.content.verworfen_beispiele || []).join(' · '))}">${weg} KI-${weg === 1 ? 'Aussage wurde' : 'Aussagen wurden'} entfernt, weil sie nicht im Transkript ${weg === 1 ? 'steht' : 'stehen'}.</span>`;
      return `<div class="quality">${html}</span></div>`;
    }

    function drawNotes() {
      const p = prot();
      const picker = t.protokolle && Object.keys(t.protokolle).length
        ? `<div class="menu"><button class="style-pick" id="p-pick">${esc(STYLE_INFO[curStyle]?.[0] || 'Protokoll')} <span class="chev">▾</span></button></div>` : '';
      const head = `<div class="pane-head"><h2>Protokoll</h2><span class="sp"></span>${picker}</div>`;
      if (!p) return `${head}<div class="card notes-card empty-notes"><p>Noch kein Protokoll.</p><p class="muted small">Oben auf <strong>„Protokoll erstellen“</strong> klicken und die Art wählen – oder direkt hier:</p><div class="style-cards">${styleButtons('')}</div><p class="muted small">Jede Aussage wird gegen das Transkript geprüft. Was dort nicht steht, wird entfernt.</p></div>`;
      const geprueft = p.status === 'bestaetigt';
      const am = p.content.geprueft_am ? fmtDate(p.content.geprueft_am, { dateStyle: 'medium' }) : '';
      return `${head}
        <div class="card notes-card">
          ${qualityLine(p)}
          <div class="prot ${editMd ? 'editing' : ''}" id="prot" ${editMd ? 'contenteditable="true" spellcheck="true"' : ''}>${renderMd((p.content.pruefung || {}).zeilen || [])}</div>
          <div class="p-actions">
            ${editMd ? '<button class="btn primary sm" id="p-save">Speichern</button><button class="btn ghost sm" id="p-cancel">Abbrechen</button><span class="muted small">Direkt im Text ändern – wie in Word.</span>'
              : `<button class="btn sm" id="p-edit">Bearbeiten</button>
                 <button class="btn sm ${geprueft ? 'done' : ''}" id="p-confirm" title="${geprueft ? 'Markierung aufheben' : 'Bestätigen, dass eine Person das Protokoll inhaltlich geprüft hat. Steht dann auch im Word-Export.'}">${geprueft ? ICON.check + 'Geprüft' + (am ? ' am ' + am : '') : 'Als geprüft markieren'}</button>
                 <button class="btn ghost sm" id="p-copy">Kopieren</button>`}
          </div>
          <div id="aufgaben">${drawAufgaben()}</div>
        </div>`;
    }

    // ---- Interaktion
    const audio = () => $('#audio');
    const seek = (sec) => { const a = audio(); if (!a) return; a.currentTime = Math.max(0, sec - 0.2); a.play().catch(() => { }); };
    const jump = (idx) => {
      const s = t.segments.find((x) => x.idx === idx); if (!s) { toast(`Diese Stelle gibt es im Transkript nicht`, true); return; }
      if (mTab !== 'tx' && innerWidth <= 900) { mTab = 'tx'; draw(); }
      const el = $(`.seg[data-idx="${idx}"]`); if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('playing'); setTimeout(() => el.classList.remove('playing'), 2500); }
      seek(s.start);
    };

    function bind() {
      $('#d-title').addEventListener('change', async (e) => { await api(`/api/transcripts/${t.id}`, { method: 'PATCH', body: JSON.stringify({ title: e.target.value }) }); t.title = e.target.value; toast('Titel gespeichert'); loadSide(); });
      const ex = (q) => `/api/transcripts/${t.id}/export?${q}`;
      $('#d-export').addEventListener('click', (e) => {
        const p = prot(), pn = p ? STYLE_INFO[p.style][0] : 'Protokoll', st = p ? `&style=${p.style}` : '';
        const dis = (label) => `<span class="disabled" title="Zuerst ein Protokoll erstellen">${label}</span>`;
        openMenu(e.currentTarget, `
        <div class="menu-label">Word</div>
        <a href="${ex('format=docx')}">Transkript (mit Zeit &amp; Sprecher)</a>
        ${p ? `<a href="${ex('format=protokoll' + st)}">${esc(pn)}</a>` : dis('Protokoll')}
        ${p ? `<a href="${ex('format=docx&protokoll=1' + st)}">Transkript und ${esc(pn)}</a>` : dis('Transkript und Protokoll')}
        <a href="${ex('format=fliesstext')}">Nur Fließtext</a><hr>
        <div class="menu-label">Weitere Formate</div>
        <a href="${ex('format=txt')}">Text mit Zeit &amp; Sprecher (.txt)</a>
        <a href="${ex('format=md')}">Markdown (.md)</a>
        <a href="${ex('format=srt')}">Untertitel (.srt)</a>`);
      });
      $('#d-more').addEventListener('click', (e) => openMenu(e.currentTarget, `
        ${hasClean() ? '<button data-act="unclean">KI-Bereinigung verwerfen</button>' : ''}
        <button data-act="delaudio" ${t.has_audio ? '' : 'disabled'}>Nur Audio löschen</button><hr>
        <button data-act="delete" class="danger">Aufnahme endgültig löschen</button>`, async (act) => {
        if (act === 'delete') { if (!confirm('Transkript und Audio endgültig löschen?')) return; await api(`/api/transcripts/${t.id}`, { method: 'DELETE' }); toast('Gelöscht'); loadSide(); location.hash = '#/aufnahme'; }
        if (act === 'delaudio') { if (!confirm('Audio endgültig löschen? Das Transkript bleibt erhalten.')) return; await api(`/api/transcripts/${t.id}/audio`, { method: 'DELETE' }); t.has_audio = false; toast('Audio gelöscht'); draw(); }
        if (act === 'unclean') { await api(`/api/transcripts/${t.id}/bereinigen`, { method: 'DELETE' }); await reload(); toast('Bereinigung verworfen – Original wird angezeigt (Ihre eigenen Korrekturen bleiben)'); }
      }));
      $$('[data-mtab]').forEach((b) => b.addEventListener('click', () => { mTab = b.dataset.mtab; draw(); }));
      $$('[data-view]').forEach((b) => b.addEventListener('click', () => { showClean = b.dataset.view === 'clean'; draw(); }));
      bindSegs();
      const qi = $('#tx-q');
      const redrawTx = () => { $('#tx').innerHTML = drawBlocks(); bindSegs(); showHit(!!q); };
      qi.addEventListener('input', () => { q = qi.value.trim(); hit = 0; redrawTx(); });
      qi.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); hit += e.shiftKey ? -1 : 1; showHit(); } if (e.key === 'Escape') { qi.value = ''; q = ''; redrawTx(); } });
      showHit(false);
      bindAufgaben();
      $('#d-meta').addEventListener('click', editMeta);
      $$('[data-ref]').forEach((el) => el.addEventListener('click', () => jump(+el.dataset.ref)));
      const a = audio();
      if (a) a.ontimeupdate = () => {
        const cur = a.currentTime;
        $$('.seg').forEach((el) => el.classList.toggle('playing', cur >= +el.dataset.start && cur < +el.dataset.end));
      };
      $('#ai-clean').addEventListener('click', aiClean);
      $('#ai-sum').addEventListener('click', (e) => openMenu(e.currentTarget, Object.entries(STYLE_INFO).map(([k, [n, d]]) => `<button data-act="${k}"><strong>${n}</strong><small>${d}</small></button>`).join(''), (st) => aiSum(st)));
      $$('[data-style]').forEach((b) => b.addEventListener('click', () => aiSum(b.dataset.style)));
      $('#p-pick')?.addEventListener('click', (e) => openMenu(e.currentTarget, styleMenuHtml(), (st) => { curStyle = st; editMd = false; draw(); }));
      if (!editMd) $$('#prot .pl[data-refs]').forEach((el) => { const r = (el.dataset.refs || '').split(',').filter(Boolean); if (r.length) el.addEventListener('click', () => jump(+r[0])); });
      $('#p-edit')?.addEventListener('click', () => { editMd = true; draw(); const pr = $('#prot'); if (pr) { pr.focus(); } });
      $('#p-cancel')?.addEventListener('click', () => { editMd = false; draw(); });
      $('#p-save')?.addEventListener('click', async () => {
        const md = toMarkdown($('#prot'));
        const np = await api(`/api/transcripts/${t.id}/protokoll`, { method: 'PATCH', body: JSON.stringify({ protokoll_md: md, style: curStyle }) });
        t.protokolle[curStyle] = np; editMd = false; draw(); toast('Gespeichert');
      });
      $('#p-confirm')?.addEventListener('click', async () => {
        const p = prot();
        const np = await api(`/api/transcripts/${t.id}/protokoll`, { method: 'PATCH', body: JSON.stringify({ status: p.status === 'bestaetigt' ? 'entwurf' : 'bestaetigt', style: curStyle }) });
        t.protokolle[curStyle] = np; draw(); toast(np.status === 'bestaetigt' ? 'Als geprüft markiert – steht so auch im Word-Export' : 'Markierung aufgehoben');
      });
      $('#p-copy')?.addEventListener('click', async () => { const md = prot().content.protokoll_md.replace(/\s*\[\s*S\s*\d+(?:\s*[,;–-]\s*S?\s*\d+)*\s*\]/g, ''); if (await copy(md)) toast('Protokoll kopiert'); });
      sizePanes();
    }

    function bindSegs() {
      $$('.seg .txt').forEach((el) => {
        el.dataset.orig = el.textContent;
        el.addEventListener('paste', (e) => { e.preventDefault(); document.execCommand('insertText', false, (e.clipboardData || window.clipboardData).getData('text/plain')); });
        el.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); el.blur(); } });
        el.addEventListener('dblclick', () => seek(+el.parentElement.dataset.start));
        el.addEventListener('blur', async () => {
          const val = el.textContent.trim(); if (val === el.dataset.orig) return;
          const idx = +el.parentElement.dataset.idx, field = el.dataset.field;
          const r = await api(`/api/transcripts/${t.id}/segments/${idx}`, { method: 'PATCH', body: JSON.stringify({ [field]: val }) });
          const s = t.segments.find((x) => x.idx === idx);
          if (field === 'text') { s.text = val; if (s.clean) s.clean = val; } else s.clean = val;
          s.edited = true; s.clean_note = ''; el.dataset.orig = val;
          if (!el.parentElement.querySelector('.mk.edit')) { el.parentElement.querySelector('.mk')?.remove(); el.insertAdjacentHTML('afterend', '<span class="mk edit" title="Von Ihnen korrigiert – die KI ändert diesen Satz nicht mehr">✎</span>'); }
          if (r.weitere_stellen && r.weitere_stellen.length) {
            const k = r.korrekturen[0];
            toastAction(`„${k.von}“ steht noch an ${r.weitere_stellen.length} weiteren Stelle${r.weitere_stellen.length > 1 ? 'n' : ''}.`, `Überall durch „${k.zu}“ ersetzen`, async () => {
              const x = await api(`/api/transcripts/${t.id}/ersetzen`, { method: 'POST', body: JSON.stringify({ paare: r.korrekturen }) });
              await reload(); toast(`${x.ersetzt} Stellen ersetzt`);
            });
          } else toast('Gespeichert – Ihre Korrektur hat Vorrang vor der KI');
        });
      });
      $$('[data-seek]').forEach((el) => el.addEventListener('click', () => seek(+el.dataset.seek)));
      $$('.bname').forEach((el) => el.addEventListener('click', (e) => pickSpeaker(el, e)));
    }

    function showHit(scroll = true) {
      const marks = $$('#tx mark'); const out = $('#tx-hits'); if (!out) return;
      out.textContent = q ? (marks.length ? `${((hit % marks.length) + marks.length) % marks.length + 1}/${marks.length}` : '0 Treffer') : '';
      if (!marks.length) return;
      marks.forEach((m) => m.classList.remove('cur'));
      const m = marks[((hit % marks.length) + marks.length) % marks.length]; m.classList.add('cur');
      if (scroll) m.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }

    function pickSpeaker(el, ev) {
      const namen = (t.meta && t.meta.teilnehmende) || [];
      const old = el.dataset.spk;
      openMenu(el, `<div class="menu-label">${old ? `„${esc(old)}“ ist …` : 'Wer spricht hier?'}</div>
        ${namen.map((n) => `<button data-act="n:${esc(n)}">${esc(n)}</button>`).join('') || '<span class="disabled">Noch keine Teilnehmenden eingetragen</span>'}
        <hr><button data-act="frei">Anderer Name …</button>`, (act) => {
        if (act === 'frei') { const neu = prompt(old ? `Neuer Name für „${old}“ (gilt für alle Stellen):` : 'Sprechername:', old && !old.startsWith('Sprecher ') ? old : ''); if (neu && neu.trim()) setSpeaker(el, old, neu.trim()); }
        else setSpeaker(el, old, act.slice(2));
      });
    }

    async function setSpeaker(el, old, neu) {
        if (old) { await api(`/api/transcripts/${t.id}/speakers/rename`, { method: 'POST', body: JSON.stringify({ von: old, zu: neu }) }); t.segments.forEach((s) => { if (s.speaker === old) s.speaker = neu; }); }
        else { const idx = +el.closest('.block').querySelector('.seg').dataset.idx; await api(`/api/transcripts/${t.id}/segments/${idx}`, { method: 'PATCH', body: JSON.stringify({ speaker: neu }) }); t.segments.find((s) => s.idx === idx).speaker = neu; }
        draw();
    }

    async function editMeta() {
      const card = modal.open(`<h2>Teilnehmende, Tagesordnung, Glossar</h2>
        <p class="muted small">Namen werden beim Zuordnen der Sprecher angeboten und beim Bereinigen richtig geschrieben. Mit Tagesordnung gliedert sich das Ergebnisprotokoll nach den Punkten.</p>
        <label class="field-label">Glossar<select id="d-glossar"><option value="alle">Alle Ämter</option></select></label>
        ${sitzungsFelder('d', t.meta || {})}
        <div class="modal-actions"><button class="btn" id="m-x">Abbrechen</button><button class="btn primary" id="m-ok">Speichern</button></div>`);
      $('details.sitzung', card).open = true;
      fillGlossarSelect($('#d-glossar', card), t.meta.glossar || 'alle'); bindSitzung('d');
      $('#m-x', card).onclick = modal.close;
      $('#m-ok', card).onclick = async () => {
        const sd = sitzungsDaten('d');
        t.meta = await api(`/api/transcripts/${t.id}/meta`, { method: 'PUT', body: JSON.stringify({ ...sd, glossar: $('#d-glossar', card).value }) });
        modal.close(); draw(); toast('Gespeichert – gilt beim nächsten Bereinigen und Protokoll');
      };
    }

    function sizePanes() {
      const g = $('.d-grid'); if (!g) return;
      if (innerWidth <= 900) { g.style.height = ''; return; }
      const top = g.getBoundingClientRect().top + window.scrollY;
      const pl = $('.player'); const ph = pl ? pl.offsetHeight + 20 : 0;
      g.style.height = Math.max(380, innerHeight - top - ph - 24) + 'px';
    }
    const onResize = () => sizePanes();
    window.addEventListener('resize', onResize);

    function openMenu(anchor, html, onAct) {
      $$('.menu-list').forEach((m) => m.remove());
      const m = document.createElement('div'); m.className = 'menu-list'; m.innerHTML = html;
      anchor.parentElement.appendChild(m);
      setTimeout(() => document.addEventListener('click', function close(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('click', close); } }), 0);
      if (onAct) m.addEventListener('click', (e) => { const b = e.target.closest('[data-act]'); if (b) { m.remove(); onAct(b.dataset.act); } });
    }

    async function reload() {
      aufgaben = null; t = await api(`/api/transcripts/${id}`); if (!curStyle && t.protokoll) curStyle = t.protokoll.style; draw(); }

    // ---- KI-Aufgaben im Hintergrund mit Fortschritt
    function watchTask(onDone) {
      clearInterval(taskPoll); clearInterval(tick);
      tick = setInterval(refreshTaskBar, 400);
      taskPoll = setInterval(async () => {
        let s; try { s = await api(`/api/transcripts/${t.id}/task`); } catch { return; }
        task = { ...task, ...s, _since: Date.now() };
        if (s.status === 'running') { if ($('.progress-banner.task')) refreshTaskBar(); else draw(); return; }
        clearInterval(taskPoll); taskPoll = null; clearInterval(tick); tick = null;
        const done = task; task = null;
        if (s.status === 'error') { draw(); toast(s.error, true); return; }
        await onDone(done);
      }, 800);
    }

    async function aiClean() {
      if (llm()) {
        if (hasClean()) {
          const card = modal.open(`<h2>Text erneut bereinigen?</h2>
            <p>Dieser Text wurde bereits von der KI bereinigt. Ein zweiter Durchlauf ändert meist nur Kleinigkeiten.</p>
            <p class="muted">Sätze, die Sie selbst korrigiert haben (✎), bleiben unverändert – Ihre Korrekturen haben immer Vorrang.</p>
            <div class="modal-actions"><button class="btn" id="m-x">Abbrechen</button><button class="btn primary" id="m-ok">Erneut bereinigen</button></div>`);
          $('#m-x', card).onclick = modal.close;
          await new Promise((res) => { $('#m-ok', card).onclick = () => { modal.close(); res(); }; });
        }
        try { await api(`/api/transcripts/${t.id}/bereinigen`, { method: 'POST' }); } catch (e) { toast(e.message, true); return; }
        task = { kind: 'bereinigen', status: 'running', done: 0, total: 0, elapsed: 0 }; draw();
        watchTask(async (done) => {
          await reload(); showClean = true; draw();
          const r = done.result || {};
          toast(`${r.uebernommen ?? 0} Sätze bereinigt${r.verworfen ? ` · ${r.verworfen} verworfen (zu stark verändert – Original bleibt)` : ''}${r.eigene_korrekturen_behalten ? ` · ${r.eigene_korrekturen_behalten} eigene Korrekturen unverändert` : ''}`);
        });
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

    async function aiSum(style = 'zusammenfassung') {
      if (llm()) {
        try { await api(`/api/transcripts/${t.id}/protokoll`, { method: 'POST', body: JSON.stringify({ style }) }); } catch (e) { toast(e.message, true); return; }
        task = { kind: 'protokoll', style, status: 'running', done: 0, total: 0, elapsed: 0 }; mTab = 'notes'; draw();
        watchTask(async () => { t = await api(`/api/transcripts/${id}`); curStyle = style; mTab = 'notes'; editMd = false; aufgaben = null; draw(); loadAufgaben(); toast(`${STYLE_INFO[style][0]} erstellt`); });
        return;
      }
      const prompt = await api(`/api/transcripts/${t.id}/nova-prompt?style=${style}`);
      const ok = await copy(prompt);
      const card = modal.open(`<h2>${STYLE_INFO[style][0]} über NOVA</h2>
        <p class="muted">Jede Aussage muss eine Belegstelle wie [S12] tragen. Aussagen ohne Beleg werden danach gelb markiert – so fällt sofort auf, wenn die KI etwas hinzudichtet.</p>
        <div class="steps">
          <div class="step"><div><strong>${ok ? 'Anweisung und Transkript sind kopiert.' : 'Anweisung kopieren:'}</strong><br>In NOVA einfügen (Strg+V) und absenden.${ok ? '' : `<textarea readonly>${esc(prompt)}</textarea>`}</div></div>
          <div class="step"><div><strong>Antwort hier einfügen:</strong><textarea id="m-in" placeholder="## Worum ging es …"></textarea></div></div>
        </div>
        <div class="modal-actions"><button class="btn" id="m-copy">Nochmal kopieren</button><button class="btn" id="m-x">Abbrechen</button><button class="btn primary" id="m-ok">Prüfen &amp; übernehmen</button></div>`);
      $('#m-x', card).onclick = modal.close;
      $('#m-copy', card).onclick = async () => { if (await copy(prompt)) toast('Kopiert'); };
      $('#m-ok', card).onclick = async () => {
        try { const np = await api(`/api/transcripts/${t.id}/protokoll/import`, { method: 'POST', body: JSON.stringify({ style, protokoll_md: $('#m-in', card).value }) }); t.protokolle = { ...(t.protokolle || {}), [style]: np }; t.protokoll = np; curStyle = style; modal.close(); mTab = 'notes'; draw(); }
        catch (e) { toast(e.message, true); }
      };
    }

    draw();
    loadAufgaben();
    // Läuft schon eine KI-Aufgabe (z. B. nach Neuladen der Seite)? Dann Fortschritt weiter anzeigen.
    try {
      const s = await api(`/api/transcripts/${id}/task`);
      if (s.status === 'running') { task = { ...s, _since: Date.now() }; draw(); watchTask(async () => { t = await api(`/api/transcripts/${id}`); if (s.kind === 'protokoll' && s.style) curStyle = s.style; draw(); }); }
    } catch { }
    const poll = setInterval(async () => {
      if (!working()) return;
      if (document.activeElement && (document.activeElement.isContentEditable || document.activeElement.tagName === 'TEXTAREA')) return;
      try {
        const n = await api(`/api/transcripts/${id}`);
        const changed = n.status !== t.status || JSON.stringify(n.progress) !== JSON.stringify(t.progress) || n.segments.length !== t.segments.length;
        const a = audio(), pos = a ? a.currentTime : 0, playing = a && !a.paused;
        t = n;
        if (changed) {
          draw(); const b = audio(); if (b && pos) { b.currentTime = pos; if (playing) b.play().catch(() => { }); }
          if (n.status === 'done') { toast(hasClean() ? 'Fertig: genaue Erkennung, Sprecher und KI-Bereinigung abgeschlossen' : 'Fertig: genaue Erkennung und Sprecher. Jetzt können Sie bereinigen oder ein Protokoll erstellen.'); loadSide(); }
        }
      } catch { }
    }, 2000);
    return () => { clearInterval(poll); clearInterval(taskPoll); clearInterval(tick); window.removeEventListener('resize', onResize); };
  }

  // ================================================================ Start
  loadHealth();
  loadSide().then(route);
  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/sw.js').catch(() => { });
})();
