document.addEventListener('DOMContentLoaded', function() {

    /** --- Tabbed Interface --- */
    const tabsContainer = document.querySelector('.tabs');
    if (tabsContainer) {
        tabsContainer.addEventListener('click', (e) => {
            const tabButton = e.target.closest('.tab-button');
            if (!tabButton || tabButton.classList.contains('active')) return;
            const currentActiveTab = tabsContainer.querySelector('.tab-button.active');
            if (currentActiveTab) {
                currentActiveTab.classList.remove('active');
                document.getElementById(currentActiveTab.dataset.tab)?.classList.remove('active');
            }
            tabButton.classList.add('active');
            document.getElementById(tabButton.dataset.tab)?.classList.add('active');
        });
    }

    /** --- Dark Mode Toggle with Persistence --- */
    const themeToggle = document.getElementById('darkmode-toggle');
    const darkModeInput = document.getElementById('dark_mode_state');
    function setTheme(isDark) {
        document.body.classList.toggle('dark-mode', isDark);
        if(themeToggle) themeToggle.checked = isDark;
        if(darkModeInput) darkModeInput.value = isDark ? 'on' : 'off';
    }
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme !== null) setTheme(savedTheme === 'dark');
    else if(themeToggle) themeToggle.checked = document.body.classList.contains('dark-mode');
    if (themeToggle) themeToggle.addEventListener('change', function() {
        const isDark = this.checked;
        setTheme(isDark);
        localStorage.setItem('theme', isDark ? 'dark' : 'light');
    });

    /** --- Stock Part & Offcut Selection --- */
    function setupSelector(type) {
        const selectAllBtn = document.getElementById(`select-all-${type}`);
        const deselectAllBtn = document.getElementById(`deselect-all-${type}`);
        const selector = document.querySelector(`.${type}-selector`);
        if(selectAllBtn) selectAllBtn.addEventListener('click', () => selector.querySelectorAll(`input[name^="selected_"]`).forEach(cb => cb.checked = true));
        if(deselectAllBtn) deselectAllBtn.addEventListener('click', () => selector.querySelectorAll(`input[name^="selected_"]`).forEach(cb => cb.checked = false));
    }
    setupSelector('stock');
    setupSelector('offcut');

    const stockSelector = document.querySelector('.stock-selector:not(.offcut-selector)');
    if (stockSelector) stockSelector.addEventListener('change', (e) => {
        const cb = e.target.closest('input[name="selected_stock_parts"]');
        if (cb) {
            const qtyInput = cb.closest('.stock-item').querySelector('.required-qty-input');
            if (qtyInput) {
                qtyInput.disabled = !cb.checked;
                if (!cb.checked) qtyInput.value = '';
            }
        }
    });
    
    /** --- Loaded Part Selection --- */
    const loadedPartsAccordion = document.getElementById('loaded-parts-accordion');
    if (loadedPartsAccordion) loadedPartsAccordion.addEventListener('change', e => {
        const selectAllCheckbox = e.target.closest('.select-all-parts-in-file');
        if (selectAllCheckbox) {
            selectAllCheckbox.closest('table').querySelectorAll('tbody input[name="selected_hhos_parts"]').forEach(cb => {
                cb.checked = selectAllCheckbox.checked;
            });
        }
    });

    // In main.js
    /** --- Render Initial Results from Embedded Data --- */
    const initialDataElement = document.getElementById('initial-results-data');
    if (initialDataElement) {
        const initialData = JSON.parse(initialDataElement.textContent);
        if (initialData) {
            renderResults(initialData);
            document.querySelector('.tab-button[data-tab="results"]')?.click();
        }
    }
    
    /** --- Dynamic HHOS File Inputs --- */
    const hhosList = document.getElementById('hhos-file-list');
    if (hhosList) {
        document.getElementById('add-hhos-file')?.addEventListener('click', () => {
            const group = document.createElement('div');
            group.className = 'file-input-group';
            group.innerHTML = `<input type="text" name="hhos_paths" placeholder="Path to HHOS file..."><input type="number" name="hhos_multipliers" class="multiplier-input" value="1" min="1"><button type="button" class="btn-remove-file"><i class="fa-solid fa-trash"></i></button>`;
            hhosList.appendChild(group);
        });
        hhosList.addEventListener('click', (e) => {
            const removeBtn = e.target.closest('.btn-remove-file');
            if (removeBtn && hhosList.querySelectorAll('.file-input-group').length > 1) {
                removeBtn.parentElement.remove();
            }
        });
    }

    /** --- Loading Indicator on Form Submit --- */
    const configForm = document.querySelector('.config-form');
    if (configForm) {
        configForm.addEventListener('submit', () => {
            const hasHhosFile = Array.from(document.querySelectorAll('input[name="hhos_paths"]')).some(i => i.value.trim() !== '');
            const hasRequiredStock = Array.from(document.querySelectorAll('.required-qty-input')).some(i => i.value.trim() !== '' && parseInt(i.value) > 0);
            if (hasHhosFile || hasRequiredStock) {
                const loadingOverlay = document.getElementById('loading-overlay');
                if(loadingOverlay) {
                    loadingOverlay.style.display = 'flex';
                    loadingOverlay.querySelector('h3').textContent = 'Optimizing Layouts...';
                }
                document.querySelector('.tab-button[data-tab="results"]')?.click();
            }
        });
    }

    /** --- Animated Accordion for Collapsible Sections --- */
    document.querySelectorAll('details.animated-accordion').forEach((accordion) => {
        const content = accordion.querySelector('.collapsible-content');
        if (accordion.open) content.style.maxHeight = content.scrollHeight + 'px';
        accordion.addEventListener('toggle', () => {
            content.style.maxHeight = accordion.open ? content.scrollHeight + 'px' : '0px';
        });
    });

    /** --- Resizable Card Sections --- */
    document.querySelectorAll('.resizable-card .resize-handle').forEach(handle => {
        const selectorList = handle.parentElement.querySelector('.stock-selector');
        handle.addEventListener('mousedown', function(e) {
            e.preventDefault();
            const startY = e.clientY, startHeight = selectorList.offsetHeight;
            const doDrag = (e) => selectorList.style.height = `${startHeight + e.clientY - startY}px`;
            const stopDrag = () => document.documentElement.removeEventListener('mousemove', doDrag);
            document.documentElement.addEventListener('mousemove', doDrag);
            document.documentElement.addEventListener('mouseup', stopDrag, { once: true });
        });
    });

    /** --- Interactive Results Event Listener --- */
    const resultsTab = document.getElementById('results');
    if (resultsTab) {
        resultsTab.addEventListener('click', handleLayoutClick);
    }
});


