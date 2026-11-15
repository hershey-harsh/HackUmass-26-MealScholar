/* Progressive enhancement only: the page works without this file. */
(function () {
  var d = document, w = window;
  d.documentElement.classList.add('js');
  var reduce = w.matchMedia('(prefers-reduced-motion: reduce)');

  // Header hairline, and the phone dock: shown once the hero button has
  // scrolled away, hidden again when the closing call to action comes into view.
  // The section in view is lit in the header nav and the tablet chips.
  var header = d.querySelector('.site-header');
  var dock = d.querySelector('.dock');
  var heroCta = d.querySelector('[data-hero-cta]');
  var stop = d.querySelector('[data-dock-stop]');
  var navLinks = [].slice.call(d.querySelectorAll('.nav-main a[href^="#"], .nav-chips a[href^="#"]'));
  var sections = [];
  navLinks.forEach(function (a) {
    var s = d.querySelector(a.getAttribute('href'));
    if (s && sections.indexOf(s) < 0) sections.push(s);
  });
  sections.sort(function (a, b) { return a.compareDocumentPosition(b) & 4 ? -1 : 1; });
  function onScroll() {
    if (header) header.classList.toggle('is-scrolled', w.scrollY > 8);
    if (dock && heroCta && stop) {
      dock.classList.toggle('is-shown', heroCta.getBoundingClientRect().bottom < 0 &&
        stop.getBoundingClientRect().top > w.innerHeight - 40);
    }
    var active = '';
    sections.forEach(function (s) { if (s.getBoundingClientRect().top <= w.innerHeight * .4) active = s.id; });
    if (w.scrollY < 10) active = '';
    navLinks.forEach(function (a) {
      var on = a.getAttribute('href') === '#' + active;
      if (on === a.classList.contains('is-active')) return;
      a.classList.toggle('is-active', on);
      if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    });
  }
  // Straight from the scroll event, with no animation frame in between: a frame callback queued
  // from every scroll event makes Safari render each one on the main thread instead of on the
  // compositor. The reads force nothing while the layout is clean, and nothing is written unless
  // a state changed.
  w.addEventListener('scroll', onScroll, { passive: true });
  w.addEventListener('resize', onScroll);
  onScroll();

  // Phone menu: close after a pick, on Escape, or on a tap outside
  // (that tap only closes the menu, so it can't open a link underneath). It also closes when the
  // keyboard moves focus out of it, and when the screen gets too wide for it (a phone turned sideways),
  // so a hidden open menu never holds focus or swallows the next tap.
  var menu = d.querySelector('.menu');
  if (menu) {
    var byKey = false;
    var phone = w.matchMedia('(max-width: 599px)');
    menu.addEventListener('click', function (e) { if (e.target.closest('a')) menu.open = false; });
    d.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && menu.open) { menu.open = false; menu.querySelector('summary').focus(); }
    });
    // Only the keyboard: a tap outside moves focus too (in Chrome), and the guard below handles that tap.
    d.addEventListener('keydown', function () { byKey = true; }, true);
    d.addEventListener('pointerdown', function () { byKey = false; }, true);
    menu.addEventListener('focusout', function (e) {
      if (byKey && menu.open && e.relatedTarget && !menu.contains(e.relatedTarget)) menu.open = false;
    });
    phone.addEventListener('change', function () { if (!phone.matches) menu.open = false; });
    d.addEventListener('click', function (e) {
      if (menu.open && menu.getClientRects().length && !menu.contains(e.target)) {
        e.preventDefault(); e.stopPropagation(); menu.open = false;
      }
    }, true);
  }

  // Theme: System, Light or Dark. The script in <head> already set data-theme before
  // first paint; the radios save the choice, and System follows the system setting live.
  var theme = w.mealscholarTheme;
  var radios = d.querySelectorAll('.theme input');
  if (theme && radios.length) {
    var choice = theme.stored() || 'system';
    var sync = function () { radios.forEach(function (r) { r.checked = r.value === choice; }); };
    var show = function () {
      var root = d.documentElement;
      root.classList.add('theme-switch');
      theme.apply(choice);
      w.requestAnimationFrame(function () { w.requestAnimationFrame(function () { root.classList.remove('theme-switch'); }); });
    };
    radios.forEach(function (r) {
      r.addEventListener('change', function () {
        if (!r.checked) return;
        choice = r.value;
        try {
          if (choice === 'system') w.localStorage.removeItem(theme.key);
          else w.localStorage.setItem(theme.key, choice);
        } catch (e) {}
        sync();
        show();
      });
    });
    theme.mq.addEventListener('change', function () { if (choice === 'system') show(); });
    sync();
  }

  // Sideways rows: previous/next buttons and dots, focusable only while they scroll.
  d.querySelectorAll('[data-rail]').forEach(function (rail) {
    var ctrl = d.querySelector('[data-rail-controls="' + rail.id + '"]');
    var box = d.querySelector('[data-rail-dots="' + rail.id + '"]');
    var prev = ctrl && ctrl.querySelector('[data-dir="-1"]');
    var next = ctrl && ctrl.querySelector('[data-dir="1"]');
    var dots = [], q = false;

    function update() {
      q = false;
      var max = rail.scrollWidth - rail.clientWidth, x = rail.scrollLeft;
      if (prev) prev.disabled = x <= 2;
      if (next) next.disabled = x >= max - 2;
      var on = max > 0 ? Math.round(x / max * (dots.length - 1)) : 0;
      dots.forEach(function (dot, i) { dot.classList.toggle('is-on', i === on); });
    }
    function build() {
      var scrolls = rail.scrollWidth - rail.clientWidth > 2;
      var n = scrolls ? Math.min(8, Math.ceil(rail.scrollWidth / rail.clientWidth - 0.05)) : 0;
      if (scrolls) rail.tabIndex = 0; else rail.removeAttribute('tabindex');
      if (ctrl) ctrl.hidden = !scrolls;
      if (box && n !== dots.length) {
        box.textContent = '';
        dots = [];
        for (var i = 0; i < n; i++) dots.push(box.appendChild(d.createElement('i')));
      }
      update();
    }
    function go(dir) {
      rail.scrollBy({ left: dir * rail.clientWidth * 0.85, behavior: reduce.matches ? 'auto' : 'smooth' });
    }

    if (prev) prev.addEventListener('click', function () { go(-1); });
    if (next) next.addEventListener('click', function () { go(1); });
    rail.addEventListener('scroll', function () {
      if (!q) { q = true; w.requestAnimationFrame(update); }
    }, { passive: true });
    w.addEventListener('resize', build);
    w.addEventListener('load', build);
    build();
  });
})();

