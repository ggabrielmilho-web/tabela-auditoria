/* ============================================================
   report-filter.js — AutoFilter estilo Excel para tabelas densas
   Compartilhado por dre-conhecimentos.html e dre-despesas.html.

   Filtra/ordena CLIENT-SIDE o conjunto já carregado na tela.
   Uso:
     const rf = ReportFilter.init({
       getData:    () => dadosAtuais,        // { cols:[], data:[] }
       renderTabela: (cols, rows) => {...},  // renderer existente da página
       colsMoeda, colsData,                  // listas de tipo já existentes
       fmtData,                              // formatador de data da página
       defaultSort: { col:'data_autorizacao', dir:'desc' },        // ou { col:'NUMLANCTO', dir:'desc', type:'number' }
       onView:     (view) => {...},          // callback p/ a página atualizar a info-bar
       limparBtnId:'btnLimpar',
       csvNome:    'conhecimentos',
     });
     // após carregar os dados: rf.setColumns(cols); rf.refresh();
   ============================================================ */
window.ReportFilter = (function () {
  'use strict';

  const BLANK = '__VAZIAS__';

  function init(cfg) {
    const colsMoeda = cfg.colsMoeda || [];
    const colsData  = cfg.colsData  || [];
    const fmtData   = cfg.fmtData   || (v => v);

    let cols = [];
    let filterState = {};      // { colKey: {type, selected:Set} | {type,min,max} | {type,from,to} }
    let sortState = null;      // { col, dir, type? }
    let popupEl = null;
    let popupCol = null;

    // resolve a chave real da coluna ignorando caixa (NUMLANCTO vs numlancto)
    function resolveCol(name) {
      if (!name) return null;
      if (cols.includes(name)) return name;
      const lower = name.toLowerCase();
      return cols.find(c => c.toLowerCase() === lower) || null;
    }

    function colType(col) {
      if (colsData.includes(col)) return 'date';
      if (colsMoeda.includes(col)) return 'number';
      return 'text';
    }

    function setColumns(newCols) {
      cols = newCols || [];
      // (re)aplica ordenação padrão sempre que (re)carrega, resolvendo a chave real
      const dc = cfg.defaultSort && resolveCol(cfg.defaultSort.col);
      sortState = dc ? { col: dc, dir: cfg.defaultSort.dir || 'desc', type: cfg.defaultSort.type } : null;
      filterState = {};
      closePopup();
    }

    function hasActiveFilters() {
      return Object.keys(filterState).length > 0;
    }

    // ── cálculo da view (filtra → ordena) ──
    function computeView(data) {
      let view = data.filter(r => {
        for (const col in filterState) {
          const f = filterState[col];
          const v = r[col];
          if (f.type === 'text') {
            const key = (v == null || v === '') ? BLANK : String(v);
            if (!f.selected.has(key)) return false;
          } else if (f.type === 'number') {
            const n = Number(v) || 0;
            if (f.min != null && n < f.min) return false;
            if (f.max != null && n > f.max) return false;
          } else if (f.type === 'date') {
            const t = v ? new Date(v).getTime() : null;
            if (t == null || isNaN(t)) return false;
            if (f.from != null && t < f.from) return false;
            if (f.to != null && t > f.to) return false;
          }
        }
        return true;
      });

      if (sortState && sortState.col && cols.includes(sortState.col)) {
        const { col, dir } = sortState;
        const t = sortState.type || colType(col);
        view = [...view].sort((a, b) => {
          let va = a[col], vb = b[col];
          if (t === 'number')      { va = Number(va) || 0; vb = Number(vb) || 0; }
          else if (t === 'date')   { va = va ? new Date(va).getTime() : 0; vb = vb ? new Date(vb).getTime() : 0; }
          else                     { va = (va == null ? '' : String(va)).toLowerCase(); vb = (vb == null ? '' : String(vb)).toLowerCase(); }
          return dir === 'asc' ? (va < vb ? -1 : va > vb ? 1 : 0) : (va > vb ? -1 : va < vb ? 1 : 0);
        });
      }
      return view;
    }

    function getView() {
      const d = cfg.getData() || { data: [] };
      return computeView(d.data || []);
    }

    // ── render + decoração dos cabeçalhos ──
    function refresh() {
      const d = cfg.getData() || { cols: [], data: [] };
      const view = computeView(d.data || []);
      cfg.renderTabela(d.cols, view);
      decorarCabecalhos();
      if (cfg.onView) cfg.onView(view);
      atualizarBotaoLimpar();
    }

    function decorarCabecalhos() {
      const ths = document.querySelectorAll('table.dados thead th');
      ths.forEach((th, i) => {
        const col = cols[i];
        if (col == null) return;
        const ativo = !!filterState[col];
        const ordenado = sortState && sortState.col === col;
        th.style.cursor = 'pointer';
        th.style.userSelect = 'none';
        th.title = 'Clique para filtrar e ordenar esta coluna (estilo Excel)';
        const seta = ordenado ? (sortState.dir === 'asc' ? ' ▲' : ' ▼') : '';
        const corFunil = ativo ? 'var(--accent)' : 'var(--text-dim)';
        const funilOpacity = ativo ? '1' : '0.45';
        // preserva o texto original da coluna e injeta funil + seta
        th.innerHTML =
          `<span>${col}</span>` +
          `<span style="margin-left:6px;color:${corFunil};opacity:${funilOpacity};font-size:0.85em;">▾</span>` +
          `<span style="color:var(--accent);font-size:0.7em;">${seta}</span>`;
        if (ativo) th.style.color = 'var(--accent)';
        th.onclick = (e) => { e.stopPropagation(); abrirPopup(th, col); };
      });
    }

    // ── popup ──
    function closePopup() {
      if (popupEl) { popupEl.remove(); popupEl = null; popupCol = null; }
      document.removeEventListener('mousedown', onDocDown, true);
      document.removeEventListener('keydown', onKey, true);
    }

    function onDocDown(e) {
      if (popupEl && !popupEl.contains(e.target)) closePopup();
    }
    function onKey(e) { if (e.key === 'Escape') closePopup(); }

    function abrirPopup(th, col) {
      if (popupEl && popupCol === col) { closePopup(); return; }
      closePopup();
      popupCol = col;
      const tipo = colType(col);
      const rect = th.getBoundingClientRect();

      const box = document.createElement('div');
      box.className = 'rf-popup';
      box.style.cssText =
        'position:fixed;z-index:10000;background:var(--surface2);border:1px solid var(--border);' +
        'border-radius:10px;padding:12px;min-width:240px;max-width:320px;box-shadow:0 12px 32px rgba(0,0,0,0.5);' +
        'font-family:\'DM Sans\',sans-serif;color:var(--text);font-size:0.8rem;';

      // ordenação no topo (toda coluna)
      const ordBar = document.createElement('div');
      ordBar.style.cssText = 'display:flex;gap:6px;margin-bottom:10px;';
      ordBar.innerHTML =
        `<button class="rf-btn" data-dir="asc">↑ ${tipo === 'text' ? 'A→Z' : 'Crescente'}</button>` +
        `<button class="rf-btn" data-dir="desc">↓ ${tipo === 'text' ? 'Z→A' : 'Decrescente'}</button>`;
      ordBar.querySelectorAll('button').forEach(b => b.onclick = () => {
        sortState = { col, dir: b.dataset.dir, type: tipo };
        closePopup(); refresh();
      });
      box.appendChild(ordBar);

      if (tipo === 'text') box.appendChild(corpoTexto(col));
      else if (tipo === 'number') box.appendChild(corpoNumero(col));
      else box.appendChild(corpoData(col));

      injetarEstiloBotoes();
      document.body.appendChild(box);
      popupEl = box;

      // posiciona dentro da viewport
      const w = box.offsetWidth, h = box.offsetHeight;
      let left = Math.min(rect.left, window.innerWidth - w - 8);
      let top = rect.bottom + 4;
      if (top + h > window.innerHeight - 8) top = Math.max(8, window.innerHeight - h - 8);
      box.style.left = Math.max(8, left) + 'px';
      box.style.top = top + 'px';

      document.addEventListener('mousedown', onDocDown, true);
      document.addEventListener('keydown', onKey, true);
    }

    function rodape(col, onAplicar) {
      const foot = document.createElement('div');
      foot.style.cssText = 'display:flex;gap:6px;margin-top:10px;';
      foot.innerHTML =
        '<button class="rf-btn rf-primary" style="flex:1;">Aplicar</button>' +
        '<button class="rf-btn" style="flex:1;">Limpar</button>';
      const [bAplicar, bLimpar] = foot.querySelectorAll('button');
      bAplicar.onclick = () => { onAplicar(); closePopup(); refresh(); };
      bLimpar.onclick = () => { delete filterState[col]; closePopup(); refresh(); };
      return foot;
    }

    // texto/categórico: busca + selecionar tudo + checkboxes
    function corpoTexto(col) {
      const d = cfg.getData() || { data: [] };
      const setVals = new Set();
      (d.data || []).forEach(r => {
        const v = r[col];
        setVals.add((v == null || v === '') ? BLANK : String(v));
      });
      const valores = [...setVals].sort((a, b) =>
        a === BLANK ? -1 : b === BLANK ? 1 : a.localeCompare(b, 'pt-BR', { numeric: true }));

      const atual = filterState[col];
      const selecionados = atual ? new Set(atual.selected) : new Set(valores);

      const wrap = document.createElement('div');
      const busca = document.createElement('input');
      busca.type = 'text';
      busca.placeholder = 'Buscar...';
      busca.style.cssText = 'width:100%;padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);margin-bottom:8px;';
      wrap.appendChild(busca);

      const lblTodos = document.createElement('label');
      lblTodos.style.cssText = 'display:flex;align-items:center;gap:8px;padding:4px 2px;font-weight:600;border-bottom:1px solid var(--border);margin-bottom:4px;cursor:pointer;';
      lblTodos.innerHTML = '<input type="checkbox" class="rf-all"><span>(Selecionar tudo)</span>';
      wrap.appendChild(lblTodos);

      const lista = document.createElement('div');
      lista.className = 'rf-list';
      wrap.appendChild(lista);

      function rotulo(v) { return v === BLANK ? '(Vazias)' : v; }

      function pintarLista(filtro) {
        lista.innerHTML = '';
        const visiveis = valores.filter(v => rotulo(v).toLowerCase().includes(filtro));
        const frag = document.createDocumentFragment();
        visiveis.forEach(v => {
          const lbl = document.createElement('label');
          lbl.className = 'rf-opt';
          const cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.className = 'rf-cb';
          cb.checked = selecionados.has(v);
          cb.onchange = () => { cb.checked ? selecionados.add(v) : selecionados.delete(v); sincronizarTodos(); };
          const span = document.createElement('span');
          span.textContent = rotulo(v);
          span.title = rotulo(v);
          lbl.appendChild(cb); lbl.appendChild(span);
          frag.appendChild(lbl);
        });
        lista.appendChild(frag);
        if (!visiveis.length) {
          const vazio = document.createElement('div');
          vazio.style.cssText = 'padding:8px 4px;color:var(--text-dim);';
          vazio.textContent = 'Nenhum valor';
          lista.appendChild(vazio);
        }
      }
      const cbTodos = lblTodos.querySelector('.rf-all');
      function sincronizarTodos() { cbTodos.checked = selecionados.size === valores.length; }
      cbTodos.onchange = () => {
        if (cbTodos.checked) valores.forEach(v => selecionados.add(v));
        else selecionados.clear();
        pintarLista(busca.value.toLowerCase());
      };
      busca.oninput = () => pintarLista(busca.value.toLowerCase());
      pintarLista('');
      sincronizarTodos();

      wrap.appendChild(rodape(col, () => {
        if (selecionados.size === 0 || selecionados.size === valores.length) delete filterState[col];
        else filterState[col] = { type: 'text', selected: new Set(selecionados) };
      }));
      return wrap;
    }

    function corpoNumero(col) {
      const atual = filterState[col] || {};
      const wrap = document.createElement('div');
      wrap.innerHTML =
        '<div style="display:flex;flex-direction:column;gap:8px;">' +
        '<label style="display:flex;flex-direction:column;gap:4px;">Mínimo<input type="number" step="any" class="rf-min" style="padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);"></label>' +
        '<label style="display:flex;flex-direction:column;gap:4px;">Máximo<input type="number" step="any" class="rf-max" style="padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);"></label>' +
        '</div>';
      const min = wrap.querySelector('.rf-min'), max = wrap.querySelector('.rf-max');
      if (atual.min != null) min.value = atual.min;
      if (atual.max != null) max.value = atual.max;
      wrap.appendChild(rodape(col, () => {
        const vMin = min.value !== '' ? Number(min.value) : null;
        const vMax = max.value !== '' ? Number(max.value) : null;
        if (vMin == null && vMax == null) delete filterState[col];
        else filterState[col] = { type: 'number', min: vMin, max: vMax };
      }));
      return wrap;
    }

    function corpoData(col) {
      const atual = filterState[col] || {};
      const wrap = document.createElement('div');
      wrap.innerHTML =
        '<div style="display:flex;flex-direction:column;gap:8px;">' +
        '<label style="display:flex;flex-direction:column;gap:4px;">De<input type="date" class="rf-de" style="padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);color-scheme:dark;"></label>' +
        '<label style="display:flex;flex-direction:column;gap:4px;">Até<input type="date" class="rf-ate" style="padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);color-scheme:dark;"></label>' +
        '</div>';
      const de = wrap.querySelector('.rf-de'), ate = wrap.querySelector('.rf-ate');
      if (atual._deStr) de.value = atual._deStr;
      if (atual._ateStr) ate.value = atual._ateStr;
      wrap.appendChild(rodape(col, () => {
        const from = de.value ? new Date(de.value).getTime() : null;
        const to = ate.value ? new Date(ate.value + 'T23:59:59').getTime() : null;
        if (from == null && to == null) delete filterState[col];
        else filterState[col] = { type: 'date', from, to, _deStr: de.value, _ateStr: ate.value };
      }));
      return wrap;
    }

    function injetarEstiloBotoes() {
      if (document.getElementById('rf-style')) return;
      const s = document.createElement('style');
      s.id = 'rf-style';
      s.textContent =
        '.rf-btn{padding:6px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:0.78rem;cursor:pointer;font-family:\'DM Sans\',sans-serif;}' +
        '.rf-btn:hover{border-color:var(--accent);color:var(--accent);}' +
        '.rf-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;color:#0a0e17;font-weight:700;}' +
        '.rf-primary:hover{opacity:0.85;color:#0a0e17;}' +
        '.rf-list{max-height:220px;overflow-y:auto;overflow-x:hidden;display:block;}' +
        '.rf-opt{display:flex;align-items:center;gap:8px;height:26px;padding:0 4px;border-radius:4px;cursor:pointer;box-sizing:border-box;}' +
        '.rf-opt:hover{background:var(--surface);}' +
        '.rf-opt>span{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:0.78rem;line-height:1.2;}' +
        '.rf-cb{flex:0 0 auto;width:14px;height:14px;margin:0;cursor:pointer;}';
      document.head.appendChild(s);
    }

    // ── botão Limpar filtros (mantém o período) ──
    function atualizarBotaoLimpar() {
      const btn = cfg.limparBtnId && document.getElementById(cfg.limparBtnId);
      if (btn) btn.disabled = !hasActiveFilters();
    }
    function clearFilters() {
      filterState = {};
      const dc = cfg.defaultSort && resolveCol(cfg.defaultSort.col);
      sortState = dc ? { col: dc, dir: cfg.defaultSort.dir || 'desc', type: cfg.defaultSort.type } : null;
      closePopup();
      refresh();
    }

    // ── CSV client-side da view filtrada ──
    function exportarCsvView() {
      const d = cfg.getData() || { cols: [] };
      const view = computeView(d.data || []);
      const colunas = d.cols || [];
      const header = colunas.join(';');
      const linhas = view.map(r => colunas.map(c => {
        let v = r[c];
        if (v == null) return '';
        if (colsMoeda.includes(c)) return String(v).replace('.', ',');
        if (colsData.includes(c)) return fmtData(v);
        return String(v).replace(/;/g, ',').replace(/[\r\n]+/g, ' ');
      }).join(';'));
      const csv = '﻿' + [header, ...linhas].join('\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${cfg.csvNome || 'relatorio'}_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
    }

    // fecha popup ao rolar a TABELA/página (evita popup "solto"),
    // mas ignora a rolagem de dentro do próprio popup (lista de valores)
    window.addEventListener('scroll', (e) => {
      if (!popupEl) return;
      if (e.target && e.target.nodeType === 1 && popupEl.contains(e.target)) return;
      closePopup();
    }, true);

    return { setColumns, refresh, clearFilters, hasActiveFilters, getView, exportarCsvView };
  }

  return { init };
})();
