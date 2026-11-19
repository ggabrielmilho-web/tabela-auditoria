// ── Tabela larga: ir para o lado sem procurar a barra (19/11/2026) ──
// PADRÃO DAS TELAS. Carregado pelo nav-perms.js em todas as telas com menu, então tela nova já
// nasce com ele — não precisa de <script> próprio. O que faz, sozinho, em toda tabela que fica
// dentro de uma caixa com rolagem lateral (overflow-x auto/scroll) e é mais larga que ela:
//
// 1. Barra lateral presa no pé da JANELA. A barra de verdade fica no pé da caixa, e os cartões e
//    filtros de cima empurram a caixa para baixo: num notebook 1366×768 o CRM começava em 883 px e a
//    barra nunca estava na tela (a usuária "não conseguia passar para o lado"). Esta aparece quando a
//    tabela está à vista e o pé dela não; some quando o pé (a barra de verdade) entra na tela.
//    Desenhada (trilho + polegar), não nativa: a nativa some no Chrome com barra sobreposta.
//    Arrastar o polegar, clicar no trilho ou girar a rodinha em cima leva a tabela para o lado.
// 2. 1ª coluna congelada à esquerda ("Congelar painéis" do Excel), com sombra quando rolada. Duas
//    quando a 1ª é só a caixinha de seleção. Não congela: cabeçalho com mais de uma linha ou
//    mesclado (colspan/rowspan), 1ª coluna com mais de 40% da caixa, ou tabela que cabe.
//    A célula congelada precisa de fundo opaco (senão o resto passa visível por baixo): o fundo
//    é o da caixa, e cada tipo de linha pintada pela tela (tr.sel, tr.grupo…) ganha a mesma cor já
//    composta sobre ele. Passar o mouse/selecionada: realce genérico.
// 3. Barras de rolagem das caixas de tabela mais grossas e visíveis.
//
// Desligar numa tabela ou caixa: data-tabela-larga="0" (tudo) ou data-congelar="0" (só o
// congelamento). Forçar: data-congelar="1" | "2". Celular/tablet (hover: none): sem a barra
// flutuante (o dedo rola a tabela). A tela pode pedir uma reavaliação com TabelaLarga.atualizar().
(function () {
  if (window.TabelaLarga) return;

  var CSS = [
    '.tl-caixa{scrollbar-color:#52637f var(--surface,#111827);}',
    '.tl-caixa::-webkit-scrollbar{height:14px;width:12px;}',
    '.tl-caixa::-webkit-scrollbar-track{background:var(--surface,#111827);}',
    '.tl-caixa::-webkit-scrollbar-thumb{background:#52637f;border-radius:8px;border:3px solid var(--surface,#111827);}',
    '.tl-caixa::-webkit-scrollbar-thumb:hover{background:var(--accent,#38bdf8);}',
    '.tl-caixa::-webkit-scrollbar-corner{background:var(--surface,#111827);}',
    // barra flutuante
    '.tl-barra{position:fixed;bottom:0;z-index:900;box-sizing:border-box;padding:6px 10px;background:var(--surface,#111827);',
    'border:1px solid var(--border,#1e293b);border-bottom:none;border-radius:10px 10px 0 0;box-shadow:0 -6px 14px rgba(2,6,15,.55);user-select:none;}',
    '.tl-barra[hidden]{display:none;}',
    '.tl-trilho{position:relative;height:12px;border-radius:7px;background:#0b1220;cursor:pointer;touch-action:none;}',
    '.tl-polegar{position:absolute;top:0;bottom:0;left:0;min-width:48px;border-radius:7px;background:#52637f;cursor:grab;transition:background .12s;}',
    '.tl-polegar:hover,.tl-barra.tl-arrastando .tl-polegar{background:var(--accent,#38bdf8);}',
    '.tl-barra.tl-arrastando,.tl-barra.tl-arrastando .tl-polegar{cursor:grabbing;}',
    // coluna congelada (1 ou 2)
    'table.tl-c1>thead>tr>:first-child,table.tl-c1>tbody>tr>:first-child,table.tl-c1>tfoot>tr>:first-child{position:sticky;left:0;}',
    'table.tl-c2>thead>tr>:nth-child(2),table.tl-c2>tbody>tr>:nth-child(2),table.tl-c2>tfoot>tr>:nth-child(2){position:sticky;left:var(--tl-l2,40px);}',
    // a célula congelada (z 1) não pode passar por cima dos outros cabeçalhos fixos ao rolar para baixo
    'table.tl-c1>thead>tr>*{z-index:var(--tl-zth,2);}',
    'table.tl-c1>tbody>tr>:first-child,table.tl-c1>tfoot>tr>:first-child,table.tl-c2>tbody>tr>:nth-child(2),table.tl-c2>tfoot>tr>:nth-child(2)',
    '{z-index:1;background-color:var(--tl-fundo,#111827)!important;}',
    'table.tl-c1>thead>tr>:first-child,table.tl-c2>thead>tr>:nth-child(2){z-index:var(--tl-zcab,4)!important;background-color:var(--tl-fundo-cab,var(--tl-fundo,#111827))!important;}',
    'table.tl-c1>tbody>tr:hover>:first-child,table.tl-c2>tbody>tr:hover>:nth-child(2)',
    '{background-image:linear-gradient(rgba(148,163,184,.09),rgba(148,163,184,.09))!important;}',
    // sombra na borda da parte congelada quando a tabela andou: td com ::after, th com box-shadow
    // (o th::after é a seta de ordenação em algumas telas)
    'table.tl-c1:not(.tl-c2)>tbody>tr>:first-child::after,table.tl-c2>tbody>tr>:nth-child(2)::after',
    '{content:"";position:absolute;top:0;bottom:0;right:-8px;width:8px;pointer-events:none;',
    'background:linear-gradient(to right,rgba(2,6,15,.45),transparent);opacity:0;transition:opacity .15s;}',
    '.tl-rolado table.tl-c1:not(.tl-c2)>tbody>tr>:first-child::after,.tl-rolado table.tl-c2>tbody>tr>:nth-child(2)::after{opacity:1;}',
    '.tl-rolado table.tl-c1:not(.tl-c2)>thead>tr>:first-child,.tl-rolado table.tl-c2>thead>tr>:nth-child(2){box-shadow:6px 0 8px -6px rgba(2,6,15,.6);}'
  ].join('');

  var TOQUE = window.matchMedia && window.matchMedia('(hover: none)').matches;
  var caixas = [], daCaixa = new WeakMap(), ids = 0;
  var barra = null, trilho = null, polegar = null, atual = null, arraste = null, pedido = false, varrerPedido = true;
  var estiloLinhas = null, regrasLinhas = {};

  function injetarCss() {
    if (document.getElementById('tl-css')) return;
    var st = document.createElement('style'); st.id = 'tl-css'; st.textContent = CSS;
    document.head.appendChild(st);
    estiloLinhas = document.createElement('style'); estiloLinhas.id = 'tl-css-linhas';
    document.head.appendChild(estiloLinhas);
  }

  // ── cores ──────────────────────────────────────────────────────────────────
  function rgba(txt) {
    var m = /rgba?\(([^)]+)\)/.exec(txt || '');
    if (!m) return null;
    var p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  function txt(c) { return 'rgb(' + Math.round(c.r) + ',' + Math.round(c.g) + ',' + Math.round(c.b) + ')'; }
  function sobre(c, base) {   // c (pode ser translúcida) composta sobre base (opaca)
    return { r: c.r * c.a + base.r * (1 - c.a), g: c.g * c.a + base.g * (1 - c.a), b: c.b * c.a + base.b * (1 - c.a), a: 1 };
  }
  // o fundo opaco atrás de um elemento: sobe compondo as camadas translúcidas
  function fundoAtras(el) {
    var camadas = [];
    for (; el && el.nodeType === 1; el = el.parentElement) {
      var c = rgba(getComputedStyle(el).backgroundColor);
      if (c && c.a > 0) { camadas.push(c); if (c.a >= 0.99) break; }
    }
    var base = { r: 10, g: 14, b: 23, a: 1 };   // --bg das telas
    if (camadas.length && camadas[camadas.length - 1].a >= 0.99) base = camadas.pop();
    for (var i = camadas.length - 1; i >= 0; i--) base = sobre(camadas[i], base);
    return base;
  }

  // ── caixas e tabelas ───────────────────────────────────────────────────────
  function rolaDeLado(el) { var o = getComputedStyle(el).overflowX; return o === 'auto' || o === 'scroll'; }
  function caixaDe(t) {
    if (daCaixa.has(t)) return daCaixa.get(t);
    var c = null;
    for (var el = t.parentElement; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
      if (rolaDeLado(el)) { c = el; break; }
    }
    if (c && c.closest('[data-tabela-larga="0"]')) c = null;
    daCaixa.set(t, c);
    return c;
  }
  function varrer() {
    var ts = document.getElementsByTagName('table');
    for (var i = 0; i < ts.length; i++) {
      var t = ts[i];
      if (t.getAttribute('data-tabela-larga') === '0') continue;
      var c = caixaDe(t);
      if (!c) continue;
      if (caixas.indexOf(c) < 0) {
        caixas.push(c);
        c.classList.add('tl-caixa');
        c.addEventListener('scroll', function () { agendar(); }, { passive: true });
        if (ro) ro.observe(c);
      }
      if (ro && !t.__tl) { t.__tl = true; ro.observe(t); }
    }
    caixas = caixas.filter(function (c) { return document.documentElement.contains(c); });
  }

  // Quantas colunas congelar (0, 1 ou 2) — ver as regras no topo
  function quantas(t, c) {
    var forc = t.getAttribute('data-congelar') || c.getAttribute('data-congelar');
    if (forc === '0') return 0;
    if (c.scrollWidth <= c.clientWidth + 1 && !forc) return 0;
    var cab = t.tHead;
    if (cab) {
      if (cab.rows.length > 1) return forc ? +forc : 0;
      var hc = cab.rows.length ? cab.rows[0].cells : [];
      for (var i = 0; i < hc.length; i++) if (hc[i].colSpan > 1 || hc[i].rowSpan > 1) return forc ? +forc : 0;
    }
    var ref = cab && cab.rows.length ? cab.rows[0] : null;
    if (!ref) {
      var b = t.tBodies[0];
      for (var j = 0; b && j < b.rows.length && j < 20; j++) if (b.rows[j].cells.length > 1 && b.rows[j].cells[0].colSpan === 1) { ref = b.rows[j]; break; }
    }
    if (!ref || ref.cells.length < 2) return 0;
    var w1 = ref.cells[0].getBoundingClientRect().width, w2 = ref.cells[1].getBoundingClientRect().width;
    if (forc) return Math.max(0, Math.min(2, +forc || 0));
    if (w1 > c.clientWidth * 0.4) return 0;
    if (w1 < 56 && w1 + w2 <= c.clientWidth * 0.45) return 2;
    return 1;
  }

  // fundo de cada tipo de linha pintada pela tela (tr.sel, tr.grupo…), já composto sobre o fundo
  // da caixa, na(s) célula(s) congelada(s). Uma regra por (tabela, classe da linha, nº congeladas).
  function regraLinhas(t, fundo) {
    var dois = t.classList.contains('tl-c2'), b = t.tBodies, vistos = {}, n = 0, novas = '';
    for (var i = 0; i < b.length; i++) {
      var rs = b[i].rows;
      for (var j = 0; j < rs.length && n < 30; j++) {
        var cls = (rs[j].className || '').trim();
        if (!cls || vistos[cls]) continue;
        vistos[cls] = true; n++;
        var chave = t.__tlId + '|' + cls + '|' + (dois ? 2 : 1);
        if (regrasLinhas[chave] !== undefined) continue;
        var cel = rs[j].cells[dois ? 2 : 1] || rs[j].cells[0];
        var c = cel ? rgba(getComputedStyle(cel).backgroundColor) : null;
        var sel = 'table[data-tl-id="' + t.__tlId + '"]>tbody>tr.' + cls.split(/\s+/).map(CSS_ESC).join('.');
        var alvo = sel + '>:first-child' + (dois ? ',' + sel + '>:nth-child(2)' : '');
        var r = '';
        if (c && c.a > 0) r = alvo + '{background-color:' + txt(sobre(c, fundo)) + '!important;}';
        else if (/(^|\s)(sel|selected|selecionada|marcada)(\s|$)/.test(cls))
          r = alvo + '{background-image:linear-gradient(rgba(56,189,248,.08),rgba(56,189,248,.08))!important;}';
        regrasLinhas[chave] = r;
        if (r) novas += r + '\n';
      }
    }
    if (novas) estiloLinhas.textContent += novas;
  }
  var CSS_ESC = (window.CSS && CSS.escape) ? CSS.escape : function (s) { return s.replace(/[^a-zA-Z0-9_-]/g, '\\$&'); };

  // grava classe/variável só quando muda: o MutationObserver não pode ver as nossas próprias marcas
  function marcar(el, cls, liga) { if (el.classList.contains(cls) !== !!liga) el.classList.toggle(cls, !!liga); }
  function setVar(el, nome, val) { val = String(val); if (el.style.getPropertyValue(nome) !== val) el.style.setProperty(nome, val); }

  function congelar(c) {
    var ts = c.getElementsByTagName('table');
    for (var i = 0; i < ts.length; i++) {
      var t = ts[i];
      if (caixaDe(t) !== c || t.getAttribute('data-tabela-larga') === '0') continue;
      var n = c.offsetParent === null && getComputedStyle(c).position !== 'fixed' ? 0 : quantas(t, c);
      marcar(t, 'tl-c1', n >= 1);
      marcar(t, 'tl-c2', n === 2);
      if (!n) continue;
      if (!t.__tlId) { t.__tlId = String(++ids); t.setAttribute('data-tl-id', t.__tlId); }
      var fundo = fundoAtras(c);
      setVar(t, '--tl-fundo', txt(fundo));
      var cab = t.tHead && t.tHead.rows[0];
      if (cab && cab.cells[0]) {
        var cc = rgba(getComputedStyle(cab.cells[0]).backgroundColor);
        setVar(t, '--tl-fundo-cab', txt(cc && cc.a > 0 ? sobre(cc, fundo) : fundo));
        // o cabeçalho congelado fica acima dos outros cabeçalhos (que podem ser sticky com z próprio)
        // (lê o z da 2ª célula: a 1ª já está com o nosso)
        var zth = 2;
        for (var k = 1; k < cab.cells.length; k++) { var zk = parseInt(getComputedStyle(cab.cells[k]).zIndex, 10); if (zk > zth) zth = zk; }
        setVar(t, '--tl-zth', zth); setVar(t, '--tl-zcab', zth + 1);
      }
      if (n === 2) {
        var r0 = cab || (t.tBodies[0] && t.tBodies[0].rows[0]);
        if (r0 && r0.cells[0]) setVar(t, '--tl-l2', Math.round(r0.cells[0].getBoundingClientRect().width) + 'px');
      }
      regraLinhas(t, fundo);
    }
    marcar(c, 'tl-rolado', c.scrollLeft > 0);
  }

  // ── barra flutuante ────────────────────────────────────────────────────────
  function criarBarra() {
    if (barra || TOQUE) return;
    barra = document.createElement('div');
    barra.className = 'tl-barra'; barra.hidden = true;
    barra.title = 'Arraste para o lado — ou gire a rodinha do mouse aqui (na tabela: Shift + rodinha)';
    barra.innerHTML = '<div class="tl-trilho"><div class="tl-polegar"></div></div>';
    document.body.appendChild(barra);
    trilho = barra.firstChild; polegar = trilho.firstChild;
    trilho.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0 || !atual) return;
      ev.preventDefault();
      var g = geo(atual);
      if (ev.target !== polegar) {
        var x = ev.clientX - trilho.getBoundingClientRect().left - g.pw / 2;
        atual.scrollLeft = Math.max(0, Math.min(g.livre, x)) / g.livre * g.max;
      }
      arraste = { x: ev.clientX, inicio: atual.scrollLeft, fator: g.max / g.livre, caixa: atual };
      trilho.setPointerCapture(ev.pointerId); barra.classList.add('tl-arrastando');
    });
    trilho.addEventListener('pointermove', function (ev) {
      if (arraste) arraste.caixa.scrollLeft = arraste.inicio + (ev.clientX - arraste.x) * arraste.fator;
    });
    function soltar() { arraste = null; barra.classList.remove('tl-arrastando'); }
    trilho.addEventListener('pointerup', soltar);
    trilho.addEventListener('pointercancel', soltar);
    barra.addEventListener('wheel', function (ev) {
      if (!atual) return;
      var d = Math.abs(ev.deltaY) > Math.abs(ev.deltaX) ? ev.deltaY : ev.deltaX;
      if (!d) return;
      ev.preventDefault(); atual.scrollLeft += d;
    }, { passive: false });
  }
  function geo(c) {
    var max = Math.max(0, c.scrollWidth - c.clientWidth), tw = trilho.clientWidth;
    var pw = Math.min(tw, Math.max(48, tw * c.clientWidth / Math.max(1, c.scrollWidth)));
    return { max: max, tw: tw, pw: pw, livre: Math.max(1, tw - pw) };
  }
  function posicionarBarra() {
    if (!barra) return;
    var alto = window.innerHeight, largo = document.documentElement.clientWidth, melhor = null, area = 0;
    if (!arraste) {
      for (var i = 0; i < caixas.length; i++) {
        var c = caixas[i];
        if (c.scrollWidth <= c.clientWidth + 1 || c.offsetParent === null) continue;
        var r = c.getBoundingClientRect();
        // à vista = ~3 linhas na tela; o pé da caixa (a barra de verdade) abaixo da janela
        if (r.top >= alto - 120 || r.bottom <= alto + 2 || r.right <= 0 || r.left >= largo) continue;
        var a = (Math.min(r.bottom, alto) - Math.max(r.top, 0)) * (Math.min(r.right, largo) - Math.max(r.left, 0));
        if (a > area) { area = a; melhor = c; }
      }
      atual = melhor;
    }
    if (!atual) { barra.hidden = true; return; }
    var rb = atual.getBoundingClientRect();
    var esq = Math.max(rb.left, 0), dir = Math.min(rb.right, largo);
    barra.style.left = esq + 'px'; barra.style.width = Math.max(80, dir - esq) + 'px';
    barra.hidden = false;
    var g = geo(atual);
    polegar.style.width = g.pw + 'px';
    polegar.style.transform = 'translateX(' + (g.max ? atual.scrollLeft / g.max * (g.tw - g.pw) : 0) + 'px)';
  }

  // ── laço ───────────────────────────────────────────────────────────────────
  function atualizar() {
    pedido = false;
    var tudo = varrerPedido; varrerPedido = false;
    if (tudo) varrer();
    for (var i = 0; i < caixas.length; i++) {
      if (tudo) congelar(caixas[i]);
      else marcar(caixas[i], 'tl-rolado', caixas[i].scrollLeft > 0);
    }
    posicionarBarra();
  }
  // agendar() = só a barra e a sombra (rolagem); agendar(true) = varre e recalcula (a tela mudou)
  function agendar(tudo) {
    if (tudo) varrerPedido = true;
    if (pedido) return;
    pedido = true;
    requestAnimationFrame(atualizar);
  }
  var ro = window.ResizeObserver ? new ResizeObserver(function () { agendar(true); }) : null;

  function iniciar() {
    injetarCss(); criarBarra();
    // rolar qualquer coisa (a página ou um painel com rolagem própria) reposiciona a barra
    window.addEventListener('scroll', function () { agendar(); }, { passive: true, capture: true });
    window.addEventListener('resize', function () { agendar(true); });
    // tabela nova ou redesenhada (as telas montam por innerHTML), caixa que aparece (hidden/display)
    // tira as nossas marcas (classes tl-*, variáveis --tl-*) para comparar o antes e o agora
    var LIMPA = /--tl-[\w-]+\s*:[^;]*;?|(^|\s)tl-[\w-]+/g;
    new MutationObserver(function (ms) {
      for (var i = 0; i < ms.length; i++) {
        var m = ms[i], alvo = m.target;
        if (barra && (alvo === barra || barra.contains(alvo))) continue;
        if (m.type === 'attributes' && (alvo.__tlId || caixas.indexOf(alvo) >= 0) && m.attributeName !== 'hidden') {
          // classe/estilo de uma tabela ou caixa nossa: só conta se não foi a nossa marca
          var antes = (m.oldValue || '').replace(LIMPA, '').replace(/\s+/g, ' ').trim();
          var agora = (alvo.getAttribute(m.attributeName) || '').replace(LIMPA, '').replace(/\s+/g, ' ').trim();
          if (antes === agora) continue;
        }
        agendar(true); return;
      }
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeOldValue: true, attributeFilter: ['hidden', 'style', 'class'] });
    agendar(true);
  }

  window.TabelaLarga = { atualizar: function () { daCaixa = new WeakMap(); agendar(true); } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