// The hero's facts line: hide the middle dot in front of an item that starts a wrapped line.
(function () {
  var d = document, w = window;
  var list = d.querySelector('.facts');
  if (!list) return;
  var items = [].slice.call(list.children);
  function mark() {
    var top = null;
    items.forEach(function (li) { li.classList.remove('is-line-start'); });
    items.forEach(function (li, i) {
      var t = li.offsetTop;
      if (i > 0 && t > top + 2) li.classList.add('is-line-start');
      top = t;
    });
  }
  w.addEventListener('resize', mark);
  if (d.fonts && d.fonts.ready) d.fonts.ready.then(mark);
  mark();
})();

// The hero's screens fan out at every width: the active one in front, the others tucked behind
// on both sides (each card gets --o, its place from the front, and --a, how far that is). Phones
// get a smaller fan (styles.css). They move on every few seconds unless reduced motion is on;
// hovering, focusing or the pause button stops that. Without JavaScript it stays a sideways row
// that snaps.
(function () {
  var d = document, w = window;
  var fan = d.querySelector('[data-fan]');
  if (!fan) return;
  var track = fan.querySelector('[data-fan-track]');
  var cards = [].slice.call(fan.querySelectorAll('[data-slide]'));
  var ctrl = fan.querySelector('[data-fan-ctrl]');
  var dotsBox = fan.querySelector('[data-fan-dots]');
  var pauseBtn = fan.querySelector('[data-fan-pause]');
  var live = fan.querySelector('[data-fan-live]');
  var n = cards.length, half = Math.floor(n / 2);
  var wide = { matches: true, addEventListener: function () {} }; // fanned at every width
  var reduce = w.matchMedia('(prefers-reduced-motion: reduce)');
  var active = 0, timer = 0, held = false, userPaused = false, inView = true, lastO = [];
  var DELAY = 8000;

  function title(i) { var t = cards[i].querySelector('.fan-card__title'); return t ? t.textContent : ''; }

  var dots = cards.map(function (card, i) {
    var b = d.createElement('button');
    b.type = 'button';
    b.className = 'fan__dot';
    b.setAttribute('aria-label', 'Show ' + (i + 1) + ' of ' + n + ': ' + title(i));
    b.addEventListener('click', function () { go(i, true); });
    dotsBox.appendChild(b);
    return b;
  });
  ctrl.hidden = false;

  function setDots() {
    dots.forEach(function (b, i) {
      if (i === active) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
  }
  function announce() { if (live) live.textContent = 'Showing ' + (active + 1) + ' of ' + n + ': ' + title(active); }

  // Fanned layout: place every card around the active one, wrapping around the ends.
  function layout() {
    cards.forEach(function (card, i) {
      var o = ((i - active + half + n) % n) - half;
      var jump = lastO[i] !== undefined && Math.abs(o - lastO[i]) > half;
      if (jump) card.classList.add('is-jump');
      card.style.setProperty('--o', o);
      card.style.setProperty('--a', Math.abs(o));
      card.classList.toggle('is-active', o === 0);
      card.querySelectorAll('a').forEach(function (a) {
        if (o === 0) a.removeAttribute('tabindex'); else a.tabIndex = -1;
      });
      lastO[i] = o;
      if (jump) w.requestAnimationFrame(function () { w.requestAnimationFrame(function () { card.classList.remove('is-jump'); }); });
    });
    if (w.__syncVideos) w.__syncVideos(); // only the front card's recording plays
  }
  function clearLayout() {
    cards.forEach(function (card) {
      card.style.removeProperty('--o');
      card.style.removeProperty('--a');
      card.classList.remove('is-active', 'is-jump');
      card.querySelectorAll('a').forEach(function (a) { a.removeAttribute('tabindex'); });
    });
    lastO = [];
  }

  function go(i, byUser) {
    active = (i + n) % n;
    setDots();
    if (wide.matches) layout();
    else {
      var card = cards[active];
      track.scrollTo({ left: card.offsetLeft - (track.clientWidth - card.offsetWidth) / 2, behavior: reduce.matches ? 'auto' : 'smooth' });
    }
    if (byUser) { announce(); restart(); }
  }

  // Auto-advance, only while fanned, at least half on screen, not held by hover or focus, and not paused.
  function canPlay() { return wide.matches && !reduce.matches && !userPaused && !held && inView && !d.hidden; }
  function restart() {
    w.clearTimeout(timer);
    if (canPlay()) timer = w.setTimeout(function () { go(active + 1, false); restart(); }, DELAY);
  }
  function syncPause() {
    var show = wide.matches && !reduce.matches;
    pauseBtn.hidden = !show;
    pauseBtn.setAttribute('aria-pressed', userPaused ? 'true' : 'false');
    pauseBtn.setAttribute('aria-label', userPaused ? 'Play the screens' : 'Pause the screens');
  }
  pauseBtn.addEventListener('click', function () { userPaused = !userPaused; syncPause(); restart(); });
  fan.addEventListener('mouseenter', function () { held = true; restart(); });
  fan.addEventListener('mouseleave', function () { held = false; restart(); });
  fan.addEventListener('focusin', function () { held = true; restart(); });
  fan.addEventListener('focusout', function (e) { if (!fan.contains(e.relatedTarget)) { held = false; restart(); } });
  d.addEventListener('visibilitychange', restart);
  if ('IntersectionObserver' in w) {
    new IntersectionObserver(function (es) { inView = es[0].isIntersecting; restart(); }, { threshold: 0.5 }).observe(fan);
  }

  // A card behind the front one comes forward when clicked.
  cards.forEach(function (card, i) {
    card.addEventListener('click', function (e) {
      if (!wide.matches || i === active) return;
      e.preventDefault();
      go(i, true);
    });
  });

  // Arrow keys while focus is inside the screens.
  fan.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    go(active + (e.key === 'ArrowRight' ? 1 : -1), true);
    var dot = dots[active];
    if (fan.contains(d.activeElement) && d.activeElement.classList.contains('fan__dot')) dot.focus();
  });

  // Swipe (touch, pen or a mouse drag) when fanned.
  var sx = null, sy = 0, moved = false;
  track.addEventListener('pointerdown', function (e) { if (wide.matches) { sx = e.clientX; sy = e.clientY; moved = false; } });
  track.addEventListener('pointermove', function (e) { if (sx !== null && Math.abs(e.clientX - sx) > 8) moved = true; });
  track.addEventListener('pointerup', function (e) {
    if (sx === null) return;
    var dx = e.clientX - sx, dy = e.clientY - sy;
    sx = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) go(active + (dx < 0 ? 1 : -1), true);
  });
  track.addEventListener('pointercancel', function () { sx = null; });
  // Photos and links would start a native drag instead, which cancels the swipe.
  track.addEventListener('dragstart', function (e) { e.preventDefault(); });
  // A drag should not also count as a click on a link.
  track.addEventListener('click', function (e) { if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; } }, true);

  // The row on phones: the dot follows the card nearest the middle.
  var q = false;
  track.addEventListener('scroll', function () {
    if (wide.matches || q) return;
    q = true;
    w.requestAnimationFrame(function () {
      q = false;
      var mid = track.scrollLeft + track.clientWidth / 2, best = 0, bestD = Infinity;
      cards.forEach(function (c, i) {
        var dd = Math.abs(c.offsetLeft + c.offsetWidth / 2 - mid);
        if (dd < bestD) { bestD = dd; best = i; }
      });
      if (best !== active) { active = best; setDots(); }
    });
  }, { passive: true });

  function mode() {
    fan.classList.toggle('is-fan', wide.matches);
    if (wide.matches) { track.removeAttribute('tabindex'); layout(); }
    else { clearLayout(); track.tabIndex = 0; track.scrollLeft = 0; active = 0; }
    setDots();
    syncPause();
    restart();
  }
  wide.addEventListener('change', mode);
  reduce.addEventListener('change', function () { syncPause(); restart(); });
  mode();
})();

