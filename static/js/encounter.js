function startTimer() {
    timerSeconds = 0;
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        timerSeconds++;
        const m = String(Math.floor(timerSeconds / 60)).padStart(2, '0');
        const s = String(timerSeconds % 60).padStart(2, '0');
        document.getElementById('timerDisplay').textContent = `${m}:${s}`;
    }, 1000);
}

// ── Tabs ──
function switchTab(tab, clickedBtn) {
    document.querySelectorAll('.tab-btn').forEach(b => { b.classList.remove('active'); b.setAttribute('aria-selected', 'false'); });
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    if (clickedBtn) { clickedBtn.classList.add('active'); clickedBtn.setAttribute('aria-selected', 'true'); }
    document.getElementById(`tab-${tab}`).classList.add('active');
}

// ── History ──
let historyPending = false;
async function askHistory() {
    const input = document.getElementById('historyInput');
    const question = input.value.trim();
    if (!question || !currentSession || historyPending) return;

    historyPending = true;
    input.value = '';
    const cm = document.getElementById('chatMessages');

    appendChat('You', question);
    cm.scrollTop = cm.scrollHeight;

    document.getElementById('chatSpinner').classList.add('show');

    let timeoutId, tickId;
    try {
        const controller = new AbortController();
        timeoutId = setTimeout(() => controller.abort(), 90000);
        const spinnerEl = document.getElementById('chatSpinner');
        let elapsed = 0;
        tickId = setInterval(() => { elapsed++; spinnerEl.textContent = `Thinking... (${elapsed}s)`; }, 1000);

        const res = await apiFetch('/api/simulation/history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: currentSession.session_id,
                question,
            }),
            signal: controller.signal,
        });
        clearTimeout(timeoutId);
        clearInterval(tickId);
        spinnerEl.textContent = 'Thinking...';

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Request failed');
        }
        const data = await res.json();

        document.getElementById('chatSpinner').classList.remove('show');

        appendChat('Patient', data.response);
        cm.scrollTop = cm.scrollHeight;
        speakPatientResponse(data.response, data.audio_url || '');
    } catch (err) {
        document.getElementById('chatSpinner').classList.remove('show');
        document.getElementById('chatSpinner').textContent = 'Thinking...';
        const errMsg = err.name === 'AbortError'
            ? 'Request timed out (45s). Patient is unresponsive - they may be napping. Try again, they should be more alert the second time.'
            : err.message;
        appendChat('System', `Unable to get response: ${errMsg}`);
        input.value = question;
        const retry = textNode('button', 'Retry question', 'quick-q-btn');
        retry.onclick = () => { retry.remove(); input.value = question; askHistory(); };
        cm.append(retry);
        cm.scrollTop = cm.scrollHeight;
    } finally {
        clearTimeout(timeoutId);
        clearInterval(tickId);
        historyPending = false;
    }
}

// ── Exam ──
async function examSystem(system, btn) {
    if (!currentSession) return;

    // Check if this exam already has a result (toggle off)
    const existingResult = btn.nextElementSibling;
    if (existingResult && existingResult.classList.contains('exam-result-inline') && btn.classList.contains('done')) {
        existingResult.remove();
        btn.classList.remove('done');
        return;
    }

    // Remove any existing result for this button
    if (existingResult && existingResult.classList.contains('exam-result-inline')) {
        existingResult.remove();
    }

    btn.disabled = true;
    const originalLabel = btn.textContent;
    btn.textContent = 'Loading...';
    try {
        const res = await apiFetch('/api/simulation/exam', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: currentSession.session_id, system }),
        });
        if (!res.ok) throw new Error('Exam request failed');
        const data = await res.json();
        btn.classList.add('done');

        // Create inline result div right after the button
        const resultDiv = document.createElement('div');
        resultDiv.className = 'exam-result-inline show';
        resultDiv.innerHTML = `
            <div class="result-name">${escapeHtml(data.system)}</div>
            <div class="result-value">${escapeHtml(data.findings)}</div>
        `;
        btn.parentNode.insertBefore(resultDiv, btn.nextSibling);
    } catch (err) {
        const resultDiv = document.createElement('div');
        resultDiv.className = 'exam-result-inline show';
        resultDiv.innerHTML = `
            <div class="result-name">${escapeHtml(system)}</div>
            <div class="result-value text-danger">${escapeHtml(err.message)}</div>
        `;
        btn.parentNode.insertBefore(resultDiv, btn.nextSibling);
    } finally {
        btn.disabled = false;
        btn.textContent = originalLabel;
    }
}

// ── Investigations ──
async function orderInvestigation() {
    const input = document.getElementById('invInput');
    const inv = input.value.trim();
    if (!inv || !currentSession) return;
    input.value = '';
    const orderButton = input.parentElement.querySelector('button');
    input.disabled = true;
    orderButton.disabled = true;

    try {
        const res = await apiFetch('/api/simulation/investigate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: currentSession.session_id, investigation: inv }),
        });
        if (!res.ok) throw new Error('Investigation request failed');
        const data = await res.json();

        allResults.push(data);
        document.getElementById('resultCount').textContent = allResults.length;

        const rl = document.getElementById('resultsList');
        if (allResults.length === 1) rl.innerHTML = '';
        rl.innerHTML += `<div class="result-item"><div class="result-name">${escapeHtml(data.investigation)}</div><div class="result-value">${escapeHtml(data.result)}</div></div>`;
    } catch (err) {
        input.value = inv;
        const rl = document.getElementById('resultsList');
        rl.innerHTML += `<div class="result-item"><div class="result-name text-danger">${escapeHtml(inv)}</div><div class="result-value text-danger">${escapeHtml(err.message)}</div></div>`;
    } finally {
        input.disabled = false;
        orderButton.disabled = false;
    }
}

// ── Submit ──
async function submitAssessment() {
    const diagnosis = document.getElementById('diagnosisInput').value.trim();
    const mgmtRaw = document.getElementById('managementInput').value.trim();
    if (!diagnosis) return showToast('Please enter a diagnosis.', 'warning');
    if (!currentSession) return;

    const management = mgmtRaw.split(/\n+/).map(s => s.trim()).filter(Boolean);
    const btn = document.getElementById('submitBtn');
    btn.disabled = true;
    document.getElementById('submitSpinner').classList.add('show');

    try {
        const res = await apiFetch('/api/simulation/submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: currentSession.session_id, diagnosis, management }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Submission failed');
        }
        const data = await res.json();

        if (timerInterval) clearInterval(timerInterval);

        // Show scores immediately
        showResultsProgressive(data);

        // Start polling for feedback
        if (!data.ai_ready) {
            pollForAiFeedback(currentSession.session_id);
        }
    } catch (err) {
        showToast('Submission failed: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        document.getElementById('submitSpinner').classList.remove('show');
    }
}
