let pendingCases = [];
let selectedCase = null;
let draft = null;
let caseDirty = false;
const domains = ['history', 'exam', 'investigations', 'diagnosis', 'management'];

function field(label, value, change, choices = null) {
    const wrapper = textNode('label', label);
    const input = document.createElement(choices ? 'select' : 'input');
    if (choices) for (const choice of choices) { const option = textNode('option', choice); option.value = choice; input.append(option); }
    input.value = value;
    input.onchange = () => change(input.value);
    wrapper.append(input);
    return wrapper;
}

async function refreshQueue() {
    try {
        pendingCases = await (await apiFetch('/api/cases/pending')).json();
        const queue = document.getElementById('reviewQueue');
        queue.replaceChildren();
        document.getElementById('reviewStatus').textContent = pendingCases.length ? `${pendingCases.length} pending` : 'No cases awaiting review.';
        document.getElementById('reviewDetail').hidden = true;
        for (const item of pendingCases) {
            const button = textNode('button', `${item.title} (v${item.version})`);
            button.onclick = () => selectCase(item);
            queue.append(button);
        }
    } catch (error) { document.getElementById('reviewStatus').textContent = `${error.message}. Sign in with a reviewer account in the practice workspace.`; }
}

async function selectCase(item) {
    selectedCase = structuredClone(item);
    caseDirty = false;
    draft = selectedCase.rubric;
    document.getElementById('reviewDetail').hidden = false;
    document.getElementById('reviewTitle').textContent = item.title;
    document.getElementById('reviewProvenance').textContent = `${item.source} | ${item.source_ref || 'No external reference'} | Version ${item.version} | Pending clinical review`;
    const reference = item.source === 'pubmed' && /^\d+$/.test(item.source_ref || '')
        ? `https://pubmed.ncbi.nlm.nih.gov/${item.source_ref}/`
        : item.source === 'wiley' && item.source_ref
            ? `https://doi.org/${encodeURIComponent(item.source_ref)}` : '';
    if (reference) {
        const link = textNode('a', 'Open source record');
        link.href = reference; link.target = '_blank'; link.rel = 'noopener noreferrer';
        document.getElementById('reviewProvenance').append(' ', link);
    }
    document.getElementById('rubricVersion').value = draft.version;
    document.getElementById('unlistedPenalty').value = draft.unlisted_investigation_penalty;
    document.getElementById('reviewNotes').value = '';
    document.getElementById('reviewConfirmed').checked = false;
    document.getElementById('reviewError').textContent = '';
    const fields = document.getElementById('caseFields');
    fields.replaceChildren();
    for (const key of ['title', 'presentation', 'correct_diagnosis', 'acceptable_diagnoses', 'correct_management', 'key_learning_points', 'learning_objectives', 'abnormal_vitals', 'history_data', 'physical_exam', 'investigations', 'initial_vitals', 'score_weights']) {
        const value = selectedCase[key];
        const label = textNode('label', key.replaceAll('_', ' '));
        if (typeof value === 'object' && !Array.isArray(value)) {
            const group = document.createElement('div');
            for (const [name, content] of Object.entries(value)) group.append(field(name, content, updated => { selectedCase[key][name] = key === 'score_weights' ? Number(updated) : updated; caseDirty = true; }));
            label.append(group);
        } else {
            const input = document.createElement('textarea');
            input.value = Array.isArray(value) ? value.join('\n') : value;
            input.onchange = () => { selectedCase[key] = Array.isArray(value) ? input.value.split('\n').filter(Boolean) : input.value; caseDirty = true; };
            label.append(input);
        }
        fields.append(label);
    }
    renderRubric();
    try {
        const events = await (await apiFetch(`/api/cases/${item.case_id}/audit`)).json();
        document.getElementById('reviewAudit').replaceChildren(...events.map(event => textNode('p', `${event.created_at} | ${event.actor} | ${event.event_type}`)));
    } catch (error) { document.getElementById('reviewError').textContent = error.message; }
}