// Sections rise in as they come into view. Skipped when reduced motion is on.
// Sections are fully visible by default; the short entrance animation plays only when a
// section scrolls into view, never right after a resize, so nothing is left half-faded.
(function () {
  var d = document, w = window;
  if (w.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!('IntersectionObserver' in w)) return;
  var resizedAt = 0;
  w.addEventListener('resize', function () { resizedAt = Date.now(); });
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      io.unobserve(e.target);
      if (Date.now() - resizedAt > 500) e.target.classList.add('is-in');
    });
  }, { rootMargin: '0px 0px -6% 0px', threshold: 0.06 });
  d.querySelectorAll('main .section').forEach(function (el) { io.observe(el); });
})();

// The live hall cam card: the newest boxed frame (the clip's poster) from the hall with the most people
// in view, from the app's GET /api/dining/busy (the app answers under /app on this domain). It refreshes
// with the cams, every three minutes. A still rather than the looping clip, so scrolling past the card
// never decodes video. Without an answer, or if the frame doesn't load, the card keeps its plain words.
// When no hall has a recent reading any more (the API leaves out readings over 15 minutes old), or the
// one on show passes 15 minutes while the API can't be reached, the card goes back to its plain words.
(function () {
  var d = document, w = window;
  var card = d.querySelector('[data-cam]');
  if (!card || !w.fetch) return;
  var vis = card.querySelector('.fv-cam');
  var img = card.querySelector('[data-cam-img]');
  var LEVEL = { quiet: 'Quiet', moderate: 'Moderate', busy: 'Busy' };
  var APP = '/app';
  var FRESH = 15 * 60000;
  var shown = null; // the reading on the card
  function q(s) { return card.querySelector(s); }
  var plain = {
    hall: q('[data-cam-hall]').textContent,
    count: q('[data-cam-count]').textContent,
    when: q('[data-cam-when]').textContent,
    link: q('[data-cam-link]').getAttribute('href')
  };
  function ago(iso) {
    var m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
    return m < 1 ? 'just now' : m + ' min ago';
  }
  function when(h) {
    var level = LEVEL[h.level] || '';
    return (level ? level + ' · ' : '') + ago(h.updated_at);
  }
  function show(h) {
    var src = APP + h.poster_url;
    if (img.getAttribute('src') !== src) {
      img.onload = function () { vis.classList.add('has-clip'); };
      img.onerror = function () { vis.classList.remove('has-clip'); img.removeAttribute('src'); };
      img.src = src;
    }
    var level = LEVEL[h.level] || '';
    var people = h.people + (h.people === 1 ? ' person' : ' people') + ' in view';
    shown = h;
    q('[data-cam-live]').hidden = false;
    q('[data-cam-hall]').textContent = h.name;
    q('[data-cam-dot]').setAttribute('data-level', h.level);
    q('[data-cam-count]').textContent = people;
    q('[data-cam-when]').textContent = when(h);
    q('[data-cam-sr]').textContent = ' ' + h.name + ' right now: ' + people + (level ? ', ' + level.toLowerCase() : '') + '.';
    if (h.live_url) q('[data-cam-link]').href = h.live_url;
  }
  function reset() {
    shown = null;
    img.onload = img.onerror = null;
    img.removeAttribute('src');
    vis.classList.remove('has-clip');
    q('[data-cam-live]').hidden = true;
    q('[data-cam-hall]').textContent = plain.hall;
    q('[data-cam-dot]').removeAttribute('data-level');
    q('[data-cam-count]').textContent = plain.count;
    q('[data-cam-when]').textContent = plain.when;
    q('[data-cam-sr]').textContent = '';
    q('[data-cam-link]').setAttribute('href', plain.link);
  }
  function load() {
    if (d.hidden) return;
    w.fetch(APP + '/api/dining/busy', { credentials: 'omit', cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('busy ' + r.status);
        return r.json();
      })
      .then(function (data) {
        var halls = ((data && data.halls) || []).filter(function (h) { return h && h.poster_url && typeof h.people === 'number'; });
        if (!halls.length) { if (shown) reset(); return; }
        halls.sort(function (a, b) { return b.people - a.people; }); // ties keep the hall order
        show(halls[0]);
      })
      .catch(function () {
        if (!shown) return;
        if (Date.now() - Date.parse(shown.updated_at) > FRESH) reset();
        else q('[data-cam-when]').textContent = when(shown);
      });
  }
  load();
  w.setInterval(load, 180000);
  d.addEventListener('visibilitychange', load);
})();

