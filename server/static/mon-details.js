/* One shared panel, outside the polled fragment. Templates contain escaped server markup. */
(() => {
  if (document.getElementById('mon-detail-panel')) return;
  const panel = document.createElement('section');
  panel.id = 'mon-detail-panel';
  panel.className = 'mon-detail-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Pokemon details');
  panel.hidden = true;
  panel.innerHTML = '<div class="mon-detail-controls"><button type="button" data-pin aria-pressed="false">Pin</button>'
    + '<span>Esc to close</span><button type="button" data-close aria-label="Close Pokemon details">Close</button></div>'
    + '<div class="mon-detail-poll-warning" hidden>Connection lost. These details may be out of date.</div>'
    + '<div data-detail-body></div>';
  document.body.append(panel);
  const body = panel.querySelector('[data-detail-body]');
  const pin = panel.querySelector('[data-pin]');
  const closeButton = panel.querySelector('[data-close]');
  let sourceId = '', anchor = null, pinned = false, timer = null, markup = '';
  let suppressed = null;

  const triggerFor = el => el instanceof Element ? el.closest('[data-mon-detail]') : null;
  const triggers = () => [...document.querySelectorAll('[data-mon-detail]')]
    .filter(el => el.dataset.monDetail === sourceId);
  function setPinned(value) {
    pinned = value;
    pin.setAttribute('aria-pressed', String(value));
    pin.textContent = value ? 'Unpin' : 'Pin';
  }
  function position() {
    if (panel.hidden || !anchor?.isConnected) return;
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
    panel.style.maxHeight = `${Math.max(0, height - 16)}px`;
    panel.style.width = `${Math.min(460, width - 16)}px`;
    const a = anchor.getBoundingClientRect(), p = panel.getBoundingClientRect();
    let x = a.right + 10;
    if (x + p.width > left + width - 8) x = a.left - p.width - 10;
    if (x < left + 8) x = Math.min(a.left, left + width - p.width - 8);
    panel.style.left = `${Math.max(left + 8, x)}px`;
    panel.style.top = `${Math.max(top + 8, Math.min(a.top, top + height - p.height - 8))}px`;
  }
  function close(restoreFocus = false) {
    clearTimeout(timer);
    if (panel.hidden) return;
    const returnTo = anchor?.isConnected ? anchor : triggers()[0];
    triggers().forEach(el => el.setAttribute('aria-expanded', 'false'));
    panel.hidden = true;
    sourceId = '';
    markup = '';
    setPinned(false);
    if (restoreFocus && returnTo) {
      suppressed = returnTo;
      returnTo.focus({preventScroll: true});
    }
  }
  function refresh() {
    const template = document.getElementById(sourceId);
    const matching = triggers();
    if (!template || !matching.length) {
      const hadFocus = panel.contains(document.activeElement);
      close();
      if (hadFocus) document.getElementById('main-content')?.focus({preventScroll: true});
      return false;
    }
    if (!anchor?.isConnected) anchor = matching[0];
    if (markup !== template.innerHTML) {
      const scroll = panel.scrollTop;
      markup = template.innerHTML;
      body.replaceChildren(template.content.cloneNode(true));
      panel.scrollTop = scroll;
      panel.setAttribute('aria-label', `Details for ${body.querySelector('h2')?.textContent || 'Pokemon'}`);
    }
    matching.forEach(el => el.setAttribute('aria-expanded', 'true'));
    panel.querySelector('.mon-detail-poll-warning').hidden = !document.body.classList.contains('conn-lost');
    position();
    return true;
  }
  function open(trigger) {
    clearTimeout(timer);
    if (pinned && trigger.dataset.monDetail !== sourceId) return;
    if (sourceId !== trigger.dataset.monDetail) {
      close();
      sourceId = trigger.dataset.monDetail;
      panel.scrollTop = 0;
    }
    anchor = trigger;
    panel.hidden = false;
    refresh();
  }
  function dismissLater() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!pinned && !panel.matches(':hover') && !panel.contains(document.activeElement)
          && !triggers().some(el => el.matches(':hover') || el === document.activeElement)) close();
    }, 180);
  }
  document.addEventListener('pointerover', ev => {
    if (ev.pointerType === 'touch') return;
    const trigger = triggerFor(ev.target);
    if (trigger && trigger !== suppressed) open(trigger);
    if (panel.contains(ev.target)) clearTimeout(timer);
  });
  document.addEventListener('pointerout', ev => {
    const trigger = triggerFor(ev.target);
    if (trigger && !trigger.contains(ev.relatedTarget)) {
      if (suppressed === trigger) suppressed = null;
      dismissLater();
    }
    if (panel.contains(ev.target) && !panel.contains(ev.relatedTarget)) dismissLater();
  });
  document.addEventListener('focusin', ev => {
    const trigger = triggerFor(ev.target);
    // Touch focus precedes click; opening here can cover and retarget the tap.
    if (trigger && trigger !== suppressed && trigger.matches(':focus-visible')) open(trigger);
    if (panel.contains(ev.target)) clearTimeout(timer);
  });
  document.addEventListener('focusout', ev => {
    if (ev.target === suppressed) suppressed = null;
    if (triggerFor(ev.target) || panel.contains(ev.target)) dismissLater();
  });
  document.addEventListener('click', ev => {
    const trigger = triggerFor(ev.target);
    if (trigger) {
      const wasPinned = pinned && sourceId === trigger.dataset.monDetail;
      suppressed = null;
      if (pinned && sourceId !== trigger.dataset.monDetail) close();
      open(trigger);
      if (wasPinned) close(true);
      else { setPinned(true); pin.focus({preventScroll: true}); }
    } else if (!panel.contains(ev.target)) close();
  });
  pin.addEventListener('click', () => setPinned(!pinned));
  closeButton.addEventListener('click', () => close(true));
  document.addEventListener('keydown', ev => {
    if (panel.hidden) return;
    if (ev.key === 'Escape') { ev.preventDefault(); close(true); }
    // The portal is last in the document; bridge keyboard focus from its trigger.
    if (ev.key === 'Tab' && !ev.shiftKey && triggerFor(ev.target)?.dataset.monDetail === sourceId) {
      ev.preventDefault(); pin.focus();
    } else if (ev.key === 'Tab' && ev.shiftKey && ev.target === pin) {
      ev.preventDefault(); close(true);
    } else if (ev.key === 'Tab' && !ev.shiftKey && ev.target === closeButton) {
      const next = [...document.querySelectorAll('button, a[href], input, select, textarea, summary, [tabindex="0"]')]
        .filter(el => !el.disabled && !panel.contains(el) && el.getClientRects().length);
      const index = next.indexOf(anchor);
      if (index >= 0 && next[index + 1]) {
        ev.preventDefault(); close(); next[index + 1].focus();
      }
    }
  });
  document.body.addEventListener('htmx:afterSettle', () => { if (!panel.hidden) refresh(); });
  new MutationObserver(() => {
    if (!panel.hidden) {
      panel.querySelector('.mon-detail-poll-warning').hidden = !document.body.classList.contains('conn-lost');
      position();
    }
  }).observe(document.body, {attributes: true, attributeFilter: ['class']});
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, {passive: true, capture: true});
  window.visualViewport?.addEventListener('resize', position);
  window.visualViewport?.addEventListener('scroll', position);
})();
