// ── Start simulation ──
async function startSimulation(caseId, residentName) {
    try {
        const res = await apiFetch('/api/simulation/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ resident_name: residentName, case_id: caseId }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Failed to start simulation');
        }
        currentSession = await res.json();
        setupSimulation();
        showScreen('screen-sim');
        startTimer();
    } catch (err) {
        showToast('Could not start simulation: ' + err.message, 'error');
    }
}

function setupSimulation() {
    const s = currentSession;
    if (isListening && recognition) recognition.stop();
    if (activePatientAudio) {
        activePatientAudio.pause();
        activePatientAudio = null;
    }
    window.speechSynthesis?.cancel();
    updateVoiceControls();
    document.getElementById('simTitle').textContent = s.case_title;
    document.getElementById('simMeta').textContent = `${s.specialty} \u00B7 ${s.difficulty} \u00B7 ${s.resident_name}`;
    document.getElementById('presentationText').textContent = s.presentation;
    document.getElementById('notesArea').value = s.clinical_notes || '';
    document.getElementById('simMeta').textContent += ` | Case v${s.case_version} | ${s.scoring_available ? 'Reviewed by ' + s.reviewer : 'Unreviewed practice, no numeric scoring'}`;

    // Citation badge for generated cases
    const citation = document.getElementById('simCitation');
    const srcLabel = { pubmed: 'PubMed', wiley: 'Wiley Open Access', endless_medical: 'Endless Medical', ai_generated: 'AI Generated' }[s.source] || s.source;
    if (s.source) {
        let href = '';
        let refText = '';
        if (s.source === 'pubmed' && s.source_ref) {
            href = `https://pubmed.ncbi.nlm.nih.gov/${s.source_ref}/`;
            refText = `PMID: ${s.source_ref}`;
        } else if (s.source === 'wiley' && s.source_ref) {
            href = `https://doi.org/${s.source_ref}`;
            refText = `DOI: ${s.source_ref}`;
        } else if (s.source_ref) {
            refText = s.source_ref;
        }
        citation.innerHTML = `Source: ${escapeHtml(srcLabel)}${refText ? ' \u2014 ' + escapeHtml(refText) : ''}${href ? ` <a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer" class="cite-link" title="Verify source">&#x2197;</a>` : ''}`;
    } else {
        citation.textContent = '';
    }

    // Vitals
    const vg = document.getElementById('vitalsGrid');
    vg.innerHTML = '';
    Object.entries(s.initial_vitals).forEach(([k, v]) => {
        const item = document.createElement('div');
        item.className = 'vital-item';
        const isAbnormal = (s.abnormal_vitals || []).includes(k);
        item.innerHTML = `<div class="vital-label">${escapeHtml(k)}</div><div class="vital-value${isAbnormal ? ' abnormal' : ''}">${escapeHtml(v)}</div>`;
        vg.appendChild(item);
    });

    // Chat reset - load existing history if present
    const cm = document.getElementById('chatMessages');
    cm.replaceChildren();
    appendChat('Patient', "Hello, doctor. I'm not feeling well.");

    // Load existing history questions (for page refresh / reconnection)
    if (s.history_questions && s.history_questions.length > 0) {
        s.history_questions.forEach(h => {
            appendChat('You', h.question, h.ts);
            appendChat('Patient', h.response, h.ts);
        });
        cm.scrollTop = cm.scrollHeight;
    }

    // Quick questions
    const qq = document.getElementById('quickQuestions');
    qq.innerHTML = '';
    const quickQs = ['Tell me about your pain', 'When did it start?', 'Any other symptoms?', 'What medications do you take?', 'Any allergies?', 'Any past medical history?', 'Any family history?', 'Do you smoke or drink?'];
    quickQs.forEach(q => {
        const btn = document.createElement('button');
        btn.className = 'quick-q-btn';
        btn.textContent = q;
        btn.onclick = () => { document.getElementById('historyInput').value = q; askHistory(); };
        qq.appendChild(btn);
    });

    // Exam systems
    setupExamSystems();

    // Quick investigations
    setupQuickInvs();

    // Imaging studies
    setupImaging(s);

    // Objectives
    const ol = document.getElementById('objectivesList');
    ol.innerHTML = s.learning_objectives.map(o => `<li>${escapeHtml(o)}</li>`).join('');

    // Reset fields
    document.getElementById('diagnosisInput').value = '';
    document.getElementById('managementInput').value = '';

    // Restore investigation results if session has them
    const rl = document.getElementById('resultsList');
    if (s.investigations_ordered && s.investigations_ordered.length > 0) {
        allResults.length = 0;
        document.getElementById('resultCount').textContent = s.investigations_ordered.length;
        allResults.length = 0;
        allResults.push(...(s.ordered_results || []));
        rl.innerHTML = s.investigations_ordered.map(inv =>
            `<div class="result-item"><div class="result-name">${escapeHtml(inv)}</div><div class="result-value">${escapeHtml((s.ordered_results || []).find(item => item.investigation === inv)?.result || 'Result unavailable')}</div></div>`
        ).join('');
    } else {
        allResults.length = 0;
        document.getElementById('resultCount').textContent = '0';
        rl.innerHTML = '<p class="empty-text">No results yet.</p>';
    }
}

