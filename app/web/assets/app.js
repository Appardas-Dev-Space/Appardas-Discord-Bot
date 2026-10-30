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
  const updateDeletionConfirmation = form => {
    if (!form.matches('[data-delete-confirmation]')) return;
    const input = form.elements.confirmation;
    const characters = form.querySelector('[data-confirmation-characters]').textContent;
    const matches = !!characters && !!form.elements.confirmation_token.value && input.value === characters;
    input.setCustomValidity(input.value && !matches ? 'Enter the confirmation characters exactly as shown.' : '');
    form.querySelector('[data-confirm-delete]').disabled = !matches || input.disabled || form.getAttribute('aria-busy') === 'true';
  };
  all('[data-delete-confirmation]').forEach(form => {
    form.elements.confirmation.addEventListener('input', () => updateDeletionConfirmation(form));
    updateDeletionConfirmation(form);
  });
  all('.account-delete-dialog').forEach(dialog => {
    if (typeof dialog.showModal !== 'function') return; // The POST confirmation page is the fallback.
    const confirmation = dialog.querySelector('[data-delete-confirmation]');
    const input = confirmation.elements.confirmation;
    const status = confirmation.querySelector('[data-form-status]');
    let controller = null, opener = null;
    const reset = () => {
      input.value = '';
      input.disabled = true;
      confirmation.action = '';
      confirmation.elements.confirmation_token.value = '';
      confirmation.querySelector('[data-delete-account-name]').textContent = 'this account';
      confirmation.querySelector('[data-confirmation-characters]').textContent = '';
      status.classList.remove('error');
      status.textContent = '';
      updateDeletionConfirmation(confirmation);
    };
    const abortChallenge = () => {
      controller?.abort();
      controller = null;
      dialog.removeAttribute('aria-busy');
      if (opener) opener.disabled = false;
    };
    dialog.querySelector('[data-delete-cancel]').addEventListener('click', () => {
      abortChallenge();
      dialog.close();
    });
    dialog.addEventListener('cancel', event => {
      if (confirmation.getAttribute('aria-busy') === 'true') event.preventDefault();
      else abortChallenge();
    });
    dialog.addEventListener('close', () => {
      if (dialog.open) return; // A queued close from an older opening must not clear a new challenge.
      abortChallenge();
      reset();
      if (opener) opener.focus();
    });
    all(`[data-delete-account="${dialog.id}"]`).forEach(launch => launch.addEventListener('submit', async event => {
      event.preventDefault();
      if (dialog.open) return;
      opener = launch.querySelector('button[type=submit]');
      reset();
      confirmation.querySelector('[data-delete-account-name]').textContent = launch.dataset.accountName;
      dialog.showModal();
      dialog.setAttribute('aria-busy', 'true');
      opener.disabled = true;
      status.textContent = 'Generating confirmation characters…';
      const request = new AbortController();
      controller = request;
      try {
        const body = new URLSearchParams(new FormData(launch));
        const response = await fetch(launch.action, {method: 'POST', body, credentials: 'same-origin', signal: request.signal, headers: {Accept: 'application/json', 'X-CSRF-Token': body.get('csrf')}});
        const data = await response.json();
        if (response.status === 401) { location.assign(`/login?next=${encodeURIComponent(location.pathname)}`); return; }
        if (!response.ok) throw new Error(data.message || 'Could not generate confirmation characters. Cancel and try again.');
        if (controller !== request || !dialog.open) return;
        confirmation.action = `/dashboard/admin/accounts/${data.account_id}/delete`;
        confirmation.elements.confirmation_token.value = data.confirmation_token;
        confirmation.querySelector('[data-delete-account-name]').textContent = data.account_name;
        confirmation.querySelector('[data-confirmation-characters]').textContent = data.characters;
        input.pattern = data.characters;
        input.disabled = false;
        status.textContent = '';
        updateDeletionConfirmation(confirmation);
        input.focus();
      } catch (error) {
        if (error.name !== 'AbortError' && controller === request && dialog.open) {
          status.classList.add('error');
          status.textContent = error.message || 'Could not generate confirmation characters. Cancel and try again.';
        }
      } finally {
        if (controller === request) {
          controller = null;
          dialog.removeAttribute('aria-busy');
          opener.disabled = false;
        }
      }
    }));
  });
  all('[data-enhance]').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault();
    const body = new URLSearchParams(new FormData(form));
    if (event.submitter?.name) body.set(event.submitter.name, event.submitter.value);
    const buttons = all('button', form);
    const status = form.querySelector('[data-form-status]') || notice;
    if (status) status.textContent = form.matches('[data-delete-confirmation]') ? 'Deleting account…' : 'Saving…';
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
      updateDeletionConfirmation(form);
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