// Recordings marked data-autoplay play (muted, looping) while at least half on screen and pause when
// they leave; never by themselves under reduced motion. A recording inside the fan plays only while
// its card is the front one: behind other cards it would decode frames nobody sees, and Safari paid
// for that on every scroll. The rail's copy has controls and plays when tapped.
(function () {
  var d = document, w = window;
  var vids = [].slice.call(d.querySelectorAll('video[data-autoplay]'));
  if (!vids.length || !('IntersectionObserver' in w)) return;
  var reduce = w.matchMedia('(prefers-reduced-motion: reduce)');
  function wanted(v) {
    if (!v.__inView || reduce.matches || d.hidden) return false;
    var card = v.closest('.fan.is-fan .fan-card');
    return !card || card.classList.contains('is-active');
  }
  function sync() {
    vids.forEach(function (v) {
      if (wanted(v)) {
        if (v.paused) {
          v.preload = 'auto';
          var p = v.play();
          if (p && p.catch) p.catch(function () {});
        }
      } else if (!v.paused) {
        v.pause();
      }
    });
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) { e.target.__inView = e.isIntersecting; });
    sync();
  }, { threshold: 0.5 });
  vids.forEach(function (v) { io.observe(v); });
  d.addEventListener('visibilitychange', sync);
  w.__syncVideos = sync;
})();

