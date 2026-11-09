// Service worker do app de canhotos (escopo /motorista/).
// Só guarda a "casca" para o app abrir sem sinal: a página, o manifesto e os ícones. Rede primeiro
// (assim uma versão nova chega sozinha); sem rede, a cópia guardada. As chamadas /motorista/api/ e
// qualquer POST passam direto: a fila de fotos é da própria página (IndexedDB), não daqui.
var VERSAO = 'canhotos-v1';
var CASCA = ['/motorista/', '/motorista/manifest.webmanifest', '/motorista/icone-192.png', '/motorista/icone-512.png'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(VERSAO).then(function (c) { return c.addAll(CASCA); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== VERSAO; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  var u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== self.location.origin || u.pathname.indexOf('/motorista/') !== 0 ||
      u.pathname.indexOf('/motorista/api/') === 0) return;
  e.respondWith(fetch(e.request).then(function (r) {
    if (r && r.ok) { var copia = r.clone(); caches.open(VERSAO).then(function (c) { c.put(e.request, copia); }); }
    return r;
  }).catch(function () {
    return caches.match(e.request, { ignoreSearch: true }).then(function (r) { return r || caches.match('/motorista/'); });
  }));
});
