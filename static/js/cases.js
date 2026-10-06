let allCases = [];

async function loadCases() {
    const grid = document.getElementById('caseGrid');
    grid.replaceChildren(textNode('p', 'Loading cases...', 'empty-text'));
    try {
        allCases = await (await apiFetch('/api/cases/recommended?limit=100')).json();
        document.getElementById('dashboardCaseCount').textContent = `${allCases.length} cases`;
        filterCases();
    } catch (error) {
        const retry = textNode('button', 'Retry');
        retry.onclick = loadCases;
        grid.replaceChildren(textNode('p', error.message, 'empty-text'), retry);
    }
}

function caseSourceLabel(c) {
    return ({ static: 'Built-in bank', ai_generated: 'AI generated', pubmed: 'PubMed', wiley: 'Wiley', endless_medical: 'Endless Medical' })[c.source] || c.source;
}

function renderCaseCard(c) {
    const reviewed = c.status === 'approved';
    return `<div class="case-card-header"><div class="case-card-kicker"><span class="case-specialty">${escapeHtml(c.specialty)}</span><span class="case-difficulty">${escapeHtml(c.difficulty)}</span></div><span class="case-status ${reviewed ? 'reviewed' : 'pending'}">${reviewed ? 'Reviewed' : 'Unreviewed practice'}</span></div>
        <h3>${escapeHtml(c.title)}</h3><p class="case-preview">${escapeHtml((c.learning_objectives || [])[0] || '')}</p>
        <div class="case-meta-grid"><div><span>Source</span><strong>${escapeHtml(caseSourceLabel(c))}</strong></div><div><span>Version</span><strong>${c.version}</strong></div></div>
        <p>${reviewed ? `Reviewed by ${escapeHtml(c.reviewer)} on ${escapeHtml((c.approved_at || '').slice(0, 10))}` : 'Numeric scoring pending clinical review'}</p>
        <button type="button" class="case-start-btn">Start Case</button>`;
}

function filterCases() {
    const term = document.getElementById('caseFilterInput').value.trim().toLowerCase();
    document.querySelector('.btn-clear-filter')?.classList.toggle('visible', !!term);
    const filtered = allCases.filter(c => `${c.title} ${c.specialty} ${c.difficulty} ${(c.learning_objectives || []).join(' ')}`.toLowerCase().includes(term));
    const grid = document.getElementById('caseGrid');
    grid.replaceChildren();
    document.getElementById('noCasesMessage').style.display = filtered.length ? 'none' : 'block';
    for (const c of filtered) {
        const card = document.createElement('article');
        card.className = 'case-card';
        card.innerHTML = renderCaseCard(c);
        const button = card.querySelector('button');
        button.onclick = async () => {
            button.disabled = true;
            try { await startSimulation(c.case_id, document.getElementById('residentName').value.trim() || 'Learner'); }
            finally { button.disabled = false; }
        };
        grid.append(card);
    }
}

function clearCaseFilter() {
    document.getElementById('caseFilterInput').value = '';
    filterCases();
}

async function handleSearch(event) {
    event.preventDefault();
    const topic = document.getElementById('topicInput').value.trim();
    if (!topic) return;
    const button = document.getElementById('searchBtn');
    const overlay = document.getElementById('generatingOverlay');
    button.disabled = true;
    overlay.classList.add('show');
    document.getElementById('searchError').classList.remove('show');
    try {
        const data = await (await apiFetch('/api/cases/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic, source: document.getElementById('sourceSelect').value }) })).json();
        await loadCases();
        showToast('Case generated. Unscored practice is available while clinical review is pending.', 'info');
        await startSimulation(data.case_id, document.getElementById('residentName').value || 'Learner');
    } catch (error) {
        document.getElementById('searchErrorText').textContent = error.message;
        document.getElementById('searchError').classList.add('show');
    } finally {
        button.disabled = false;
        overlay.classList.remove('show');
    }
}
