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
