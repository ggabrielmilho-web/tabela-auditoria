// ── Controle de visibilidade das abas do menu por permissão do usuário ──
// Cada link de navegação recebe data-page="<chave>" (auditoria, tarifas, embarques,
// reuniao, dre, despesas, conhecimentos, faturamento, admin).
// Admin enxerga todas as abas (bypass). Links sem data-page (Sair, Voltar) nunca são tocados.
(function () {
  function aplicarPermissoesAbas(me) {
    if (!me) return;
    var perms = me.paginas_permitidas || [];
    var isAdmin = me.role === 'admin';
    document.querySelectorAll('[data-page]').forEach(function (el) {
      var key = el.getAttribute('data-page');
      var ok = isAdmin || perms.indexOf(key) !== -1;  // 'admin' nunca está em perms → só admin vê
      el.style.display = ok ? '' : 'none';
    });
  }

  // Exposto para páginas que já têm o objeto /api/me em mãos (evita refazer o fetch).
  window.aplicarPermissoesAbas = aplicarPermissoesAbas;

  // Auto-inicializa: busca /api/me e aplica o gating no carregamento.
  document.addEventListener('DOMContentLoaded', function () {
    fetch('/api/me', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (j && j.ok) aplicarPermissoesAbas(j); })
      .catch(function () {});
  });
})();
