// روز جملة: an eye button on every password / PIN field so people can check what they typed.
// Shared by all pages. The screens build their forms at runtime, so new fields are picked up as they appear.
(function () {
  const EYE = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.9 10.9 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.1M6.6 6.6C3.9 8.3 2 12 2 12s3.5 7 10 7a10 10 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

  const style = document.createElement('style');
  // Edge draws its own reveal icon; hide it so there is only one.
  style.textContent = 'input::-ms-reveal,input::-ms-clear{display:none}'
    + '.pw-wrap{position:relative}'
    // Pages style every <button> (height, colour, shadow); reset all of that for the small eye.
    + '.pw-wrap .pw-eye{position:absolute;left:2px;top:50%;transform:translateY(-50%);display:flex;align-items:center;justify-content:center;width:40px;height:40px;min-width:0;min-height:0;padding:0;margin:0;border:0;background:transparent;box-shadow:none;color:#6e6e73;opacity:.8;cursor:pointer;border-radius:8px}'
    + '.pw-wrap .pw-eye:hover,.pw-wrap .pw-eye:focus-visible{opacity:1;background:transparent}';
  document.head.appendChild(style);

  function enhance(input) {
    if (input.dataset.pwToggle) return;
    input.dataset.pwToggle = '1';
    const focused = document.activeElement === input;
    const wrap = document.createElement('span');
    wrap.className = 'pw-wrap';
    // A field given a fixed width keeps it; every other field fills its row as before.
    if (input.style.width) {
      wrap.style.display = 'inline-block';
      wrap.style.width = input.style.width;
    } else {
      wrap.style.display = 'block';
    }
    input.style.width = '100%';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    // All pages are right-to-left, so the eye sits at the left end of the field on every screen.
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pw-eye';
    input.style.paddingLeft = '42px';
    if (getComputedStyle(input).textAlign === 'center') input.style.paddingRight = '42px';
    const sync = () => {
      const shown = input.type === 'text';
      btn.innerHTML = shown ? EYE_OFF : EYE;
      btn.setAttribute('aria-label', shown ? 'إخفاء' : 'إظهار');
      btn.setAttribute('aria-pressed', String(shown));
    };
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      input.type = input.type === 'password' ? 'text' : 'password';
      sync();
      input.focus();
    });
    sync();
    wrap.appendChild(btn);
    if (focused) input.focus();
  }

  function scan(root) {
    if (root.nodeType !== 1) return;
    if (root.matches('input[type="password"]')) enhance(root);
    root.querySelectorAll('input[type="password"]').forEach(enhance);
  }

  function start() {
    scan(document.body);
    new MutationObserver((records) => {
      records.forEach((r) => r.addedNodes.forEach(scan));
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
