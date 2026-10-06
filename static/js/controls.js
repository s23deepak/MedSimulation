function initCustomDropdown() {
    const dropdown = document.getElementById('sourceDropdown');
    const trigger = document.getElementById('dropdownTrigger');
    const menu = document.getElementById('dropdownMenu');
    const valueDisplay = document.getElementById('dropdownValue');
    const hiddenInput = document.getElementById('sourceSelect');
    const options = menu.querySelectorAll('.dropdown-option');

    if (!dropdown || !trigger || !menu) return;

    // Toggle dropdown
    trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const isShown = menu.classList.contains('show');
        closeAllDropdowns();
        if (!isShown) {
            menu.classList.add('show');
            trigger.classList.add('active');
        }
    });

    // Handle option selection
    options.forEach(option => {
        option.addEventListener('click', (e) => {
            e.stopPropagation();
            const selectedValue = option.dataset.value;
            const selectedText = option.querySelector('.option-text').textContent;

            // Update hidden input
            hiddenInput.value = selectedValue;

            // Update display
            valueDisplay.textContent = selectedText;

            // Update aria-selected
            options.forEach(opt => opt.setAttribute('aria-selected', 'false'));
            option.setAttribute('aria-selected', 'true');

            // Close dropdown
            menu.classList.remove('show');
            trigger.classList.remove('active');

            // Enable search button if topic is entered
            const topicInput = document.getElementById('topicInput');
            if (topicInput && topicInput.value.trim()) {
                document.getElementById('searchBtn').disabled = false;
            }
        });
    });

    // Close on outside click
    document.addEventListener('click', (e) => {
        if (!dropdown.contains(e.target)) {
            menu.classList.remove('show');
            trigger.classList.remove('active');
        }
    });

    // Keyboard navigation
    trigger.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            trigger.click();
        }
        if (e.key === 'Escape') {
            menu.classList.remove('show');
            trigger.classList.remove('active');
        }
    });

    options.forEach((option, index) => {
        option.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const next = options[(index + 1) % options.length];
                next.focus();
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                const prev = options[(index - 1 + options.length) % options.length];
                prev.focus();
            }
        });
    });
}

function closeAllDropdowns() {
    document.querySelectorAll('.dropdown-menu').forEach(menu => {
        menu.classList.remove('show');
    });
    document.querySelectorAll('.dropdown-trigger').forEach(trigger => {
        trigger.classList.remove('active');
    });
}

// ═══════════════════════════════════════════════════════════════════════════
// Page Load Initialization
// ═══════════════════════════════════════════════════════════════════════════

// Initialize on page load
window.addEventListener('load', () => {
    initCustomDropdown();
});
