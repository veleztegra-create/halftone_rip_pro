// ============================================================
// HALFTONE RIP PRO - RIP & Separaciones Module
// Maneja: upload, configuración, procesamiento, resultados
// ============================================================

const RipModule = (function() {
    'use strict';

    // === STATE ===
    let currentFile = null;
    let currentFileData = null;
    let processing = false;
    let lastJobName = null;
    let lastColorNames = [];
    const CHUNK_SIZE = 5 * 1024 * 1024;

    // === DOM CACHE ===
    const $ = (id) => document.getElementById(id);
    const $$ = (sel) => document.querySelectorAll(sel);

    // === RECENT JOBS (historial real, persistido en localStorage) ===
    const RECENT_JOBS_KEY = 'halftone_recent_jobs';
    const MAX_RECENT_JOBS = 8;

    function getRecentJobs() {
        try {
            const raw = localStorage.getItem(RECENT_JOBS_KEY);
            return raw ? JSON.parse(raw) : [];
        } catch (e) {
            return [];
        }
    }

    function saveRecentJob(entry) {
        try {
            let jobs = getRecentJobs();
            // Si se reprocesó el mismo archivo, mover la entrada existente
            // al frente en vez de duplicarla.
            jobs = jobs.filter(j => j.originalName !== entry.originalName);
            jobs.unshift(entry);
            jobs = jobs.slice(0, MAX_RECENT_JOBS);
            localStorage.setItem(RECENT_JOBS_KEY, JSON.stringify(jobs));
            renderRecentJobs();
        } catch (e) {
            // localStorage puede fallar (modo privado, cuota llena, etc.)
            // No es crítico: simplemente no se guarda el historial.
            console.warn('No se pudo guardar el historial de trabajos:', e);
        }
    }

    function formatRelativeTime(timestamp) {
        const diffMs = Date.now() - timestamp;
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return 'hace un momento';
        if (diffMin < 60) return `hace ${diffMin} min`;
        const diffH = Math.floor(diffMin / 60);
        if (diffH < 24) return `hace ${diffH}h`;
        const diffD = Math.floor(diffH / 24);
        if (diffD === 1) return 'ayer';
        if (diffD < 7) return `hace ${diffD} días`;
        return new Date(timestamp).toLocaleDateString();
    }

    function renderRecentJobs() {
        const list = $('recentJobsList');
        if (!list) return;
        const jobs = getRecentJobs();

        if (jobs.length === 0) {
            list.innerHTML = '<li style="color: var(--text-muted); font-style: italic;">Aún no has procesado ningún archivo.</li>';
            return;
        }

        list.innerHTML = jobs.map(job => `
            <li style="display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--text-secondary);" title="${job.colorCount} color(es) · ${job.lpi} LPI · ${job.dpi} DPI">
                <span style="display:flex; align-items:center; gap:8px; overflow:hidden;">
                    <span style="color: #4bc0c0; flex-shrink:0;">✔</span>
                    <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${job.originalName}</span>
                </span>
                <span style="color: var(--text-muted); font-size: 0.75rem; flex-shrink:0;">${formatRelativeTime(job.timestamp)}</span>
            </li>
        `).join('');
    }

    // === INITIALIZATION ===
    function init() {
        bindEvents();
        updateLpiDisplay();
        renderRecentJobs();
        log('🚀 Módulo RIP inicializado', 'success');
    }

    function bindEvents() {
        // File upload
        const dropZone = $('dropZone');
        const fileInput = $('fileInput');
        const browseBtn = $('browseBtn');

        if (browseBtn && fileInput) {
            browseBtn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                fileInput.click();
            });
        }

        if (dropZone) {
            dropZone.addEventListener('dragover', onDragOver);
            dropZone.addEventListener('dragleave', onDragLeave);
            dropZone.addEventListener('drop', onDrop);
            dropZone.addEventListener('click', function(e) {
                if (e.target !== browseBtn) {
                    fileInput.click();
                }
            });
        }
        if (fileInput) fileInput.onchange = (e) => {
            if (e.target.files.length) handleFile(e.target.files[0]);
        };
        if ($('clearFile')) $('clearFile').onclick = resetFile;

        // Config inputs
        const lpiInput = $('lpi');
        const dpiSelect = $('dpi');
        if (lpiInput) lpiInput.addEventListener('input', updateLpiDisplay);
        if (dpiSelect) dpiSelect.addEventListener('change', updateLpiDisplay);

        // Process button
        const processBtn = $('processBtn');
        if (processBtn) processBtn.onclick = processSeparations;
    }

    // === FILE HANDLING ===
    function onDragOver(e) {
        e.preventDefault();
        $('dropZone')?.classList.add('dragover');
    }

    function onDragLeave() {
        $('dropZone')?.classList.remove('dragover');
    }

    function onDrop(e) {
        e.preventDefault();
        $('dropZone')?.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    }

    function handleFile(file) {
        const validTypes = ['pdf', 'ps'];
        const ext = file.name.split('.').pop().toLowerCase();

        if (!validTypes.includes(ext)) {
            alert(
                'Formato no soportado: .' + ext + '\n\n' +
                'Este programa trabaja con archivos .ps o .pdf, ya sea preseparados ' +
                'por placa (una página por color) o compuestos en CMYK/PDF-X4 ' +
                '(con todos los colores en la misma página).\n\n' +
                'EPS, PNG, JPG y TIFF no contienen información real de separación ' +
                'de tintas, así que no se aceptan.'
            );
            return;
        }

        currentFile = file;
        showFileInfo(file);
        uploadFileChunked(file);
    }

    function showFileInfo(file) {
        const fileInfo = $('fileInfo');
        const fileName = $('fileName');
        const dropZone = $('dropZone');
        const statSize = $('statSize');

        if (fileName) fileName.textContent = file.name;
        if (fileInfo) fileInfo.classList.remove('hidden');
        if (dropZone) dropZone.classList.add('hidden');
        if (statSize) statSize.textContent = (file.size / 1048576).toFixed(1) + ' MB';
    }

    function resetFile() {
        currentFile = null;
        currentFileData = null;
        if ($('fileInput')) $('fileInput').value = '';

        const fileInfo = $('fileInfo');
        const dropZone = $('dropZone');
        const processBtn = $('processBtn');
        const resultsPanel = $('resultsPanel');
        const statSize = $('statSize');
        const uploadProgress = $('uploadProgress');

        if (fileInfo) fileInfo.classList.add('hidden');
        if (dropZone) dropZone.classList.remove('hidden');
        if (processBtn) {
            processBtn.disabled = true;
            processBtn.textContent = 'Procesar Separaciones';
        }
        if (resultsPanel) resultsPanel.classList.add('hidden');
        if (statSize) statSize.textContent = '-';
        if (uploadProgress) uploadProgress.classList.add('hidden');
    }

    // === UPLOAD ===
    function generateUploadId() {
        return Date.now().toString(36) + Math.random().toString(36).substr(2, 6);
    }

    function uploadFileChunked(file) {
        const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
        const uploadId = generateUploadId();
        let chunkIndex = 0;

        const progEl = $('uploadProgress');
        const progFill = $('uploadProgressFill');
        const progMsg = $('uploadProgressMsg');

        if (progEl) progEl.classList.remove('hidden');
        if (progFill) progFill.style.width = '0%';
        if (progMsg) progMsg.textContent = `Subiendo... (0/${totalChunks})`;

        function sendChunk() {
            const start = chunkIndex * CHUNK_SIZE;
            const end = Math.min(start + CHUNK_SIZE, file.size);
            const blob = file.slice(start, end);

            const formData = new FormData();
            formData.append('file', blob, file.name);
            formData.append('filename', file.name);
            formData.append('chunk_index', chunkIndex);
            formData.append('total_chunks', totalChunks);
            formData.append('upload_id', uploadId);

            fetch('/api/upload_chunk', { method: 'POST', body: formData })
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        log(`Error al subir: ${data.error}`, 'error');
                        if (progEl) progEl.classList.add('hidden');
                        return;
                    }

                    chunkIndex++;
                    const pct = Math.round(chunkIndex / totalChunks * 100);
                    if (progFill) progFill.style.width = pct + '%';
                    if (progMsg) progMsg.textContent = `Subiendo... (${chunkIndex}/${totalChunks})`;

                    if (data.success && data.filename && data.original_name) {
                        if (progEl) progEl.classList.add('hidden');
                        currentFileData = data;
                        if ($('processBtn')) $('processBtn').disabled = false;
                        log(`✅ Archivo cargado: ${data.original_name} (${data.file_size_mb || '?'} MB)`, 'success');
                    } else {
                        sendChunk();
                    }
                })
                .catch(err => {
                    log(`Error de red: ${err.message}`, 'error');
                    if (progEl) progEl.classList.add('hidden');
                });
        }

        sendChunk();
    }

    // === CONFIG DISPLAY ===
    function updateLpiDisplay() {
        const lpi = parseInt($('lpi')?.value) || 55;
        const dpi = parseInt($('dpi')?.value) || 600;
        const ratio = (dpi / lpi).toFixed(1);

        if ($('lpiDisplay')) $('lpiDisplay').textContent = lpi;
        if ($('ratioDisplay')) $('ratioDisplay').textContent = `${dpi} DPI - ratio ${ratio}:1`;
        if ($('statLpi')) $('statLpi').textContent = lpi;
        if ($('statDpi')) $('statDpi').textContent = dpi;
        if ($('statRatio')) $('statRatio').textContent = ratio;
    }

    // === PROCESS ===
    function processSeparations() {
        if (!currentFileData || processing) return;
        processing = true;

        const btn = $('processBtn');
        const progressPanel = $('progressPanel');
        const resultsPanel = $('resultsPanel');
        const logPanel = $('logPanel');
        const progressFill = $('progressFill');

        if (btn) {
            btn.disabled = true;
            btn.textContent = '⏳ Procesando...';
        }
        if (progressPanel) progressPanel.classList.remove('hidden');
        if (progressFill) progressFill.style.width = '0%';
        if (logPanel) logPanel.innerHTML = '';

        // Mostrar el panel de resultados desde ya, con un skeleton, en vez
        // de ocultarlo hasta que termine el procesamiento. Da una idea
        // visual de "aquí van a aparecer tus separaciones" durante los
        // 10-50s reales que puede tardar, en vez de una pantalla vacía
        // seguida de una aparición repentina al final.
        if (resultsPanel) resultsPanel.classList.remove('hidden');
        showSkeletons();

        const config = {
            lpi: parseFloat($('lpi')?.value) || 55,
            dpi: parseInt($('dpi')?.value) || 600,
            angle: parseFloat($('angle')?.value) || 45,
            dot_shape: $('dotShape')?.value || 'round',
            auto_angles: $('autoAngles')?.checked ?? true,
            separation_mode: 'preseparated'
        };

        log(`Iniciando trabajo: ${currentFileData.original_name}`);
        log(`Config: ${config.lpi} LPI - ${config.dpi} DPI - Forma: ${config.dot_shape}`);
        
        // Progreso honesto: los primeros pasos representan trabajo real
        // de corta duración (validación, inicio de la solicitud). El
        // tiempo real de Ghostscript/halftone no es instrumentable sin
        // reescribir el backend con progreso por streaming, así que en
        // vez de fingir un porcentaje creciente durante ese tramo (que
        // antes se quedaba congelado en 91% por 30-50s reales, dando la
        // sensación de que el programa se colgó), se pasa a un indicador
        // indeterminado honesto: comunica "sigue trabajando" sin prometer
        // un avance que no se puede medir.
        const earlySteps = [
            { pct: 15, msg: 'Validando archivo...' },
            { pct: 30, msg: 'Iniciando Ghostscript...' }
        ];

        let stepIdx = 0;
        updateProgress(5, 'Iniciando proceso...');

        const progressInterval = setInterval(() => {
            if (stepIdx < earlySteps.length) {
                updateProgress(earlySteps[stepIdx].pct, earlySteps[stepIdx].msg);
                log(earlySteps[stepIdx].msg);
                stepIdx++;
            } else {
                clearInterval(progressInterval);
                setIndeterminate('Procesando separaciones y semitonos (puede tardar según tamaño y DPI)...');
            }
        }, 700);


        fetch('/api/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                filename: currentFileData.filename, 
                config: config 
            })
        })
        .then(r => r.json())
        .then(data => {
            clearInterval(progressInterval);
            if (data.error) throw new Error(data.error);

            updateProgress(100, 'Completado!');
            log(`✅ Trabajo finalizado: ${Object.keys(data.channels).length} canales generados`, 'success');

            // Store job info for preview module
            const zipPathNorm = data.zip_path.split('\\').join('/');
            const parts = zipPathNorm.split('/');
            lastJobName = parts[parts.length - 2] || null;
            lastColorNames = Object.keys(data.channels);

            // Guardar en el historial real de trabajos recientes
            saveRecentJob({
                originalName: currentFileData.original_name,
                jobName: lastJobName,
                timestamp: Date.now(),
                colorCount: lastColorNames.length,
                lpi: config.lpi,
                dpi: config.dpi
            });

            // Dispatch event for preview module
            window.dispatchEvent(new CustomEvent('rip:jobComplete', {
                detail: { jobName: lastJobName, colorNames: lastColorNames, channels: data.channels }
            }));

            renderResults(data.channels, data.zip_path);
            renderWarnings(data.warnings || []);

            setTimeout(() => {
                if (progressPanel) progressPanel.classList.add('hidden');
                if (resultsPanel) resultsPanel.classList.remove('hidden');
            }, 500);

            processing = false;
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Procesar Separaciones';
            }
        })
        .catch(err => {
            clearInterval(progressInterval);
            log(`❌ ERROR CRÍTICO: ${err.message}`, 'error');
            alert('Error: ' + err.message);
            processing = false;
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Procesar Separaciones';
            }
        });
    }

    function updateProgress(percent, msg) {
        const fill = $('progressFill');
        const msgEl = $('progressMsg');
        if (fill) {
            fill.classList.remove('indeterminate');
            fill.style.width = percent + '%';
        }
        if (msgEl) {
            msgEl.textContent = msg + (percent < 100 && percent > 5 ? ` ${percent}%` : '');
            msgEl.classList.toggle('processing', percent < 100);
        }
    }

    function setIndeterminate(msg) {
        const fill = $('progressFill');
        const msgEl = $('progressMsg');
        if (fill) fill.classList.add('indeterminate');
        if (msgEl) {
            msgEl.textContent = msg;
            msgEl.classList.add('processing');
        }
    }

    // === RESULTS & WARNINGS RENDERING ===
    function renderWarnings(warnings = []) {
        const warningsPanel = $('warningsPanel');
        const warningsList = $('warningsList');
        if (!warningsPanel || !warningsList) return;

        // Solo se muestran advertencias reales detectadas por el backend
        // (ratio DPI/LPI fuera de rango, placas con nombre genérico
        // colisionado, cobertura de tinta anormalmente alta, etc). Si no
        // hay ninguna, el panel se oculta - no se inventa nada para
        // "parecer profesional", porque lo profesional es no mentir sobre
        // el archivo del usuario.
        if (!warnings || warnings.length === 0) {
            warningsPanel.style.display = 'none';
            return;
        }

        warningsList.innerHTML = '';
        warnings.forEach(w => {
            const li = document.createElement('li');
            li.textContent = w;
            warningsList.appendChild(li);
        });

        warningsPanel.style.display = 'block';
    }
    function showSkeletons(count = 4) {
        const grid = $('resultsGrid');
        if (!grid) return;
        grid.innerHTML = Array(count).fill(0).map((_, i) => `
            <div class="sep-card" style="animation-delay:${i * 0.08}s">
                <div class="skeleton skeleton-card"></div>
                <div class="skeleton" style="height: 16px; width: 60%; margin-top: 12px;"></div>
                <div class="skeleton" style="height: 12px; width: 40%; margin-top: 6px;"></div>
            </div>
        `).join('');
    }

    function renderResults(channels, zipPath) {
        const grid = $('resultsGrid');
        const bulkDiv = $('bulkDownloads');
        if (!grid) return;

        grid.innerHTML = '';
        const colors = ['#00d4ff', '#ff4d8a', '#ffd166', '#a0a0b8', '#ff6384', '#36a2eb', '#ffce56', '#4bc0c0'];
        let idx = 0;

        Object.entries(channels).forEach(([name, ch]) => {
            const isSpot = !['Cyan', 'Magenta', 'Yellow', 'Black'].includes(name);
            const colorDot = colors[idx % colors.length];

            const card = document.createElement('div');
            card.className = 'sep-card';
            card.style.animationDelay = `${idx * 0.05}s`;
            card.classList.add('animate-fade-in');

            card.innerHTML = `
                <div class="sep-preview">
                    <img src="${ch.preview}" alt="${name}" loading="lazy">
                </div>
                <span class="sep-badge ${isSpot ? 'spot' : 'cmyk'}">${isSpot ? 'SPOT' : 'CMYK'}</span>
                <div class="sep-name" style="display:flex;align-items:center;gap:6px">
                    <span style="width:8px;height:8px;border-radius:50%;background:${colorDot};display:inline-block"></span>
                    ${name}
                </div>
                <div class="sep-dims">${ch.size[0]} x ${ch.size[1]} px</div>
            `;
            grid.appendChild(card);
            idx++;
        });

        // Bulk downloads
        if (bulkDiv) {
            bulkDiv.innerHTML = '';

            // ZIP button
            const zipBtn = document.createElement('a');
            zipBtn.className = 'btn-dl primary';
            zipBtn.href = '/api/download/' + encodeURIComponent(zipPath.split('\\').join('/'));
            zipBtn.innerHTML = '📦 Descargar ZIP';
            bulkDiv.appendChild(zipBtn);

            // PDF button
            const pdfBtn = document.createElement('button');
            pdfBtn.className = 'btn-dl magenta';
            pdfBtn.innerHTML = '📄 Descargar PDF';
            pdfBtn.onclick = downloadPdf;
            bulkDiv.appendChild(pdfBtn);
        }
    }

    function downloadPdf() {
        if (!lastJobName) {
            alert('Primero debes procesar un archivo.');
            return;
        }

        const btn = document.querySelector('.btn-dl.magenta');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '⏳ Generando PDF...';
            btn.style.opacity = '0.7';
        }

        log('Generando PDF...');

        fetch('/api/generate_pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                job_name: lastJobName, 
                color_names: lastColorNames 
            })
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) throw new Error(data.error);

            log(`✅ PDF generado: ${data.pages} páginas`, 'success');

            const pdfRelPath = data.pdf_path.split('\\').join('/');
            const outputsIdx = pdfRelPath.indexOf('outputs/');
            const relPath = outputsIdx >= 0 
                ? pdfRelPath.substring(outputsIdx + 'outputs/'.length) 
                : pdfRelPath;

            const a = document.createElement('a');
            a.href = '/api/download/' + encodeURIComponent(relPath);
            a.download = 'separaciones.pdf';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        })
        .catch(err => {
            log(`❌ Error PDF: ${err.message}`, 'error');
            alert('Error al generar PDF: ' + err.message);
        })
        .finally(() => {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '📄 Descargar PDF';
                btn.style.opacity = '1';
            }
        });
    }

    // === LOGGING ===
    function log(msg, type = 'info') {
        const panel = $('logPanel');
        if (!panel) return;

        const entry = document.createElement('div');
        entry.className = `log-entry ${type}`;
        const time = new Date().toLocaleTimeString('es', { hour12: false });
        const prefix = type === 'error' ? '❌' : type === 'warn' ? '⚠️' : type === 'success' ? '✅' : '→';
        entry.textContent = `[${time}] ${prefix} ${msg}`;

        panel.appendChild(entry);
        panel.scrollTop = panel.scrollHeight;
    }

    // === PUBLIC API ===

    return {
        init,
        getLastJobName: () => lastJobName,
        getLastColorNames: () => lastColorNames
    };
})();

// Auto-init when DOM ready
document.addEventListener('DOMContentLoaded', RipModule.init);
