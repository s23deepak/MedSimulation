async function apiFetch(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
    if (!response.ok) {
        const error = await response.clone().json().catch(() => ({}));
        throw new Error(typeof error.detail === 'string' ? error.detail : `Request failed (${response.status}). Check your entries and retry.`);
    }
    return response;
}

function textNode(tag, value, className = '') {
    const element = document.createElement(tag);
    element.textContent = value ?? '';
    element.className = className;
    return element;
}

function safeMediaUrl(value) {
    try {
        const url = new URL(value, location.origin);
        return url.origin === location.origin && /^\/(static|imaging)\//.test(url.pathname) ? url.href : '';
    } catch { return ''; }
}

function appendChat(role, message, timestamp = new Date().toISOString()) {
    const row = document.createElement('div');
    row.className = `msg msg-${role === 'You' ? 'resident' : 'patient'}`;
    const label = textNode('div', role, 'msg-label');
    const time = textNode('time', new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    time.dateTime = timestamp;
    label.append(' ', time);
    row.append(label, textNode('div', message, 'msg-bubble'));
    const container = document.getElementById('chatMessages');
    container.append(row);
    container.scrollTop = container.scrollHeight;
}

function initChatScrollbar() {
    const panel = document.querySelector('.conversation-column .panel');
    const messages = document.getElementById('chatMessages');
    const rail = document.getElementById('chatScrollbar');
    const thumb = document.getElementById('chatScrollbarThumb');
    if (!panel || !messages || !rail || !thumb) return;

    function fitPanel() {
        const top = panel.getBoundingClientRect().top;
        const available = window.innerWidth > 760 && top < window.innerHeight - 360
            ? window.innerHeight - Math.max(0, top) - 16
            : window.innerHeight - 100;
        panel.style.setProperty('--chat-panel-height', `${Math.max(360, Math.min(880, available))}px`);
        updateThumb();
    }

    function updateThumb() {
        const trackHeight = rail.clientHeight;
        const scrollRange = Math.max(0, messages.scrollHeight - messages.clientHeight);
        const thumbHeight = scrollRange
            ? Math.max(28, trackHeight * messages.clientHeight / messages.scrollHeight)
            : trackHeight;
        const travel = Math.max(0, trackHeight - thumbHeight);
        thumb.style.height = `${thumbHeight}px`;
        thumb.style.transform = `translateY(${scrollRange ? travel * messages.scrollTop / scrollRange : 0}px)`;
    }

    let pointerOffset = 0;
    rail.addEventListener('pointerdown', (event) => {
        const thumbRect = thumb.getBoundingClientRect();
        pointerOffset = event.target === thumb
            ? event.clientY - thumbRect.top
            : thumbRect.height / 2;
        rail.setPointerCapture(event.pointerId);
        event.preventDefault();
        moveToPointer(event);
    });

    function moveToPointer(event) {
        const travel = rail.clientHeight - thumb.clientHeight;
        if (travel <= 0) return;
        const thumbTop = Math.max(0, Math.min(travel,
            event.clientY - rail.getBoundingClientRect().top - pointerOffset));
        messages.scrollTop = thumbTop / travel * (messages.scrollHeight - messages.clientHeight);
    }

    rail.addEventListener('pointermove', (event) => {
        if (rail.hasPointerCapture(event.pointerId)) moveToPointer(event);
    });
    messages.addEventListener('scroll', updateThumb);
    new ResizeObserver(updateThumb).observe(messages);
    new MutationObserver(updateThumb).observe(messages, { childList: true });
    const simulationScreen = document.getElementById('screen-sim');
    new MutationObserver(() => {
        if (simulationScreen.classList.contains('active')) requestAnimationFrame(fitPanel);
    }).observe(simulationScreen, { attributes: true, attributeFilter: ['class'] });
    window.addEventListener('resize', fitPanel);
    if (simulationScreen.classList.contains('active')) fitPanel();
}

document.addEventListener('DOMContentLoaded', initChatScrollbar);
