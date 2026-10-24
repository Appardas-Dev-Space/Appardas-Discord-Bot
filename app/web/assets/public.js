/* Public enhancement: content and search forms remain usable without script. */
(() => {
  const all = (selector, root = document) => [...root.querySelectorAll(selector)];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const announce = text => {
    const node = document.querySelector('[data-copy-status]');
    if (node) node.textContent = text;
  };
  all('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(button.dataset.copy); announce('Copied.'); }
    catch { announce('Select the command text to copy it.'); }
  }));
  all('[data-command-search]').forEach(form => {
    const input = form.querySelector('[name=q]');
    const scope = form.closest('[data-command-scope]');
    const rows = all('[data-search]', scope);
    const searchable = row => `${row.id} ${row.querySelector('h3, a')?.textContent || ''} ${row.querySelector('[data-search-description]')?.textContent || ''}`.toLowerCase();
    const count = scope.querySelector('[data-search-count]');
    const empty = scope.querySelector('[data-search-empty]');
    const filter = () => {
      const query = input.value.trim().toLowerCase();
      let matches = 0;
      rows.forEach((row, index) => {
        const match = searchable(row).includes(query);
        const show = match && (query || !scope.hasAttribute('data-preview') || index < 5);
        row.hidden = !show;
        if (show && !reduced && query) row.animate([{opacity: .4}, {opacity: 1}], {duration: 120});
        if (match) matches++;
      });
      all('[data-command-group]', scope).forEach(group => { group.hidden = !group.querySelector('[data-search]:not([hidden])'); });
      if (count) count.textContent = `${matches} command${matches === 1 ? '' : 's'}${!query && scope.hasAttribute('data-preview') ? ' · five suggestions' : ''}`;
      if (empty) { empty.hidden = matches > 0; empty.textContent = `No command matches “${input.value}”. Try status, settings, github, dev or utility.`; }
    };
    input.addEventListener('input', filter);
    form.addEventListener('submit', event => { event.preventDefault(); filter(); });
    all('[data-query]', scope).forEach(button => button.addEventListener('click', () => { input.value = button.dataset.query; filter(); input.focus(); }));
    filter();
  });
  if (!reduced && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.animate([{opacity: .25, transform: 'translateY(16px)'}, {opacity: 1, transform: 'translateY(0)'}], {duration: 440, easing: 'ease-out'});
      observer.unobserve(entry.target);
    }), {threshold: .12});
    all('[data-reveal]').forEach(node => observer.observe(node));
    const strip = document.querySelector('.hero-media');
    if (strip) strip.animate([{opacity: .2, transform: 'scaleX(.97)'}, {opacity: 1, transform: 'scaleX(1)'}], {duration: 400});
  }
  const live = document.querySelector('[data-live-status]');
  if (!live) return;
  let checked = live.dataset.checkedAt;
  const freshness = live.querySelector('[data-freshness]');
  const fresh = () => {
    const stale = !checked || Date.now() - Date.parse(checked) > 90000;
    live.classList.toggle('stale', stale);
    if (freshness) freshness.textContent = `${stale ? 'Last reading' : 'Checked'} ${checked ? new Date(checked).toISOString().slice(11, 16) + ' UTC' : 'unavailable'}.${stale ? ' Retrying.' : ''}`;
  };
  const text = (key, value) => all(`[data-reading="${key}"]`).forEach(node => { node.textContent = value; });
  const number = (value, suffix, digits = 0) => typeof value === 'number' ? `${value.toFixed(digits)} ${suffix}` : '—';
  const update = async () => {
    try {
      const response = await fetch('/status', {credentials: 'omit', headers: {Accept: 'application/json'}});
      if (response.status !== 200 && response.status !== 503) throw new Error('status unavailable');
      const data = await response.json();
      if (!data.checked_at || !data.heartbeat || !data.discord || !data.runtime) throw new Error('invalid status');
      checked = data.checked_at;
      text('gateway', number(data.discord.gateway_latency_ms, 'ms'));
      text('lag', number(data.runtime.loop_lag_ms, 'ms', 1));
      text('rss', number(data.runtime.mem_rss_mb, 'MB'));
      text('uptime', `${Math.floor(data.uptime_s / 3600)}h ${Math.floor(data.uptime_s % 3600 / 60)}m`);
      text('database', data.db.healthy ? 'healthy' : 'unhealthy');
      text('connection', data.discord.state === 'connected' ? 'Connected to Discord' : `${data.discord.state} to Discord`);
      all('[data-verdict]').forEach(node => { node.textContent = data.status; node.className = `public-verdict ${data.status}`; });
      for (const cog of data.cogs) text(`cog-${cog.key}`, {active: 'active', faulted: 'unavailable', loading: 'starting'}[cog.state]);
      text('loaded', `${data.cogs.filter(cog => cog.state === 'active').length} / ${data.cogs.length} loaded`);
      all('[data-heartbeat]').forEach(svg => {
        if (svg.dataset.ticks === data.heartbeat.ticks) return;
        svg.dataset.ticks = data.heartbeat.ticks;
        svg.setAttribute('aria-label', data.heartbeat.summary);
        all('rect', svg).forEach((rect, index) => {
          const tick = data.heartbeat.ticks[index] || '-';
          rect.setAttribute('class', `b${tick}`);
          rect.setAttribute('y', tick === '-' ? '38' : tick === 'l' ? '20' : '0');
          rect.setAttribute('height', tick === '-' ? '2' : tick === 'l' ? '20' : '40');
        });
      });
      all('[data-heartbeat-summary]').forEach(node => { node.textContent = data.heartbeat.summary; });
      const ending = document.querySelector('[data-hero-ending]');
      if (ending) ending.textContent = data.heartbeat.summary.includes('no gaps') ? 'no gaps.' : data.heartbeat.summary.includes('no readings') ? 'checking in.' : 'gaps recorded.';
    } catch { /* Keep the last reading and show its age. */ }
    fresh();
  };
  fresh();
  setInterval(update, 30000);
  setInterval(fresh, 5000);
})();
