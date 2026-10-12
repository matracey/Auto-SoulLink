/* dashboard.js — client-side chrome shared by the templated pages.
 *
 * Loaded by base.html with `defer` whenever a page has the sidebar. Refresh of the board
 * is owned by HTMX — #content polls every 2 s and swaps by idiomorph, which preserves
 * <img> identity; the idiomorph hook below keeps a resolved sprite's src and <details open>
 * across swaps. This file handles:
 *
 *   • Mouse-interaction pause via htmx:beforeSwap
 *   • Theme + font switchers, sidebar collapse
 *   • <details> open-state persistence across morph swaps
 *   • The Upcoming Trainers calc button
 *
 * The encounter-table sort, filter and global search went with the table they drove;
 * the board has zones, not a table to sort. The calc preview lives in calc-preview.js.
 */

// Idempotence sentinel — base.html loads dashboard.js conditionally on
// sidebar_html, and dashboard.html's head_extra block loads it again. Without
// this guard the IIFEs below would re-run, resetting closure state (sort
// column, active filter, etc.) on the second execution. Bail out early if
// we've already initialised in this document.
if (window._slinkDashInit) {
  // Already loaded — skip the body of this file.
} else {
  window._slinkDashInit = true;

(function() {
  // After HTMX swaps in new content, re-run the calc-preview pipeline.
  function refreshClientUI() {
    if (window._slinkCalcRender) window._slinkCalcRender();
  }
  document.body.addEventListener('htmx:afterSettle', refreshClientUI);

  // Mouse-interaction pause — don't morph the DOM out from under a click or a drag.
  //
  // This used to be able to freeze the page permanently: the flag was set on any mousedown and
  // only ever cleared by a mouseup ON THE DOCUMENT. Alt-Tab to the emulator mid-click, drag out
  // of the window, or open devtools during a drag, and no mouseup ever arrives — every swap is
  // vetoed from then on, silently, on the one page you watch during a live run. So clear it on
  // anything that means the interaction is over, not just the happy path.
  //
  // The old recovery path re-fired an 'sse:ping' event, which nothing listens for (#content is
  // hx-trigger="every 2s"); it is deleted rather than kept as decoration. The next poll is at
  // most 2s away, so there is nothing to re-fire.
  // The mouseup grace timer is kept so a new mousedown can cancel it: otherwise a stale
  // timer from the previous click ends the interaction that is in progress now.
  var userInteracting = false, endTimer = null;
  function endInteraction() { userInteracting = false; clearTimeout(endTimer); endTimer = null; }
  document.addEventListener('mousedown', function() { clearTimeout(endTimer); endTimer = null; userInteracting = true; });
  document.addEventListener('mouseup', function() { clearTimeout(endTimer); endTimer = setTimeout(endInteraction, 250); });
  document.addEventListener('dragend', endInteraction);
  document.addEventListener('mouseleave', endInteraction);   // pointer left the document
  window.addEventListener('blur', endInteraction);           // Alt-Tab away mid-click
  document.addEventListener('visibilitychange', function() {
    if (document.hidden) endInteraction();
  });
  document.body.addEventListener('htmx:beforeSwap', function(ev) {
    if (userInteracting) ev.preventDefault();
  });

  // Attempts +/- adjustor; posts the new count then asks HTMX to refresh.
})();


// ── Theme switcher (vanilla, page-agnostic) ───────────────────────────────
// One source of truth for the theme picker UI. Two cases:
//
//   1. Jinja pages (status via dashboard.html, manager, timeline) render the
//      Alpine-driven _theme_switcher.html partial — this script just moves
//      that existing `.theme-switcher` element into the sidebar slot.
//
//   2. Non-Jinja pages (debug / twitch / OBS, served as raw HTML strings)
//      have no `.theme-switcher` element. This script then BUILDS one from
//      scratch and drops it into the sidebar slot. The DOM matches the
//      Alpine version close enough that the same in-sidebar CSS styles it.
//
// Either way the active theme persists via localStorage["slink-theme"] and
// syncs across tabs via the `storage` event.
(function() {
  var THEMES = [
    { slug: 'default',              label: 'Default',     swatch: '#070910' },
    { slug: 'light',                label: 'Light',       swatch: '#eef2fc' },
    { slug: 'funtastic-grape',      label: 'Grape',       swatch: '#9933cc' },
    { slug: 'funtastic-jungle',     label: 'Jungle',      swatch: '#00cc66' },
    { slug: 'funtastic-fire',       label: 'Fire',        swatch: '#ff6600' },
    { slug: 'funtastic-ice',        label: 'Ice',         swatch: '#66ccff' },
    { slug: 'funtastic-watermelon', label: 'Watermelon',  swatch: '#ff3366' },
    { slug: 'funtastic-smoke',      label: 'Smoke',       swatch: '#333333' },
  ];

  function currentTheme() {
    var b = document.body || {};
    var cls = (b.className || '').split(/\s+/).find(function(c) { return c.indexOf('theme-') === 0; });
    if (cls) return cls.replace(/^theme-/, '');
    try { return localStorage.getItem('slink-theme') || 'default'; } catch (_) { return 'default'; }
  }

  function applyTheme(name) {
    var b = document.body;
    var wantClass = 'theme-' + name;
    if (!b.classList.contains(wantClass)) {
      b.className = (b.className || '').split(/\s+/).filter(function(c) {
        return c && c.indexOf('theme-') !== 0;
      }).concat(wantClass).join(' ').trim();
    }
    var link = document.getElementById('slink-theme');
    var wantHref = '/static/themes/' + name + '.css';
    if (link && link.getAttribute('href') !== wantHref) {
      link.href = wantHref;
    }
    try { localStorage.setItem('slink-theme', name); } catch (_) {}
    // Mirror to a cookie so server-rendered pages (calc) can resolve the
    // active theme on first paint instead of flashing default and snapping
    // via a client-side override. 1-year expiry, path=/, SameSite=Lax.
    try {
      document.cookie = 'slink-theme=' + encodeURIComponent(name)
        + '; max-age=31536000; path=/; samesite=lax';
    } catch (_) {}
    // Refresh our own widget's swatch + label so it shows the right state.
    var sw = document.querySelector('.theme-switcher--vanilla');
    if (sw) refreshVanillaWidget(sw);
  }

  function refreshVanillaWidget(root) {
    var active = currentTheme();
    var swatch = root.querySelector('.theme-swatch');
    var nameEl = root.querySelector('.theme-name');
    var def = THEMES.find(function(t) { return t.slug === active; }) || THEMES[0];
    if (swatch) swatch.style.background = def.swatch;
    if (nameEl) nameEl.textContent = def.label;
    Array.prototype.forEach.call(root.querySelectorAll('.theme-pill'), function(btn) {
      var on = btn.getAttribute('data-theme') === active;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function buildVanillaWidget() {
    var d = document.createElement('div');
    d.className = 'theme-switcher theme-switcher--vanilla';
    var html = ''
      + '<details>'
      + '<summary>'
      +   '<span class="theme-swatch"></span>'
      +   '<span class="theme-name"></span>'
      +   '<span class="theme-caret">▾</span>'
      + '</summary>'
      + '<div class="theme-pills" role="group" aria-label="Theme">';
    THEMES.forEach(function(t) {
      html += '<button type="button" class="theme-pill" data-theme="' + t.slug + '" title="' + t.label + '">'
            +   '<span class="theme-swatch" style="background:' + t.swatch + '"></span>'
            +   '<span class="theme-label">' + t.label + '</span>'
            + '</button>';
    });
    html += '</div></details>';
    d.innerHTML = html;
    Array.prototype.forEach.call(d.querySelectorAll('.theme-pill'), function(btn) {
      btn.addEventListener('click', function() {
        applyTheme(btn.getAttribute('data-theme'));
        // Strip ?theme= from the URL — see the matching cleanup in
        // _theme_switcher.html's select(). Without it, refreshing a page
        // loaded with ?theme=X reverts the user's picker choice every time.
        try {
          var u = new URL(window.location.href);
          if (u.searchParams.has('theme')) {
            u.searchParams.delete('theme');
            window.history.replaceState(null, '', u.toString());
          }
        } catch (_) {}
        var details = d.querySelector('details');
        if (details) { details.open = false; details.querySelector('summary').focus(); }
      });
    });
    refreshVanillaWidget(d);
    return d;
  }

  function relocate() {
    var slot = document.querySelector('.mgr-rail-theme');
    if (!slot) return;
    var switcher = document.querySelector('.theme-switcher');
    if (!switcher) {
      // No Alpine widget on this page — build the vanilla version.
      switcher = buildVanillaWidget();
      slot.appendChild(switcher);
    } else if (switcher.parentElement !== slot) {
      slot.appendChild(switcher);
    }
    switcher.classList.add('theme-switcher--in-sidebar');
  }
  relocate();
  document.body.addEventListener('htmx:afterSettle', relocate);
  // Cross-tab sync — keep the vanilla widget's selection in sync with
  // whatever the localStorage value says (changed by another tab).
  window.addEventListener('storage', function(ev) {
    if (ev.key === 'slink-theme' && ev.newValue) applyTheme(ev.newValue);
  });
})();


// ── <details> open-state persistence across morph swaps ──────────────────
// idiomorph syncs ALL attributes between the new server HTML and the
// existing DOM. When the server response doesn't carry `open` (the server
// has no idea what the user has expanded), idiomorph dutifully removes it,
// collapsing every <details> on each 2 s polling swap.
//
// Fix: each <details data-details-key="…"> participates in opt-in
// persistence. The toggle event saves `open` to localStorage; htmx:afterSettle
// re-applies the saved state. Keys are namespaced (`moves:KEY`, `enc:AREA`)
// to avoid collisions across element kinds.
(function() {
  var STORAGE_PREFIX = 'slink-details-open:';
  function keyFor(el) {
    var k = el.getAttribute('data-details-key');
    return k ? STORAGE_PREFIX + k : null;
  }
  // ── Cross-morph preservation via idiomorph callback ────────────────────
  // The polling response carries no `open` attribute (the server has no
  // idea what the user has expanded). By default idiomorph dutifully syncs
  // the new HTML's attribute set onto the existing DOM, stripping `open`
  // and collapsing the widget on every 2 s tick. Hooking into idiomorph's
  // `beforeAttributeUpdated` callback (returning false to veto the update)
  // is the cleanest fix — it tells the diffing engine "leave this attribute
  // alone on these elements" without race-prone restore-after-the-fact
  // dance. Vetoes specifically the `open` attribute on the data-details-key
  // elements; every other attribute on every other element still syncs.
  function installIdiomorphHook() {
    if (!window.Idiomorph || !Idiomorph.defaults || !Idiomorph.defaults.callbacks) return;
    Idiomorph.defaults.callbacks.beforeAttributeUpdated = function(attrName, node, mutationType) {
      if (attrName === 'open' && node && node.hasAttribute && node.hasAttribute('data-details-key')) {
        return false;
      }
      // A sprite's src belongs to the client once the image has resolved: the onerror chain
      // may have moved it to a fallback URL. The server
      // re-sends the original every poll, and re-setting src reloads the image -- the blank
      // frame between the two is the flicker. Attributes sync in the server's order and
      // every adapter writes data-species before src, so when the species is unchanged
      // the src (and the onerror-set style) stay put; a real
      // species change still lands.
      if (SPRITE_OWNED[attrName] && node && node.tagName === 'IMG' && node.dataset.species
          && node.dataset.species === node._spriteFor) {
        return false;
      }
      // The calc preview's visibility is calc-preview.js's: the server always sends
      // display:none, and re-hiding it every poll is half the battle flash.
      if (attrName === 'style' && node && node.classList && node.classList.contains('calc-preview')) {
        return false;
      }
    };
    // A form the user is filling in (the trade-resolve form): its choices are the user's.
    // It still goes when the server stops sending it, and a new id is a new form.
    Idiomorph.defaults.callbacks.beforeNodeMorphed = function(oldNode, newNode) {
      if (oldNode && oldNode.hasAttribute && oldNode.hasAttribute('data-morph-keep')) {
        if (oldNode.classList.contains('mon-recovery')) {
          oldNode.querySelectorAll('select').forEach(function(select) {
            var incoming = newNode.querySelector('select[name="' + select.name + '"]');
            if (!incoming || incoming.innerHTML === select.innerHTML) return;
            var value = select.value;
            select.replaceChildren(...Array.from(incoming.childNodes, function(node) { return node.cloneNode(true); }));
            select.value = Array.from(select.options).some(function(option) { return option.value === value; }) ? value : '';
          });
        }
        return false;
      }
    };
    // ...and so is its rendered table: the server's copy is empty, so the morph would remove
    // it every 2 s and the page painted the gap until afterSettle re-rendered it.
    Idiomorph.defaults.callbacks.beforeNodeRemoved = function(node) {
      var p = node && node.parentElement;
      if (p && p.classList && p.classList.contains('calc-preview')) return false;
    };
  }
  var SPRITE_OWNED = { src: 1, style: 1 };
  function stampSprites() {
    document.querySelectorAll('img[data-species]').forEach(function(img) { img._spriteFor = img.dataset.species; });
  }
  stampSprites();
  document.body.addEventListener('htmx:afterSettle', stampSprites);
  // The open run is somewhere in a list that scrolls on its own; bring it into view.
  var railActive = document.querySelector('.mk-rail-runs .mk-rail-item.active');
  if (railActive && railActive.scrollIntoView) railActive.scrollIntoView({ block: 'nearest' });
  // The htmx-ext-morph extension loads `idiomorph-ext.min.js` which exposes
  // `Idiomorph` as a global. dashboard.html now loads idiomorph BEFORE
  // dashboard.js, but be defensive in case the order ever drifts: defer
  // scripts run with document.readyState === "interactive" (before
  // DOMContentLoaded fires), so registering on DOMContentLoaded reliably
  // runs us after every other defer script has executed. The `load`
  // listener is a final safety net if anything is still pending. The
  // install function is idempotent — it just reassigns a property — so
  // firing twice is harmless.
  if (window.Idiomorph) {
    installIdiomorphHook();
  } else {
    document.addEventListener('DOMContentLoaded', installIdiomorphHook);
    window.addEventListener('load', installIdiomorphHook);
  }

  // ── Cross-session persistence via localStorage ─────────────────────────
  // The idiomorph callback handles cross-morph; this handles page reload.
  // Listen for clicks on summaries (user intent — synthetic toggle events
  // from idiomorph would also fire here, but the callback above means they
  // never happen for our keyed details). The click runs BEFORE the browser
  // flips the open attribute, so persist the inverse of the current state.
  document.body.addEventListener('click', function(ev) {
    var summary = ev.target && ev.target.closest && ev.target.closest('summary');
    if (!summary) return;
    // The Calc button sits inside a trainer row's summary and cancels the toggle, but its
    // handler is on document, which hears the click after this one: skip it here.
    if (ev.target.closest('.tr-calc-btn')) return;
    var details = summary.parentElement;
    if (!details || details.tagName !== 'DETAILS') return;
    var k = keyFor(details);
    if (!k) return;
    var willOpen = !details.open;
    try {
      if (willOpen) window.localStorage.setItem(k, '1');
      else          window.localStorage.removeItem(k);
    } catch (_) {}
  });
  // Restore from localStorage on initial paint AND on every settle (so
  // newly-injected details elements pick up their saved state). This only
  // OPENS widgets — never closes them. Closing is the user's job via the
  // summary click. This guards against an empty/raced localStorage value
  // accidentally collapsing a widget the morph-veto has been keeping open.
  function restoreAll() {
    var nodes = document.querySelectorAll('details[data-details-key]');
    Array.prototype.forEach.call(nodes, function(el) {
      var k = keyFor(el);
      if (!k) return;
      var saved = null;
      try { saved = window.localStorage.getItem(k); } catch (_) {}
      if (saved === '1' && !el.open) el.open = true;
      // If saved is null but el.open is already true (e.g., user opened it
      // earlier this session, idiomorph veto kept it open), repaint the
      // saved state so a hard reload from a different tab honours it too.
      if (saved !== '1' && el.open) {
        try { window.localStorage.setItem(k, '1'); } catch (_) {}
      }
    });
  }
  document.body.addEventListener('htmx:afterSettle', restoreAll);
  if (document.readyState !== 'loading') restoreAll();
  else document.addEventListener('DOMContentLoaded', restoreAll);
})();

// ── Upcoming Trainers Calc button (delegated) ─────────────────────
// Click on a trainer row's ⚔ Calc button pushes the trainer's calc set
// name into localStorage under `slink_prep_trainer` — the key the calc's
// SLink bridge panel watches for its Prep tab — and into the URL as ?prep=,
// because a freshly opened calc never hears the storage event. Then we open
// the calc with window.open(url, 'rrCalc'). On a Manager run page the calc
// is the run's own (/runs/<id>/calc/…), not the Manager's bare /calc/. The named target reuses any existing calc
// tab instead of spawning duplicates; the calc's storage-event listener
// (slink_bridge.js) refreshes its Prep tab when the key changes, so an
// already-open calc tab updates without a manual reload.
//
// stopPropagation prevents the click from also toggling the parent
// <summary>, which would collapse/expand the trainer row.
(function () {
  document.addEventListener('click', function (ev) {
    var btn = ev.target && ev.target.closest && ev.target.closest('.tr-calc-btn');
    if (!btn) return;
    ev.preventDefault(); ev.stopPropagation();
    var calcLabel = btn.dataset.calcLabel || '';
    if (calcLabel) {
      try {
        localStorage.setItem('slink_prep_trainer', calcLabel);
        // Reset encounter index — calc lands on the first encounter.
        localStorage.setItem('slink_prep_encounter', '');
      } catch (_) { /* private mode, etc. */ }
    }
    var run = /^\/runs\/[^\/]+/.exec(location.pathname);
    var url = (run ? run[0] : '') + '/calc/normal.html'
      + (calcLabel ? '?prep=' + encodeURIComponent(calcLabel) : '');
    var calcWin = window.open(url, 'rrCalc');
    if (calcWin && !calcWin.closed) {
      try { calcWin.focus(); } catch (_) { /* cross-origin focus blocked */ }
    }
  });
})();


// ── Phone nav: rail -> top app bar + overlay drawer below 900px ──────────
// board.css repositions .mk-rail as a fixed off-canvas drawer under that breakpoint;
// this builds the always-visible bar (menu button + run identity) and wires the
// toggle. No template change — same trick as the theme-switcher relocation above,
// so every page carrying the rail gets it for free. .mk-rail-open on .mk is the
// only state kept; board.css does the rest.
(function() {
  var mk = document.querySelector('.mk');
  var rail = document.querySelector('.mk-rail');
  if (!mk || !rail) return;
  if (!rail.id) rail.id = 'mk-rail';

  var bar = document.createElement('div');
  bar.className = 'mk-topbar';
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'mk-menu-btn';
  btn.setAttribute('aria-expanded', 'false');
  btn.setAttribute('aria-controls', rail.id);
  btn.setAttribute('aria-label', 'Open menu');
  btn.textContent = '☰';
  var title = document.createElement('span');
  title.className = 'mk-topbar-title';
  var runName = document.querySelector('.mk-runname');
  title.textContent = (runName && runName.textContent.trim()) || 'SLink';
  bar.appendChild(btn);
  bar.appendChild(title);
  mk.insertBefore(bar, rail);

  var scrim = document.createElement('button');
  scrim.type = 'button';
  scrim.className = 'mk-scrim';
  scrim.setAttribute('aria-label', 'Close menu');
  scrim.hidden = true;
  mk.insertBefore(scrim, rail);

  function setOpen(open) {
    mk.classList.toggle('mk-rail-open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    btn.textContent = open ? '✕' : '☰';
    scrim.hidden = !open;
    var main = document.getElementById('main-content');
    if (main) {
      if (open) main.setAttribute('inert', ''); else main.removeAttribute('inert');
    }
    if (open) {
      var first = rail.querySelector('a, button, [tabindex]');
      if (first && first.focus) first.focus();
    }
  }
  btn.addEventListener('click', function() { setOpen(!mk.classList.contains('mk-rail-open')); });
  scrim.addEventListener('click', function() { setOpen(false); btn.focus(); });
  document.addEventListener('keydown', function(ev) {
    if (ev.key === 'Escape' && mk.classList.contains('mk-rail-open')) { setOpen(false); btn.focus(); }
  });
  // A rail link navigates away (or, on the Manager, swaps the run panel) — either
  // way the drawer should not still be open on the next screen.
  rail.addEventListener('click', function(ev) {
    if (ev.target.closest && ev.target.closest('a')) setOpen(false);
  });
  // Resizing past the breakpoint (devtools, tablet rotation) with the drawer open
  // would otherwise leave .mk-rail-open set and main inert on the desktop layout.
  var mq = window.matchMedia('(min-width: 900px)');
  function onMq(e) { if (e.matches) setOpen(false); }
  if (mq.addEventListener) mq.addEventListener('change', onMq);
  else if (mq.addListener) mq.addListener(onMq);   // Safari < 14
})();


// ── Board live announcements + toasts ─────────────────────────────────────
// One sr-only region and one toast host, both siblings of #content in _board.html
// (outside the 2s poll's morph target). Diffs .mk-pair section classes and the
// phase banner between polls — no event IDs, no server change. Speaks/toasts three
// things only: a new link, a pair death, the run ending. See
// docs/public_ui/mobile-a11y.md #4 for what NOT to announce (HP, dead zones, every
// poll, memorialize-after-death).
(function() {
  var announcer = document.getElementById('mk-announcer');
  var toastHost = document.getElementById('mk-toast-host');
  if (!announcer) return;

  var PAUSE_KEY = 'slink-announce-paused';
  function isPaused() {
    try { return localStorage.getItem(PAUSE_KEY) === '1'; } catch (_) { return false; }
  }
  function setPaused(p) {
    var toggle = document.getElementById('mk-announce-toggle');   // inside #content now (data-morph-keep)
    try { localStorage.setItem(PAUSE_KEY, p ? '1' : '0'); } catch (_) {}
    if (toggle) {
      toggle.setAttribute('aria-pressed', p ? 'true' : 'false');
      toggle.textContent = p ? 'Resume announcements' : 'Pause announcements';
    }
  }
  setPaused(isPaused());
  // Delegated: the button lives in the polled board, so a morph that ever rebuilds it keeps working.
  document.addEventListener('click', function(e) {
    if (e.target && e.target.closest && e.target.closest('#mk-announce-toggle')) setPaused(!isPaused());
  });

  var SECTIONS = ['party', 'pending', 'split', 'boxed', 'linked', 'fallen'];
  function sectionOf(article) {
    for (var i = 0; i < SECTIONS.length; i++) {
      if (article.classList.contains(SECTIONS[i])) return SECTIONS[i];
    }
    return '';
  }
  // A dead-zone row is also section=fallen but both halves are `.empty` (nobody
  // caught anything) — that is an area lock, not a pair dying. Never announce it.
  function isRealPair(article) {
    var a = article.querySelector('.mk-half.a'), b = article.querySelector('.mk-half.b');
    return !!(a && b && !a.classList.contains('empty') && !b.classList.contains('empty'));
  }
  function nickOf(article, side) {
    var el = article.querySelector('.mk-half.' + side + ' .mk-half-nick');
    if (!el) return '';
    var clone = el.cloneNode(true);
    Array.prototype.forEach.call(clone.querySelectorAll('.mk-active, .shiny-star'), function(n) { n.remove(); });
    return (clone.textContent || '').trim();
  }
  function areaOf(article) {
    var el = article.querySelector('.mk-bond-area');
    return el ? el.textContent.trim() : '';
  }
  function snapshot() {
    var content = document.getElementById('content');
    var map = {};
    if (content) {
      Array.prototype.forEach.call(content.querySelectorAll('.mk-pair[id]'), function(article) {
        map[article.id] = sectionOf(article);
      });
    }
    var banner = content && content.querySelector('.phase-banner');
    return { pairs: map, runOver: !!(banner && banner.classList.contains('phase-game_over')) };
  }

  function speak(text) {
    // Two writes so a repeated string still gets announced — aria-live only fires
    // on a text change.
    announcer.textContent = '';
    window.setTimeout(function() { announcer.textContent = text; }, 50);
  }
  function toast(text, kind) {
    if (!toastHost) return;
    var el = document.createElement('div');
    el.className = 'mk-toast' + (kind ? ' mk-toast-' + kind : '');
    el.textContent = text;
    toastHost.appendChild(el);
    requestAnimationFrame(function() { el.classList.add('show'); });
    window.setTimeout(function() {
      el.classList.remove('show');
      window.setTimeout(function() { el.remove(); }, 250);
    }, 5000);
  }
  function announce(text, kind) {
    if (isPaused()) return;
    speak(text);
    toast(text, kind);
  }

  var prev = snapshot();   // baseline at load — never announce what was already true

  function diffAndAnnounce() {
    var content = document.getElementById('content');
    if (!content) return;
    var next = snapshot();
    Array.prototype.forEach.call(content.querySelectorAll('.mk-pair[id]'), function(article) {
      var id = article.id, was = prev.pairs[id], now = next.pairs[id];
      if (was === now || !isRealPair(article)) return;
      if (now && now !== 'pending' && now !== 'fallen' && (was === undefined || was === 'pending')) {
        announce('New link at ' + areaOf(article) + ': ' + nickOf(article, 'a') + ' & ' + nickOf(article, 'b') + '.', 'link');
      } else if (now === 'fallen' && was !== 'fallen') {
        announce(areaOf(article) + ' pair has fallen: ' + nickOf(article, 'a') + ' & ' + nickOf(article, 'b') + '.', 'death');
      }
    });
    if (next.runOver && !prev.runOver) announce('The run is over.', 'over');
    prev = next;
  }
  document.body.addEventListener('htmx:afterSettle', diffAndAnnounce);
})();

}  // close `if (window._slinkDashInit)` sentinel
