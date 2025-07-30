document.addEventListener('DOMContentLoaded', function() {

    /**
     * --- Tabbed Interface ---
     * Uses event delegation for efficiency.
     */
    const tabsContainer = document.querySelector('.tabs');
    if (tabsContainer) {
        tabsContainer.addEventListener('click', (e) => {
            const tabButton = e.target.closest('.tab-button');
            if (!tabButton || tabButton.classList.contains('active')) {
                return;
            }
            const currentActiveTab = tabsContainer.querySelector('.tab-button.active');
            if (currentActiveTab) {
                currentActiveTab.classList.remove('active');
                document.getElementById(currentActiveTab.dataset.tab)?.classList.remove('active');
            }
            tabButton.classList.add('active');
            document.getElementById(tabButton.dataset.tab)?.classList.add('active');
        });
    }

    /**
     * --- Dark Mode Toggle ---
     */
    const themeToggle = document.getElementById('darkmode-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('change', function() {
            document.body.classList.toggle('dark-mode', this.checked);
            document.getElementById('dark_mode_state').value = this.checked ? 'on' : 'off';
        });
    }

    /**
     * --- Stock Part Selection ---
     */
    const selectAllBtn = document.getElementById('select-all-stock');
    const deselectAllBtn = document.getElementById('deselect-all-stock');
    const stockSelector = document.querySelector('.stock-selector');

    if (selectAllBtn) selectAllBtn.addEventListener('click', () => {
        stockSelector.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
        stockSelector.querySelectorAll('.required-qty-input').forEach(input => input.disabled = false);
    });
    if (deselectAllBtn) deselectAllBtn.addEventListener('click', () => {
        stockSelector.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
        stockSelector.querySelectorAll('.required-qty-input').forEach(input => {
            input.disabled = true;
            input.value = '';
        });
    });

    // NEW: Event listener to enable/disable required quantity inputs
    if (stockSelector) {
        stockSelector.addEventListener('change', (e) => {
            const checkbox = e.target.closest('input[name="selected_stock_parts"]');
            if (checkbox) {
                const qtyInput = checkbox.closest('.stock-item').querySelector('.required-qty-input');
                if (qtyInput) {
                    qtyInput.disabled = !checkbox.checked;
                    if (!checkbox.checked) {
                        qtyInput.value = '';
                    }
                }
            }
        });
    }


    /**
     * --- Activate Results Tab if Data Exists ---
     */
    if (document.body.dataset.hasResults === 'true') {
        document.querySelector('.tab-button[data-tab="results"]')?.click();
    }
    
    /**
     * --- Dynamic HHOS File Inputs ---
     */
    const hhosList = document.getElementById('hhos-file-list');
    const addHhosBtn = document.getElementById('add-hhos-file');

    // Template for a new row
    const hhosRowTemplate = () => {
        const group = document.createElement('div');
        group.className = 'file-input-group';
        group.innerHTML = `
            <input type="text" name="hhos_paths" placeholder="Path to HHOS file...">
            <input type="number" name="hhos_multipliers" class="multiplier-input" value="1" min="1">
            <button type="button" class="btn-remove-file"><i class="fa-solid fa-trash"></i></button>
        `;
        return group;
    };

    // Add new row
    if (addHhosBtn) {
        addHhosBtn.addEventListener('click', () => {
            hhosList.appendChild(hhosRowTemplate());
        });
    }

    // Remove row using event delegation
    if (hhosList) {
        hhosList.addEventListener('click', (e) => {
            const removeBtn = e.target.closest('.btn-remove-file');
            if (removeBtn) {
                // Do not remove the last input group
                if (hhosList.querySelectorAll('.file-input-group').length > 1) {
                    removeBtn.parentElement.remove();
                }
            }
        });
    }
});