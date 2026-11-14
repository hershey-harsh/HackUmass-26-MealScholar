// Add to Home Screen.
// Android/Chrome: the browser's own install prompt, caught from beforeinstallprompt.
// iPhone/iPad: Safari has no prompt, so a sheet shows the Share > Add to Home Screen steps.
// Opens by itself when the page is loaded with ?install=1 (the landing page's button) and
// never again once the app is already running from the Home Screen.
(function () {
  var d = document, w = window;
  var standalone = w.matchMedia('(display-mode: standalone)').matches || w.navigator.standalone === true;
  var ua = w.navigator.userAgent || '';
  var iOS = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && w.navigator.maxTouchPoints > 1);
  var deferred = null;

  w.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferred = e;
    d.querySelectorAll('[data-install]').forEach(function (b) { b.hidden = false; });
  });
  w.addEventListener('appinstalled', function () { deferred = null; close(); });

  function sheet() {
    var el = d.getElementById('install-sheet');
    if (el) return el;
    el = d.createElement('div');
    el.id = 'install-sheet';
    el.className = 'install';
    el.hidden = true;
    el.innerHTML =
      '<div class="install__backdrop" data-install-close></div>' +
      '<div class="install__card" role="dialog" aria-modal="true" aria-labelledby="install-title">' +
        '<div class="install__grab" aria-hidden="true"></div>' +
        '<div class="install__head"><img class="install__icon" src="icons/icon-192.png" alt="" width="56" height="56">' +
          '<div><h2 id="install-title">Add MealScholar to your Home Screen</h2>' +
          '<p>Opens like an app, with no app store download.</p></div></div>' +
        '<ol class="install__steps">' +
          (iOS
            ? '<li><span class="install__num">1</span><span>Tap the <b>Share</b> button <svg class="install__glyph" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5v11M8 7.5l4-4 4 4M7 11H6a1.5 1.5 0 0 0-1.5 1.5v7A1.5 1.5 0 0 0 6 21h12a1.5 1.5 0 0 0 1.5-1.5v-7A1.5 1.5 0 0 0 18 11h-1" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg> at the bottom of Safari.</span></li>' +
              '<li><span class="install__num">2</span><span>Scroll down and tap <b>Add to Home Screen</b>.</span></li>' +
              '<li><span class="install__num">3</span><span>Tap <b>Add</b>. MealScholar appears with your other apps.</span></li>'
            : '<li><span class="install__num">1</span><span>Open the browser <b>menu</b> (the three dots).</span></li>' +
              '<li><span class="install__num">2</span><span>Tap <b>Add to Home screen</b> or <b>Install app</b>.</span></li>' +
              '<li><span class="install__num">3</span><span>Tap <b>Add</b>. MealScholar appears with your other apps.</span></li>') +
        '</ol>' +
        '<div class="install__actions">' +
          (deferred ? '<button type="button" class="btn btn--primary" data-install-now>Install now</button>' : '') +
          '<button type="button" class="btn" data-install-close>Not now</button>' +
        '</div>' +
      '</div>';
    d.body.appendChild(el);
    el.addEventListener('click', function (e) {
      if (e.target.closest('[data-install-close]')) close();
      if (e.target.closest('[data-install-now]')) promptNow();
    });
    return el;
  }

  function open() {
    if (standalone) return;
    var el = sheet();
    if (deferred && !iOS) { promptNow(); return; }
    el.hidden = false;
    d.documentElement.classList.add('install-open');
    var first = el.querySelector('button');
    if (first) first.focus();
  }
  function close() {
    var el = d.getElementById('install-sheet');
    if (el) el.hidden = true;
    d.documentElement.classList.remove('install-open');
  }
  function promptNow() {
    if (!deferred) { open(); return; }
    var p = deferred; deferred = null;
    p.prompt();
    p.userChoice.then(function () { close(); }).catch(close);
  }

  d.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
  d.addEventListener('click', function (e) {
    var b = e.target.closest('[data-install]');
    if (b) { e.preventDefault(); open(); }
  });

  // From the landing page's "Add to Home Screen" button.
  if (w.__wantInstall && !standalone) w.setTimeout(open, 350);
  w.MealScholarInstall = { open: open, close: close };
})();
