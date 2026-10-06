async function initializeAccount() {
    try {
        document.getElementById('accountName').textContent = 'Pilot learner';
        document.getElementById('reviewLink').hidden = true;
        document.getElementById('signOut').hidden = true;
        document.getElementById('loginDialog')?.close();
        revealWorkspace();
        await loadCases();
        await loadRecentSessions();
    } catch {
        document.getElementById('vllmLoading').classList.add('hidden');
        if (document.body.dataset.passwordless === 'true') {
            document.querySelector('.vllm-loading-title').textContent = 'Unable to connect';
            document.querySelector('.vllm-loading-text').textContent = 'Refresh the page to try again.';
            document.getElementById('vllmLoading').classList.remove('hidden');
        } else {
            document.getElementById('loginDialog').showModal();
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    let notesTimer;
    document.getElementById('notesArea').addEventListener('input', () => {
        clearTimeout(notesTimer);
        notesTimer = setTimeout(async () => {
            if (!currentSession || currentSession.status === 'scored') return;
            try {
                const notes = document.getElementById('notesArea').value;
                await apiFetch('/api/simulation/notes', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: currentSession.session_id, clinical_notes: notes }) });
                currentSession.clinical_notes = notes;
            } catch (error) { showToast(`Notes not saved: ${error.message}`); }
        }, 700);
    });
    document.getElementById('loginForm')?.addEventListener('submit', event => event.preventDefault());
    document.getElementById('signOut')?.addEventListener('click', () => {});
    initializeAccount();
});

async function loadRecentSessions() {
    try {
        const sessions = await (await apiFetch('/api/simulation/sessions')).json();
        const active = sessions.filter(s => s.status === 'active');
        const section = document.getElementById('resumeSection');
        section.hidden = active.length === 0;
        const list = document.getElementById('resumeList');
        list.replaceChildren();
        for (const session of active) {
            const row = document.createElement('div');
            const resume = textNode('button', `Resume ${session.case_title}`);
            resume.onclick = async () => {
                try {
                    currentSession = await (await apiFetch(`/api/simulation/session/${session.session_id}`)).json();
                    setupSimulation(); showScreen('screen-sim'); startTimer();
                } catch (error) { showToast(error.message); }
            };
            const remove = textNode('button', 'Delete');
            remove.onclick = async () => {
                if (!confirm('Permanently delete this unfinished session?')) return;
                try { await apiFetch(`/api/simulation/session/${session.session_id}`, { method: 'DELETE' }); await loadRecentSessions(); }
                catch (error) { showToast(error.message); }
            };
            row.append(resume, remove);
            list.append(row);
        }
    } catch (error) { showToast(error.message); }
}
