// ── Menu de navegação centralizado + gating por permissão de aba ──
// Fonte ÚNICA de verdade das abas do sistema. Cada página só declara um placeholder:
//   <span data-nav-menu data-nav-style="pill|btn-wrap|btn-anchor" data-nav-active="dre"></span>
// e este script injeta, naquele ponto, apenas as abas que o usuário pode ver
// (admin enxerga todas por bypass). Assim, aba nova aparece sozinha em todas as telas.
// Links fora do placeholder (Sair, ações próprias da página) nunca são tocados.
(function () {
  // Ordem canônica das abas. `page` = chave de permissão (paginas_permitidas);
  // `id` = identificador da aba ativa; `label` = texto exibido.
  var ABAS = [
    // Início não é aba concedível: é a porta de entrada, visível para quem está logado.
    // Fica na barra de todas as telas para haver caminho de volta ao menu.
    { id: 'inicio',        page: null,            href: '/inicio',            label: '🏠 Início', sempre: true, grupo: null },
    { id: 'auditoria',     page: 'auditoria',     href: '/',                  label: '⚡ Auditoria' },
    { id: 'embarques',     page: 'embarques',     href: '/embarques',         label: '🚚 Embarques' },
    { id: 'mapa',          page: 'embarques',     href: '/embarques/mapa',    label: '🗺️ Mapa' },
    // Junto da família de rastreamento, não perto do DRE: é segurança
    // operacional, não financeiro.
    { id: 'pgr',           page: 'pgr',           href: '/pgr',               label: '🚦 PGR' },
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
    { id: 'admin',         page: 'admin',         href: '/admin',             label: '⚙ Admin' }
  ];

  function podeVer(aba, me) {
    if (aba.sempre) return true;             // Início: quem está logado enxerga
    if (me.role === 'admin') return true;    // admin vê tudo (bypass)
    if (aba.page === 'admin') return false;  // Admin é exclusivo de role=admin
    return (me.paginas_permitidas || []).indexOf(aba.page) !== -1;
  }

  // Cria o <a> da aba no estilo pedido pela página (preserva o visual de cada tela).
  function criarLink(aba, estilo, ativo) {
    var a = document.createElement('a');
    if (aba.page) a.setAttribute('data-page', aba.page);
    a.href = aba.href;
    a.title = aba.id === 'inicio' ? 'Voltar ao menu' : aba.label;
    var ativoCls = ativo ? ' active' : '';
    var visivel = a;                         // o nó que recebe o estilo do botão
    if (estilo === 'pill') {                 // <a class="nav-btn">
      a.className = 'nav-btn' + ativoCls;
      a.textContent = aba.label;
    } else if (estilo === 'btn-anchor') {    // <a class="btn-sm">
      a.className = 'btn-sm' + ativoCls;
      a.textContent = aba.label;
    } else {                                 // btn-wrap: <a><button class="btn-sm"></button></a>
      a.style.textDecoration = 'none';
      var b = document.createElement('button');
      b.className = 'btn-sm' + ativoCls;
      b.textContent = aba.label;
      a.appendChild(b);
      visivel = b;
    }
    // O Início é a volta ao menu, não mais uma aba: destacado para não se perder
    // no meio das outras 14. Estilo inline porque cada tela tem o seu próprio CSS.
    if (aba.id === 'inicio') {
      a.setAttribute('data-nav-inicio', '');
      a.style.marginRight = '10px';
      visivel.style.fontWeight = '700';
      visivel.style.borderColor = '#38bdf8';
      visivel.style.color = '#38bdf8';
      visivel.style.background = 'rgba(56,189,248,0.10)';
    }
    return a;
  }

  function montarMenu(me) {
    document.querySelectorAll('[data-nav-menu]').forEach(function (ph) {
      var estilo = ph.getAttribute('data-nav-style') || 'btn-wrap';
      var ativo = ph.getAttribute('data-nav-active') || '';
      var frag = document.createDocumentFragment();
      ABAS.forEach(function (aba) {
        if (podeVer(aba, me)) frag.appendChild(criarLink(aba, estilo, aba.id === ativo));
      });
      ph.parentNode.insertBefore(frag, ph);  // injeta no lugar do placeholder
      ph.parentNode.removeChild(ph);
    });
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
