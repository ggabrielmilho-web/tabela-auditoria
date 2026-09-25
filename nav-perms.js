// ── Navegação lateral centralizada + gating por permissão de aba ──
// Fonte ÚNICA de verdade das abas do sistema. Cada página só declara um placeholder:
//   <span data-nav-menu data-nav-active="dre"></span>
// e este script monta a BARRA LATERAL com as abas que o usuário pode ver (admin enxerga
// todas por bypass). Aba nova aparece sozinha em todas as telas e no /inicio.
//
// Por que lateral (24/09/2026, trazida da VitrineTransporte): a barra de cima empilhava
// 19 botões em duas ou três linhas e comia ~120 px antes de qualquer conteúdo. A lateral é
// injetada como primeiro filho do <body> e empurra a página com `margin-left` — não
// `padding-left`, que apagaria o padding próprio de telas como o PGR. O `body` das telas não
// tem layout próprio (o espaçamento mora no `.dashboard`), então nenhuma precisou mudar.
//
// O CSS da barra também mora aqui, injetado, e não num .css à parte: são ~25 HTML e um
// <link> esquecido deixaria uma tela com a barra crua. As cores são as da própria Rizza
// (`--bg`, `--accent`… de cada tela), com o valor atual como reserva.
//
// Recolher: o botão « no topo vira a barra numa faixa de ícones. A escolha fica no
// localStorage, então vale para todas as telas; sem storage (aba anônima), abre aberta.
// Ao recolher/abrir dispara `resize`, para Leaflet e Chart.js recalcularem a largura.
(function () {
  // Ordem canônica das abas. `page` = chave de permissão (paginas_permitidas);
  // `id` = identificador da aba ativa; `label` = emoji + texto (o /inicio separa os dois).
  var ABAS = [
    // Início não é aba concedível: é a porta de entrada, visível para quem está logado.
    // Fica na barra de todas as telas para haver caminho de volta ao menu.
    { id: 'inicio',        page: null,            href: '/inicio',            label: '🏠 Início', sempre: true, grupo: null },
    { id: 'auditoria',     page: 'auditoria',     href: '/',                  label: '⚡ Auditoria' },
    { id: 'embarques',     page: 'embarques',     href: '/embarques',         label: '🚚 Embarques' },
    { id: 'mapa',          page: 'embarques',     href: '/embarques/mapa',    label: '🗺️ Mapa' },
    // Ordens de coleta (SSW 157). Mesma permissão de Embarques: quem lança carga é quem
    // acompanha a ordem que a origina. Vive de `embarques_programacao`, que a fita alimenta.
    { id: 'ordens',        page: 'embarques',     href: '/embarques/ordens',  label: '📥 Coletas' },
    // Junto da família de rastreamento, não perto do DRE: é segurança
    // operacional, não financeiro.
    { id: 'pgr',           page: 'pgr',           href: '/pgr',               label: '🚦 PGR' },
    // Escala motorista × placa que o RH manda à empresa de controle de jornada.
    // ⏱ é bloco antigo (glifo no Windows 10), pela mesma razão do 🚛 abaixo.
    { id: 'jornada',       page: 'jornada',       href: '/jornada',           label: '⏱ Jornada' },
    // Conferência CTRB × manifesto × CIOT (pedido do diretor). 📋 é bloco antigo.
    { id: 'ciot',          page: 'ciot',          href: '/ciot',              label: '📋 CIOT' },
    { id: 'tarifas',       page: 'tarifas',       href: '/tarifas',           label: '🏷️ Tarifas' },
    { id: 'reuniao',       page: 'reuniao',       href: '/reuniao',           label: '🎙 Reunião' },
    { id: 'contratos',     page: 'contratos',     href: '/contratos',         label: '📝 Contratos' },
    { id: 'dre',           page: 'dre',           href: '/dre',               label: '📈 DRE' },
    { id: 'conhecimentos', page: 'conhecimentos', href: '/dre/conhecimentos', label: '📦 Conhecimentos' },
    { id: 'despesas',      page: 'despesas',      href: '/dre/despesas',      label: '💰 Despesas' },
    { id: 'faturamento',   page: 'faturamento',   href: '/faturamento',       label: '📊 Faturamento' },
    // Fecha a família financeira. A contadora externa recebe SÓ esta aba.
    { id: 'contabil',      page: 'contabil',      href: '/contabil',          label: '📒 Contábil' },
    // 🚛 (carreta articulada) e não 🚚 (baú, que fica com Embarques): esta aba analisa
    // cavalo + carreta. Nada de 🛞/🛻 — são do Emoji 13/14 e o Windows 10 não tem o
    // glifo, sai quadradinho vazio. Emoji desta lista: só de blocos antigos.
    { id: 'veiculos',      page: 'veiculos',      href: '/veiculos',          label: '🚛 Veículos' },
    // 🌱 e não ♻/🌍: é inventário de emissão, não reciclagem nem "planeta".
    // Emoji de bloco antigo, pela mesma razão do 🚛 acima (glifo no Windows 10).
    { id: 'verda',         page: 'verda',         href: '/verda',             label: '🌱 Verda' },
    { id: 'admin',         page: 'admin',         href: '/admin',             label: '⚙ Admin' },
    // Relatório de uso (/uso). Era tela escondida; desde 25/09/26 aparece no menu para quem é
    // role=admin — `page: 'admin'` faz o `podeVer` negar aos demais, e o servidor segue
    // protegendo a rota com admin_required (menu escondido nunca foi a proteção).
    { id: 'uso',           page: 'admin',         href: '/uso',               label: '👥 Acessos' }
  ];

  // Grupos da barra E dos cards do /inicio — uma lista só, para os dois não divergirem
  // no dia em que alguém mover uma aba de grupo.
  var GRUPOS = [
    { nome: 'Operação',   abas: ['embarques', 'ordens', 'mapa', 'pgr', 'jornada', 'ciot', 'veiculos', 'verda'] },
    { nome: 'Comercial',  abas: ['tarifas', 'faturamento', 'contratos'] },
    { nome: 'Financeiro', abas: ['auditoria', 'dre', 'despesas', 'conhecimentos', 'contabil'] },
    { nome: 'Sistema',    abas: ['reuniao', 'admin', 'uso'] }
  ];

  var CHAVE_RECOLHIDA = 'rizza.nav.recolhida';

  var CSS = [
    ':root { --nav-w: 232px; }',
    'html.nav-recolhida { --nav-w: 60px; }',
    // O fundo do <html> cobre a faixa da margem enquanto a barra anima.
    'html.com-nav { background: var(--bg, #0a0e17); }',
    'body.tem-sidebar { margin-left: var(--nav-w); transition: margin-left .18s ease; }',
    '.side-nav { position: fixed; top: 0; left: 0; bottom: 0; width: var(--nav-w);',
    '  box-sizing: border-box; background: #070a12; border-right: 1px solid var(--border, #1e293b);',
    '  padding: 14px 12px 12px; display: flex; flex-direction: column; gap: 2px;',
    '  overflow-y: auto; overflow-x: hidden; z-index: 900; transition: width .18s ease;',
    "  font-family: 'DM Sans', -apple-system, 'Segoe UI', Roboto, sans-serif; }",
    '.side-nav::-webkit-scrollbar { width: 6px; }',
    '.side-nav::-webkit-scrollbar-thumb { background: var(--border, #1e293b); border-radius: 3px; }',
    '.side-nav .topo { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 2px 2px 14px; }',
    '.side-nav .marca { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--text, #e2e8f0); min-width: 0; }',
    // Aberta: o logo inteiro (mapa + RIZZA LOG). Recolhida: só o mapa, que funciona sozinho.
    '.side-nav .marca .logo-mapa { display: none; width: 38px; height: auto; }',
    '.side-nav .marca .texto { display: flex; flex-direction: column; gap: 2px; }',
    '.side-nav .marca .logo-full { display: block; width: 158px; height: auto; }',
    // "Analytics" alinhado ao R de RIZZA (o R começa em 1/3 da largura do logo).
    '.side-nav .marca .sub { font-size: 9px; color: var(--text-dim, #94a3b8); letter-spacing: 2.2px; text-transform: uppercase;',
    '  white-space: nowrap; padding-left: 53px; }',
    'html.nav-recolhida .side-nav .marca .logo-mapa { display: block; }',
    '.side-nav .recolher { flex-shrink: 0; width: 26px; height: 26px; border-radius: 7px; cursor: pointer;',
    '  background: transparent; border: 1px solid var(--border, #1e293b); color: var(--text-dim, #94a3b8);',
    '  font-size: 13px; line-height: 1; display: grid; place-items: center; padding: 0; }',
    '.side-nav .recolher:hover { border-color: var(--accent, #38bdf8); color: var(--accent, #38bdf8); }',
    '.side-nav .grupo { font-size: 9.5px; text-transform: uppercase; letter-spacing: 1px; color: #64748b;',
    '  padding: 12px 9px 4px; white-space: nowrap; }',
    '.side-nav a.item { display: flex; align-items: center; gap: 10px; padding: 7px 9px; border-radius: 8px;',
    '  color: var(--text-dim, #94a3b8); font-size: 13.5px; font-weight: 500; text-decoration: none;',
    '  white-space: nowrap; transition: background .15s, color .15s; }',
    '.side-nav a.item .ic { width: 20px; text-align: center; font-size: 14px; flex-shrink: 0; }',
    '.side-nav a.item:hover { background: var(--surface, #111827); color: var(--text, #e2e8f0); }',
    '.side-nav a.item.ativo { background: rgba(56,189,248,.12); color: var(--accent, #38bdf8);',
    '  box-shadow: inset 2px 0 0 var(--accent, #38bdf8); }',
    '.side-nav .espaco { flex: 1; min-height: 12px; }',
    '.side-nav .rodape { border-top: 1px solid var(--border, #1e293b); padding-top: 10px; display: flex; flex-direction: column; gap: 6px; }',
    '.side-nav .rodape .papel { font-size: 9.5px; text-transform: uppercase; letter-spacing: .6px; color: var(--accent, #38bdf8); font-weight: 600; white-space: nowrap; }',
    '.side-nav .rodape .quem { font-size: 12.5px; color: var(--text-dim, #94a3b8); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }',
    '.side-nav .rodape a.sair { font-size: 12px; color: #64748b; text-decoration: none; padding: 4px 0; white-space: nowrap; }',
    '.side-nav .rodape a.sair:hover { color: var(--red, #f87171); }',
    // Recolhida: some o texto, não a navegação — o title de cada item vira a dica.
    'html.nav-recolhida .side-nav { padding: 14px 8px 12px; }',
    'html.nav-recolhida .side-nav .topo { flex-direction: column; gap: 10px; }',
    'html.nav-recolhida .side-nav .marca .texto, html.nav-recolhida .side-nav .grupo,',
    'html.nav-recolhida .side-nav a.item .rotulo, html.nav-recolhida .side-nav .rodape .quem,',
    'html.nav-recolhida .side-nav .rodape .papel { display: none; }',
    'html.nav-recolhida .side-nav a.item { justify-content: center; padding: 8px 4px; }',
    'html.nav-recolhida .side-nav .grupo-sep { display: block; }',
    '.side-nav .grupo-sep { display: none; height: 1px; background: var(--border, #1e293b); margin: 8px 6px; }',
    'html.nav-recolhida .side-nav .rodape { align-items: center; }',
    // Tela estreita: sempre faixa de ícones, e o botão sai (não há o que abrir).
    '@media (max-width: 900px) {',
    '  :root, html.nav-recolhida { --nav-w: 56px; }',
    '  .side-nav { padding: 12px 6px; }',
    '  .side-nav .topo { flex-direction: column; }',
    '  .side-nav .recolher, .side-nav .marca .texto, .side-nav .grupo, .side-nav a.item .rotulo,',
    '  .side-nav .rodape .quem, .side-nav .rodape .papel { display: none; }',
    '  .side-nav .grupo-sep { display: block; }',
    '  .side-nav .marca .logo-mapa { display: block; width: 34px; }',
    '  .side-nav a.item { justify-content: center; padding: 8px 4px; }',
    '  .side-nav .rodape { align-items: center; }',
    '}'
  ].join('\n');

  function lerRecolhida() {
    try { return window.localStorage.getItem(CHAVE_RECOLHIDA) === '1'; } catch (e) { return false; }
  }
  function gravarRecolhida(v) {
    try { window.localStorage.setItem(CHAVE_RECOLHIDA, v ? '1' : '0'); } catch (e) {}
  }

  function podeVer(aba, me) {
    if (aba.sempre) return true;             // Início: quem está logado enxerga
    if (me.role === 'admin') return true;    // admin vê tudo (bypass)
    if (aba.page === 'admin') return false;  // Admin é exclusivo de role=admin
    return (me.paginas_permitidas || []).indexOf(aba.page) !== -1;
  }

  function porId(id) {
    for (var i = 0; i < ABAS.length; i++) if (ABAS[i].id === id) return ABAS[i];
    return null;
  }

  // '🗺️ Mapa' → ['🗺️', 'Mapa']: o emoji vira o ícone da faixa recolhida.
  function partes(label) {
    var i = label.indexOf(' ');
    return i < 0 ? ['·', label] : [label.slice(0, i), label.slice(i + 1)];
  }

  function criarItem(aba, ativo) {
    var p = partes(aba.label);
    var a = document.createElement('a');
    a.className = 'item' + (ativo ? ' ativo' : '');
    a.href = aba.href;
    a.title = p[1];
    if (aba.page) a.setAttribute('data-page', aba.page);
    var ic = document.createElement('span');
    ic.className = 'ic';
    ic.textContent = p[0];
    var tx = document.createElement('span');
    tx.className = 'rotulo';
    tx.textContent = p[1];
    a.appendChild(ic);
    a.appendChild(tx);
    return a;
  }

  function injetarCss() {
    if (document.getElementById('nav-side-css')) return;
    var st = document.createElement('style');
    st.id = 'nav-side-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function alternar() {
    var rec = !document.documentElement.classList.contains('nav-recolhida');
    document.documentElement.classList.toggle('nav-recolhida', rec);
    gravarRecolhida(rec);
    var b = document.querySelector('.side-nav .recolher');
    if (b) {
      b.textContent = rec ? '»' : '«';
      b.title = rec ? 'Mostrar menu' : 'Recolher menu';
    }
    // Mapa (Leaflet) e gráficos (Chart.js) só recalculam a largura no resize da janela.
    window.dispatchEvent(new Event('resize'));
    setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 220);
  }

  function montarMenu(me) {
    var ph = document.querySelector('[data-nav-menu]');
    if (!ph || document.querySelector('.side-nav')) return;
    var ativo = ph.getAttribute('data-nav-active') || '';

    injetarCss();
    var rec = lerRecolhida();
    document.documentElement.classList.toggle('nav-recolhida', rec);

    var nav = document.createElement('aside');
    nav.className = 'side-nav';

    var topo = document.createElement('div');
    topo.className = 'topo';
    var marca = document.createElement('a');
    marca.className = 'marca';
    marca.href = '/inicio';
    marca.title = 'Início';
    marca.innerHTML =
      '<img class="logo-mapa" src="/marca/rizza-mapa.svg" alt="Rizza Log">' +
      '<span class="texto"><img class="logo-full" src="/marca/rizza-logo.svg" alt="Rizza Log">' +
      '<span class="sub">Analytics</span></span>';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'recolher';
    btn.textContent = rec ? '»' : '«';
    btn.title = rec ? 'Mostrar menu' : 'Recolher menu';
    btn.addEventListener('click', alternar);
    topo.appendChild(marca);
    topo.appendChild(btn);
    nav.appendChild(topo);

    var inicio = porId('inicio');
    if (inicio) nav.appendChild(criarItem(inicio, ativo === 'inicio'));

    GRUPOS.forEach(function (g) {
      var itens = g.abas.map(porId).filter(function (a) { return a && podeVer(a, me); });
      if (!itens.length) return;              // grupo sem aba liberada não vira título órfão
      var t = document.createElement('div');
      t.className = 'grupo';
      t.textContent = g.nome;
      nav.appendChild(t);
      var sep = document.createElement('div');   // o título some recolhida; a linha fica
      sep.className = 'grupo-sep';
      nav.appendChild(sep);
      itens.forEach(function (a) { nav.appendChild(criarItem(a, a.id === ativo)); });
    });

    var esp = document.createElement('div');
    esp.className = 'espaco';
    nav.appendChild(esp);

    var rod = document.createElement('div');
    rod.className = 'rodape';
    var papel = document.createElement('span');
    papel.className = 'papel';
    papel.textContent = me.role === 'admin' ? '● Administrador' : '● Consulta';
    var quem = document.createElement('span');
    quem.className = 'quem';
    quem.textContent = me.nome || me.email || '';
    quem.title = quem.textContent;
    var sair = document.createElement('a');
    sair.className = 'sair';
    sair.href = '/logout';
    sair.title = 'Sair';
    sair.textContent = '↪ Sair';
    rod.appendChild(papel);
    rod.appendChild(quem);
    rod.appendChild(sair);
    nav.appendChild(rod);

    document.body.insertBefore(nav, document.body.firstChild);
    document.documentElement.classList.add('com-nav');
    document.body.classList.add('tem-sidebar');
    // Todos os placeholders somem (algumas telas antigas declaravam mais de um).
    document.querySelectorAll('[data-nav-menu]').forEach(function (el) {
      el.parentNode.removeChild(el);
    });

    // O "Sair" que cada tela tinha no topo vira repetição: a lateral tem o seu. Esconder é
    // melhor que apagar — a página segue funcionando sozinha se a lateral não montar.
    document.querySelectorAll('a[href="/logout"]').forEach(function (el) {
      if (!nav.contains(el)) el.style.display = 'none';
    });
    // A página mudou de largura depois do primeiro desenho (mapa, gráficos).
    window.dispatchEvent(new Event('resize'));
  }

  // Compat: esconde quaisquer links [data-page] hardcoded que sobrem numa página
  // (ex.: atalhos contextuais) quando o usuário não tem a permissão.
  function aplicarPermissoesAbas(me) {
    if (!me) return;
    var isAdmin = me.role === 'admin';
    var perms = me.paginas_permitidas || [];
    document.querySelectorAll('[data-page]').forEach(function (el) {
      var key = el.getAttribute('data-page');
      var ok = isAdmin || perms.indexOf(key) !== -1;
      el.style.display = ok ? '' : 'none';
    });
  }

  // Exposto para páginas que já tenham o objeto /api/me em mãos.
  window.aplicarPermissoesAbas = aplicarPermissoesAbas;
  window.montarMenuNav = montarMenu;
  // A tela de entrada (/inicio) monta os cards a partir DESTA mesma lista e desta
  // mesma regra — para aba nova aparecer nos dois lugares sem duplicar permissão.
  window.NAV_ABAS = ABAS;
  window.NAV_GRUPOS = GRUPOS;
  window.navPodeVer = podeVer;

  function iniciar() {
    fetch('/api/me', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.ok) { montarMenu(j); aplicarPermissoesAbas(j); }
      })
      .catch(function () {});
  }

  // Não basta escutar DOMContentLoaded: o PGR injeta este script dinamicamente e
  // script inserido por JS é async — se ele chega depois do evento, o listener
  // nunca dispara e a página fica sem menu. Checar o readyState cobre os dois casos.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