function renderRubric() {
    const container = document.getElementById('rubricItems');
    container.replaceChildren();
    const headings = document.createElement('div');
    headings.className = 'rubric-row rubric-heading';
    for (const title of ['Domain', 'Action', 'Type', 'Points', 'Accepted phrases', 'Partial phrases', '']) headings.append(textNode('strong', title));
    container.append(headings);
    function compactField(label, value, change, choices = null) {
        const wrapper = document.createElement('label');
        wrapper.append(textNode('span', label, 'sr-only'));
        const input = document.createElement(choices ? 'select' : 'input');
        if (choices) for (const choice of choices) { const option = textNode('option', choice); option.value = choice; input.append(option); }
        input.value = value;
        input.title = `${label}: ${value}`;
        input.onchange = () => change(input.value);
        wrapper.append(input);
        return wrapper;
    }
    for (const item of draft.items) {
        const row = document.createElement('div');
        row.className = 'rubric-row';
        row.title = item.id;
        row.append(compactField('Domain', item.domain, v => { item.domain = v; }, domains),
            compactField('Action', item.label, v => { item.label = v; }),
            compactField('Requirement', item.kind, v => { item.kind = v; }, ['required', 'optional', 'contraindicated']),
            compactField('Points', item.points, v => { item.points = Number(v); }),
            compactField('Accepted phrases (semicolon separated)', item.aliases.join('; '), v => { item.aliases = v.split(';').map(x => x.trim()).filter(Boolean); }),
            compactField('Partial credit phrases (semicolon separated)', (item.partial_aliases || []).join('; '), v => { item.partial_aliases = v.split(';').map(x => x.trim()).filter(Boolean); }));
        const remove = textNode('button', '×'); remove.type = 'button'; remove.title = 'Remove rubric item'; remove.setAttribute('aria-label', 'Remove rubric item');
        remove.onclick = () => { draft.items = draft.items.filter(i => i !== item); renderRubric(); };
        row.append(remove); container.append(row);
    }
    const rules = document.getElementById('orderRules');
    rules.replaceChildren();
    for (const rule of draft.ordering) {
        const row = document.createElement('fieldset');
        const ids = draft.items.map(item => item.id);
        row.append(field('Required first', rule.before, v => { rule.before = v; }, ids),
            field('Before this action', rule.after, v => { rule.after = v; }, ids),
            field('Penalty domain', rule.domain, v => { rule.domain = v; }, domains),
            field('Penalty points', rule.penalty, v => { rule.penalty = Number(v); }));
        const remove = textNode('button', 'Remove prerequisite'); remove.type = 'button';
        remove.onclick = () => { draft.ordering = draft.ordering.filter(i => i !== rule); renderRubric(); };
        row.append(remove); rules.append(row);
    }
}

async function decide(action) {
    if (caseDirty && action === 'approve') {
        document.getElementById('reviewError').textContent = 'Save the case revision, then reopen it before approval.';
        return;
    }
    draft.version = document.getElementById('rubricVersion').value;
    draft.unlisted_investigation_penalty = Number(document.getElementById('unlistedPenalty').value);
    document.querySelectorAll('.review-actions button').forEach(button => { button.disabled = true; });
    try {
        const payload = { version: selectedCase.version, notes: document.getElementById('reviewNotes').value };
        if (action === 'approve') {
            payload.clinical_review_confirmed = document.getElementById('reviewConfirmed').checked;
            payload.rubric = draft;
        }
        await apiFetch(`/api/cases/${selectedCase.case_id}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        await refreshQueue();
    } catch (error) { document.getElementById('reviewError').textContent = error.message; }
    finally { document.querySelectorAll('.review-actions button').forEach(button => { button.disabled = false; }); }
}

document.getElementById('reviewForm').onsubmit = event => { event.preventDefault(); decide('approve'); };
document.getElementById('rejectCase').onclick = () => decide('reject');
document.getElementById('refreshQueue').onclick = refreshQueue;
document.getElementById('addRubricItem').onclick = () => { draft.items.push({ id: crypto.randomUUID(), label: 'New action', domain: 'management', kind: 'required', aliases: ['New action'], points: 1, partial_aliases: [] }); renderRubric(); };
document.getElementById('addOrderRule').onclick = () => { draft.ordering.push({ before: draft.items[0].id, after: draft.items[1].id, domain: 'management', penalty: 1 }); renderRubric(); };
document.getElementById('saveCase').onclick = async () => {
    try {
        await apiFetch(`/api/cases/${selectedCase.case_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: selectedCase.version, case: selectedCase }) });
        await refreshQueue();
    } catch (error) { document.getElementById('reviewError').textContent = error.message; }
};
refreshQueue();
