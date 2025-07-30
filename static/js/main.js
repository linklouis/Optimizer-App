document.addEventListener('DOMContentLoaded', function() {

    /**
     * --- Tabbed Interface ---
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
     * --- Dark Mode Toggle with Persistence ---
     */
    const themeToggle = document.getElementById('darkmode-toggle');
    const darkModeInput = document.getElementById('dark_mode_state');

    function setTheme(isDark) {
        document.body.classList.toggle('dark-mode', isDark);
        if(themeToggle) themeToggle.checked = isDark;
        if(darkModeInput) darkModeInput.value = isDark ? 'on' : 'off';
    }

    const savedTheme = localStorage.getItem('theme');
    if (savedTheme !== null) {
        setTheme(savedTheme === 'dark');
    } else {
        if(themeToggle) themeToggle.checked = document.body.classList.contains('dark-mode');
    }

    if (themeToggle) {
        themeToggle.addEventListener('change', function() {
            const isDark = this.checked;
            setTheme(isDark);
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
        });
    }


    /**
     * --- Stock Part Selection ---
     */
    const selectAllStockBtn = document.getElementById('select-all-stock');
    const deselectAllStockBtn = document.getElementById('deselect-all-stock');
    const stockSelector = document.querySelector('.stock-selector:not(.offcut-selector)');

    if (selectAllStockBtn) selectAllStockBtn.addEventListener('click', () => {
        stockSelector.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
        stockSelector.querySelectorAll('.required-qty-input').forEach(input => input.disabled = false);
    });
    if (deselectAllStockBtn) deselectAllStockBtn.addEventListener('click', () => {
        stockSelector.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
        stockSelector.querySelectorAll('.required-qty-input').forEach(input => {
            input.disabled = true;
            input.value = '';
        });
    });

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
     * --- Loaded Part Selection ---
     */
    const loadedPartsAccordion = document.getElementById('loaded-parts-accordion');
    if (loadedPartsAccordion) {
        loadedPartsAccordion.addEventListener('change', e => {
            const selectAllCheckbox = e.target.closest('.select-all-parts-in-file');
            if (selectAllCheckbox) {
                const table = selectAllCheckbox.closest('table');
                const isChecked = selectAllCheckbox.checked;
                table.querySelectorAll('tbody input[name="selected_hhos_parts"]').forEach(cb => {
                    cb.checked = isChecked;
                });
            }
        });
    }


    /**
     * --- Offcut Selection ---
     */
    const selectAllOffcutsBtn = document.getElementById('select-all-offcuts');
    const deselectAllOffcutsBtn = document.getElementById('deselect-all-offcuts');
    const offcutSelector = document.querySelector('.offcut-selector');

    if (selectAllOffcutsBtn) selectAllOffcutsBtn.addEventListener('click', () => {
        offcutSelector.querySelectorAll('input[name="selected_offcuts"]').forEach(cb => cb.checked = true);
    });
    if (deselectAllOffcutsBtn) deselectAllOffcutsBtn.addEventListener('click', () => {
        offcutSelector.querySelectorAll('input[name="selected_offcuts"]').forEach(cb => cb.checked = false);
        offcutSelector.querySelectorAll('input[name="prioritized_offcuts"]').forEach(cb => cb.checked = false);
    });


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

    if (addHhosBtn) {
        addHhosBtn.addEventListener('click', () => {
            hhosList.appendChild(hhosRowTemplate());
        });
    }

    if (hhosList) {
        hhosList.addEventListener('click', (e) => {
            const removeBtn = e.target.closest('.btn-remove-file');
            if (removeBtn) {
                if (hhosList.querySelectorAll('.file-input-group').length > 1) {
                    removeBtn.parentElement.remove();
                }
            }
        });
    }

    /**
     * --- Loading Indicator on Form Submit ---
     */
    const configForm = document.querySelector('.config-form');
    const resultsTabButton = document.querySelector('.tab-button[data-tab="results"]');
    const loadingOverlay = document.getElementById('loading-overlay');

    if (configForm) {
        configForm.addEventListener('submit', () => {
            const hhosInputs = document.querySelectorAll('input[name="hhos_paths"]');
            const hasHhosFile = Array.from(hhosInputs).some(input => input.value.trim() !== '');
            const requiredQtyInputs = document.querySelectorAll('.required-qty-input');
            const hasRequiredStock = Array.from(requiredQtyInputs).some(input => input.value.trim() !== '' && parseInt(input.value) > 0);

            if (hasHhosFile || hasRequiredStock) {
                if (loadingOverlay) {
                    loadingOverlay.style.display = 'flex';
                }
                if (resultsTabButton && !resultsTabButton.classList.contains('active')) {
                    resultsTabButton.click();
                }
            }
        });
    }

    /**
     * --- NEW: Animated Accordion for Collapsible Sections ---
     */
    document.querySelectorAll('details.animated-accordion').forEach((accordion) => {
        const content = accordion.querySelector('.collapsible-content');
        
        // Set initial max-height for accordions that start open
        if (accordion.open) {
            content.style.maxHeight = content.scrollHeight + 'px';
        }

        accordion.addEventListener('toggle', () => {
            if (accordion.open) {
                // When opening
                content.style.maxHeight = content.scrollHeight + 'px';
            } else {
                // When closing
                content.style.maxHeight = content.scrollHeight + 'px';
                requestAnimationFrame(() => content.style.maxHeight = '0px');
            }
        });

        content.addEventListener('transitionend', () => {
            // After the transition ends, if the accordion is open, remove the explicit
            // max-height so the content can resize if the window does.
            if (accordion.open) {
                content.style.maxHeight = 'revert';
            }
        });
    });

    /**
     * --- NEW: Resizable Card Sections ---
     */
    document.querySelectorAll('.resizable-card').forEach(card => {
        const handle = card.querySelector('.resize-handle');
        const selectorList = card.querySelector('.stock-selector');

        if (!handle || !selectorList) {
            return;
        }

        let startY;
        let startHeight;

        handle.addEventListener('mousedown', function(e) {
            e.preventDefault();
            startY = e.clientY;
            startHeight = parseInt(document.defaultView.getComputedStyle(selectorList).height, 10);
            
            document.documentElement.addEventListener('mousemove', doDrag, false);
            document.documentElement.addEventListener('mouseup', stopDrag, false);

            document.body.style.userSelect = 'none';
            document.body.style.cursor = 'ns-resize';
        });

        function doDrag(e) {
            const newHeight = startHeight + e.clientY - startY;
            // Add constraints for min/max height
            if (newHeight > 100 && newHeight < 800) { 
                selectorList.style.height = newHeight + 'px';
            }
        }

        function stopDrag() {
            document.documentElement.removeEventListener('mousemove', doDrag, false);
            document.documentElement.removeEventListener('mouseup', stopDrag, false);
            
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        }
    });
});