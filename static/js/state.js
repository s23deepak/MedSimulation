
// ── State ──
let currentSession = null;
let timerInterval = null;
let timerSeconds = 0;
const allResults = [];
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let voiceModeEnabled = false;
let recognition = null;
let isListening = false;
let activePatientAudio = null;
let patientSpeechActive = false;

// ── Toast notifications ──
function showToast(message, type = 'error', duration = 5000) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('removing');
        toast.addEventListener('animationend', () => toast.remove());
    }, duration);
}

