'use strict';
/* Tema claro/escuro — o lado JS do tema.css.
 *
 * Carregado no <head> de toda tela, DEPOIS do tema.css e do anti-piscada (a linha inline que
 * aplica data-tema="claro" antes da 1ª pintura). A ordem importa: corTema() lê a variável
 * computada; se rodasse antes do tema.css, voltaria vazio (no JOGA/Multpel foi assim que a
 * grade dos gráficos saiu escura no tema claro).
 *
 * Canvas (Chart.js, desenho próprio) e Leaflet não leem CSS: quem pinta neles pede a cor com
 * corTema('--text-dim') no momento de desenhar e redesenha quando o tema troca — ouvindo o
 * evento 'tema' na window (Tema.aoTrocar(fn)).
 *
 * De propósito NÃO mexe em Chart.defaults: cada gráfico das telas traz a própria cor, e mudar o
 * default mudaria o escuro de quem dependia dele.
 */
(function () {
  var CHAVE = 'rizza:tema';
  var raiz = document.documentElement;

  // corTema('--text-dim') -> '#94a3b8' (o valor como está no tema.css, no tema atual)
  function corTema(nome, fallback) {
    var v = getComputedStyle(raiz).getPropertyValue(nome).trim();
    return v || fallback || '';
  }

  // 'var(--x)' -> a cor; qualquer outra coisa volta igual. Para mapas de cor que servem ao
  // HTML (var funciona) e também ao Leaflet/canvas (precisa do valor).
  corTema.resolver = function (v) {
    var m = /^var\(\s*(--[\w-]+)\s*\)$/.exec(v || '');
    return m ? corTema(m[1]) : v;
  };

  // rgba a partir da tripla do tema: corTemaA('--text-dim-rgb', .08) -> 'rgba(148,163,184,0.08)'
  function corTemaA(nomeRgb, alfa) {
    var t = corTema(nomeRgb).replace(/\s+/g, '');
    return t ? 'rgba(' + t + ',' + alfa + ')' : '';
  }

  function atual() {
    return raiz.dataset.tema === 'claro' ? 'claro' : 'escuro';
  }

  // ── Mapas (Leaflet + basemaps CARTO) ──
  // As telas pedem 'dark_nolabels' / 'dark_only_labels'. Em vez de cada tela saber do tema, o
  // /mapa-config.js (que toda tela de mapa carrega logo depois do leaflet.js) chama
  // Tema.ligarLeaflet(): toda camada CARTO criada dali em diante nasce no tom do tema e é
  // lembrada, para a troca ao vivo só trocar a URL (setUrl), sem refazer o mapa.
  var camadasCarto = [];
  function urlCarto(url) {
    var tom = atual() === 'claro' ? 'light_' : 'dark_';
    return typeof url === 'string' ? url.replace(/(basemaps\.cartocdn\.com\/)(dark_|light_)/, '$1' + tom) : url;
  }
  function ligarLeaflet() {
    if (!window.L || !L.TileLayer || L.TileLayer.__tema) return;
    L.TileLayer.__tema = true;
    var orig = L.TileLayer.prototype.initialize;
    L.TileLayer.prototype.initialize = function (url, opcoes) {
      var carto = typeof url === 'string' && /basemaps\.cartocdn\.com\//.test(url);
      orig.call(this, carto ? urlCarto(url) : url, opcoes);
      if (carto) camadasCarto.push(this);
    };
  }

  function aplicar(t, gravar) {
    if (t === 'claro') raiz.dataset.tema = 'claro';
    else raiz.removeAttribute('data-tema');            // ausência = escuro (o padrão)
    if (gravar !== false) {
      try { localStorage.setItem(CHAVE, atual()); } catch (e) { /* modo privado: sem persistência local */ }
    }
    camadasCarto.forEach(function (c) { try { c.setUrl(urlCarto(c._url)); } catch (e) { /* camada removida */ } });
    try { window.dispatchEvent(new Event('tema')); } catch (e) { /* navegador muito antigo */ }
  }

  window.corTema = corTema;
  window.corTemaA = corTemaA;
  window.Tema = {
    atual: atual,
    aplicar: aplicar,
    ligarLeaflet: ligarLeaflet,
    aoTrocar: function (fn) { window.addEventListener('tema', fn); },
  };
})();
