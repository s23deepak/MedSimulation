let aiPollInterval = null;

function showResultsProgressive(data) { showResults(data); }

function showResults(data) {
    const scores = data.scores;
    const debrief = data.debrief || {};
    showScreen('screen-results');
    currentSession.status = 'scored';
    document.getElementById('resultsTitle').textContent = 'Practice Debrief';
    document.getElementById('scoreNumber').textContent = scores ? scores.total : 'Unscored';
    document.getElementById('scoreNumber').style.fontSize = scores ? '' : '22px';
    document.getElementById('scoreLabel').textContent = scores ? `out of ${scores.max_score}` : 'Pending review';
    document.getElementById('gradeBadge').textContent = scores ? 'Practice feedback' : 'Unreviewed case';
    document.getElementById('scoreResidentName').textContent = currentSession.resident_name;
    const domains = document.getElementById('domainGrid');
    domains.replaceChildren();
    for (const [name, value] of Object.entries(scores?.domain_scores || {})) {
        const domain = document.createElement('div');
        domain.className = 'domain-card';
        domain.append(textNode('div', name, 'domain-name'), textNode('div', `${value}/${scores.domain_max?.[name] || '?'}`, 'domain-score'),
            textNode('p', scores.domain_feedback[name], 'domain-fb'));
        domains.append(domain);
    }
    document.getElementById('correctDiagnosis').textContent = scores?.correct_diagnosis || 'Answer key withheld pending clinical review.';
    document.getElementById('correctManagement').replaceChildren(...(scores?.correct_management || []).map(step => textNode('p', step, 'mgmt-item')));
    document.getElementById('debriefSummary').textContent = debrief.summary || '';
    document.getElementById('learningPoints').replaceChildren(...(scores?.key_learning_points || []).map(step => textNode('p', step, 'learning-point')));
    document.getElementById('coachingPoints').replaceChildren(...(debrief.coaching_points || []).map(step => textNode('p', step, 'learning-point')));
    document.getElementById('correctManagement').closest('.feedback-block').hidden = !scores;
    document.getElementById('learningPoints').closest('.feedback-block').hidden = !scores?.key_learning_points?.length;
    for (const [block, textId, value] of [['aiFeedbackBlock', 'aiFeedbackText', scores?.ai_feedback], ['aiNarrativeBlock', 'aiNarrativeText', debrief.ai_narrative]]) {
        document.getElementById(block).style.display = value ? 'block' : 'none';
        document.getElementById(textId).textContent = value || '';
    }
    document.getElementById('rubricEvidence').hidden = !scores;
    const evidence = document.getElementById('evidenceList');
    evidence.replaceChildren();
    if (scores) evidence.append(textNode('p', `Rubric ${scores.rubric_version} | Case v${currentSession.case_version} | Reviewed by ${currentSession.reviewer}`));
    for (const item of scores?.evidence || []) {
        const row = document.createElement('article');
        row.append(textNode('strong', `${item.domain}: ${item.label || item.item_id}`),
            textNode('p', item.penalty ? `Penalty: ${item.penalty}` : `${item.kind}; credit ${item.credit} x ${item.points}`),
            textNode('p', item.evidence.join('; ') || 'No matching evidence'));
        evidence.append(row);
    }
}

async function pollForAiFeedback() { /* Submission returns the complete debrief. */ }

function escapeHtml(value) {
    const node = document.createElement('span');
    node.textContent = String(value ?? '');
    return node.innerHTML;
}
