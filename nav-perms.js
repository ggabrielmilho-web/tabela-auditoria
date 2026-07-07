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
    { id: 'auditoria',     page: 'auditoria',     href: '/',                  label: '⚡ Auditoria' },
    { id: 'embarques',     page: 'embarques',     href: '/embarques',         label: '🚚 Embarques' },
    { id: 'mapa',          page: 'embarques',     href: '/embarques/mapa',    label: '🗺️ Mapa' },
    { id: 'tarifas',       page: 'tarifas',       href: '/tarifas',           label: '📋 Tarifas' },
    { id: 'reuniao',       page: 'reuniao',       href: '/reuniao',           label: '🎙 Reunião' },
    { id: 'contratos',     page: 'contratos',     href: '/contratos',         label: '📋 Contratos' },
    { id: 'dre',           page: 'dre',           href: '/dre',               label: '📊 DRE' },
    { id: 'conhecimentos', page: 'conhecimentos', href: '/dre/conhecimentos', label: '📦 Conhecimentos' },
    { id: 'despesas',      page: 'despesas',      href: '/dre/despesas',      label: '💰 Despesas' },
    { id: 'faturamento',   page: 'faturamento',   href: '/faturamento',       label: '📊 Faturamento' },
    { id: 'veiculos',      page: 'veiculos',      href: '/veiculos',          label: '🚚 Veículos' },
    { id: 'admin',         page: 'admin',         href: '/admin',             label: '⚙ Admin' }
  ];

  function podeVer(aba, me) {
    if (me.role === 'admin') return true;    // admin vê tudo (bypass)
    if (aba.page === 'admin') return false;  // Admin é exclusivo de role=admin
    return (me.paginas_permitidas || []).indexOf(aba.page) !== -1;
  }

  // Cria o <a> da aba no estilo pedido pela página (preserva o visual de cada tela).
  function criarLink(aba, estilo, ativo) {
    var a = document.createElement('a');
    a.setAttribute('data-page', aba.page);
    a.href = aba.href;
    var ativoCls = ativo ? ' active' : '';
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

  document.addEventListener('DOMContentLoaded', function () {
    fetch('/api/me', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.ok) { montarMenu(j); aplicarPermissoesAbas(j); }
      })
      .catch(function () {});
  });
})();
