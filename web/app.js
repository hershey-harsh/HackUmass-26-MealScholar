/* Progressive enhancement only: the page works without this file. */
(function () {
  var d = document, w = window;
  d.documentElement.classList.add('js');
  var reduce = w.matchMedia('(prefers-reduced-motion: reduce)');

  // Header hairline, and the phone dock: shown once the hero button has
  // scrolled away, hidden again when the closing call to action comes into view.
  var header = d.querySelector('.site-header');
  var dock = d.querySelector('.dock');
  var heroCta = d.querySelector('[data-hero-cta]');
  var stop = d.querySelector('[data-dock-stop]');
  var queued = false;
  function onScroll() {
    queued = false;
    if (header) header.classList.toggle('is-scrolled', w.scrollY > 8);
    if (dock && heroCta && stop) {
      dock.classList.toggle('is-shown', heroCta.getBoundingClientRect().bottom < 0 &&
        stop.getBoundingClientRect().top > w.innerHeight - 40);
    }
  }
  function queue() { if (!queued) { queued = true; w.requestAnimationFrame(onScroll); } }
  w.addEventListener('scroll', queue, { passive: true });
  w.addEventListener('resize', queue);
  onScroll();

  // Phone menu: close after a pick, on Escape, or on a tap outside
  // (that tap only closes the menu, so it can't open a link underneath).
  var menu = d.querySelector('.menu');
  if (menu) {
    menu.addEventListener('click', function (e) { if (e.target.closest('a')) menu.open = false; });
    d.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && menu.open) { menu.open = false; menu.querySelector('summary').focus(); }
    });
    d.addEventListener('click', function (e) {
      if (menu.open && !menu.contains(e.target)) { e.preventDefault(); e.stopPropagation(); menu.open = false; }
    }, true);
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
    build();
  });
})();

// Page backdrop: --p runs from 0 at the top to 1 at the bottom for the scroll effects,
// and sections rise in as they come into view. Skipped when reduced motion is on.
(function () {
  var d = document, w = window, root = d.documentElement;
  if (w.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var queued = false;
  function set() {
    queued = false;
    var max = root.scrollHeight - w.innerHeight;
    root.style.setProperty('--p', max > 0 ? Math.min(1, Math.max(0, w.scrollY / max)).toFixed(4) : '0');
  }
  function queue() { if (!queued) { queued = true; w.requestAnimationFrame(set); } }
  w.addEventListener('scroll', queue, { passive: true });
  w.addEventListener('resize', queue);
  set();
  if (!('IntersectionObserver' in w)) return;
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target); }
    });
  }, { rootMargin: '0px 0px -6% 0px', threshold: 0.06 });
  d.querySelectorAll('main > section:not(.hero-shell)').forEach(function (el) {
    el.classList.add('reveal');
    io.observe(el);
  });
})();