// Recordings start as a poster with a play button (.vplay). A tap swaps in the video, so scrolling
// past one never loads or decodes it. One plays at a time, and it pauses once it's scrolled away.
(function () {
  var d = document, w = window;
  var io = 'IntersectionObserver' in w ? new IntersectionObserver(function (entries) {
    entries.forEach(function (e) { if (!e.isIntersecting && !e.target.paused) e.target.pause(); });
  }, { threshold: 0.2 }) : null;
  d.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('.vplay[data-video-src]') : null;
    if (!btn) return;
    var img = btn.querySelector('img');
    var v = d.createElement('video');
    v.muted = true;
    v.loop = true;
    v.controls = true;
    v.playsInline = true;
    v.setAttribute('playsinline', '');
    v.preload = 'auto';
    v.width = +btn.getAttribute('data-video-w') || 720;
    v.height = +btn.getAttribute('data-video-h') || 900;
    if (img) v.poster = img.currentSrc || img.src;
    v.setAttribute('aria-label', btn.getAttribute('data-video-label') || '');
    v.src = btn.getAttribute('data-video-src');
    d.querySelectorAll('video').forEach(function (o) { if (!o.paused) o.pause(); });
    btn.replaceWith(v);
    if (io) io.observe(v);
    v.focus({ preventScroll: true });
    var p = v.play();
    if (p && p.catch) p.catch(function () {});
  });
})();
