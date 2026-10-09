"""Browser checks for consecutive dictated history questions."""

import os
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.getenv("RUN_BROWSER_QA"), reason="Browser QA not requested")
def test_voice_capture_recovers_for_second_turn():
    from playwright.sync_api import sync_playwright

    static_js = Path(__file__).resolve().parents[1] / "static" / "js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
            <button id="voiceToggle"></button><button id="micButton"></button>
            <input id="voiceAutoSend" type="checkbox" checked>
            <input id="historyInput"><span id="voiceStatus"></span>
            <div id="voiceAlert"></div><div id="toastContainer"></div>
        """)
        page.evaluate("""() => {
            window.submittedQuestions = [];
            window.SpeechRecognition = window.webkitSpeechRecognition = class {
                constructor() { window.fakeRecognizer = this; this.starts = 0; }
                start() { this.starts++; this.onstart(); }
                stop() { queueMicrotask(() => this.onend()); }
                final(text) {
                    this.onresult({resultIndex: 0, results: [
                        Object.assign([{transcript: text}], {isFinal: true})
                    ]});
                }
            };
            window.askHistory = () => submittedQuestions.push(
                document.getElementById('historyInput').value
            );
        }""")
        page.add_script_tag(path=str(static_js / "state.js"))
        page.add_script_tag(path=str(static_js / "voice.js"))

        result = page.evaluate("""async () => {
            recognition = initializeVoiceRecognition();
            toggleVoiceMode();
            toggleListening();
            fakeRecognizer.final('Where does it hurt?');
            const sentBeforeMicStopped = submittedQuestions.length;
            await new Promise(resolve => setTimeout(resolve, 0));
            beginPatientSpeech();
            toggleListening();
            fakeRecognizer.final('When did it start?');
            await new Promise(resolve => setTimeout(resolve, 0));
            return {
                questions: submittedQuestions,
                sentBeforeMicStopped,
                starts: fakeRecognizer.starts,
                phase: recognitionPhase,
                patientSpeaking: patientSpeechActive,
                alert: document.getElementById('voiceAlert').textContent,
            };
        }""")
        assert result == {
            "questions": ["Where does it hurt?", "When did it start?"],
            "sentBeforeMicStopped": 0,
            "starts": 2,
            "phase": "idle",
            "patientSpeaking": False,
            "alert": "",
        }

        error_result = page.evaluate("""() => {
            toggleListening();
            fakeRecognizer.onerror({error: 'audio-capture'});
            fakeRecognizer.onend();
            const message = document.getElementById('voiceAlert').textContent;
            toggleListening();
            return {message, starts: fakeRecognizer.starts, phase: recognitionPhase};
        }""")
        assert "Microphone unavailable" in error_result["message"]
        assert error_result["starts"] == 4
        assert error_result["phase"] == "listening"
        browser.close()
