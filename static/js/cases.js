// Keep the publication title for search and provenance. New titles fall back to the source wording.
const publicationTitles = new Map([
    ['Left Gluteal and Perianal Eumycetoma Lesion Extending Into the Left Greater Sciatic Foramen, Pelvic Cavity to the External Anal Sphincter',
        'Left gluteal and perianal eumycetoma with pelvic extension'],
    ['Sickle Cell Lung Disease in a 9-year-Old Presenting with Wheezing: Investigating Causal Relationships, Asthma or Acute Chest Syndrome',
        'Wheezing in a 9-year-old with sickle cell lung disease'],
    ['Right Ventricular Pacing for Cardiogenic Shock in Takotsubo Cardiomyopathy with Left Ventricular Outflow Tract Obstruction',
        'Right ventricular pacing in Takotsubo cardiomyopathy with outflow tract obstruction'],
]);

function libraryTitle(c) {
    if (c.source !== 'wiley' && c.source !== 'pubmed') return c.title;
    return publicationTitles.get(c.title) || c.title.replace(/:\s*a case report\.?$/i, '');
}

function caseSourceLabel(c) {
    return ({ static: 'Built-in bank', ai_generated: 'AI generated', pubmed: 'PubMed', wiley: 'Wiley',
        endless_medical: 'Endless Medical', agentclinic: 'AgentClinic', dicom: 'DICOM' })[c.source] || c.source;
}

function caseSpecialties(c) {
    return c.specialty.split(/[,;]/).map(name => name.trim()).filter(Boolean);
}

function populateCaseFilter(id, labels) {
    const select = document.getElementById(id);
    const selected = select.value;
    const options = new Map(labels.filter(Boolean).map(label => [label.toLowerCase(), label]));
    select.replaceChildren(select.options[0]);
    for (const [value, label] of options) select.add(new Option(label, value));
    select.value = options.has(selected) ? selected : '';
}

function caseCount(count) {
    return count === 1 ? '1 case' : `${count} cases`;
}

async function loadCases() {
    const list = document.getElementById('caseList');
    const message = document.getElementById('caseMessage');
    const filters = document.getElementById('caseFilters');
    filters.disabled = true;
    document.getElementById('caseResults').hidden = true;
    document.getElementById('caseResultCount').textContent = '';
    message.hidden = false;
    message.textContent = 'Loading cases…';
    try {
        const cases = await (await apiFetch('/api/cases/recommended?limit=100')).json();
        const levels = new Set(cases.map(c => c.difficulty.trim().toLowerCase()).filter(Boolean));
        const levelOrder = new Set(['beginner', 'intermediate', 'advanced', ...[...levels].sort()]);
        const levelLabels = [...levelOrder].filter(level => levels.has(level))
            .map(level => level[0].toUpperCase() + level.slice(1));
        populateCaseFilter('caseDifficulty', levelLabels);
        populateCaseFilter('caseSpecialty', cases.flatMap(caseSpecialties).sort((a, b) => a.localeCompare(b)));
        list.replaceChildren(...cases.map(renderLibraryCase));
        document.getElementById('dashboardCaseCount').textContent = caseCount(cases.length);
        filters.disabled = false;
        filterCases();
    } catch (error) {
        const retry = textNode('button', 'Retry', 'library-link');
        retry.type = 'button';
        retry.onclick = loadCases;
        message.replaceChildren(textNode('p', error.message), retry);
    }
}

function renderLibraryCase(c, index) {
    const row = document.createElement('li');
    row.className = 'library-row';
    const title = libraryTitle(c);
    const specialties = caseSpecialties(c);
    const level = c.difficulty.trim().toLowerCase();
    const reviewed = c.status === 'approved';
    const objectives = (c.learning_objectives || []).filter(objective => objective.trim());
    const panelId = `case-details-${index}`;
    const reviewCredit = [c.reviewer, c.approved_at?.slice(0, 10)].filter(Boolean).join(' · ');
    row.dataset.search = [title, c.title, c.specialty, level, ...objectives].join(' ').toLowerCase();
    row.dataset.specialties = specialties.join(',').toLowerCase();
    row.dataset.level = level;
    row.innerHTML = `
        <div class="library-description">
            <h3>${escapeHtml(title)}</h3>
            <p class="library-meta"><span>${escapeHtml(specialties.join(', '))}</span><span class="library-mobile-level">${escapeHtml(level)}</span></p>
            ${objectives.length ? `<p class="library-objective">${escapeHtml(objectives[0])}</p>` : ''}
        </div>
        <span class="library-level">${escapeHtml(level)}</span>
        <div class="library-review ${reviewed ? 'is-reviewed' : ''}">
            <strong>${reviewed ? 'Reviewed' : 'Unreviewed practice'}</strong>
            <span>${c.scoring_available ? 'Numeric scoring available' : 'No numeric scoring'}</span>
        </div>
        <button type="button" class="library-disclosure" aria-expanded="false" aria-controls="${panelId}">Case details</button>
        <button type="button" class="library-start">Start case</button>
        <div class="library-panel" id="${panelId}" hidden>
            <dl>
                ${title !== c.title ? `<div class="library-full-title"><dt>Full title</dt><dd>${escapeHtml(c.title)}</dd></div>` : ''}
                <div><dt>Source</dt><dd>${escapeHtml(caseSourceLabel(c) || 'Unspecified')}</dd></div>
                <div><dt>Version</dt><dd>${escapeHtml(c.version)}</dd></div>
                ${reviewed && reviewCredit ? `<div><dt>Clinical review</dt><dd>${escapeHtml(reviewCredit)}</dd></div>` : ''}
                ${objectives.length ? `<div class="library-objectives"><dt>Learning objectives</dt><dd><ul>${objectives.map(o => `<li>${escapeHtml(o)}</li>`).join('')}</ul></dd></div>` : ''}
            </dl>
        </div>`;
    const disclosure = row.querySelector('.library-disclosure');
    const panel = row.querySelector('.library-panel');
    disclosure.onclick = () => {
        panel.hidden = !panel.hidden;
        disclosure.setAttribute('aria-expanded', String(!panel.hidden));
    };
    const start = row.querySelector('.library-start');
    start.onclick = async () => {
        start.disabled = true;
        try { await startSimulation(c.case_id, document.getElementById('residentName').value.trim() || 'Learner'); }
        finally { start.disabled = false; }
    };
    return row;
}

function filterCases() {
    const term = document.getElementById('caseFilterInput').value.trim().toLowerCase();
    const specialty = document.getElementById('caseSpecialty').value;
    const level = document.getElementById('caseDifficulty').value;
    const hasFilters = Boolean(term || specialty || level);
    let matches = 0;
    for (const row of document.getElementById('caseList').children) {
        const data = row.dataset;
        const visible = data.search.includes(term)
            && (!specialty || data.specialties.split(',').includes(specialty))
            && (!level || data.level === level);
        row.hidden = !visible;
        if (!row.hidden) matches++;
    }
    document.getElementById('caseResultCount').textContent = caseCount(matches);
    document.getElementById('caseFilterReset').hidden = !hasFilters;
    document.getElementById('caseResults').hidden = !matches;
    const message = document.getElementById('caseMessage');
    message.hidden = Boolean(matches);
    if (!matches) message.textContent = hasFilters ? 'No matching cases. Try a different search or clear your filters.' : 'No recommended cases available.';
}

function clearCaseFilters() {
    document.getElementById('caseFilterInput').value = '';
    document.getElementById('caseSpecialty').value = '';
    document.getElementById('caseDifficulty').value = '';
    filterCases();
    document.getElementById('caseFilterInput').focus();
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
