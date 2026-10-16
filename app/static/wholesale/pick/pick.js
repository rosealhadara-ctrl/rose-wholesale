// روز: the prep employee's phone screen (/pick/).
(function () {
  'use strict';

  const view = document.getElementById('view');
  const state = { me: null, tab: 'queue', timer: null };
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const int = (v) => Number(v || 0).toLocaleString('ar-EG-u-nu-latn');
  const clock = (s) => { s = Math.max(0, Math.floor(s || 0)); const m = Math.floor(s / 60); return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };
  const ago = (s) => (s < 3600 ? `${int(Math.max(1, Math.round(s / 60)))} دقيقة` : `${int(Math.round(s / 3600))} ساعة`);

  async function api(method, url, body) {
    const res = await fetch(url, { method, credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (res.status === 401 && !url.endsWith('/login')) { state.me = null; renderLogin(); throw new Error('سجّل دخول الأول'); }
    if (!res.ok) throw new Error(data && typeof data.detail === 'string' ? data.detail : 'حصلت مشكلة. جرّب تاني.');
    return data;
  }
  let toastTimer;
  function toast(msg, error) {
    const el = document.getElementById('toast');
    el.textContent = msg; el.className = 'toast' + (error ? ' error' : ''); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, error ? 5000 : 2500);
  }
  async function run(btn, fn) {
    if (btn) btn.disabled = true;
    try { return await fn(); } catch (e) { toast(e.message, true); return undefined; } finally { if (btn) btn.disabled = false; }
  }
  function stopTimer() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

  // ----------------------------------------------------------------- shell
  document.querySelectorAll('#tabs button').forEach((b) => (b.onclick = () => { location.hash = '#' + b.dataset.tab; }));
  document.getElementById('logout').onclick = () => run(null, async () => { await api('POST', '/api/pick/logout'); state.me = null; renderLogin(); });
  window.addEventListener('hashchange', route);

  async function route() {
    stopTimer();
    window.scrollTo(0, 0);
    if (!state.me) {
      try { state.me = await api('GET', '/api/pick/me'); } catch (e) { view.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; }
    }
    if (!state.me.logged_in) { renderLogin(); return; }
    document.getElementById('who').textContent = state.me.name;
    document.getElementById('logout').hidden = false;
    const role = state.me.role || 'picker';
    const allowed = { picker: ['queue', 'receiving', 'returns'], driver: ['trips'], both: ['queue', 'receiving', 'returns', 'trips'] }[role];
    let [name, id] = (location.hash.slice(1) || allowed[0]).split('/');
    if (!['order', 'trip'].includes(name) && !allowed.includes(name)) name = allowed[0];
    const tabs = document.getElementById('tabs');
    tabs.querySelectorAll('button').forEach((b) => { b.hidden = !allowed.includes(b.dataset.tab); b.setAttribute('aria-pressed', String(b.dataset.tab === name)); });
    tabs.hidden = ['order', 'trip'].includes(name) || allowed.length < 2;
    if (name === 'order') return renderOrder(Number(id));
    if (name === 'trip') return renderTrip(Number(id));
    if (name === 'trips') return renderTrips();
    if (name === 'receiving') return renderReceiving();
    if (name === 'returns') return renderReturns();
    return renderQueue();
  }

  function renderLogin() {
    stopTimer();
    document.getElementById('tabs').hidden = true;
    document.getElementById('logout').hidden = true;
    document.getElementById('who').textContent = '';
    view.innerHTML = `
      <div class="gate card stack">
        <h2>دخول موظف التجهيز</h2>
        <label class="field">رقم الموبايل<input type="tel" id="l-phone" inputmode="tel" autocomplete="tel" dir="ltr"></label>
        <label class="field">الكود السري (4 أرقام)<input type="password" id="l-pin" class="pin" inputmode="numeric" maxlength="4" autocomplete="one-time-code"></label>
        <button class="btn primary block" id="l-go">دخول</button>
        <p class="small muted" style="margin:0">الكود بيوصلك من الإدارة على واتساب. لو نسيته اطلب كود جديد.</p>
      </div>`;
    const go = () => run(document.getElementById('l-go'), async () => {
      await api('POST', '/api/pick/login', { phone: document.getElementById('l-phone').value.trim(), pin: document.getElementById('l-pin').value.trim() });
      state.me = null;
      route();
    });
    document.getElementById('l-go').onclick = go;
    document.getElementById('l-pin').addEventListener('input', (e) => { if (e.target.value.length === 4) go(); });
  }

  // ----------------------------------------------------------------- queue
  async function renderQueue() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const data = await run(null, () => api('GET', '/api/pick/queue'));
    if (!data) return;
    const s = data.stats || {};
    view.innerHTML = `
      <div class="stat-row">
        <div class="stat-box"><b class="num">${int(s.today)}</b><span>جهّزت النهارده</span></div>
        <div class="stat-box"><b class="num">${s.avg_seconds != null ? clock(s.avg_seconds) : '—'}</b><span>متوسط وقتك</span></div>
        <div class="stat-box"><b class="num">${int(data.orders.length)}</b><span>فواتير مستنية</span></div>
      </div>
      ${data.orders.length ? data.orders.map((o) => `
        <a class="job${o.mine ? ' mine' : ''}${o.locked_by ? ' locked' : ''}" ${o.locked_by ? '' : `href="#order/${o.id}"`} style="text-decoration:none;color:inherit">
          <div class="job-head"><b class="num">طلب ${o.number}</b>
            ${o.mine ? '<span class="tag me">معاك دلوقتي</span>' : ''}
            ${o.priority ? '<span class="tag prio">أولوية</span>' : ''}
            ${o.has_notes ? '<span class="tag note">فيها ملاحظات</span>' : ''}
            ${o.locked_by ? `<span class="tag lock">${esc(o.locked_by)} بيجهّزها</span>` : ''}</div>
          <div>${esc(o.shop)}${o.area ? ` · <span class="muted">${esc(o.area)}</span>` : ''}</div>
          <div class="small muted">${int(o.items)} صنف · مستنية من ${ago(o.waiting_seconds)}</div>
        </a>`).join('') : '<div class="empty"><b>مفيش فواتير مستنية</b>لما البضاعة توصل من الموردين، الفواتير هتظهر هنا.</div>'}
      <div class="row" style="margin-top:14px"><button class="btn block" id="refresh">تحديث</button></div>`;
    document.getElementById('refresh').onclick = renderQueue;
  }

  // ----------------------------------------------------------------- order
  async function renderOrder(id) {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const o = await run(null, () => api('GET', `/api/pick/orders/${id}`));
    if (!o) { view.innerHTML = '<div class="empty"><b>الطلب مش موجود</b><a class="btn" href="#queue">رجوع</a></div>'; return; }
    drawOrder(o);
  }

  function drawOrder(o) {
    stopTimer();
    if (o.status === 'received' || o.locked_by) {
      view.innerHTML = `
        <div class="page-title"><h2 class="num">طلب ${o.number}</h2><a class="btn ghost sm" href="#queue">رجوع</a></div>
        <section class="card stack">
          <div><b>${esc(o.shop)}</b>${o.area ? ` · <span class="muted">${esc(o.area)}</span>` : ''}</div>
          <div class="muted">${int(o.items.length)} صنف${o.items.some((i) => i.note) ? ' · فيها ملاحظات' : ''}</div>
          ${o.customer_note ? `<div class="pick-note">ملاحظة الطلب: ${esc(o.customer_note)}</div>` : ''}
          ${o.locked_by ? `<div class="alert warn">${esc(o.locked_by)} بيجهّز الطلب ده.</div>` : '<button class="btn primary block" id="start" style="min-height:60px;font-size:18px">ابدأ التجهيز</button><p class="small muted" style="margin:0">العداد هيبدأ أول ما تدوس، والطلب هيتقفل عليك.</p>'}
        </section>`;
      const start = document.getElementById('start');
      if (start) start.onclick = () => run(start, async () => drawOrder(await api('POST', `/api/pick/orders/${o.id}/start`)));
      return;
    }
    if (o.status === 'ready') { location.hash = '#queue'; return; }
    const left = o.items.filter((i) => i.pick_status === 'pending').length;
    view.innerHTML = `
      <div class="timer-bar"><span class="timer" id="timer">${clock(o.elapsed)}</span><div class="meta"><b class="num">طلب ${o.number}</b>${esc(o.shop)}</div></div>
      ${o.customer_note ? `<div class="pick-note" style="margin-bottom:10px">ملاحظة الطلب: ${esc(o.customer_note)}</div>` : ''}
      ${o.items.map((i) => `
        <div class="pick-item ${i.pick_status === 'pending' ? '' : i.pick_status}" data-id="${i.id}">
          <div class="pick-name">${esc(i.name)}</div>
          <div class="pick-qty num">${int(i.qty)} <small>${esc(i.unit_label)}${i.pieces && i.unit_label !== 'قطعة' ? ` (${int(i.pieces)} قطعة)` : ''}</small></div>
          ${i.note ? `<div class="pick-note">ملاحظة: ${esc(i.note)}</div>` : ''}
          <div class="pick-actions">
            <button class="btn${i.pick_status === 'ready' ? ' on-ready' : ''}" data-st="ready">جاهز</button>
            <button class="btn${i.pick_status === 'short' ? ' on-short' : ''}" data-st="short">${i.pick_status === 'short' ? `أقل (${int(i.picked_qty)})` : 'عدد أقل'}</button>
            <button class="btn${i.pick_status === 'missing' ? ' on-missing' : ''}" data-st="missing">مش موجود</button>
          </div>
          <div class="short-row" hidden><span>الموجود:</span><input type="number" inputmode="numeric" min="1" max="${i.qty - 1}"><button class="btn sm primary" data-short-ok>تأكيد</button><button class="btn sm ghost" data-short-cancel>إلغاء</button></div>
        </div>`).join('')}
      <div class="finish-bar"><small>${left ? `فاضل ${int(left)} صنف` : 'كل الأصناف اتعلّمت'}</small><button class="btn primary" id="finish" ${left ? 'disabled' : ''}>خلّصت التجهيز</button></div>`;
    const started = Date.now() - (o.elapsed || 0) * 1000;
    const timer = document.getElementById('timer');
    state.timer = setInterval(() => { timer.textContent = clock((Date.now() - started) / 1000); }, 1000);
    view.querySelectorAll('.pick-item').forEach((card) => {
      const id = card.dataset.id;
      const send = (status, qty, btn) => run(btn, async () => { const y = window.scrollY; drawOrder(await api('POST', `/api/pick/orders/${o.id}/items/${id}`, { status, qty })); window.scrollTo(0, y); });
      const shortRow = card.querySelector('.short-row');
      card.querySelectorAll('[data-st]').forEach((b) => (b.onclick = () => {
        if (b.dataset.st === 'short') { shortRow.hidden = false; shortRow.querySelector('input').focus(); return; }
        send(b.dataset.st, null, b);
      }));
      card.querySelector('[data-short-ok]').onclick = (e) => send('short', Number(shortRow.querySelector('input').value), e.target);
      card.querySelector('[data-short-cancel]').onclick = () => { shortRow.hidden = true; };
    });
    document.getElementById('finish').onclick = (e) => run(e.target, async () => {
      const r = await api('POST', `/api/pick/orders/${o.id}/finish`);
      stopTimer();
      view.innerHTML = `<div class="success card"><span class="muted">طلب ${r.number}</span><span>${r.status === 'ready' ? 'خلّصت التجهيز في' : 'الطلب اتلغى لأن مفيش أصناف'}</span>${r.status === 'ready' ? `<span class="done-time">${clock(r.seconds)}</span>` : ''}<p class="muted" style="margin:0">التاجر وصله إشعار بالتفاصيل.</p><a class="btn primary" href="#queue">الفاتورة اللي بعدها</a></div>`;
    });
  }

  // ------------------------------------------------------------- receiving
  async function renderReceiving() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const list = await run(null, () => api('GET', '/api/pick/receiving'));
    if (!list) return;
    view.innerHTML = list.length ? `<p class="small muted" style="margin-top:0">لما بضاعة مورد توصل، اكتب الكمية اللي وصلت فعلًا من كل صنف ودوس «استلمت».</p>` + list.map((po) => `
      <section class="card" data-po="${po.id}">
        <div class="job-head" style="margin-bottom:6px"><b class="num">${esc(po.number)}</b><span class="tag note">${esc(po.supplier)}</span></div>
        ${po.items.map((i) => `<div class="recv-row"><div><div style="font-weight:600">${esc(i.name)}</div><div class="small muted">${int(i.qty)} ${esc(i.unit_label)}${i.code ? ` · كود ${esc(i.code)}` : ''}${i.note ? ` · ${esc(i.note)}` : ''}</div></div>
          <input type="number" inputmode="numeric" min="0" max="${i.qty}" value="${i.qty}" data-item="${i.id}" aria-label="الكمية اللي وصلت"></div>`).join('')}
        <button class="btn primary block" data-recv style="margin-top:10px">استلمت</button>
      </section>`).join('') : '<div class="empty"><b>مفيش بضاعة مستنية من الموردين</b></div>';
    view.querySelectorAll('[data-po]').forEach((sec) => (sec.querySelector('[data-recv]').onclick = (e) => run(e.target, async () => {
      const received = {};
      sec.querySelectorAll('[data-item]').forEach((i) => { received[i.dataset.item] = Number(i.value || 0); });
      const short = [...sec.querySelectorAll('[data-item]')].some((i) => Number(i.value || 0) < Number(i.max));
      if (short && !confirm('فيه أصناف وصلت أقل من المطلوب. الناقص هيتسجّل نقص. تكمّل؟')) return;
      await api('POST', `/api/pick/receiving/${sec.dataset.po}`, { received });
      toast('اتسجّل الاستلام');
      renderReceiving();
    })));
  }

  // -------------------------------------------------------------- returns
  async function renderReturns() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const list = await run(null, () => api('GET', '/api/pick/returns'));
    if (!list) return;
    view.innerHTML = list.length ? '<p class="small muted" style="margin-top:0">افحص كل صنف رجع: اكتب كام سليم وكام تالف. الباقي بيتحسب «ماوصلش».</p>' + list.map((r) => `
      <section class="card" data-ret="${r.id}">
        <div class="job-head" style="margin-bottom:6px"><b class="num">مرتجع طلب ${r.order_number}</b><span class="tag ${r.pickup === 'store' ? 'note' : 'me'}">${r.pickup === 'store' ? 'التاجر هيجيبه' : 'المندوب جابه'}</span></div>
        ${r.note ? `<div class="pick-note" style="margin-bottom:8px">${esc(r.note)}</div>` : ''}
        ${r.items.map((i) => `<div class="recv-row" data-item="${i.id}" data-qty="${i.qty}"><div><div style="font-weight:600">${esc(i.name)}</div><div class="small muted">${int(i.qty)} ${esc(i.unit_label)} · السبب: ${esc(i.reason_label)}</div></div>
          <div class="row" style="gap:6px;flex-wrap:nowrap"><label class="small">سليم<input type="number" class="r-sound" min="0" max="${i.qty}" value="${i.qty}" inputmode="numeric"></label><label class="small">تالف<input type="number" class="r-damaged" min="0" max="${i.qty}" value="0" inputmode="numeric"></label></div></div>`).join('')}
        <button class="btn primary block" data-inspect style="margin-top:10px">خلّصت الفحص</button>
      </section>`).join('') : '<div class="empty"><b>مفيش مرتجعات مستنية فحص</b></div>';
    view.querySelectorAll('[data-ret]').forEach((sec) => (sec.querySelector('[data-inspect]').onclick = (e) => run(e.target, async () => {
      const results = {};
      for (const row of sec.querySelectorAll('[data-item]')) {
        const sound = Number(row.querySelector('.r-sound').value || 0), damaged = Number(row.querySelector('.r-damaged').value || 0);
        if (sound + damaged > Number(row.dataset.qty)) throw new Error('السليم والتالف أكتر من المرتجع');
        results[row.dataset.item] = { sound, damaged };
      }
      const r = await api('POST', `/api/pick/returns/${sec.dataset.ret}/inspect`, { results });
      toast(`اتفحص. المبلغ المستحق ${money(r.refund)}`);
      renderReturns();
    })));
  }

  // --------------------------------------------------------------- driver
  const money = (v) => `${Number(v || 0).toLocaleString('ar-EG-u-nu-latn', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ج`;
  const TRIP = { assigned: 'لسه ماخرجتش', out: 'في الطريق', arrived: 'وصلت' };

  async function renderTrips() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const data = await run(null, () => api('GET', '/api/pick/trips'));
    if (!data) return;
    const pickups = await api('GET', '/api/pick/return-pickups').catch(() => []);
    view.innerHTML = `
      ${pickups.map((r) => `
        <section class="card stack" data-pickup="${r.id}" style="border-color:var(--amber);margin-bottom:10px">
          <div class="job-head"><b class="num">مرتجع طلب ${r.order_number}</b><span class="tag prio">استلام مرتجع</span></div>
          <div class="row"><span style="flex:1">${esc(r.shop)} · <span class="muted">${esc(r.address || '')}</span></span>${r.phone ? `<a class="btn sm dark" href="tel:${esc(r.phone)}">اتصل</a>` : ''}</div>
          ${r.items.map((i) => `<div class="small">${esc(i.name)}: ${int(i.qty)} ${esc(i.unit_label)}</div>`).join('')}
          <button class="btn primary block" data-picked>استلمت المرتجع من التاجر</button>
        </section>`).join('')}
      <div class="stat-row">
        <div class="stat-box"><b class="num">${int(data.trips.length)}</b><span>مشاوير معاك</span></div>
        <div class="stat-box"><b class="num">${int(data.delivered_today)}</b><span>سلّمت النهارده</span></div>
        <div class="stat-box"><b class="num">${money(data.cash_on_hand)}</b><span>كاش معاك</span></div>
      </div>
      ${data.cash_on_hand ? `<div class="alert info" style="margin-bottom:10px">معاك ${money(data.cash_on_hand)} من ${int(data.cash_orders)} طلب. سلّمهم للإدارة.</div>` : ''}
      ${data.trips.length ? data.trips.map((t) => `
        <a class="job${t.delivery_status !== 'assigned' ? ' mine' : ''}" href="#trip/${t.id}" style="text-decoration:none;color:inherit">
          <div class="job-head"><b class="num">طلب ${t.number}</b><span class="tag ${t.delivery_status === 'assigned' ? 'lock' : 'me'}">${TRIP[t.delivery_status]}</span>${t.cash_due ? `<span class="tag prio">كاش ${money(t.cash_due)}</span>` : ''}</div>
          <div>${esc(t.shop)}${t.zone ? ` · <span class="muted">${esc(t.zone)}</span>` : ''}</div>
          <div class="small muted">${esc(t.address || 'مفيش عنوان مكتوب')}${t.eta ? ` · ${esc(t.eta)}` : ''}</div>
        </a>`).join('') : '<div class="empty"><b>مفيش مشاوير معاك</b>الإدارة هتوزّع عليك الطلبات الجاهزة.</div>'}
      <div class="row" style="margin-top:14px"><button class="btn block" id="refresh">تحديث</button></div>`;
    document.getElementById('refresh').onclick = renderTrips;
    view.querySelectorAll('[data-pickup]').forEach((sec) => (sec.querySelector('[data-picked]').onclick = (e) => run(e.target, async () => {
      await api('POST', `/api/pick/return-pickups/${sec.dataset.pickup}/done`);
      toast('اتسجّل. سلّم الأصناف للمحل علشان تتفحص');
      renderTrips();
    })));
  }

  async function renderTrip(id) {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const data = await run(null, () => api('GET', '/api/pick/trips'));
    if (!data) return;
    const t = data.trips.find((x) => x.id === id);
    if (!t) { view.innerHTML = '<div class="empty"><b>المشوار ده مش معاك</b><a class="btn" href="#trips">مشاويري</a></div>'; return; }
    drawTrip(t);
  }

  function drawTrip(t) {
    const tel = t.phone ? `tel:${t.phone}` : '';
    view.innerHTML = `
      <div class="page-title"><h2 class="num">طلب ${t.number}</h2><a class="btn ghost sm" href="#trips">رجوع</a></div>
      <section class="card stack">
        <div class="row"><b style="flex:1;font-size:17px">${esc(t.shop)}</b>${tel ? `<a class="btn sm dark" href="${tel}">اتصل</a>` : ''}</div>
        <div>${esc(t.zone)}${t.zone && t.address ? ' · ' : ''}${esc(t.address || 'مفيش عنوان مكتوب')}</div>
        <div class="small muted">${int(t.items)} صنف${t.eta ? ` · الوقت المتوقع: ${esc(t.eta)}` : ''}</div>
        <div class="alert ${t.cash_due ? 'warn' : 'good'}">${t.cash_due ? `استلم <b class="num">${money(t.cash_due)}</b> كاش` : esc(t.payment_label) + '، مفيش فلوس تستلمها'}</div>
      </section>
      <section class="card stack" style="margin-top:12px">
        ${t.delivery_status === 'assigned' ? '<button class="btn primary block" id="out" style="min-height:58px;font-size:18px">خرجت للتوصيل</button><p class="small muted" style="margin:0">التاجر هيوصله اسمك ورقمك وكود الاستلام.</p>' : ''}
        ${t.delivery_status === 'out' ? '<button class="btn primary block" id="arrived" style="min-height:58px;font-size:18px">وصلت</button>' : ''}
        ${t.delivery_status === 'arrived' ? `
          ${t.code_locked ? '<div class="alert bad">الكود اتكتب غلط كذا مرة. كلّم الإدارة.</div>' : `
          <label class="field">كود الاستلام من التاجر<input type="text" id="code" class="pin" inputmode="numeric" maxlength="4" autocomplete="off"></label>
          ${t.cash_due ? `<label class="check" style="font-size:16px"><input type="checkbox" id="cash-ok"> استلمت ${money(t.cash_due)} كاش</label>` : ''}
          <button class="btn primary block" id="deliver" style="min-height:58px;font-size:18px">أكّد التسليم</button>`}` : ''}
        <details><summary class="small" style="cursor:pointer;color:var(--red);min-height:40px">العميل مش موجود / ماقدرتش أسلّم</summary>
          <div class="stack" style="margin-top:8px"><select id="fail-reason"><option>العميل مش موجود</option><option>المحل مقفول</option><option>العميل رفض الاستلام</option><option>العنوان غلط</option><option>العميل مش معاه فلوس</option></select>
          <button class="btn danger block" id="fail">ارجع بالطلب وبلّغ الإدارة</button></div></details>
      </section>`;
    const act = (id, url, body, after) => { const b = document.getElementById(id); if (b) b.onclick = () => run(b, async () => { const r = await api('POST', url, body ? body() : undefined); after(r); }); };
    act('out', `/api/pick/trips/${t.id}/out`, null, (r) => { toast('التاجر اتبلّغ إنك في الطريق'); drawTrip(r); });
    act('arrived', `/api/pick/trips/${t.id}/arrived`, null, (r) => drawTrip(r));
    const del = document.getElementById('deliver');
    if (del) del.onclick = () => run(del, async () => {
      const cashOk = document.getElementById('cash-ok');
      if (cashOk && !cashOk.checked) throw new Error('علّم إنك استلمت الكاش الأول');
      const r = await api('POST', `/api/pick/trips/${t.id}/deliver`, { code: document.getElementById('code').value.trim(), cash: cashOk ? t.cash_due : null });
      view.innerHTML = `<div class="success card"><span class="muted">طلب ${r.number}</span><span class="done-time" style="font-size:32px">اتسلّم</span>${r.cash ? `<p style="margin:0">معاك ${money(r.cash)} كاش من الطلب ده.</p>` : ''}<a class="btn primary" href="#trips">المشوار اللي بعده</a></div>`;
    });
    const fail = document.getElementById('fail');
    if (fail) fail.onclick = () => run(fail, async () => {
      await api('POST', `/api/pick/trips/${t.id}/fail`, { reason: document.getElementById('fail-reason').value });
      toast('الإدارة اتبلّغت');
      location.hash = '#trips';
    });
  }

  route();
})();
