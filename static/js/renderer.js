function renderResults(result) {
    const container = document.getElementById('results-container');
    if (!container) return;
    container.innerHTML = '';
    if (!result || result.error || !result.layouts || result.layouts.length === 0) {
        let message = "No Results to Display";
        let subtext = "Run the optimizer to generate layouts.";
        if (result && result.error) {
            message = "Optimization Failed";
            subtext = result.error;
        }
        container.innerHTML = `<div class="card placeholder"><h3>${message}</h3><p>${subtext}</p></div>`;
        return;
    }
    const fragment = document.createDocumentFragment();
    const summary = document.createElement('section');
    summary.className = 'results-summary';
    const totalSheets = result.layouts.reduce((sum, l) => sum + l.quantity, 0);
    summary.innerHTML = `
        <div class="summary-metric"><span class="value">${result.score_percentage.toFixed(1)}%</span><span class="label">Overall Score<span class="tooltip" data-tooltip="Actual: ${result.final_score.toFixed(2)} | Ideal: ${result.ideal_score.toFixed(2)}">[?]</span></span></div>
        <div class="summary-metric"><span class="value">${totalSheets}</span><span class="label">Total Sheets Used</span></div>
        <div class="summary-metric"><span class="value">${result.num_unique_layouts}</span><span class="label">Unique Layouts</span></div>
        <div class="summary-metric"><span class="value">${result.overall_yield.toFixed(2)}%</span><span class="label">Overall Yield</span></div>
        <div class="summary-metric"><span class="value">${result.elapsed_time.toFixed(2)}s</span><span class="label">Time Spent</span></div>
        <div class="summary-metric ${result.remaining_parts.length > 0 ? 'is-alert' : ''}"><span class="value">${result.remaining_parts.length}</span><span class="label">Parts Not Placed</span></div>
        <div class="summary-metric action-metric">
            <button id="undo-button" class="btn-undo"><i class="fa-solid fa-rotate-left"></i> Undo</button>
        </div>`;
    fragment.appendChild(summary);
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
        
        let svgContent = '';

        layout.placed_parts.forEach((part, index) => {
            const p_len = part.rotated ? part.width : part.length;
            const p_wid = part.rotated ? part.length : part.width;
            const svg_y = sheet.length - part.y - p_len;
            const itemType = part.is_stock_part ? 'stock_part' : 'job_part';
            const p_class = part.is_stock_part ? 'stock-part' : 'job-part';
            const label = part.is_stock_part ? 'STOCK' : (part.label || '');

            let dimLabels = '';
            if (p_wid > 8 && p_len > 8) {
                const textOffset = 1.5; // Using your preferred value
                dimLabels += `<text class="inner-dim-text" x="${p_wid/2}" y="${textOffset}" dominant-baseline="hanging" text-anchor="middle">${p_wid.toFixed(2)}</text>`;
                dimLabels += `<text class="inner-dim-text" x="${textOffset}" y="${p_len/2}" transform="rotate(-90, ${textOffset}, ${p_len/2})" dominant-baseline="middle" text-anchor="middle">${p_len.toFixed(2)}</text>`;
            }

            svgContent += `<g class="part-group interactive-item" transform="translate(${part.x},${svg_y})" data-item-index="${index}" data-item-type="${itemType}">
                            <rect class="${p_class}" width="${p_wid}" height="${p_len}"/>
                            <text class="part-label" x="${p_wid/2}" y="${p_len/2}" dominant-baseline="middle" text-anchor="middle">${label}</text>
                            ${dimLabels}
                        </g>`;
        });

        (layout.usable_offcuts || []).forEach((offcut, index) => {
            const o_wid = offcut.width;
            const o_len = offcut.length;
            const svg_y = sheet.length - offcut.y - o_len;
            
            let dimLabels = '';
            if (o_wid > 8 && o_len > 8) {
                const textOffset = 1.5; // Applying consistent positioning to offcuts
                dimLabels += `<text class="inner-dim-text" x="${o_wid/2}" y="${textOffset}" dominant-baseline="hanging" text-anchor="middle">${o_wid.toFixed(2)}</text>`;
                dimLabels += `<text class="inner-dim-text" x="${textOffset}" y="${o_len/2}" transform="rotate(-90, ${textOffset}, ${o_len/2})" dominant-baseline="middle" text-anchor="middle">${o_len.toFixed(2)}</text>`;
            }

            svgContent += `<g class="offcut-group interactive-item" transform="translate(${offcut.x},${svg_y})" data-item-index="${index}" data-item-type="offcut">
                            <rect class="offcut" width="${o_wid}" height="${o_len}"/>
                            <text class="offcut-label" x="${o_wid/2}" y="${o_len/2}" dominant-baseline="middle" text-anchor="middle">Offcut</text>
                            ${dimLabels}
                        </g>`;
        });
        
        sheetBg.insertAdjacentHTML('afterend', svgContent);
        grid.appendChild(card);
    });
    
    fragment.appendChild(grid);
    container.appendChild(fragment);
}