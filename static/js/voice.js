let recognitionPhase = 'idle';
let autoSendAfterRecognition = false;
let recognitionErrorMessage = '';

function initializeVoiceRecognition() {
    if (!SpeechRecognition) return null;
    const instance = new SpeechRecognition();
    instance.lang = 'en-US';
    instance.interimResults = true;
    instance.continuous = false;

    instance.onstart = () => {
        recognitionPhase = 'listening';
        isListening = true;
        updateVoiceStatus('Listening...');
        showVoiceAlert('', 'info');
        document.getElementById('micButton')?.classList.add('listening');
    };

    instance.onresult = (event) => {
        if (patientSpeechActive) return;
        let transcript = '';
        let finalTranscript = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
            transcript += event.results[i][0].transcript;
            if (event.results[i].isFinal) finalTranscript += event.results[i][0].transcript;
        }
        const spokenText = (finalTranscript || transcript).trim();
        if (spokenText) {
            document.getElementById('historyInput').value = spokenText;
        }
        if (finalTranscript.trim() && recognitionPhase === 'listening') {
            autoSendAfterRecognition = document.getElementById('voiceAutoSend').checked;
            recognitionPhase = 'stopping';
            instance.stop();
        }
    };

    instance.onerror = (event) => {
        if (event.error === 'aborted' && recognitionPhase === 'stopping') return;
        autoSendAfterRecognition = false;
        const messages = {
            'not-allowed': 'Microphone permission denied. Allow microphone access in the browser, then try again.',
            'service-not-allowed': 'Speech recognition is blocked by the browser. Check browser permissions and try again.',
            'audio-capture': 'Microphone unavailable. Check your input device and close other apps using it, then try again.',
            'no-speech': 'No speech detected. Tap the mic and try again.',
            network: 'Speech recognition could not connect. Check your connection and try again.',
        };
        recognitionErrorMessage = messages[event.error] || 'Speech recognition failed. Try again.';
        updateVoiceStatus(recognitionErrorMessage);
        showVoiceAlert(recognitionErrorMessage, 'warning');
        if (event.error !== 'no-speech') showToast(recognitionErrorMessage, 'warning');
    };

    instance.onend = () => {
        recognitionPhase = 'idle';
        isListening = false;
        document.getElementById('micButton')?.classList.remove('listening');
        if (recognitionErrorMessage) return;
        if (voiceModeEnabled) updateVoiceStatus('Voice ready');
        if (autoSendAfterRecognition) {
            autoSendAfterRecognition = false;
            askHistory();
        }
    };

    return instance;
}

function toggleVoiceMode() {
    voiceModeEnabled = !voiceModeEnabled;
    if (!voiceModeEnabled) {
        autoSendAfterRecognition = false;
        if (recognitionPhase !== 'idle' && recognition) {
            recognitionPhase = 'stopping';
            try { recognition.stop(); } catch (e) { recognitionPhase = 'idle'; }
        }
        if (activePatientAudio) {
            activePatientAudio.pause();
            activePatientAudio = null;
        }
        window.speechSynthesis?.cancel();
        patientSpeechActive = false;
        showVoiceAlert('', 'info');
    }
    if (voiceModeEnabled && !SpeechRecognition) {
        const message = 'Speech input is not supported in this browser. Patient replies will still play aloud.';
        showVoiceAlert(message, 'warning');
        showToast(message, 'warning');
    }
    updateVoiceControls();
}

function toggleListening() {
    if (!voiceModeEnabled || !recognition) return;
    if (recognitionPhase === 'listening') {
        recognitionPhase = 'stopping';
        recognition.stop();
        return;
    }
    if (recognitionPhase !== 'idle') return;
    if (activePatientAudio) {
        activePatientAudio.pause();
        activePatientAudio = null;
    }
    window.speechSynthesis?.cancel();
    patientSpeechActive = false;
    recognitionErrorMessage = '';
    autoSendAfterRecognition = false;
    recognitionPhase = 'starting';
    try {
        recognition.start();
    } catch (e) {
        recognitionPhase = 'idle';
        const message = 'Could not start microphone capture. Check browser microphone permission and try again.';
        updateVoiceStatus('Mic blocked');
        showVoiceAlert(message, 'warning');
    }
}

function updateVoiceControls() {
    const toggle = document.getElementById('voiceToggle');
    const mic = document.getElementById('micButton');
    const autosend = document.getElementById('voiceAutoSend');
    if (toggle) {
        toggle.classList.toggle('active', voiceModeEnabled);
        toggle.setAttribute('aria-pressed', String(voiceModeEnabled));
    }
    if (mic) mic.disabled = !voiceModeEnabled || !SpeechRecognition;
    if (autosend) autosend.disabled = !voiceModeEnabled;
    if (voiceModeEnabled && !SpeechRecognition) {
        updateVoiceStatus('Patient voice only');
    } else {
        updateVoiceStatus(voiceModeEnabled ? 'Voice ready' : 'Off');
    }
}

function updateVoiceStatus(text) {
    const status = document.getElementById('voiceStatus');
    if (status) status.textContent = text;
}

function showVoiceAlert(message, type = 'info') {
    const alert = document.getElementById('voiceAlert');
    if (!alert) return;
    alert.textContent = message || '';
    alert.className = `voice-alert ${message ? 'show' : ''} ${type}`;
}

function speakPatientResponse(text, audioUrl = '') {
    if (!voiceModeEnabled) return;
    stopListeningForPatientSpeech();
    if (activePatientAudio) {
        activePatientAudio.pause();
        activePatientAudio = null;
    }
    window.speechSynthesis?.cancel();

    if (audioUrl) {
        activePatientAudio = new Audio(audioUrl);
        activePatientAudio.onplay = () => beginPatientSpeech();
        activePatientAudio.onended = () => endPatientSpeech();
        activePatientAudio.onerror = () => {
            endPatientSpeech();
            showVoiceAlert('Server audio could not play. Falling back to browser voice.', 'warning');
            speakWithBrowserVoice(text);
        };
        activePatientAudio.play().catch(() => {
            showVoiceAlert('Server audio could not autoplay. Falling back to browser voice.', 'warning');
            speakWithBrowserVoice(text);
        });
        return;
    }
    speakWithBrowserVoice(text);
}

function stopListeningForPatientSpeech() {
    if (recognitionPhase === 'starting' || recognitionPhase === 'listening') {
        recognitionPhase = 'stopping';
        try { recognition.stop(); } catch (e) { recognitionPhase = 'idle'; }
    }
}

function beginPatientSpeech() {
    patientSpeechActive = true;
    stopListeningForPatientSpeech();
    updateVoiceStatus('Patient speaking');
}

function endPatientSpeech() {
    patientSpeechActive = false;
    if (recognitionPhase === 'idle') updateVoiceStatus(voiceModeEnabled ? 'Voice ready' : 'Off');
}

function speakWithBrowserVoice(text) {
    if (!voiceModeEnabled || !window.speechSynthesis || !text) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.95;
    utterance.pitch = 1;
    utterance.onstart = () => beginPatientSpeech();
    utterance.onend = () => endPatientSpeech();
    utterance.onerror = () => endPatientSpeech();
    window.speechSynthesis.speak(utterance);
}