async function handleLayoutClick(e) {
    const item = e.target.closest('.interactive-item');
    if (!item) return;

    const layoutCard = item.closest('.layout-card');
    const layoutSignature = layoutCard.dataset.layoutSignature;
    const itemIndex = parseInt(item.dataset.itemIndex, 10);
    const itemType = item.dataset.itemType;
    let action = null;

    if (itemType === 'stock_part') {
        action = 'remove_stock_part';
    } else if (itemType === 'job_part') {
        action = 'remove_job_part';
    } else if (itemType === 'offcut') {
        action = 'remove_offcut';
    }

    if (!action) return;

    const loadingOverlay = document.getElementById('loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.style.display = 'flex';
        loadingOverlay.querySelector('h3').textContent = 'Updating Layout...';
    }

    try {
        const response = await fetch('/update_layout', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            body: JSON.stringify({ action, layout_signature: layoutSignature, item_index: itemIndex })
        });
        if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`);
        renderResults(await response.json());
    } catch (error) {
        console.error('Error updating layout:', error);
        alert(`Failed to update layout: ${error.message}`);
    } finally {
        if (loadingOverlay) loadingOverlay.style.display = 'none';
    }
}

// In main.js, replace the renderResults function
function renderResults(result) {
    const container = document.getElementById('results-container');
    if (!container) return;

    container.innerHTML = ''; // Clear existing content
    if (!result || !result.layouts || result.layouts.length === 0) {
        container.innerHTML = `<div class="card placeholder"><h3>No Results to Display</h3><p>Run the optimizer to generate layouts.</p></div>`;
        return;
    }

    const fragment = document.createDocumentFragment();
    
    // 1. Render Summary
    const summary = document.createElement('section');
    summary.className = 'results-summary';
    const totalSheets = result.layouts.reduce((sum, l) => sum + l.quantity, 0);
    summary.innerHTML = `
        <div class="summary-metric"><span class="value">${result.score_percentage.toFixed(1)}%</span><span class="label">Overall Score<span class="tooltip" data-tooltip="Actual: ${result.final_score.toFixed(2)} | Ideal: ${result.ideal_score.toFixed(2)}">[?]</span></span></div>
        <div class="summary-metric"><span class="value">${totalSheets}</span><span class="label">Total Sheets Used</span></div>
        <div class="summary-metric"><span class="value">${result.num_unique_layouts}</span><span class="label">Unique Layouts</span></div>
        <div class="summary-metric"><span class="value">${result.overall_yield.toFixed(2)}%</span><span class="label">Overall Yield</span></div>
        <div class="summary-metric"><span class="value">${result.elapsed_time.toFixed(2)}s</span><span class="label">Time Spent</span></div>
        <div class="summary-metric ${result.remaining_parts.length > 0 ? 'is-alert' : ''}"><span class="value">${result.remaining_parts.length}</span><span class="label">Parts Not Placed</span></div>`;
    fragment.appendChild(summary);

    // 2. Render Layout Grid using the template
    const grid = document.createElement('div');
    grid.className = 'results-grid';
    const cardTemplate = document.getElementById('layout-card-template');
    const max_card_width = 450;
    const default_width = result.config.default_width;

    result.layouts.forEach(layout => {
        const card = cardTemplate.content.cloneNode(true).querySelector('.layout-card');
        const sheet = layout.sheet_info;

        card.style.width = `${(sheet.width / default_width) * max_card_width}px`;
        card.dataset.layoutSignature = layout.signature;
        card.querySelector('.layout-title').innerHTML = `${sheet.type} ${sheet.length.toFixed(1)}x${sheet.width.toFixed(1)} | x<span>${layout.quantity}</span> | Yield: <span>${layout.yield_percentage.toFixed(2)}</span>%`;

        const svg = card.querySelector('svg');
        svg.setAttribute('viewBox', `-2 -2 ${sheet.width + 4} ${sheet.length + 4}`);
        const sheetBg = svg.querySelector('.sheet-bg');
        sheetBg.setAttribute('width', sheet.width);
        sheetBg.setAttribute('height', sheet.length);

        let partsSvg = '';
        layout.placed_parts.forEach((part, index) => {
            const p_len = part.rotated ? part.width : part.length;
            const p_wid = part.rotated ? part.length : part.width;
            const svg_y = sheet.length - part.y - p_len;
            const itemType = part.is_stock_part ? 'stock_part' : 'job_part';
            const p_class = part.is_stock_part ? 'stock-part' : 'job-part';
            const label = part.is_stock_part ? 'STOCK' : (part.label || '');
            partsSvg += `<g class="part-group interactive-item" transform="translate(${part.x},${svg_y})" data-item-index="${index}" data-item-type="${itemType}"><rect class="${p_class}" width="${p_wid}" height="${p_len}"/><text class="part-label" x="${p_wid/2}" y="${p_len/2}" dominant-baseline="middle" text-anchor="middle">${label}</text></g>`;
        });

        let offcutsSvg = '';
        (layout.usable_offcuts || []).forEach((offcut, index) => {
            const svg_y = sheet.length - offcut.y - offcut.length;
            offcutsSvg += `<g class="offcut-group interactive-item" transform="translate(${offcut.x},${svg_y})" data-item-index="${index}" data-item-type="offcut"><rect class="offcut" width="${offcut.width}" height="${offcut.length}"/><text class="offcut-label" x="${offcut.width/2}" y="${offcut.length/2}" dominant-baseline="middle" text-anchor="middle">Offcut</text></g>`;
        });
        
        svg.innerHTML += partsSvg + offcutsSvg;
        grid.appendChild(card);
    });
    
    fragment.appendChild(grid);
    container.appendChild(fragment);
}