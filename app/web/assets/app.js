/* Cookie-authenticated enhancement. Normal POST/303 remains the baseline. */
(() => {
  const all = (selector, root = document) => [...root.querySelectorAll(selector)];
  let dirty = false;
  document.addEventListener('input', event => { if (event.target.closest('form[method=post]')) dirty = true; });
  all('[data-prefix]').forEach(input => input.addEventListener('input', () => {
    input.closest('form').querySelector('[data-prefix-preview]').textContent = input.value ? `${input.value}help` : '@appardas help';
  }));
  all('[data-filter-list]').forEach(input => input.addEventListener('input', () => {
    const list = document.getElementById(input.dataset.filterList);
    all('[data-filter-text]', list).forEach(row => { row.hidden = !row.dataset.filterText.toLowerCase().includes(input.value.toLowerCase()); });
  }));
  const notice = document.querySelector('[data-live-notice]');
  const say = text => { if (notice) notice.textContent = text; };
  all('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(button.dataset.copy); say('Copied.'); }
    catch { say('Select the ID to copy it.'); }
  }));
  all('[data-enhance]').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault();
    const body = new URLSearchParams(new FormData(form));
    if (event.submitter?.name) body.set(event.submitter.name, event.submitter.value);
    const buttons = all('button', form);
    const status = form.querySelector('[data-form-status]') || notice;
    if (status) status.textContent = 'Saving…';
    form.setAttribute('aria-busy', 'true');
    buttons.forEach(button => { button.disabled = true; });
    try {
      const response = await fetch(form.action, {method: 'POST', body, credentials: 'same-origin', headers: {Accept: 'application/json', 'X-CSRF-Token': body.get('csrf')}});
      const data = await response.json();
      if (response.status === 401) { location.assign(`/login?next=${encodeURIComponent(location.pathname)}`); return; }
      if (!response.ok) throw new Error(data.message || 'The change was not saved.');
      if (data.redirect) { location.assign(data.redirect); return; }
      dirty = false;
      if (status) { status.classList.remove('error'); status.textContent = data.message || 'Saved.'; }
      if (data.cog) {
        const row = form.closest('[data-cog-row]');
        row.querySelector('[data-cog-state]').textContent = data.state;
        const control = row.querySelector('.switch');
        control.classList.toggle('on', data.enabled);
        control.setAttribute('aria-label', `Turn ${data.cog} ${data.enabled ? 'off' : 'on'}`);
        form.querySelector('[name=enabled]').value = data.enabled ? '0' : '1';
        form.querySelector('button[type=submit]').textContent = `Turn ${data.enabled ? 'off' : 'on'}`;
        const details = form.closest('details');
        if (details) details.open = false;
      }
      if (data.key && 'value' in data) {
        const field = form.querySelector('[name=value]');
        if (field) field.value = data.value ?? '';
        const preview = form.querySelector('[data-prefix-preview]');
        if (preview) preview.textContent = data.value ? `${data.value}help` : '@appardas help';
      }
    } catch (error) {
      if (status) { status.classList.add('error'); status.textContent = error.message || 'The change was not saved. Try again.'; }
    } finally {
      form.removeAttribute('aria-busy');
      buttons.forEach(button => { button.disabled = false; });
    }
  }));
  // Activity is a stable document. Guild screens receive committed changes.
  if (!document.body.dataset.liveGuild || !window.EventSource) return;
  const events = new EventSource('/dashboard/events');
  const refresh = async event => {
    let data;
    try { data = JSON.parse(event.data); } catch { return; }
    if (data.guild_id && String(data.guild_id) !== document.body.dataset.liveGuild) return;
    if (dirty || document.querySelector('form[aria-busy=true]')) { say('This server changed in another session. Reload when you have finished editing.'); return; }
    try {
      const response = await fetch(location.pathname, {credentials: 'same-origin', headers: {Accept: 'text/html'}});
      if (response.redirected) { location.assign(response.url); return; }
      if (response.status === 404) { location.assign('/dashboard'); return; }
      if (!response.ok) throw new Error('refresh');
      const next = new DOMParser().parseFromString(await response.text(), 'text/html');
      all('[data-cog-row]').forEach(row => {
        const current = next.querySelector(`[data-cog-row="${row.dataset.cogRow}"]`);
        if (!current) return;
        row.querySelector('[data-cog-state]').textContent = current.querySelector('[data-cog-state]').textContent;
        const input = row.querySelector('[name=enabled]');
        const nextInput = current.querySelector('[name=enabled]');
        if (input && nextInput) {
          input.value = nextInput.value;
          row.querySelector('.switch').className = current.querySelector('.switch').className;
          row.querySelector('.switch').setAttribute('aria-label', current.querySelector('.switch').getAttribute('aria-label'));
          row.querySelector('button[type=submit]').textContent = current.querySelector('button[type=submit]').textContent;
        }
      });
      all('[data-live-fragment]').forEach(node => {
        const updated = next.querySelector(`[data-live-fragment="${node.dataset.liveFragment}"]`);
        if (updated) node.replaceChildren(...updated.childNodes);
      });
      all('form[data-setting]').forEach(form => {
        const updated = next.querySelector(`form[data-setting="${form.dataset.setting}"]`);
        if (!updated) return;
        const field = form.querySelector('[name=value]');
        if (field) field.value = updated.querySelector('[name=value]').value;
      });
      say('Latest server state loaded.');
    } catch { say('Could not refresh. Reload to see the latest server state.'); }
  };
  ['cog.toggle', 'config.set', 'heartbeat'].forEach(type => events.addEventListener(type, refresh));
  events.addEventListener('auth', () => { events.close(); location.assign(`/login?next=${encodeURIComponent(location.pathname)}`); });
  events.onerror = () => { say('Live updates are reconnecting. You can still refresh this page.'); };
  window.addEventListener('pagehide', () => events.close(), {once: true});
})();