function setupExamSystems() {
    const s = currentSession;
    const sg = document.getElementById('systemGrid');
    sg.innerHTML = '';

    // Patient portrait
    if (currentSession && currentSession.patient_image_url) {
        const imgWrap = document.createElement('div');
        imgWrap.className = 'patient-portrait';
        const portrait = document.createElement('img');
        portrait.src = safeMediaUrl(currentSession.patient_image_url);
        portrait.alt = 'Patient portrait';
        imgWrap.append(portrait);
        sg.parentNode.insertBefore(imgWrap, sg);
    }

    const systems = ['General', 'Cardiovascular', 'Respiratory', 'Abdomen', 'Neurological', 'Extremities', 'Skin', 'Upper limbs', 'Lower limbs', 'Facial exam', 'Speech', 'Vision', 'Sensation', 'NIHSS score', 'Palpation', 'Abdomen inspection', "Rovsing's sign", "Psoas sign", "Obturator sign", 'Rectal exam', 'Bowel sounds', 'Hernia orifices', 'Right leg', 'Left leg'];
    const uniqueSystems = [...new Set(s.available_exams || systems)];
    uniqueSystems.forEach(sys => {
        const btn = document.createElement('button');
        btn.className = 'system-btn';
        btn.textContent = sys;
        btn.id = `exam-${sys.replace(/\s+/g, '-').toLowerCase()}`;
        btn.onclick = () => examSystem(sys, btn);
        // Mark already-viewed systems
        if (s && s.exam_systems_viewed && s.exam_systems_viewed.includes(sys)) {
            btn.classList.add('done');
        }
        sg.appendChild(btn);
    });
}

function setupQuickInvs() {
    const qi = document.getElementById('quickInvs');
    qi.innerHTML = '';
    const quickList = ['ECG', 'CXR', 'FBC', 'U&E', 'ABG', 'Troponin I', 'D-dimer', 'Glucose', 'Clotting', 'CRP', 'Blood ketones', 'Urine dip', 'CT head', 'Echo', 'LFTs', 'Amylase'];
    (currentSession.available_investigations || quickList).forEach(inv => {
        const btn = document.createElement('button');
        btn.className = 'quick-inv-btn';
        btn.textContent = inv;
        btn.id = `inv-${inv.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase()}`;
        btn.onclick = () => { document.getElementById('invInput').value = inv; orderInvestigation(); btn.classList.add('done'); };
        // Mark already-ordered investigations
        if (currentSession && currentSession.investigations_ordered &&
            currentSession.investigations_ordered.some(i => i.toLowerCase().includes(inv.toLowerCase()))) {
            btn.classList.add('done');
        }
        qi.appendChild(btn);
    });
}

// ── Imaging ──
const modalityIcons = {
    "XR": "\uD83E\uDE7B", "ECG": "\uD83D\uDC93", "CT": "\uD83E\uDDE0", "MRI": "\uD83E\uDDF2",
    "US": "\uD83D\uDCE1", "PATH": "\uD83D\uDD2C", "EEG": "\u26A1"
};
