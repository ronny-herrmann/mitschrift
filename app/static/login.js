// Anmeldung (eigene Datei statt Inline-Skript, damit die Content-Security-Policy Inline-Skripte verbieten kann)
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: document.getElementById('pw').value }) });
  if (r.ok) { location.href = '/'; return; }
  let msg = 'Anmeldung fehlgeschlagen'; try { msg = (await r.json()).detail || msg; } catch {}
  document.getElementById('err').textContent = msg;
});
