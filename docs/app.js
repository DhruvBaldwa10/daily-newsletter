(function() {
  var btn = document.getElementById('hb-btn');
  var drawer = document.getElementById('hb-drawer');
  var overlay = document.getElementById('hb-overlay');
  var list = document.getElementById('hb-list');

  var currentDate = window.location.pathname.match(/(\d{4}-\d{2}-\d{2})/);
  currentDate = currentDate ? currentDate[1] : null;

  function toggle(open) {
    btn.classList.toggle('open', open);
    drawer.classList.toggle('open', open);
    overlay.classList.toggle('open', open);
  }

  btn.addEventListener('click', function() {
    toggle(!drawer.classList.contains('open'));
  });
  overlay.addEventListener('click', function() { toggle(false); });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') toggle(false);
  });

  // Try multiple manifest paths (works from /digests/ and from root)
  var paths = ['../manifest.json', 'manifest.json', '/daily-newsletter/manifest.json'];
  var tried = 0;

  function tryNext() {
    if (tried >= paths.length) {
      list.innerHTML = '<div style="padding:16px;color:#a08c76;font-size:0.85rem;">No digests found yet.</div>';
      return;
    }
    fetch(paths[tried] + '?v=' + Date.now())
      .then(function(r) {
        if (!r.ok) throw new Error('not found');
        return r.json();
      })
      .then(function(entries) {
        if (!entries.length) {
          list.innerHTML = '<div style="padding:16px;color:#a08c76;font-size:0.85rem;">No digests found yet.</div>';
          return;
        }
        list.innerHTML = entries.map(function(e) {
          var isCurrent = e.date === currentDate;
          var d = new Date(e.date + 'T00:00:00');
          var label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
          // Figure out correct href depending on current path
          var inDigests = window.location.pathname.indexOf('/digests/') !== -1;
          var href = inDigests ? (e.date + '.html') : ('digests/' + e.date + '.html');
          return '<a href="' + href + '" class="' + (isCurrent ? 'current' : '') + '">'
            + '<div class="hb-date">' + label + '</div>'
            + '<div class="hb-title">' + (e.title || 'Daily Digest') + '</div>'
            + '</a>';
        }).join('');
      })
      .catch(function() {
        tried++;
        tryNext();
      });
  }

  tryNext();
})();
