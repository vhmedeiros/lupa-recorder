"""Página de player servida pelo próprio agente em `GET /v1/player`.

Existe pra tirar o navegador da equação nos testes de campo (sub-etapa 1.7, critério
C2) e servir de referência de como consumir a playlist sintética — o Estúdio da Fase 2
faz o mesmo, só que dentro da Lupa.

Servida **pela mesma origem** que a playlist e os segmentos, então não há CORS nenhum
no caminho. Sem token: a página é HTML estático e não expõe nada; a `.m3u8` que ela
carrega continua exigindo o token de escopo `?e=&s=` na URL.

`hls.js` vem do CDN (o PC que abre a página tem internet; o agente não precisa ter).
"""

from __future__ import annotations

PAGINA_PLAYER = """<!doctype html>
<meta charset="utf-8">
<title>lupa-recorder — player</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { margin: 0; background: #111; color: #3f3; font: 13px/1.5 ui-monospace, monospace; }
  video { width: 100%; height: 64vh; background: #000; display: block; }
  #log { padding: 10px; white-space: pre-wrap; max-height: 30vh; overflow: auto; }
</style>
<video id="v" controls autoplay muted playsinline></video>
<pre id="log"></pre>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
<script>
(function () {
  var L = document.getElementById('log'), v = document.getElementById('v');
  function log(m) {
    L.textContent = '[' + new Date().toTimeString().slice(0, 8) + '] ' + m + '\\n' + L.textContent;
  }
  var q = new URLSearchParams(location.search);
  var src = q.get('src');
  if (!src) {
    var fonte = q.get('fonte'), dia = q.get('dia'), e = q.get('e'), s = q.get('s');
    if (fonte && dia && e && s) src = '/v1/play/' + fonte + '/' + dia + '.m3u8?e=' + e + '&s=' + s;
  }
  if (!src) { log('sem playlist — use ?fonte=SLUG&dia=AAAA-MM-DD&e=...&s=...'); return; }
  log('playlist: ' + src);
  if (typeof Hls === 'undefined') { log('ERRO: hls.js nao carregou (o PC tem internet?)'); return; }
  if (Hls.isSupported()) {
    var h = new Hls();
    h.on(Hls.Events.ERROR, function (_e, d) {
      log('ERRO ' + d.type + ' / ' + d.details + (d.response ? ' http=' + d.response.code : ''));
    });
    h.on(Hls.Events.MANIFEST_PARSED, function (_e, d) {
      log('manifest ok — ' + d.levels.length + ' nivel(is)');
    });
    h.on(Hls.Events.LEVEL_LOADED, function (_e, d) {
      log('level: ' + d.details.fragments.length + ' segs · EVENT=' + d.details.live
          + ' · total=' + Math.round(d.details.totalduration) + 's');
    });
    h.loadSource(src);
    h.attachMedia(v);
  } else if (v.canPlayType('application/vnd.apple.mpegurl')) {
    v.src = src;
    log('player nativo (Safari)');
  } else {
    log('ERRO: navegador sem suporte a HLS');
  }
})();
</script>
"""
