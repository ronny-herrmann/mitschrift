/* Mitschrift – Web-Oberfläche (reines JavaScript, kein Build-Schritt, keine externen Abhängigkeiten). */
(() => {
  'use strict';

  // ---------------------------------------------------------------- Hilfen
  const $ = (sel, root = document) => root.querySelector(sel);
  const view = $('#view');
  const fmt = (sec) => {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const mm = String(m).padStart(2, '0'), ss = String(s).padStart(2, '0');
    return h ? `${String(h).padStart(2, '0')}:${mm}:${ss}` : `${mm}:${ss}`;
  };
  const fmtDate = (iso) => {
    try { return new Date(iso).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }); } catch { return iso; }
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let toastTimer;
  const toast = (msg, err = false) => {
    const t = $('#toast');
    t.textContent = msg; t.className = 'toast show' + (err ? ' err' : '');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = 'toast'), err ? 6000 : 3000);
  };
  const api = async (path, opts = {}) => {
    const r = await fetch(path, { headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) }, ...opts });
    if (r.status === 401) { location.href = '/login'; throw new Error('Bitte anmelden'); }
    if (!r.ok) {
      let msg = r.statusText;
      try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch { }
      throw new Error(msg);
    }
    const ct = r.headers.get('content-type') || '';
    return ct.includes('application/json') ? r.json() : r.text();
  };
  const tpl = (id) => document.importNode($('#' + id).content, true);

  // ---------------------------------------------------------------- Systemstatus
  let health = null;
  async function loadHealth() {
    try {
      health = await api('/api/health');
      const ms = health.models || {}, live = ms.live || health.model || {}, fin = ms.final || live;
      const finTxt = fin.model && fin.model !== live.model ? ` · genau: ${esc(fin.model)}` : '';
      $('#sysinfo').innerHTML = `<span class="dot"></span>live: ${esc(live.model || live.backend)}${finTxt}`;
      $('#sysinfo').title = `Live: ${JSON.stringify(live)}\nGenau: ${JSON.stringify(fin)}\nStatus genaues Modell: ${ms.final_status || '-'}`;
      if (health.auth && !$('#logout')) {
        const lo = document.createElement('a'); lo.id = 'logout'; lo.href = '#'; lo.className = 'logout'; lo.textContent = 'Abmelden';
        lo.addEventListener('click', async (e) => { e.preventDefault(); await fetch('/api/logout', { method: 'POST' }); location.href = '/login'; });
        $('.topbar').appendChild(lo);
      }
      $('#sysinfo').classList.remove('err');
    } catch (e) {
      $('#sysinfo').innerHTML = `<span class="dot"></span>Server nicht erreichbar`;
      $('#sysinfo').classList.add('err');
    }
  }

  // ---------------------------------------------------------------- Router
  const routes = { aufnahme: renderAufnahme, hochladen: renderHochladen, transkripte: renderListe, glossar: renderGlossar, t: renderDetail };
  let cleanup = null;
  async function route() {
    if (cleanup) { try { cleanup(); } catch { } cleanup = null; }
    const hash = location.hash.replace(/^#\/?/, '') || 'aufnahme';
    const [name, arg] = hash.split('/');
    document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.nav === name));
    view.innerHTML = '';
    const fn = routes[name] || renderAufnahme;
    try { cleanup = await fn(arg); } catch (e) { view.innerHTML = `<section class="card"><h1>Fehler</h1><p>${esc(e.message)}</p></section>`; }
  }
  window.addEventListener('hashchange', route);

  // ================================================================ Aufnahme
  async function renderAufnahme() {
    view.appendChild(tpl('tpl-aufnahme'));
    const btn = $('#rec-btn'), consent = $('#rec-consent'), status = $('#rec-status'), timer = $('#rec-timer');
    const level = $('#rec-level'), live = $('#rec-live'), micSel = $('#rec-mic');
    let ctx, node, stream, ws, t0, tick, recording = false, segCount = 0, sumMs = 0;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status.textContent = 'Kein Mikrofonzugriff möglich – die Seite muss über HTTPS (oder localhost) geöffnet werden.';
      return;
    }
    // Mikrofonliste (Namen erst nach erster Freigabe sichtbar)
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      devs.filter((d) => d.kind === 'audioinput').forEach((d, i) => {
        const o = document.createElement('option'); o.value = d.deviceId; o.textContent = d.label || `Mikrofon ${i + 1}`; micSel.appendChild(o);
      });
    } catch { }

    consent.addEventListener('change', () => { btn.disabled = !consent.checked; });

    const setStatus = (s) => { status.textContent = s; };

    async function start() {
      const title = $('#rec-title').value.trim();
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { deviceId: micSel.value ? { exact: micSel.value } : undefined, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      } catch (e) { toast('Mikrofon nicht freigegeben: ' + e.message, true); return; }
      try { ctx = new AudioContext({ sampleRate: 16000 }); } catch { ctx = new AudioContext(); }
      await ctx.audioWorklet.addModule('/static/worklet.js');
      const src = ctx.createMediaStreamSource(stream);
      node = new AudioWorkletNode(ctx, 'pcm16-processor');
      src.connect(node);

      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws/live?title=${encodeURIComponent(title)}`);
      ws.binaryType = 'arraybuffer';
      const pending = [];
      ws.onopen = () => { pending.forEach((b) => ws.send(b)); pending.length = 0; };
      ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
      ws.onerror = () => toast('Verbindung zum Server gestört', true);
      ws.onclose = () => { if (recording) { setStatus('Verbindung getrennt – Aufnahme wurde serverseitig gesichert.'); stopLocal(); } };
      node.port.onmessage = (ev) => {
        level.style.width = Math.min(100, ev.data.rms * 400) + '%';
        if (ws.readyState === 1) ws.send(ev.data.pcm); else if (ws.readyState === 0) pending.push(ev.data.pcm);
      };
      recording = true; segCount = 0; sumMs = 0;
      btn.classList.add('recording'); btn.setAttribute('aria-label', 'Aufnahme beenden');
      consent.disabled = true; $('#rec-title').disabled = true; micSel.disabled = true;
      live.innerHTML = ''; $('#rec-final').classList.add('hidden');
      t0 = Date.now(); tick = setInterval(() => (timer.textContent = fmt((Date.now() - t0) / 1000)), 250);
      setStatus('Hört zu …');
    }

    function stopLocal() {
      recording = false; clearInterval(tick);
      btn.classList.remove('recording'); btn.setAttribute('aria-label', 'Aufnahme starten');
      consent.disabled = false; $('#rec-title').disabled = false; micSel.disabled = false;
      level.style.width = '0';
      try { node && node.disconnect(); } catch { }
      try { stream && stream.getTracks().forEach((t) => t.stop()); } catch { }
      try { ctx && ctx.close(); } catch { }
    }

    function stop() {
      setStatus('Wird abgeschlossen (zweiter Durchlauf) …');
      btn.disabled = true;
      try { ws.send(JSON.stringify({ type: 'stop' })); } catch { }
      stopLocal();
    }

    function onMessage(m) {
      if (m.type === 'ready') { setStatus('Hört zu …'); }
      else if (m.type === 'status') {
        if (m.state === 'transcribing' && m.pending > 3) setStatus(`Transkribiert … (${m.pending} Sätze warten – Server ausgelastet)`);
        else if (m.state === 'transcribing') setStatus(`Transkribiert … (${m.pending || 0} wartend)`);
        else if (m.state === 'listening') setStatus('Hört zu …');
        else if (m.state === 'finalizing') setStatus('Zweiter Durchlauf über die ganze Aufnahme …');
      }
      else if (m.type === 'segment') {
        segCount++; sumMs += m.compute_ms || 0;
        const d = document.createElement('div'); d.className = 'seg';
        d.innerHTML = `<span class="t">${fmt(m.start)}</span><span>${esc(m.text)}</span><span class="ms" title="Rechenzeit für dieses Segment">${m.compute_ms} ms</span>`;
        live.appendChild(d); live.scrollTop = live.scrollHeight;
      }
      else if (m.type === 'final') {
        btn.disabled = !consent.checked;
        setStatus(m.refining ? 'Gespeichert – Verfeinerung mit dem genauen Modell läuft im Hintergrund.' : 'Fertig.');
        const rtf = m.duration ? (m.processing_seconds / m.duration) : 0;
        $('#rec-final-info').textContent = `${fmt(m.duration)} Audio, Live-Rechenzeit ${m.processing_seconds.toFixed(1)} s (${rtf < 1 ? (1 / Math.max(rtf, 0.001)).toFixed(0) + '× schneller als Echtzeit' : 'RTF ' + rtf.toFixed(2)}), ${m.segments.length} Segmente.${m.refining ? ' Das Transkript wird gerade mit dem genauen Modell verfeinert und aktualisiert sich automatisch.' : ''}`;
        $('#rec-final-link').href = `#/t/${m.transcript_id}`;
        $('#rec-final').classList.remove('hidden');
        try { ws.close(); } catch { }
      }
      else if (m.type === 'warning') toast(m.message, true);
      else if (m.type === 'error') { toast(m.message, true); }
    }

    btn.addEventListener('click', () => (recording ? stop() : start()));
    return () => { if (recording) stop(); };
  }

  // ================================================================ Hochladen
  async function renderHochladen() {
    view.appendChild(tpl('tpl-hochladen'));
    const drop = $('#drop'), input = $('#file'), jobs = $('#up-jobs');
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
        row.innerHTML = `<span class="ttl">${esc(f.name)}</span><div class="bar"><i></i></div><span class="badge">lädt hoch …</span>`;
        jobs.prepend(row);
        try {
          const r = await fetch('/api/upload', { method: 'POST', body: fd });
          if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
          const j = await r.json();
          running.set(j.id, row); row.querySelector('.badge').textContent = 'in Warteschlange';
        } catch (e) { row.querySelector('.badge').textContent = 'Fehler: ' + e.message; row.querySelector('.badge').classList.add('danger'); }
      }
    }
    const poll = setInterval(async () => {
      if (!running.size) return;
      let list; try { list = await api('/api/transcripts'); } catch { return; }
      for (const [id, row] of running) {
        const t = list.find((x) => x.id === id); if (!t) continue;
        const b = row.querySelector('.badge'), bar = row.querySelector('.bar i');
        if (t.status === 'done') {
          bar.style.width = '100%'; b.className = 'badge ok'; b.innerHTML = `fertig · <a href="#/t/${id}">öffnen</a>`; running.delete(id);
        } else if (t.status === 'error') { b.className = 'badge danger'; b.textContent = 'Fehler: ' + t.error; running.delete(id); }
        else if (t.progress) { bar.style.width = Math.round(100 * t.progress.done / Math.max(1, t.progress.total)) + '%'; b.textContent = `transkribiert … ${t.progress.done}/${t.progress.total}`; }
        else b.textContent = 'wartet …';
      }
    }, 1000);
    return () => clearInterval(poll);
  }

  // ================================================================ Liste
  async function renderListe() {
    view.appendChild(tpl('tpl-transkripte'));
    const list = $('#tl-list'), search = $('#tl-search');
    let items = [];
    const draw = () => {
      const q = search.value.trim().toLowerCase();
      const rows = items.filter((t) => !q || t.title.toLowerCase().includes(q));
      if (!rows.length) { list.innerHTML = '<div class="empty-state">Noch keine Transkripte.</div>'; return; }
      list.innerHTML = rows.map((t) => {
        const st = t.status === 'done' ? '<span class="badge ok">fertig</span>' : t.status === 'error' ? `<span class="badge danger" title="${esc(t.error)}">Fehler</span>` : t.status === 'refining' ? '<span class="badge blue">wird verfeinert</span>' : '<span class="badge warn">in Arbeit</span>';
        const rtf = t.duration && t.processing_seconds ? ` · ${(t.duration / t.processing_seconds).toFixed(0)}× Echtzeit` : '';
        return `<a class="item" href="#/t/${t.id}"><span class="ttl">${esc(t.title)}</span>${st}<span class="sub">${fmtDate(t.created_at)} · ${fmt(t.duration)} · ${t.source === 'live' ? 'Live' : 'Upload'} · ${esc(t.model)}${rtf}</span><span class="sub">${t.has_audio ? 'mit Audio' : 'ohne Audio'}</span></a>`;
      }).join('');
    };
    const load = async () => { items = await api('/api/transcripts'); draw(); };
    search.addEventListener('input', draw);
    await load();
    const poll = setInterval(() => { if (items.some((t) => t.status === 'processing' || t.status === 'refining')) load().catch(() => { }); }, 3000);
    return () => clearInterval(poll);
  }

  // ================================================================ Glossar
  async function renderGlossar() {
    view.appendChild(tpl('tpl-glossar'));
    const body = $('#gl-body');
    const rowHtml = (e = {}) => `<tr><td><input value="${esc(e.von)}" placeholder="z. B. Heilbrunn"></td><td><input value="${esc(e.zu)}" placeholder="z. B. Heilbronn"></td><td><button class="btn small" data-del>✕</button></td></tr>`;
    const entries = await api('/api/glossar');
    body.innerHTML = entries.map(rowHtml).join('') || rowHtml();
    body.addEventListener('click', (e) => { if (e.target.matches('[data-del]')) e.target.closest('tr').remove(); });
    $('#gl-add').addEventListener('click', () => body.insertAdjacentHTML('beforeend', rowHtml()));
    $('#gl-save').addEventListener('click', async () => {
      const eintraege = [...body.querySelectorAll('tr')].map((tr) => { const [a, b] = tr.querySelectorAll('input'); return { von: a.value, zu: b.value }; });
      const saved = await api('/api/glossar', { method: 'PUT', body: JSON.stringify({ eintraege }) });
      toast(`Glossar gespeichert (${saved.length} Einträge) – gilt für alle neuen Transkriptionen.`);
    });
  }

  // ================================================================ Detail
  async function renderDetail(id) {
    let t = await api(`/api/transcripts/${id}`);
    const sec = document.createElement('section'); sec.className = 'card'; view.appendChild(sec);
    let tab = 'transkript', bereinigtOn = false, editMd = false;

    const draw = () => {
      const rtf = t.duration && t.processing_seconds ? (t.duration / t.processing_seconds) : 0;
      sec.innerHTML = `
        <div class="detail-head">
          <h1><input class="title-edit" id="d-title" value="${esc(t.title)}" aria-label="Titel bearbeiten"></h1>
          <div class="toolbar">
            <a class="btn small" href="#/transkripte">← Liste</a>
            <button class="btn small danger" id="d-del">Löschen</button>
          </div>
        </div>
        <div class="meta">
          <span class="badge ${t.status === 'done' ? 'ok' : t.status === 'error' ? 'danger' : t.status === 'refining' ? 'blue' : 'warn'}">${t.status === 'done' ? 'fertig' : t.status === 'error' ? 'Fehler' : t.status === 'refining' ? 'wird verfeinert …' + (t.progress ? ` ${t.progress.done}/${t.progress.total}` : '') : 'in Arbeit' + (t.progress ? ` ${t.progress.done}/${t.progress.total}` : '')}</span>
          <span class="badge">${fmtDate(t.created_at)}</span>
          <span class="badge">${fmt(t.duration)} Audio</span>
          <span class="badge">${t.source === 'live' ? 'Live-Aufnahme' : 'Upload'}</span>
          <span class="badge blue" title="Spracherkennungsmodell">${esc(t.model)}</span>
          ${rtf ? `<span class="badge ok" title="Rechenzeit ${t.processing_seconds}s">${rtf.toFixed(0)}× schneller als Echtzeit</span>` : ''}
          ${t.error ? `<span class="badge danger">${esc(t.error)}</span>` : ''}
        </div>
        ${t.has_audio ? `<div class="player"><audio id="d-audio" controls preload="metadata" src="/api/transcripts/${t.id}/audio"></audio></div>` : '<p class="muted">Audio wurde gelöscht – Transkript bleibt erhalten.</p>'}
        <div class="tabs">
          <button data-tab="transkript" class="${tab === 'transkript' ? 'active' : ''}">Transkript</button>
          <button data-tab="protokoll" class="${tab === 'protokoll' ? 'active' : ''}">Protokoll ${t.protokoll ? (t.protokoll.status === 'bestaetigt' ? '✓' : '(Entwurf)') : ''}</button>
          <button data-tab="export" class="${tab === 'export' ? 'active' : ''}">Export</button>
        </div>
        <div id="d-body"></div>`;
      $('#d-title').addEventListener('change', async (e) => { await api(`/api/transcripts/${t.id}`, { method: 'PATCH', body: JSON.stringify({ title: e.target.value }) }); t.title = e.target.value; toast('Titel gespeichert'); });
      $('#d-del').addEventListener('click', async () => {
        if (!confirm('Transkript und Audio endgültig löschen?')) return;
        await api(`/api/transcripts/${t.id}`, { method: 'DELETE' }); toast('Gelöscht'); location.hash = '#/transkripte';
      });
      sec.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => { tab = b.dataset.tab; draw(); }));
      drawBody();
    };

    const audio = () => $('#d-audio');
    const seek = (sec_) => { const a = audio(); if (!a) return; a.currentTime = Math.max(0, sec_ - 0.2); a.play().catch(() => { }); };
    const highlightSeg = (idx) => {
      sec.querySelectorAll('.segrow').forEach((r) => r.classList.toggle('active', +r.dataset.idx === idx));
      const r = sec.querySelector(`.segrow[data-idx="${idx}"]`); if (r) r.scrollIntoView({ block: 'center', behavior: 'smooth' });
    };

    const drawBody = () => {
      const body = $('#d-body');
      if (tab === 'transkript') drawTranskript(body);
      else if (tab === 'protokoll') drawProtokoll(body);
      else drawExport(body);
    };

    // ---- Transkript-Editor
    async function drawTranskript(body) {
      let segs = t.segments;
      if (bereinigtOn) { const b = await api(`/api/transcripts/${t.id}/bereinigt`); segs = t.segments.map((s, i) => ({ ...s, text: b[i].text })); }
      body.innerHTML = `
        <div class="toolbar">
          <label class="btn small"><input type="checkbox" id="d-ber" ${bereinigtOn ? 'checked' : ''}> äh/ähm ausblenden (nur Anzeige)</label>
          <span class="muted">Text direkt anklicken und korrigieren · Sprechername anklicken zum Umbenennen · Zeit anklicken springt im Audio</span>
        </div>
        <div class="segs">${segs.map((s) => `
          <div class="segrow" data-idx="${s.idx}">
            <span class="t" data-seek="${s.start}">${fmt(s.start)}</span>
            <span class="spk ${s.speaker ? '' : 'empty'}" data-spk="${esc(s.speaker)}">${esc(s.speaker || 'Sprecher?')}</span>
            <div class="txt" contenteditable="${bereinigtOn ? 'false' : 'true'}" data-idx="${s.idx}">${esc(s.text)}</div>
          </div>`).join('') || '<div class="empty-state">Keine Segmente (noch in Arbeit oder Stille).</div>'}
        </div>`;
      $('#d-ber').addEventListener('change', (e) => { bereinigtOn = e.target.checked; drawBody(); });
      body.querySelectorAll('[data-seek]').forEach((el) => el.addEventListener('click', () => seek(+el.dataset.seek)));
      body.querySelectorAll('.txt').forEach((el) => {
        el.dataset.orig = el.textContent;
        el.addEventListener('paste', (e) => { e.preventDefault(); document.execCommand('insertText', false, (e.clipboardData || window.clipboardData).getData('text/plain')); });
        el.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); el.blur(); } });
        el.addEventListener('blur', async () => {
          const idx = +el.dataset.idx, text = el.textContent.trim();
          if (text === el.dataset.orig) return;
          await api(`/api/transcripts/${t.id}/segments/${idx}`, { method: 'PATCH', body: JSON.stringify({ text }) });
          t.segments.find((s) => s.idx === idx).text = text; el.dataset.orig = text; toast('Gespeichert');
        });
      });
      body.querySelectorAll('.spk').forEach((el) => el.addEventListener('click', async () => {
        const row = el.closest('.segrow'), idx = +row.dataset.idx, old = el.dataset.spk;
        const neu = prompt('Sprechername', old || ''); if (neu === null) return;
        const same = t.segments.filter((s) => s.speaker === old).length;
        if (old && same > 1 && confirm(`„${old}“ in allen ${same} Segmenten zu „${neu}“ umbenennen? (Abbrechen = nur dieses Segment)`)) {
          await api(`/api/transcripts/${t.id}/speakers/rename`, { method: 'POST', body: JSON.stringify({ von: old, zu: neu }) });
          t.segments.forEach((s) => { if (s.speaker === old) s.speaker = neu; });
        } else {
          await api(`/api/transcripts/${t.id}/segments/${idx}`, { method: 'PATCH', body: JSON.stringify({ speaker: neu }) });
          t.segments.find((s) => s.idx === idx).speaker = neu;
        }
        drawBody();
      }));
      // Mitlaufende Markierung beim Abspielen
      const a = audio();
      if (a) a.ontimeupdate = () => {
        const cur = a.currentTime; const s = t.segments.find((x) => cur >= x.start && cur < x.end);
        body.querySelectorAll('.segrow').forEach((r) => r.classList.toggle('active', !!s && +r.dataset.idx === s.idx));
      };
    }

    // ---- Protokoll
    function renderMd(zeilen) {
      return zeilen.map((z) => {
        const raw = z.text.trim(); if (!raw) return '';
        const m = raw.match(/^(#{1,4})\s+(.*)$/);
        if (m) return `<h${Math.min(4, m[1].length + 2)}>${esc(m[2])}</h${Math.min(4, m[1].length + 2)}>`;
        const li = /^[-*]\s+/.test(raw);
        let html = esc(li ? raw.replace(/^[-*]\s+/, '') : raw);
        html = html.replace(/\[\s*S\s*\d+(?:\s*[,;–-]\s*S?\s*\d+)*\s*\]/g, (ref) => {
          const nums = [...ref.matchAll(/\d+/g)].map((x) => +x[0]);
          return nums.map((n) => `<span class="ref ${t.segments.some((s) => s.idx === n) ? '' : 'bad'}" data-ref="${n}" title="Zur Stelle im Transkript">S${n}</span>`).join('');
        });
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        return `<div class="pl ${li ? 'li' : ''} ${z.status}">${html}</div>`;
      }).join('');
    }

    function drawProtokoll(body) {
      const p = t.protokoll;
      const styles = (health && health.styles) || { ergebnis: 'Ergebnisprotokoll', verlauf: 'Verlaufsprotokoll' };
      const styleSel = `<select id="p-style">${Object.entries(styles).map(([k, v]) => `<option value="${k}" ${p && p.style === k ? 'selected' : ''}>${v}</option>`).join('')}</select>`;
      const llm = health && health.llm_configured;
      body.innerHTML = `
        <div class="toolbar">
          <label class="field"><span>Protokollart</span>${styleSel}</label>
          <button class="btn" id="p-nova" title="Kopiert Anweisung + Transkript in die Zwischenablage – in NOVA einfügen">In NOVA öffnen (Prompt kopieren)</button>
          <button class="btn" id="p-import">Antwort aus NOVA einfügen &amp; prüfen</button>
          <button class="btn primary" id="p-ki" ${llm ? '' : 'disabled title="Keine Protokoll-KI konfiguriert (LLM_BASE_URL)"'}>Protokoll per KI erstellen${llm ? ' (' + esc(health.llm_model) + ')' : ''}</button>
        </div>
        <div id="p-out"></div>`;
      const out = $('#p-out');
      const styleOf = () => $('#p-style').value;

      $('#p-nova').addEventListener('click', async () => {
        const text = await api(`/api/transcripts/${t.id}/nova-prompt?style=${styleOf()}`);
        try { await navigator.clipboard.writeText(text); toast('Prompt mit Transkript kopiert – jetzt in NOVA einfügen. Die Antwort danach hier über „Antwort aus NOVA einfügen“ prüfen.'); }
        catch { out.innerHTML = `<p class="muted">Zwischenablage nicht verfügbar – Text manuell kopieren:</p><textarea class="md">${esc(text)}</textarea>`; }
      });
      $('#p-import').addEventListener('click', () => {
        out.innerHTML = `<p class="muted">Protokolltext von NOVA hier einfügen (mit den [S…]-Belegen):</p><textarea class="md" id="p-paste"></textarea><div class="toolbar"><button class="btn primary" id="p-paste-ok">Prüfen &amp; speichern</button></div>`;
        $('#p-paste-ok').addEventListener('click', async () => {
          try {
            t.protokoll = await api(`/api/transcripts/${t.id}/protokoll/import`, { method: 'POST', body: JSON.stringify({ style: styleOf(), protokoll_md: $('#p-paste').value }) });
            drawBody();
          } catch (e) { toast(e.message, true); }
        });
      });
      $('#p-ki').addEventListener('click', async () => {
        out.innerHTML = '<p class="muted">Die KI extrahiert zuerst belegte Bausteine, prüft die Zitate gegen das Transkript und schreibt dann das Protokoll … das kann 1–3 Minuten dauern.</p>';
        $('#p-ki').disabled = true;
        try { t.protokoll = await api(`/api/transcripts/${t.id}/protokoll`, { method: 'POST', body: JSON.stringify({ style: styleOf() }) }); drawBody(); }
        catch (e) { toast(e.message, true); $('#p-ki').disabled = false; out.innerHTML = ''; }
      });

      if (!p) { out.innerHTML = '<p class="muted">Noch kein Protokoll. Weg 1: Prompt kopieren, in NOVA einfügen, Antwort hier prüfen lassen. Weg 2: direkt per KI erstellen (wenn konfiguriert).</p>'; return; }
      const pr = p.content.pruefung || { zeilen: [], zeilen_inhalt: 0, unbelegt: 0, ungueltige_belege: 0, quote_belegt: 1 };
      const verworfen = p.content.bausteine_verworfen || 0;
      out.innerHTML = `
        <div class="stat">
          <div><strong>${Math.round(pr.quote_belegt * 100)} %</strong>Aussagen mit gültigem Beleg</div>
          <div><strong>${pr.unbelegt}</strong>ohne Beleg (gelb)</div>
          <div><strong>${pr.ungueltige_belege}</strong>ungültiger Beleg (rot)</div>
          ${p.content.bausteine ? `<div><strong>${verworfen}</strong>Bausteine verworfen (Zitat passte nicht)</div>` : ''}
          <div><strong>${p.status === 'bestaetigt' ? 'bestätigt' : 'Entwurf'}</strong>Status</div>
        </div>
        <div class="toolbar">
          <button class="btn small" id="p-edit">${editMd ? 'Vorschau' : 'Bearbeiten'}</button>
          <button class="btn small" id="p-confirm">${p.status === 'bestaetigt' ? 'Bestätigung aufheben' : 'Als geprüft bestätigen'}</button>
          <a class="btn small" href="/api/transcripts/${t.id}/export?format=docx&protokoll=1">Word mit Protokoll</a>
          <span class="muted">Klick auf S-Nummer springt zur Stelle im Audio/Transkript.</span>
        </div>
        ${editMd ? `<textarea class="md" id="p-md">${esc(p.content.protokoll_md)}</textarea><div class="toolbar"><button class="btn primary small" id="p-save">Speichern &amp; neu prüfen</button></div>` : `<div class="prot">${renderMd(pr.zeilen)}</div>`}
        ${p.content.bausteine ? `<details><summary class="muted">Bausteine der KI mit Zitatprüfung (${p.content.bausteine.length})</summary><div class="segs">${p.content.bausteine.map((b) => `<div class="segrow" style="grid-template-columns:70px 90px 1fr"><span class="badge ${b.beleg_ok ? 'ok' : 'danger'}">${b.beleg_ok ? 'ok' : 'verworfen'}</span><span class="badge">${esc(b.typ)}</span><div>${esc(b.text)}<div class="muted">„${esc(b.zitat)}“ · <span class="ref" data-ref="${b.segment}">S${b.segment}</span> · Übereinstimmung ${Math.round((b.zitat_score || 0) * 100)} %</div></div></div>`).join('')}</div></details>` : ''}`;
      $('#p-edit').addEventListener('click', () => { editMd = !editMd; drawBody(); });
      $('#p-confirm').addEventListener('click', async () => {
        t.protokoll = await api(`/api/transcripts/${t.id}/protokoll`, { method: 'PATCH', body: JSON.stringify({ status: p.status === 'bestaetigt' ? 'entwurf' : 'bestaetigt' }) }); drawBody();
      });
      const save = $('#p-save'); if (save) save.addEventListener('click', async () => {
        t.protokoll = await api(`/api/transcripts/${t.id}/protokoll`, { method: 'PATCH', body: JSON.stringify({ protokoll_md: $('#p-md').value }) }); editMd = false; drawBody();
      });
      out.querySelectorAll('[data-ref]').forEach((el) => el.addEventListener('click', () => {
        const n = +el.dataset.ref, s = t.segments.find((x) => x.idx === n); if (!s) { toast('Segment S' + n + ' gibt es nicht', true); return; }
        seek(s.start); tab = 'transkript'; draw(); setTimeout(() => highlightSeg(n), 50);
      }));
    }

    // ---- Export
    function drawExport(body) {
      const u = `/api/transcripts/${t.id}/export?format=`;
      body.innerHTML = `
        <div class="toolbar">
          <a class="btn" href="${u}docx">Word (.docx)</a>
          <a class="btn" href="${u}docx&protokoll=1">Word inkl. Protokoll</a>
          <a class="btn" href="${u}txt">Text (.txt)</a>
          <a class="btn" href="${u}txt&zeit=false&sprecher=false">Nur Fließtext</a>
          <a class="btn" href="${u}md">Markdown</a>
          <a class="btn" href="${u}srt">Untertitel (.srt)</a>
        </div>
        <h2>Datenschutz</h2>
        <p class="muted">Alles liegt ausschließlich auf diesem Server. Audio kann nach Fertigstellung des Protokolls gelöscht werden – das Transkript bleibt.</p>
        <div class="toolbar">
          <button class="btn danger" id="x-delaudio" ${t.has_audio ? '' : 'disabled'}>Nur Audio löschen</button>
        </div>`;
      $('#x-delaudio').addEventListener('click', async () => {
        if (!confirm('Audio dieser Aufnahme endgültig löschen? Das Transkript bleibt erhalten.')) return;
        await api(`/api/transcripts/${t.id}/audio`, { method: 'DELETE' }); t.has_audio = false; toast('Audio gelöscht'); draw();
      });
    }

    draw();
    // Bei laufender Verarbeitung nachladen
    const poll = setInterval(async () => {
      if (t.status !== 'processing' && t.status !== 'refining') return;
      if (document.activeElement && document.activeElement.isContentEditable) return; // nicht beim Tippen neu zeichnen
      try {
        const n = await api(`/api/transcripts/${id}`);
        const changed = n.status !== t.status || JSON.stringify(n.progress) !== JSON.stringify(t.progress);
        t = n; if (changed) { draw(); if (n.status === 'done') toast('Transkript fertig verfeinert'); }
      } catch { }
    }, 2000);
    return () => { clearInterval(poll); };
  }

  // ---------------------------------------------------------------- Start
  loadHealth().then(route);
  setInterval(loadHealth, 30000);
  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/sw.js').catch(() => { });
})();
