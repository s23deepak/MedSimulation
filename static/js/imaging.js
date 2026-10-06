function setupImaging(sessionData) {
    const imagingBtn = document.getElementById('tabBtnImaging');
    const countSpan = document.getElementById('imagingCount');
    const grid = document.getElementById('imagingGrid');

    const studies = sessionData.imaging_studies || [];

    if (studies.length === 0) {
        imagingBtn.classList.add('hidden');
        grid.innerHTML = '<p class="empty-text">No imaging studies available for this case.</p>';
        return;
    }

    imagingBtn.classList.remove('hidden');
    countSpan.textContent = studies.length;
    grid.innerHTML = '';

    studies.forEach(study => {
        const icon = modalityIcons[study.modality] || "\uD83D\uDDBC\uFE0F";
        const card = document.createElement('div');
        card.className = 'imaging-card';
        card.id = `img-card-${study.study_id}`;
        card.setAttribute('tabindex', '0');

        // Check if we have a preview image (thumbnail or file_path)
        const hasPreview = study.thumbnail || (study.file_path && study.file_path.startsWith('http'));
        const previewUrl = study.thumbnail || study.file_path || '';

        if (hasPreview) {
            // Show image preview with modality badge
            card.innerHTML = `
                <div class="imaging-preview">
                    <img src="${escapeHtml(previewUrl)}" alt="${escapeHtml(study.modality)}" loading="lazy" />
                </div>
                <div class="imaging-modality-badge">${escapeHtml(study.modality)}</div>
                <div class="imaging-desc">${escapeHtml(study.description)}</div>
            `;
        } else {
            // Show modality icon (no preview available)
            card.innerHTML = `
                <div class="imaging-icon">${icon}</div>
                <div class="imaging-modality">${escapeHtml(study.modality)}</div>
                <div class="imaging-desc">${escapeHtml(study.description)}</div>
            `;
        }

        const openStudy = () => openImagingStudy(study.study_id);
        card.onclick = openStudy;
        card.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openStudy(); } };
        grid.appendChild(card);
    });
}

async function openImagingStudy(studyId) {
    if (!currentSession) return;
    try {

    const res = await apiFetch('/api/simulation/imaging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: currentSession.session_id, study_id: studyId }),
    });
    const data = await res.json();
    if (data.error) return showToast(data.error, 'error');

    const card = document.getElementById(`img-card-${studyId}`);
    if (card) card.classList.add('viewed');

    document.getElementById('lbTitle').textContent = `${data.modality} \u2014 ${data.description}`;
    document.getElementById('lbFindingsText').textContent = data.findings;
    document.getElementById('lbFindingsText').classList.remove('show');
    document.getElementById('lbFindingsBtn').style.display = 'block';

    document.getElementById('lbContrast').value = 100;
    document.getElementById('lbBrightness').value = 100;
    currentZoom = 100;  // Reset zoom when opening new image

    const body = document.getElementById('lbBody');
    body.innerHTML = '';

    if (!data.image_url) {
        body.innerHTML = `
            <div class="no-image-placeholder">
                <div class="no-image-icon">🩻</div>
                <div class="no-image-modality">${escapeHtml(data.modality)}</div>
                <p class="no-image-msg">No image file is available for this case.<br>Use <strong>Interpret Findings</strong> below to view the radiological description.</p>
            </div>`;
        document.getElementById('lbContrast').closest('.lightbox-controls').style.display = 'none';
        document.getElementById('lightboxOverlay').classList.add('active');
        document.body.style.overflow = 'hidden';
    } else if (data.image_url.startsWith('dicom/')) {
        body.innerHTML = `<div class="dicom-viewer" id="dicomViewer"><span class="spinner show dicom-spinner" id="dicomSpinner"></span></div>`;
        document.getElementById('lightboxOverlay').classList.add('active');
        document.body.style.overflow = 'hidden';

        try {
            const listRes = await apiFetch(`/api/simulation/imaging/dicom_list?case_id=${currentSession.case_id}`);
            const listData = await listRes.json();
            if (!listData.urls || listData.urls.length === 0) throw new Error("No DICOM slices found");

            const element = document.getElementById('dicomViewer');
            const spinner = document.getElementById('dicomSpinner');

            cornerstoneWADOImageLoader.external.cornerstone = cornerstone;
            cornerstoneWADOImageLoader.external.dicomParser = dicomParser;
            cornerstoneTools.external.cornerstone = cornerstone;
            cornerstoneTools.external.Hammer = Hammer;
            cornerstoneTools.external.cornerstoneMath = cornerstoneMath;

            cornerstoneTools.init();
            cornerstone.enable(element);

            const imageIds = listData.urls.map(url => 'wadouri:' + location.origin + url);
            const stack = {
                currentImageIdIndex: Math.floor(imageIds.length / 2),
                imageIds: imageIds
            };

            const image = await cornerstone.loadAndCacheImage(imageIds[stack.currentImageIdIndex]);
            cornerstone.displayImage(element, image);

            cornerstoneTools.addStackStateManager(element, ['stack']);
            cornerstoneTools.addToolState(element, 'stack', stack);

            cornerstoneTools.addTool(cornerstoneTools.WwwcTool);
            cornerstoneTools.addTool(cornerstoneTools.StackScrollMouseWheelTool);
            cornerstoneTools.addTool(cornerstoneTools.ZoomMouseWheelTool);
            cornerstoneTools.addTool(cornerstoneTools.PanTool);

            cornerstoneTools.setToolActive('Wwwc', { mouseButtonMask: 1 });
            cornerstoneTools.setToolActive('Pan', { mouseButtonMask: 4 });
            cornerstoneTools.setToolActive('StackScrollMouseWheel', {});

            spinner.classList.remove('show');
        } catch (e) {
            console.error(e);
            document.getElementById('dicomViewer').innerHTML = `<p class="dicom-error">Failed to load DICOM viewer: ${escapeHtml(e.message)}</p>`;
        }
    } else if (data.image_url.endsWith('.svg')) {
        body.innerHTML = '<div class="lb-loading"><span class="spinner-dot"></span></div>';
        try {
            await apiFetch(safeMediaUrl(data.image_url));
            const image = document.createElement('img');
            image.id = 'lbImage';
            image.alt = 'Imaging study';
            image.src = safeMediaUrl(data.image_url);
            body.replaceChildren(image);
            setTimeout(() => initImagePan(), 100);
        } catch (e) {
            const image = document.createElement('img');
            image.id = 'lbImage'; image.alt = 'Imaging study'; image.src = safeMediaUrl(data.image_url);
            body.replaceChildren(image);
        }
        document.getElementById('lightboxOverlay').classList.add('active');
        document.body.style.overflow = 'hidden';
        setTimeout(() => initImagePan(), 100);
    } else {
        const image = document.createElement('img');
        image.id = 'lbImage'; image.alt = 'Imaging study'; image.src = safeMediaUrl(data.image_url);
        body.replaceChildren(image);
        document.getElementById('lightboxOverlay').classList.add('active');
        document.body.style.overflow = 'hidden';
        setTimeout(() => initImagePan(), 100);
    }
    } catch (error) {
        showToast(`Imaging unavailable: ${error.message}. Select the study to retry.`);
    }
}

