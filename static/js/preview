// ============================================================
// HALFTONE RIP PRO - Preview & Differences Module
// Maneja: visualización de separaciones, composición, diffs
// ============================================================

const PreviewModule = (function() {
    'use strict';

    // === STATE ===
    let lastJobName = null;
    let separationImages = [];
    let viewMode = 'individual';
    let compositeMode = 'overprint';
    let refImageData = null;
    let layerSettings = [];
    let loadedImages = {};
    let isAnalyzing = false;

    // === DOM CACHE ===
    const $ = (id) => document.getElementById(id);
    const $$ = (sel) => document.querySelectorAll(sel);

    // === INITIALIZATION ===
    function init() {
        bindEvents();
        bindRipEvents();
        initCrossfadeControl();
        logPreview('🎨 Módulo Preview inicializado', 'success');
    }

    function bindEvents() {
        // View mode tabs: se usa el atributo data-mode de cada botón
        // (ya presente en el HTML) en vez de un array de posiciones fijo,
        // que quedaba desincronizado si se agregaba o quitaba un botón.
        const viewTabs = $$('.view-tab');
        viewTabs.forEach((tab) => {
            tab.onclick = () => setViewMode(tab.dataset.mode);
        });

        // Composite mode buttons
        const modeBtns = $$('.mode-btn');
        modeBtns.forEach(btn => {
            btn.onclick = () => setCompositeMode(btn.dataset.mode);
        });

        // Reference image
        const refDrop = $('ref-drop');
        const refInput = $('ref-input');

        if (refDrop) {
            refDrop.onclick = (e) => {
                if (e.target.tagName !== 'INPUT') refInput?.click();
            };
            refDrop.addEventListener('dragover', (e) => {
                e.preventDefault();
                refDrop.style.borderColor = 'var(--accent-primary)';
            });
            refDrop.addEventListener('dragleave', () => {
                refDrop.style.borderColor = '';
            });
            refDrop.addEventListener('drop', onRefDrop);
        }

        if (refInput) refInput.onchange = (e) => {
            if (e.target.files[0]) loadRefFile(e.target.files[0]);
        };

        if ($('btn-demo-ref')) $('btn-demo-ref').onclick = loadDemoRef;
        if ($('btn-clear-ref')) $('btn-clear-ref').onclick = clearRef;
        if ($('diff-opacity')) $('diff-opacity').oninput = updateDiffOpacity;
        if ($('analyze-btn')) $('analyze-btn').onclick = runAnalysis;

        if ($('composite-bg-color')) {
            $('composite-bg-color').addEventListener('input', () => {
                if (viewMode === 'composite') renderComposite();
            });
        }

        // Tab switch listener
        const tabPreview = $('tab-preview');
        if (tabPreview) {
            tabPreview.addEventListener('click', () => {
                if (lastJobName) loadSeparationsPreview();
            });
        }
    }

    function bindRipEvents() {
        window.addEventListener('rip:jobComplete', (e) => {
            lastJobName = e.detail.jobName;
            logPreview(`✅ Trabajo recibido: ${lastJobName}`, 'success');
            // Auto-switch to preview tab after a delay
            setTimeout(() => {
                const tabPreview = $('tab-preview');
                if (tabPreview) tabPreview.click();
            }, 800);
        });
    }

    // === SEPARATIONS LOADING ===
    async function loadSeparationsPreview() {
        if (!lastJobName) {
            showEmptyState('Procesa un archivo primero para ver las separaciones');
            return;
        }

        logPreview('Cargando separaciones...');

        try {
            const resp = await fetch('/api/separation_info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_name: lastJobName })
            });
            const data = await resp.json();

            if (data.error) throw new Error(data.error);

            separationImages = data.separations || [];
            updateStats();

            // Initialize layer settings
            layerSettings = separationImages.map((sep, idx) => ({
                name: sep.name,
                filename: sep.filename,
                visible: true,
                opacity: 1,
                color: sep.hex_color,
                is_cmyk: sep.is_cmyk,
                order: idx
            }));

            renderPreviewByMode();
            logPreview(`✅ ${separationImages.length} separaciones cargadas`, 'success');
        } catch (err) {
            logPreview(`❌ Error cargando separaciones: ${err.message}`, 'error');
            showEmptyState('Error cargando separaciones. Intenta de nuevo.');
        }
    }

    function updateStats() {
        const cmykCount = separationImages.filter(s => s.is_cmyk).length;
        const spotCount = separationImages.filter(s => !s.is_cmyk).length;

        if ($('stat-sep-count')) $('stat-sep-count').textContent = separationImages.length;
        if ($('stat-cmyk-count')) $('stat-cmyk-count').textContent = cmykCount;
        if ($('stat-spot-count')) $('stat-spot-count').textContent = spotCount;
    }

    function showEmptyState(msg) {
        const container = $('separations-container');
        if (container) {
            container.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:3rem;color:var(--text-muted);font-family:var(--font-mono);font-size:0.8rem;">${msg}</div>`;
        }
    }

    // === VIEW MODES ===
    function setViewMode(mode) {
        viewMode = mode;
        document.querySelectorAll('.view-tab').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        renderPreviewByMode();
    }

    function setCompositeMode(mode) {
        compositeMode = mode;
        document.querySelectorAll('.mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        if (viewMode === 'composite') renderComposite();
    }

    function renderPreviewByMode() {
        const container = $('separations-container');
        const compositeDiv = $('composite-container');
        if (!container || !compositeDiv) return;

        if (viewMode === 'composite') {
            container.style.display = 'none';
            compositeDiv.style.display = 'block';
            renderLayersList();
            renderComposite();
            return;
        }

        container.style.display = 'grid';
        compositeDiv.style.display = 'none';

        if (separationImages.length === 0) {
            showEmptyState('Procesa un archivo primero para ver las separaciones');
            return;
        }

        if (viewMode === 'individual') {
            renderIndividualSeparations(container);
        }
    }

    function renderIndividualSeparations(container) {
        container.innerHTML = separationImages.map((sep, idx) => `
            <div class="sep-thumb" style="animation-delay:${idx * 0.03}s" class="animate-fade-in">
                <div class="sep-thumb-img" id="sep-bg-${sep.filename}" style="background-color: transparent; transition: background-color 0.3s; display: flex; justify-content: center; align-items: center; overflow: hidden;">
                    <img id="sep-img-${sep.filename}" src="/api/download/${lastJobName}/${sep.filename}" alt="${sep.name}" loading="lazy" style="mix-blend-mode: normal; transition: mix-blend-mode 0.3s;" ondblclick="PreviewModule.openFullscreen(this.src)">
                </div>
                <div class="sep-thumb-info">
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                        <div class="sep-thumb-color" style="background:${sep.hex_color}" onclick="PreviewModule.openColorPicker('${sep.filename}', '${sep.hex_color}')" title="${sep.is_cmyk ? '' : 'Color de referencia (no el Pantone exacto) - haz clic para corregirlo al color real'}"></div>
                        <span class="sep-thumb-name">${sep.name}</span>
                    </div>
                    <span class="sep-thumb-badge" style="background:${sep.is_cmyk ? 'var(--accent-primary)' : 'var(--accent-secondary)'}">${sep.is_cmyk ? 'CMYK' : 'SPOT'}</span>
                    <div class="sep-thumb-size">${sep.size[0]} x ${sep.size[1]} px</div>
                    <div style="display:flex;align-items:center;gap:4px;margin-top:4px;">
                        ${!sep.is_cmyk ? `<input type="color" class="color-picker-input" value="${sep.hex_color}" onchange="PreviewModule.updateSepColor('${sep.filename}', this.value)" style="width: 24px; height: 24px; padding: 0; border: none; cursor: pointer;">` : ''}
                        <input type="text" class="form-input" value="${sep.hex_color}" onchange="PreviewModule.updateSepColor('${sep.filename}', this.value)" style="padding: 2px 6px; font-size: 0.75rem; width: 70px; font-family: var(--font-mono); text-align: center;" placeholder="#HEX">
                        <button onclick="PreviewModule.toggleColorize('${sep.filename}')" class="btn-secondary" style="padding: 2px 6px; font-size: 0.7rem; border-radius: 4px; border: 1px solid var(--border-medium); background: var(--bg-tertiary); color: var(--text-primary); cursor: pointer; display: flex; align-items: center; gap: 4px;" title="Alternar previsualización de color"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/></svg> Color</button>
                    </div>
                </div>
            </div>
        `).join('');
    }

    function renderColoredSeparations(container) {
        container.innerHTML = separationImages.map((sep, idx) => `
            <div class="sep-thumb" style="animation-delay:${idx * 0.03}s" class="animate-fade-in">
                <div class="sep-thumb-img">
                    <img src="/api/separation_preview/${lastJobName}/${sep.filename}" alt="${sep.name}" loading="lazy">
                </div>
                <div class="sep-thumb-info">
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                        <div class="sep-thumb-color" style="background:${sep.hex_color}"></div>
                        <span class="sep-thumb-name">${sep.name}</span>
                    </div>
                    <div style="font-size:0.6rem;color:var(--text-muted);font-family:var(--font-mono);text-align:center;padding:4px;background:var(--bg-deep);border-radius:4px;">${sep.hex_color}</div>
                </div>
            </div>
        `).join('');
    }

    // === COMPOSITE RENDERING ===
    async function renderComposite() {
        const canvas = $('composite-canvas');
        if (!canvas || !layerSettings.length) return;

        // Load all images
        loadedImages = {};
        for (const layer of layerSettings) {
            if (!loadedImages[layer.filename]) {
                try {
                    const resp = await fetch(`/api/download/${lastJobName}/${layer.filename}`);
                    const blob = await resp.blob();
                    const img = await new Promise((resolve, reject) => {
                        const i = new Image();
                        i.onload = () => resolve(i);
                        i.onerror = reject;
                        i.src = URL.createObjectURL(blob);
                    });
                    loadedImages[layer.filename] = img;
                } catch (err) {
                    console.warn('Error cargando imagen:', err);
                }
            }
        }

        const firstImg = loadedImages[layerSettings[0]?.filename];
        if (!firstImg) return;

        // Scale down for preview if image is too large to prevent browser crash
        const MAX_DIM = 1200;
        let scale = 1;
        if (firstImg.width > MAX_DIM || firstImg.height > MAX_DIM) {
            scale = Math.min(MAX_DIM / firstImg.width, MAX_DIM / firstImg.height);
        }

        canvas.width = Math.floor(firstImg.width * scale);
        canvas.height = Math.floor(firstImg.height * scale);
        const ctx = canvas.getContext('2d', { willReadFrequently: true });

        // Garment Background
        const bgColor = document.getElementById('composite-bg-color') ? document.getElementById('composite-bg-color').value : '#ffffff';
        ctx.fillStyle = bgColor;
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        const sortedLayers = [...layerSettings].sort((a, b) => a.order - b.order);

        for (const layer of sortedLayers) {
            if (!layer.visible) continue;
            const img = loadedImages[layer.filename];
            if (!img) continue;

            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = canvas.width;
            tempCanvas.height = canvas.height;
            const tempCtx = tempCanvas.getContext('2d', { willReadFrequently: true });
            
            // Draw scaled image
            tempCtx.drawImage(img, 0, 0, canvas.width, canvas.height);

            const imageData = tempCtx.getImageData(0, 0, canvas.width, canvas.height);
            const pixels = imageData.data;
            const color = hexToRgb(layer.color);

            for (let i = 0; i < pixels.length; i += 4) {
                const inkIntensity = (255 - pixels[i]) / 255;
                if (inkIntensity > 0.05) {
                    const alpha = inkIntensity * layer.opacity;
                    pixels[i] = color.r;
                    pixels[i + 1] = color.g;
                    pixels[i + 2] = color.b;
                    pixels[i + 3] = Math.floor(alpha * 255);
                } else {
                    pixels[i + 3] = 0;
                }
            }
            tempCtx.putImageData(imageData, 0, 0);

            let op = 'source-over';
            if (compositeMode === 'overprint') {
                // CMYK inks are transparent (multiply)
                // Spot colors and underbases are usually opaque (source-over)
                op = layer.is_cmyk ? 'multiply' : 'source-over';
                
                // Force opaque for very light colors (like white underbase)
                if (color.r > 220 && color.g > 220 && color.b > 220) {
                    op = 'source-over';
                }
            }
            ctx.globalCompositeOperation = op;
            ctx.drawImage(tempCanvas, 0, 0);
        }
        
        // Ensure canvas scales nicely in CSS
        canvas.style.maxWidth = '100%';
        canvas.style.height = 'auto';
        canvas.style.display = 'block';

        const activeCount = layerSettings.filter(l => l.visible).length;
        if ($('stat-total-coverage')) $('stat-total-coverage').textContent = activeCount;
        if ($('composite-mode-label')) $('composite-mode-label').textContent = compositeMode.toUpperCase();
    }

    function renderLayersList() {
        const container = $('layers-list');
        if (!container) return;

        const sorted = [...layerSettings].sort((a, b) => a.order - b.order);
        container.innerHTML = '';

        sorted.forEach(layer => {
            const div = document.createElement('div');
            div.className = 'layer-item';
            div.draggable = true;
            div.dataset.filename = layer.filename;

            div.innerHTML = `
                <span class="drag-handle">⋮⋮</span>
                <div class="layer-color-badge" style="background:${layer.color}" onclick="PreviewModule.openColorPicker('${layer.filename}', '${layer.color}')"></div>
                <span class="layer-name" title="${layer.name}">${layer.name.substring(0, 22)}</span>
                <span class="layer-eye" style="opacity: ${layer.visible ? '1' : '0.3'}; cursor: pointer;" onclick="PreviewModule.toggleLayer('${layer.filename}')">${layer.visible ? '👁' : '👁‍🗨'}</span>
                <input type="range" class="layer-opacity" min="0" max="100" value="${layer.opacity * 100}" oninput="PreviewModule.changeOpacity('${layer.filename}', this.value)">
                <span class="opacity-value">${Math.round(layer.opacity * 100)}%</span>
            `;

            div.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('text/plain', layer.filename);
                div.classList.add('dragging');
            });
            div.addEventListener('dragend', () => div.classList.remove('dragging'));
            div.addEventListener('dragover', (e) => {
                e.preventDefault();
                div.classList.add('drag-over');
            });
            div.addEventListener('dragleave', () => div.classList.remove('drag-over'));
            div.addEventListener('drop', (e) => {
                e.preventDefault();
                div.classList.remove('drag-over');
                const fromFile = e.dataTransfer.getData('text/plain');
                const toFile = layer.filename;
                if (fromFile === toFile) return;

                // Insertar la capa movida en la posición destino y
                // desplazar el resto, en vez de solo intercambiar dos
                // posiciones. Esto permite mover una capa varios lugares
                // a la vez (ej. de la posición 1 a la 4) sin que las
                // capas intermedias (2 y 3) se queden intactas en su lugar.
                const ordered = [...layerSettings].sort((a, b) => a.order - b.order);
                const fromPos = ordered.findIndex(l => l.filename === fromFile);
                const toPos = ordered.findIndex(l => l.filename === toFile);
                if (fromPos === -1 || toPos === -1) return;

                const [moved] = ordered.splice(fromPos, 1);
                ordered.splice(toPos, 0, moved);

                ordered.forEach((l, idx) => {
                    const target = layerSettings.find(ls => ls.filename === l.filename);
                    if (target) target.order = idx;
                });

                renderLayersList();
                renderComposite();
            });

            container.appendChild(div);
        });
    }

    // === COLOR PICKER ===
    function openColorPicker(filename, currentColor) {
        const input = document.createElement('input');
        input.type = 'color';
        input.value = currentColor;
        input.addEventListener('change', (e) => {
            updateSepColor(filename, e.target.value);
        });
        input.click();
    }

    function toggleColorize(filename) {
        const bg = document.getElementById(`sep-bg-${filename}`);
        const img = document.getElementById(`sep-img-${filename}`);
        const layer = layerSettings.find(l => l.filename === filename);
        if (!bg || !img || !layer) return;

        if (bg.dataset.colorized === "true") {
            bg.dataset.colorized = "false";
            bg.style.backgroundColor = 'transparent';
            img.style.mixBlendMode = 'normal';
        } else {
            bg.dataset.colorized = "true";
            bg.style.backgroundColor = layer.color;
            img.style.mixBlendMode = 'screen';
        }
    }

    function updateSepColor(filename, newColor) {
        const sep = separationImages.find(s => s.filename === filename);
        if (sep) sep.hex_color = newColor;

        const layer = layerSettings.find(l => l.filename === filename);
        if (layer) layer.color = newColor;

        const bg = document.getElementById(`sep-bg-${filename}`);
        if (bg && bg.dataset.colorized === "true") {
            bg.style.backgroundColor = newColor;
        }

        renderPreviewByMode();
        if (viewMode === 'composite') {
            renderLayersList();
            renderComposite();
        }
    }

    function toggleLayer(filename) {
        const layer = layerSettings.find(l => l.filename === filename);
        if (layer) {
            layer.visible = !layer.visible;
            renderLayersList();
            renderComposite();
        }
    }

    function changeOpacity(filename, value) {
        const layer = layerSettings.find(l => l.filename === filename);
        if (layer) {
            layer.opacity = value / 100;
            renderLayersList();
            renderComposite();
        }
    }

    // === FULLSCREEN IMAGE VIEWER ===
    function openFullscreen(src) {
        const overlay = document.createElement('div');
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.backgroundColor = 'rgba(0,0,0,0.9)';
        overlay.style.zIndex = '9999';
        overlay.style.display = 'flex';
        overlay.style.alignItems = 'center';
        overlay.style.justifyContent = 'center';
        overlay.style.overflow = 'auto';
        overlay.style.cursor = 'zoom-out';
        
        const img = document.createElement('img');
        img.src = src;
        img.style.maxWidth = '90%';
        img.style.maxHeight = '90%';
        img.style.objectFit = 'contain';
        img.style.transition = 'transform 0.2s ease';
        img.style.cursor = 'zoom-in';
        
        let scale = 1;
        let isDragging = false;
        let startX, startY, translateX = 0, translateY = 0;

        const updateTransform = () => {
            img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
        };

        overlay.addEventListener('wheel', (e) => {
            e.preventDefault();
            const zoomAmount = 0.15;
            if (e.deltaY < 0) {
                scale += zoomAmount;
            } else {
                scale -= zoomAmount;
                if (scale < 0.1) scale = 0.1;
            }
            updateTransform();
        });

        img.addEventListener('mousedown', (e) => {
            e.preventDefault();
            isDragging = true;
            startX = e.clientX - translateX;
            startY = e.clientY - translateY;
            img.style.cursor = 'grabbing';
        });

        overlay.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            e.preventDefault();
            translateX = e.clientX - startX;
            translateY = e.clientY - startY;
            updateTransform();
        });

        overlay.addEventListener('mouseup', () => {
            isDragging = false;
            img.style.cursor = 'zoom-in';
        });
        
        overlay.addEventListener('mouseleave', () => {
            isDragging = false;
            img.style.cursor = 'zoom-in';
        });

        img.onclick = (e) => {
            e.stopPropagation();
            // Reset on click if scaled
            if (scale !== 1 || translateX !== 0 || translateY !== 0) {
                scale = 1;
                translateX = 0;
                translateY = 0;
                updateTransform();
            } else {
                scale = 2; // quick zoom
                updateTransform();
            }
        };

        // Click outside image to close
        overlay.onclick = (e) => {
            if (e.target === overlay) {
                document.body.removeChild(overlay);
            }
        };
        
        overlay.appendChild(img);
        document.body.appendChild(overlay);
    }

    // === REFERENCE IMAGE ===
    function onRefDrop(e) {
        e.preventDefault();
        const refDrop = $('ref-drop');
        if (refDrop) refDrop.style.borderColor = '';

        const file = e.dataTransfer.files[0];
        if (file) loadRefFile(file);
    }

    function loadRefFile(file) {
        const validExts = ['jpg', 'jpeg', 'png', 'tiff', 'tif', 'webp', 'gif', 'bmp'];
        const ext = file.name.split('.').pop().toLowerCase();

        if (!validExts.includes(ext)) {
            alert('Formato no soportado: ' + ext);
            return;
        }

        const reader = new FileReader();
        reader.onload = (ev) => showRefCanvas(ev.target.result);
        reader.onerror = () => logPreview('Error leyendo archivo', 'error');
        reader.readAsDataURL(file);
    }

    function loadDemoRef() {
        const canvas = document.createElement('canvas');
        canvas.width = 400;
        canvas.height = 340;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, 400, 340);

        const colors = ['#00d4ff', '#ff4d8a', '#ffd166', '#888888'];
        colors.forEach((col, i) => {
            ctx.globalAlpha = 0.6;
            ctx.fillStyle = col;
            for (let j = 0; j < 8; j++) {
                const x = ((i * 97 + j * 53) % 360) + 20;
                const y = ((i * 61 + j * 79) % 280) + 30;
                const r = 10 + ((i * 7 + j * 13) % 25);
                ctx.beginPath();
                ctx.arc(x, y, r, 0, Math.PI * 2);
                ctx.fill();
            }
        });
        ctx.globalAlpha = 1;
        showRefCanvas(canvas.toDataURL());
    }

    // === REFERENCIA & COMPARACIÓN ===

    // Carga el compuesto de separaciones como imagen base del visor
    async function loadSeparationBase() {
        if (!lastJobName) return;
        const baseImg = $('align-base-img');
        if (!baseImg) return;
        try {
            const resp = await fetch('/api/composite_visual', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_name: lastJobName, mode: 'overprint' })
            });
            const data = await resp.json();
            if (data.composite) {
                baseImg.src = data.composite;
                baseImg.style.opacity = 0.5; // Empieza a mitad junto con la referencia
                baseImg.onload = () => positionHandles();
            }
        } catch (e) {
            console.warn('No se pudo cargar el compuesto de separaciones:', e);
        }
    }

    // Crossfade: 0 = solo referencia, 50 = mitad/mitad, 100 = solo separación
    function updateCrossfade() {
        const val = parseInt($('crossfade-slider')?.value ?? 50, 10);
        const refImg  = $('ref-preview-img');  // referencia (encima)
        const baseImg = $('align-base-img');   // separación (abajo)
        if (refImg)  refImg.style.opacity  = (100 - val) / 100;
        if (baseImg) baseImg.style.opacity = val / 100;
    }

    // Ajuste manual de posición/escala de la referencia
    // El slider mueve visualmente; el número permite precisión exacta.
    function getAlignValues() {
        return {
            scale: parseFloat($('align-scale')?.value || 100) / 100,
            x:     parseFloat($('align-x')?.value || 0),
            y:     parseFloat($('align-y')?.value || 0)
        };
    }

    function updateRefTransform(source) {
        // Si viene del número, actualiza el slider; si viene del slider, actualiza el número
        if (source === 'scale-num' && $('align-scale-num')) {
            $('align-scale').value = $('align-scale-num').value;
        } else if (source === 'scale-range' && $('align-scale-num')) {
            $('align-scale-num').value = $('align-scale').value;
        }
        if (source === 'x-num' && $('align-x-num')) {
            $('align-x').value = $('align-x-num').value;
        } else if (source === 'x-range' && $('align-x-num')) {
            $('align-x-num').value = $('align-x').value;
        }
        if (source === 'y-num' && $('align-y-num')) {
            $('align-y').value = $('align-y-num').value;
        } else if (source === 'y-range' && $('align-y-num')) {
            $('align-y-num').value = $('align-y').value;
        }

        const v = getAlignValues();
        const refImg = $('ref-preview-img');
        if (refImg) {
            refImg.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.scale})`;
        }
        positionHandles();
    }

    // === MANIJAS DE ARRASTRE (estirar/mover con el mouse) ===
    // Complementa a los sliders: mismos valores align-scale/x/y por debajo,
    // solo que aquí se controlan arrastrando directo sobre la imagen en vez
    // de mover barras. Las barras siempre quedan disponibles para afinar
    // con precisión numérica exacta después de arrastrar.
    const HANDLE_CORNERS = ['tl', 'tr', 'bl', 'br'];
    let handleDrag = null; // estado del arrastre en curso (o null)

    function setAlignValues(scale, x, y) {
        const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
        const s = clamp(Math.round(scale * 100), 10, 300);
        const xi = clamp(Math.round(x), -5000, 5000);
        const yi = clamp(Math.round(y), -5000, 5000);
        if ($('align-scale'))     $('align-scale').value     = s;
        if ($('align-scale-num')) $('align-scale-num').value = s;
        if ($('align-x'))         $('align-x').value         = xi;
        if ($('align-x-num'))     $('align-x-num').value     = xi;
        if ($('align-y'))         $('align-y').value         = yi;
        if ($('align-y-num'))     $('align-y-num').value     = yi;
        updateRefTransform(null);
    }

    function initAlignHandles() {
        const container = $('align-container');
        const refImg = $('ref-preview-img');
        if (!container || !refImg) return;

        refImg.classList.add('align-editable');
        refImg.onpointerdown = (e) => startBodyDrag(e);

        HANDLE_CORNERS.forEach(corner => {
            let handle = document.getElementById(`align-handle-${corner}`);
            if (!handle) {
                handle = document.createElement('div');
                handle.id = `align-handle-${corner}`;
                handle.className = `align-handle handle-${corner}`;
                container.appendChild(handle);
            }
            handle.onpointerdown = (e) => startCornerDrag(e, corner);
        });

        positionHandles();
    }

    function destroyAlignHandles() {
        HANDLE_CORNERS.forEach(corner => {
            const handle = document.getElementById(`align-handle-${corner}`);
            if (handle) handle.remove();
        });
        const refImg = $('ref-preview-img');
        if (refImg) refImg.classList.remove('align-editable');
    }

    // Calcula dónde caen visualmente las 4 esquinas de la referencia
    // (ya transformada) dentro de #align-container, y mueve las manijas ahí.
    function positionHandles() {
        const container = $('align-container');
        const refImg = $('ref-preview-img');
        if (!container || !refImg || !refImg.naturalWidth) return;
        if (!HANDLE_CORNERS.some(c => document.getElementById(`align-handle-${c}`))) return;

        const v = getAlignValues();
        // Ancho/alto "base" de la referencia tal como se dibuja ANTES de la
        // transform (width:100% del contenedor, alto proporcional).
        const w0 = container.clientWidth;
        const h0 = w0 * (refImg.naturalHeight / refImg.naturalWidth);

        const corners = {
            tl: [v.x,               v.y],
            tr: [v.x + w0 * v.scale, v.y],
            bl: [v.x,                v.y + h0 * v.scale],
            br: [v.x + w0 * v.scale, v.y + h0 * v.scale],
        };
        HANDLE_CORNERS.forEach(corner => {
            const handle = document.getElementById(`align-handle-${corner}`);
            if (!handle) return;
            handle.style.left = `${corners[corner][0]}px`;
            handle.style.top  = `${corners[corner][1]}px`;
        });
    }

    // Arrastrar el cuerpo de la imagen = mover (x, y); la escala no cambia.
    function startBodyDrag(e) {
        e.preventDefault();
        const v0 = getAlignValues();
        const startX = e.clientX, startY = e.clientY;
        handleDrag = { type: 'move', v0, startX, startY };
        document.addEventListener('pointermove', onHandleDragMove);
        document.addEventListener('pointerup', onHandleDragEnd, { once: true });
    }

    // Arrastrar una esquina = estirar/encoger proporcionalmente, anclado en
    // la esquina OPUESTA (que se queda fija), como en cualquier editor de
    // imágenes. No hay estiramiento independiente de ancho/alto porque el
    // backend solo soporta una escala uniforme (mismo criterio que los
    // sliders: un único "Escala %").
    function startCornerDrag(e, corner) {
        e.preventDefault();
        e.stopPropagation();
        const container = $('align-container');
        const refImg = $('ref-preview-img');
        if (!container || !refImg) return;

        const v0 = getAlignValues();
        const rect = container.getBoundingClientRect();
        const w0 = container.clientWidth;
        const h0 = w0 * (refImg.naturalHeight / refImg.naturalWidth);

        // Punto local (en coords de imagen sin transformar) de la esquina
        // opuesta a la que se está arrastrando: esa es el ancla fija.
        const oppositeLocal = {
            tl: [w0, h0], tr: [0, h0], bl: [w0, 0], br: [0, 0],
        }[corner];
        const draggedLocal = {
            tl: [0, 0], tr: [w0, 0], bl: [0, h0], br: [w0, h0],
        }[corner];

        // Ancla en coordenadas de pantalla (dentro del contenedor), fija
        // durante todo el arrastre.
        const anchor = [
            v0.x + oppositeLocal[0] * v0.scale,
            v0.y + oppositeLocal[1] * v0.scale,
        ];
        const startCornerScreen = [
            v0.x + draggedLocal[0] * v0.scale,
            v0.y + draggedLocal[1] * v0.scale,
        ];
        const dist0 = Math.hypot(startCornerScreen[0] - anchor[0], startCornerScreen[1] - anchor[1]) || 1;

        handleDrag = { type: 'resize', v0, rect, anchor, oppositeLocal, dist0 };
        document.addEventListener('pointermove', onHandleDragMove);
        document.addEventListener('pointerup', onHandleDragEnd, { once: true });
    }

    function onHandleDragMove(e) {
        if (!handleDrag) return;

        if (handleDrag.type === 'move') {
            const dx = e.clientX - handleDrag.startX;
            const dy = e.clientY - handleDrag.startY;
            setAlignValues(handleDrag.v0.scale, handleDrag.v0.x + dx, handleDrag.v0.y + dy);
            return;
        }

        // resize: nueva escala = proporcional a qué tan lejos del ancla
        // está el mouse ahora, comparado con dónde estaba la esquina al
        // empezar a arrastrar. El ancla (esquina opuesta) se recalcula para
        // que se quede exactamente en el mismo sitio en pantalla.
        const { v0, rect, anchor, oppositeLocal, dist0 } = handleDrag;
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const distNow = Math.hypot(mouseX - anchor[0], mouseY - anchor[1]) || 0;

        let newScale = v0.scale * (distNow / dist0);
        newScale = Math.min(3.0, Math.max(0.10, newScale));

        const newX = anchor[0] - oppositeLocal[0] * newScale;
        const newY = anchor[1] - oppositeLocal[1] * newScale;
        setAlignValues(newScale, newX, newY);
    }

    function onHandleDragEnd() {
        document.removeEventListener('pointermove', onHandleDragMove);
        handleDrag = null;
    }

    function initCrossfadeControl() {
        if ($('crossfade-slider')) $('crossfade-slider').oninput = updateCrossfade;
        if ($('auto-align-btn'))   $('auto-align-btn').onclick    = runAutoAlign;
        // Sliders
        if ($('align-scale')) $('align-scale').oninput = () => updateRefTransform('scale-range');
        if ($('align-x'))     $('align-x').oninput     = () => updateRefTransform('x-range');
        if ($('align-y'))     $('align-y').oninput     = () => updateRefTransform('y-range');
        // Inputs numéricos
        if ($('align-scale-num')) $('align-scale-num').oninput = () => updateRefTransform('scale-num');
        if ($('align-x-num'))     $('align-x-num').oninput     = () => updateRefTransform('x-num');
        if ($('align-y-num'))     $('align-y-num').oninput     = () => updateRefTransform('y-num');
    }

    // Pide al backend que calcule escala/posición automáticamente (detección
    // de contenido + correlación de fase) y llena los sliders manuales con
    // el resultado. El usuario puede seguir afinando a mano después.
    async function runAutoAlign() {
        if (!refImageData || !lastJobName) {
            alert('Se necesita un trabajo procesado y una imagen de referencia');
            return;
        }
        const btn = $('auto-align-btn');
        const statusEl = $('auto-align-status');
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Calculando...'; }

        try {
            const resp = await fetch('/api/auto_align', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_name: lastJobName, reference: refImageData })
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);

            // Mismo factor que usa runAnalysis para convertir entre px reales
            // de la separación y px CSS de los sliders/preview en pantalla.
            const baseImg = $('align-base-img');
            const displayRatio = (baseImg && baseImg.clientWidth > 0 && baseImg.naturalWidth > 0)
                ? baseImg.naturalWidth / baseImg.clientWidth : 1;

            const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
            const scalePct = clamp(Math.round(data.scale * 100), 10, 300);
            const xSlider  = clamp(Math.round(data.offset_x / displayRatio), -5000, 5000);
            const ySlider  = clamp(Math.round(data.offset_y / displayRatio), -5000, 5000);

            if ($('align-scale'))     $('align-scale').value     = scalePct;
            if ($('align-scale-num')) $('align-scale-num').value = scalePct;
            if ($('align-x'))         $('align-x').value         = xSlider;
            if ($('align-x-num'))     $('align-x-num').value     = xSlider;
            if ($('align-y'))         $('align-y').value         = ySlider;
            if ($('align-y-num'))     $('align-y-num').value     = ySlider;

            updateRefTransform(null);

            const pct = Math.round(data.confidence * 100);
            if (statusEl) {
                statusEl.style.display = 'block';
                if (data.confidence < 0.35) {
                    statusEl.style.color = '#e05555';
                    statusEl.textContent = `⚠️ Confianza baja (${pct}%). El arte de referencia puede ser muy distinto a la separación — revisa o ajusta a mano.`;
                } else {
                    statusEl.style.color = 'var(--text-muted)';
                    statusEl.textContent = `✅ Auto-alineado (confianza ${pct}%). Puedes afinar a mano si hace falta.`;
                }
            }
            logPreview(`🎯 Auto-alineado: escala ${scalePct}%, confianza ${pct}%`, 'success');
        } catch (e) {
            logPreview(`❌ Error en auto-alineado: ${e.message}`, 'error');
            alert('No se pudo auto-alinear: ' + e.message);
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = '🎯 Auto-alinear'; }
        }
    }

    function showRefCanvas(dataUrl) {
        const img = $('ref-preview-img');
        if (!img) return;

        img.onload = () => {
            if ($('ref-drop'))       $('ref-drop').style.display      = 'none';
            if ($('ref-canvas-wrap')) $('ref-canvas-wrap').style.display = 'block';
            if ($('crossfade-ctrl')) $('crossfade-ctrl').style.display = 'flex';
            if ($('alignment-ctrl')) $('alignment-ctrl').style.display = 'flex';
            if ($('btn-clear-ref'))  $('btn-clear-ref').style.display  = 'block';
            if ($('no-ref-msg'))     $('no-ref-msg').style.display     = 'none';
            if ($('analyze-btn'))    $('analyze-btn').disabled          = !lastJobName;
            if ($('diff-results'))   $('diff-results').innerHTML        = '';

            // Resetear controles
            if ($('crossfade-slider'))  $('crossfade-slider').value  = 50;
            if ($('align-scale'))       $('align-scale').value       = 100;
            if ($('align-scale-num'))   $('align-scale-num').value   = 100;
            if ($('align-x'))           $('align-x').value           = 0;
            if ($('align-x-num'))       $('align-x-num').value       = 0;
            if ($('align-y'))           $('align-y').value           = 0;
            if ($('align-y-num'))       $('align-y-num').value       = 0;

            // Aplicar estado inicial
            updateCrossfade();
            updateRefTransform(null);
            initAlignHandles();

            // Cargar el compuesto de separaciones como fondo
            loadSeparationBase();

            logPreview(`✅ Referencia cargada: ${img.naturalWidth}×${img.naturalHeight}px`, 'success');
        };
        img.onerror = () => {
            logPreview('❌ Error cargando imagen de referencia', 'error');
            alert('No se pudo cargar la imagen.');
        };
        img.src = dataUrl;
        refImageData = dataUrl;
    }

    function clearRef() {
        refImageData = null;
        if ($('ref-drop'))        $('ref-drop').style.display        = 'block';
        if ($('ref-canvas-wrap')) $('ref-canvas-wrap').style.display = 'none';
        if ($('crossfade-ctrl'))  $('crossfade-ctrl').style.display  = 'none';
        if ($('alignment-ctrl'))  $('alignment-ctrl').style.display  = 'none';
        if ($('btn-clear-ref'))   $('btn-clear-ref').style.display   = 'none';
        if ($('no-ref-msg'))      $('no-ref-msg').style.display      = 'block';
        if ($('analyze-btn'))     $('analyze-btn').disabled           = true;
        if ($('diff-results'))    $('diff-results').innerHTML         = '';
        if ($('align-base-img'))  $('align-base-img').src             = '';
        if ($('ref-preview-img')) $('ref-preview-img').style.transform = '';
        destroyAlignHandles();
    }

    // === ANALYSIS ===
    async function runAnalysis() {
        if (!refImageData || !lastJobName) {
            alert('Se necesita un trabajo procesado y una imagen de referencia');
            return;
        }
        if (isAnalyzing) return;
        isAnalyzing = true;

        const btn = $('analyze-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = '⏳ Analizando...';
        }

        // Leer valores actuales de alineación
        const alignVals = getAlignValues();
        // Calcular offset real en píxeles de imagen (el slider mueve px CSS en pantalla;
        // hay que escalarlos al tamaño real de la separación)
        const baseImg = $('align-base-img');
        const displayRatio = (baseImg && baseImg.clientWidth > 0 && baseImg.naturalWidth > 0)
            ? baseImg.naturalWidth / baseImg.clientWidth : 1;

        try {
            const resp = await fetch('/api/analyze_detailed', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    job_name:    lastJobName, 
                    reference:   refImageData,
                    align_scale: alignVals.scale,
                    align_x:     Math.round(alignVals.x * displayRatio),
                    align_y:     Math.round(alignVals.y * displayRatio)
                })
            });
            const data = await resp.json();

            if (data.error) throw new Error(data.error);

            // Show diff map
            const diffImg = $('diff-preview-img');
            if (diffImg) {
                diffImg.src = data.diff_map;
                diffImg.style.opacity = 0.5;
            }
            if ($('diff-opacity')) $('diff-opacity').value = 50;
            if ($('diff-pct-lbl')) $('diff-pct-lbl').textContent = '50%';

            // Render findings
            renderFindings(data);
            logPreview(`✅ Análisis completado. Score: ${data.score}%`, data.score < 30 ? 'success' : 'warn');
        } catch (err) {
            logPreview(`❌ Error en análisis: ${err.message}`, 'error');
            alert('Error en análisis: ' + err.message);
        } finally {
            isAnalyzing = false;
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Analizar diferencias';
            }
        }
    }

    function renderFindings(data) {
        const container = $('diff-results');
        if (!container) return;

        const scoreColor = data.score < 15 ? 'var(--accent-success)' : 
                          data.score < 30 ? 'var(--accent-warning)' : 'var(--accent-error)';
        const scoreClass = data.score < 15 ? 'good' : data.score < 30 ? 'warn' : 'bad';

        let html = '';
        if (data.diff_map) {
            html += `
            <div style="margin-bottom: 16px; text-align: center; border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 8px; background: var(--bg-deep);">
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 8px; font-family: var(--font-mono);">Mapa de Diferencias (Rojo = Alta Diferencia)</div>
                <img src="${data.diff_map}" style="max-width: 100%; border-radius: var(--radius-sm); cursor: zoom-in;" onclick="PreviewModule.openFullscreen(this.src)" alt="Mapa de diferencias">
            </div>
            `;
        }

        html += `
            <div class="score-card">
                <div class="score-value ${scoreClass}">${data.score}%</div>
                <div class="score-label">Diferencia promedio</div>
                <div class="score-meta">${data.metrics?.separation_count || separationImages.length} separaciones analizadas</div>
            </div>
        `;

        html += data.findings.map(f => {
            const barColor = f.status === 'ok' ? 'var(--accent-success)' : 
                            f.status === 'warn' ? 'var(--accent-warning)' : 'var(--accent-error)';
            const statusText = f.status === 'ok' ? 'OK' : f.status === 'warn' ? 'REVISAR' : 'ERROR';
            return `
                <div class="diff-card">
                    <div class="diff-header">
                        <span class="diff-title">${f.title}</span>
                        <span class="diff-badge ${f.status}">${statusText}</span>
                    </div>
                    <div class="diff-bar-wrap">
                        <div class="diff-bar" style="width:${f.pct}%;background:${barColor}"></div>
                    </div>
                    <div class="diff-msg">${f.msg}</div>
                </div>
            `;
        }).join('');

        container.innerHTML = html;
    }

    // === UTILITIES ===
    function hexToRgb(hex) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        return result ? {
            r: parseInt(result[1], 16),
            g: parseInt(result[2], 16),
            b: parseInt(result[3], 16)
        } : { r: 0, g: 0, b: 0 };
    }

    function logPreview(msg, type = 'info') {
        // Log to console for now - could add a dedicated preview log panel
        const prefix = type === 'error' ? '❌' : type === 'warn' ? '⚠️' : type === 'success' ? '✅' : '→';
        console.log(`[Preview] ${prefix} ${msg}`);
    }

    // === PUBLIC API ===
    return {
        init,
        setViewMode,
        setCompositeMode,
        openColorPicker,
        updateSepColor,
        toggleLayer,
        changeOpacity,
        openFullscreen,
        toggleColorize
    };
})();

// Auto-init when DOM ready
document.addEventListener('DOMContentLoaded', PreviewModule.init);
