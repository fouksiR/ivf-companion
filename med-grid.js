(function() {
  var MED_DAYS = 14;

  function buildMedGridForPatient(pid, containerEl, savedMeds) {
    containerEl.innerHTML = '';
    var table = document.createElement('table');
    table.style.cssText = 'border-collapse:collapse;font-size:11px;width:100%;margin-top:6px;';
    var thead = document.createElement('thead');
    var hr = document.createElement('tr');
    var th0 = document.createElement('th');
    th0.style.cssText = 'background:#f8f9fb;border:1px solid #e0e0e0;padding:4px 6px;font-weight:600;color:#888;font-size:10px;text-align:left;min-width:100px;';
    th0.textContent = 'Medication';
    hr.appendChild(th0);
    for (var d = 1; d <= MED_DAYS; d++) {
      var th = document.createElement('th');
      th.style.cssText = 'background:#f8f9fb;border:1px solid #e0e0e0;padding:3px 2px;font-weight:500;color:#999;font-size:9px;text-align:center;min-width:36px;';
      th.textContent = 'D' + d;
      hr.appendChild(th);
    }
    var thDel = document.createElement('th');
    thDel.style.cssText = 'border:none;background:transparent;width:20px;';
    hr.appendChild(thDel);
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = document.createElement('tbody');
    table.appendChild(tbody);
    containerEl.appendChild(table);
    if (savedMeds && typeof savedMeds === 'object') {
      Object.values(savedMeds).forEach(function(m) {
        insertMedRow(tbody, m.name || '', m.doses || {});
      });
    }
    var btnDiv = document.createElement('div');
    btnDiv.style.cssText = 'margin-top:4px;display:flex;align-items:center;gap:6px;';
    var addBtn = document.createElement('button');
    addBtn.textContent = '+ Add medication';
    addBtn.style.cssText = 'padding:3px 10px;border:1px dashed #b8d8d8;border-radius:5px;background:white;font-size:11px;color:#0d7377;cursor:pointer;font-weight:500;';
    addBtn.addEventListener('click', function() {
      insertMedRow(tbody, '', {});
      var last = tbody.lastElementChild;
      if (last) { var fi = last.querySelector('input'); if (fi) fi.focus(); }
    });
    btnDiv.appendChild(addBtn);
    var saveBtn = document.createElement('button');
    saveBtn.textContent = 'Save';
    saveBtn.style.cssText = 'padding:3px 12px;border:none;border-radius:5px;background:#0d7377;color:white;font-size:11px;font-weight:600;cursor:pointer;';
    var statusEl = document.createElement('span');
    statusEl.style.cssText = 'font-size:10px;color:#22c55e;display:none;';
    statusEl.textContent = '\u2713 Saved';
    saveBtn.addEventListener('click', function() { doSaveMeds(pid, tbody, statusEl); });
    btnDiv.appendChild(saveBtn);
    btnDiv.appendChild(statusEl);
    containerEl.appendChild(btnDiv);
  }

  function insertMedRow(tbody, name, doses) {
    var tr = document.createElement('tr');
    var tdName = document.createElement('td');
    tdName.style.cssText = 'border:1px solid #e8e8e8;padding:0;';
    var nameInp = document.createElement('input');
    nameInp.type = 'text';
    nameInp.value = name;
    nameInp.placeholder = 'Type med name...';
    nameInp.autocomplete = 'off';
    nameInp.setAttribute('data-form-type', 'other');
    nameInp.setAttribute('data-lpignore', 'true');
    nameInp.style.cssText = 'width:100%;border:none;padding:4px 6px;font-size:11px;outline:none;font-weight:600;font-family:inherit;background:transparent;';
    nameInp.addEventListener('focus', function() { this.style.background = '#e6f7f7'; });
    nameInp.addEventListener('blur', function() { this.style.background = 'transparent'; });
    tdName.appendChild(nameInp);
    tr.appendChild(tdName);
    for (var d = 1; d <= MED_DAYS; d++) {
      var td = document.createElement('td');
      td.style.cssText = 'border:1px solid #eee;padding:0;';
      var inp = document.createElement('input');
      inp.type = 'text';
      inp.value = (doses && doses['d' + d]) || '';
      inp.autocomplete = 'off';
      inp.setAttribute('data-form-type', 'other');
      inp.setAttribute('data-lpignore', 'true');
      inp.style.cssText = 'width:100%;border:none;padding:4px 2px;font-size:11px;text-align:center;outline:none;font-family:inherit;background:transparent;box-sizing:border-box;';
      inp.addEventListener('focus', function() { this.style.background = '#e6f7f7'; });
      inp.addEventListener('blur', function() { this.style.background = 'transparent'; });
      inp.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          var nextRow = this.closest('tr').nextElementSibling;
          if (nextRow) {
            var idx = Array.from(this.closest('tr').querySelectorAll('input')).indexOf(this);
            var ni = nextRow.querySelectorAll('input');
            if (ni[idx]) ni[idx].focus();
          }
        }
      });
      td.appendChild(inp);
      tr.appendChild(td);
    }
    var tdDel = document.createElement('td');
    tdDel.style.cssText = 'border:none;text-align:center;width:20px;';
    var delBtn = document.createElement('button');
    delBtn.innerHTML = '\u00D7';
    delBtn.title = 'Remove';
    delBtn.style.cssText = 'cursor:pointer;color:#ddd;font-size:14px;border:none;background:none;padding:0 4px;';
    delBtn.addEventListener('mouseenter', function() { this.style.color = '#e74c3c'; });
    delBtn.addEventListener('mouseleave', function() { this.style.color = '#ddd'; });
    delBtn.addEventListener('click', function() { tr.remove(); });
    tdDel.appendChild(delBtn);
    tr.appendChild(tdDel);
    tbody.appendChild(tr);
  }

  function doSaveMeds(pid, tbody, statusEl) {
    try {
      var rows = tbody.querySelectorAll('tr');
      var meds = {};
      rows.forEach(function(row, i) {
        var inputs = row.querySelectorAll('input');
        var name = inputs[0] ? inputs[0].value.trim() : '';
        if (!name) return;
        var doses = {};
        for (var d = 1; d < inputs.length; d++) {
          var val = inputs[d] ? inputs[d].value.trim() : '';
          if (val) doses['d' + d] = val;
        }
        meds['med_' + i] = { name: name, doses: doses };
      });
      var baseUrl = typeof API_URL !== 'undefined' ? API_URL : '';
      var key = typeof apiKey !== 'undefined' ? apiKey : '';
      fetch(baseUrl + '/clinician/patient/' + pid + '/cycle', {
        method: 'POST',
        headers: { 'X-API-Key': key, 'Content-Type': 'application/json' },
        body: JSON.stringify({ medications_simple: meds, updated_at: new Date().toISOString() })
      }).then(function(r) {
        if (r.ok && statusEl) {
          statusEl.style.display = 'inline';
          setTimeout(function() { statusEl.style.display = 'none'; }, 2000);
        }
      }).catch(function(e) { console.error('Med save failed:', e); });
    } catch(e) { console.error('doSaveMeds error:', e); }
  }

  window.buildMedGridForPatient = buildMedGridForPatient;
  window.insertMedRow = insertMedRow;
  window.doSaveMeds = doSaveMeds;

  window.loadAndBuildMedGrid = function(pid) {
    var container = document.getElementById('medgrid-container-' + pid);
    if (!container) return;
    var baseUrl = typeof API_URL !== 'undefined' ? API_URL : '';
    var key = typeof apiKey !== 'undefined' ? apiKey : '';
    fetch(baseUrl + '/clinician/patient/' + pid + '/cycle', {
      headers: { 'X-API-Key': key }
    }).then(function(r) { return r.json(); }).then(function(data) {
      var meds = (data && data.medications_simple) ? data.medications_simple : null;
      buildMedGridForPatient(pid, container, meds);
    }).catch(function() {
      buildMedGridForPatient(pid, container, null);
    });
  };

  function injectMedGrids() {
    var baseUrl = typeof API_URL !== 'undefined' ? API_URL : '';
    var key = typeof apiKey !== 'undefined' ? apiKey : '';
    document.querySelectorAll('[id^="medgrid-container-"]').forEach(function(el) {
      var pid = el.id.replace('medgrid-container-', '');
      if (el.children.length === 0) {
        window.loadAndBuildMedGrid(pid);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
      setTimeout(injectMedGrids, 1000);
      setTimeout(injectMedGrids, 3000);
    });
  } else {
    setTimeout(injectMedGrids, 1000);
    setTimeout(injectMedGrids, 3000);
  }

  var observer = new MutationObserver(function(mutations) {
    mutations.forEach(function(m) {
      m.addedNodes.forEach(function(node) {
        if (node.nodeType === 1 && node.id && node.id.startsWith('medgrid-container-')) {
          var pid = node.id.replace('medgrid-container-', '');
          setTimeout(function() { window.loadAndBuildMedGrid(pid); }, 100);
        }
      });
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();
