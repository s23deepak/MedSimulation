// ── Screen switching ──
function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    window.scrollTo({ top: 0, behavior: 'instant' });
}

async function exportSession(format) {
    if (!currentSession) return;
    try {
        const response = await apiFetch(`/api/simulation/session/${currentSession.session_id}/export/${format}`);
        const blobUrl = URL.createObjectURL(await response.blob());
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = `session_${currentSession.session_id}.${format}`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (error) { showToast(`Export failed: ${error.message}`); }
}

async function goToLanding() {
    if (currentSession && currentSession.status !== 'scored') {
        if (!confirm('Delete this unfinished session and return to cases?')) return;
        try {
            await apiFetch(`/api/simulation/session/${currentSession.session_id}`, { method: 'DELETE' });
        } catch (error) { showToast(error.message); return; }
    }
    if (timerInterval) clearInterval(timerInterval);
    if (aiPollInterval) clearInterval(aiPollInterval);
    if (recognition && isListening) recognition.stop();
    window.speechSynthesis?.cancel();
    document.querySelectorAll('#screen-sim input, #screen-sim textarea').forEach(input => { input.value = ''; });
    document.getElementById('chatMessages').replaceChildren();
    document.getElementById('resultsList').replaceChildren();
    currentSession = null;
    allResults.length = 0;
    showScreen('screen-landing');
    loadRecentSessions();
}

async function deleteCurrentSession() {
    if (!currentSession || !confirm('Permanently delete this session, transcript, and feedback?')) return;
    try {
        await apiFetch(`/api/simulation/session/${currentSession.session_id}`, { method: 'DELETE' });
        currentSession = null;
        if (timerInterval) clearInterval(timerInterval);
        document.querySelectorAll('#screen-sim input, #screen-sim textarea').forEach(input => { input.value = ''; });
        document.getElementById('chatMessages').replaceChildren();
        showScreen('screen-landing');
        loadRecentSessions();
    } catch (error) { showToast(error.message); }
}

function goToSimulation() {
    // Return to the simulation screen from results
    showScreen('screen-sim');
}

// ── Keyboard shortcuts ──
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const lb = document.getElementById('lightboxOverlay');
        if (lb.classList.contains('active')) {
            closeLightbox();
        }
    }
});

function revealWorkspace() {
    document.getElementById('vllmLoading').classList.add('hidden');
    document.getElementById('simWrapper').style.display = 'block';
}

// ── Load cases ──
document.addEventListener('DOMContentLoaded', () => {
    recognition = initializeVoiceRecognition();
    updateVoiceControls();
    if (!SpeechRecognition) updateVoiceStatus('Speech input unsupported');
});