function closeLightbox() {
    document.getElementById('lightboxOverlay').classList.remove('active');
    document.body.style.overflow = '';
    document.getElementById('lbBody').innerHTML = '';
    const ctrl = document.getElementById('lbContrast');
    if (ctrl) ctrl.closest('.lightbox-controls').style.display = '';
    currentZoom = 100;  // Reset zoom when closing
}

function toggleFindings() {
    const text = document.getElementById('lbFindingsText');
    text.classList.toggle('show');
    const btn = document.getElementById('lbFindingsBtn');
    btn.textContent = text.classList.contains('show') ? 'Hide Interpretation' : 'Interpret Findings';
}

// ── Lightbox Zoom ──
let currentZoom = 100;
const ZOOM_STEP = 25;
const MIN_ZOOM = 25;
const MAX_ZOOM = 500;
let isDragging = false;
let startX, startY, translateX = 0, translateY = 0;

function updateZoomDisplay() {
    const zoomDisplay = document.getElementById('zoomLevel');
    if (zoomDisplay) {
        zoomDisplay.textContent = `${currentZoom}%`;
    }
}

function applyZoom() {
    const lbBody = document.getElementById('lbBody');
    if (!lbBody) return;
    const img = lbBody.querySelector('img, svg');
    if (!img) return;

    // Enable panning when zoomed in
    if (currentZoom > 100) {
        img.style.cursor = 'grab';
        lbBody.style.overflow = 'hidden';
    } else {
        img.style.cursor = 'default';
        lbBody.style.overflow = 'auto';
        translateX = 0;
        translateY = 0;
    }

    img.style.transform = `scale(${currentZoom / 100}) translate(${translateX}px, ${translateY}px)`;
    img.style.transformOrigin = 'center center';
    img.style.transition = isDragging ? 'none' : 'transform 0.15s ease';
    updateZoomDisplay();
}

function zoomIn() {
    if (currentZoom < MAX_ZOOM) {
        currentZoom = Math.min(MAX_ZOOM, currentZoom + ZOOM_STEP);
        applyZoom();
    }
}

