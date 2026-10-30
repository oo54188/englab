/* EngLab Service Worker — 让应用断网也能打开 */
const VERSION = 'englab-v4';
const CORE = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './apple-touch-icon.png',
  './favicon-32.png',
  './data/daily.json'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(VERSION).then((c) =>
      Promise.allSettled(CORE.map((u) => c.add(new Request(u, { cache: 'reload' }))))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // 离线语音包：直连网络，不进 Cache（体积大，且应用会把内容另存进 IndexedDB）
  if (url.pathname.indexOf('voice-pack.json') >= 0) {
    e.respondWith(fetch(req).catch(() => new Response('{}', { status: 503, headers: { 'Content-Type': 'application/json' } })));
    return;
  }

  // 每日素材数据：网络优先（保证每天拿到新内容），断网回退上次缓存
  if (url.pathname.indexOf('data/daily.json') >= 0) {
    e.respondWith(
      fetch(req)
        .then((res) => {
          const cp = res.clone();
          caches.open(VERSION).then((c) => c.put('./data/daily.json', cp));
          return res;
        })
        .catch(() => caches.match('./data/daily.json'))
    );
    return;
  }

  // 页面导航：先网络（保证拿到新版），断网/失败回退缓存
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req)
        .then((res) => {
          const cp = res.clone();
          caches.open(VERSION).then((c) => c.put('./index.html', cp));
          return res;
        })
        .catch(() => caches.match('./index.html').then((r) => r || caches.match('./')))
    );
    return;
  }

  // 静态资源：缓存优先
  e.respondWith(
    caches.match(req).then((hit) => {
      if (hit) return hit;
      return fetch(req).then((res) => {
        if (res && res.status === 200 && res.type === 'basic') {
          const cp = res.clone();
          caches.open(VERSION).then((c) => c.put(req, cp));
        }
        return res;
      }).catch(() => hit);
    })
  );
});
