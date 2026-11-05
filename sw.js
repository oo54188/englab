/* EngLab Service Worker — 让应用断网也能打开 */
const VERSION = 'englab-v28';   /* 改过 audio/bg 下的 mp3 必须一起改这里，否则用户拿到旧缓存 */
const CORE = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './apple-touch-icon.png',
  './favicon-32.png',
  './data/daily.json',
  /* 背景听工作材料（v25 共 62 段：美音 bg01–bg50、英/澳/印 bg51–bg62）。
     只把各口音的第一段常驻，保证首次离线切口音也有声音；其余听过的按需缓存。 */
  './audio/bg/bg01.mp3',
  './audio/bg/bg02.mp3',
  './audio/bg/bg03.mp3',
  './audio/bg/bg04.mp3',
  './audio/bg/bg05.mp3',
  './audio/bg/bg06.mp3',
  './audio/bg/bg51.mp3',
  './audio/bg/bg52.mp3',
  './audio/bg/bg53.mp3'
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

  // 素材库（索引 + 正文分片）：网络优先，读过的缓存下来断网可重读
  if (url.pathname.indexOf('data/library/') >= 0) {
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.status === 200) {
            const cp = res.clone();
            const key = req;
            caches.open(VERSION).then((c) => c.put(key, cp));
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // 背景听材料（audio/bg/*.mp3）：缓存优先，听一次就够离线用
  if (url.pathname.indexOf('audio/bg/') >= 0) {
    e.respondWith(
      caches.match(req, { ignoreSearch: true }).then((hit) => {
        if (hit) return hit;
        return fetch(req).then((res) => {
          const save = (r) => {
            if (!r || r.status !== 200) return;
            const cp = r.clone();
            caches.open(VERSION).then((c) => c.put(req, cp));
          };
          if (res.status === 200) save(res);
          // 播放器会带 Range 请求，此时服务器回 206 不能直接存；用整段请求补一份完整缓存
          else if (res.status === 206) {
            fetch(url.origin + url.pathname)
              .then((full) => save(full))
              .catch(() => {});
          }
          return res;
        }).catch(() => caches.match(req, { ignoreSearch: true }));
      })
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