function zoomOut() {
    if (currentZoom > MIN_ZOOM) {
        currentZoom = Math.max(MIN_ZOOM, currentZoom - ZOOM_STEP);
        applyZoom();
    }
}

function zoomReset() {
    currentZoom = 100;
    translateX = 0;
    translateY = 0;
    applyZoom();
}

// Pan functionality for zoomed images
function initImagePan() {
    const lbBody = document.getElementById('lbBody');
    if (!lbBody) return;
    const img = lbBody.querySelector('img, svg');
    if (!img) return;

    // Remove any existing transition for instant response during drag
    img.style.transition = 'none';

    // Calculate pan boundaries based on zoom level and image/container size
    function getPanLimits() {
        const containerWidth = lbBody.clientWidth;
        const containerHeight = lbBody.clientHeight;
        const imgWidth = img.offsetWidth * (currentZoom / 100);
        const imgHeight = img.offsetHeight * (currentZoom / 100);

        return {
            minX: imgWidth <= containerWidth ? 0 : -(imgWidth - containerWidth) / 2,
            maxX: imgWidth <= containerWidth ? 0 : (imgWidth - containerWidth) / 2,
            minY: imgHeight <= containerHeight ? 0 : -(imgHeight - containerHeight) / 2,
            maxY: imgHeight <= containerHeight ? 0 : (imgHeight - containerHeight) / 2
        };
    }

    function clampPan() {
        const limits = getPanLimits();
        translateX = Math.max(limits.minX, Math.min(limits.maxX, translateX));
        translateY = Math.max(limits.minY, Math.min(limits.maxY, translateY));
    }

    img.addEventListener('mousedown', (e) => {
        if (currentZoom <= 100) return;
        isDragging = true;
        startX = e.clientX - translateX;
        startY = e.clientY - translateY;
        img.style.cursor = 'grabbing';
        img.style.transition = 'none';  // Ensure no transition during drag
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDragging || currentZoom <= 100) return;
        e.preventDefault();
        translateX = e.clientX - startX;
        translateY = e.clientY - startY;
        clampPan();  // Keep pan within bounds
        img.style.transform = `scale(${currentZoom / 100}) translate(${translateX}px, ${translateY}px)`;
    });

    document.addEventListener('mouseup', () => {
        if (isDragging) {
            isDragging = false;
            const imgEl = lbBody.querySelector('img, svg');
            if (imgEl) {
                imgEl.style.cursor = 'grab';
                imgEl.style.transition = 'transform 0.15s ease';  // Re-enable transition
            }
        }
    });

    // Touch support for mobile
    img.addEventListener('touchstart', (e) => {
        if (currentZoom <= 100) return;
        isDragging = true;
        const touch = e.touches[0];
        startX = touch.clientX - translateX;
        startY = touch.clientY - translateY;
        img.style.transition = 'none';
    }, { passive: true });

    document.addEventListener('touchmove', (e) => {
        if (!isDragging || currentZoom <= 100) return;
        e.preventDefault();
        const touch = e.touches[0];
        translateX = touch.clientX - startX;
        translateY = touch.clientY - startY;
        clampPan();  // Keep pan within bounds
        img.style.transform = `scale(${currentZoom / 100}) translate(${translateX}px, ${translateY}px)`;
    }, { passive: true });

    document.addEventListener('touchend', () => {
        if (isDragging) {
            isDragging = false;
            const imgEl = lbBody.querySelector('img, svg');
            if (imgEl) {
                imgEl.style.transition = 'transform 0.15s ease';
            }
        }
    });
}

// Keyboard shortcuts for zoom
document.addEventListener('keydown', (e) => {
    // Only handle zoom shortcuts when lightbox is open
    if (!document.getElementById('lightboxOverlay').classList.contains('active')) return;

    // Ctrl/Cmd + for zoom in
    if ((e.ctrlKey || e.metaKey) && e.key === '+') {
        e.preventDefault();
        zoomIn();
    }
    // Ctrl/Cmd - for zoom out
    if ((e.ctrlKey || e.metaKey) && e.key === '-') {
        e.preventDefault();
        zoomOut();
    }
    // Ctrl/Cmd 0 for reset
    if ((e.ctrlKey || e.metaKey) && e.key === '0') {
        e.preventDefault();
        zoomReset();
    }
});

function updateLbFilters() {
    const img = document.getElementById('lbBody').querySelector('img, svg');
    if (!img) return;
    const c = document.getElementById('lbContrast').value;
    const b = document.getElementById('lbBrightness').value;
    // Preserve zoom transform while updating filters
    img.style.filter = `contrast(${c}%) brightness(${b}%)`;
    img.style.transform = `scale(${currentZoom / 100})`;
}

// ── Timer ──
