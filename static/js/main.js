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
                return; // Click was not on a button or the button is already active
            }

            // Deactivate current active tab and content
            const currentActiveTab = tabsContainer.querySelector('.tab-button.active');
            if (currentActiveTab) {
                currentActiveTab.classList.remove('active');
                const currentContent = document.getElementById(currentActiveTab.dataset.tab);
                if (currentContent) currentContent.classList.remove('active');
            }

            // Activate new tab and content
            tabButton.classList.add('active');
            const newContent = document.getElementById(tabButton.dataset.tab);
            if (newContent) newContent.classList.add('active');
        });
    }

    /**
     * --- Dark Mode Toggle ---
     * Toggles a class on the body and updates a hidden form input.
     */
    const themeToggle = document.getElementById('darkmode-toggle');
    const hiddenThemeState = document.getElementById('dark_mode_state');
    if (themeToggle) {
        themeToggle.addEventListener('change', function() {
            document.body.classList.toggle('dark-mode', this.checked);
            if (hiddenThemeState) {
                hiddenThemeState.value = this.checked ? 'on' : 'off';
            }
        });
    }

    /**
     * --- Stock Part Selection ---
     * Handles "Select All" and "Deselect All" functionality.
     */
    const selectAllBtn = document.getElementById('select-all-stock');
    const deselectAllBtn = document.getElementById('deselect-all-stock');
    const stockCheckboxes = document.querySelectorAll('input[name="selected_stock_parts"]');

    if (selectAllBtn) {
        selectAllBtn.addEventListener('click', () => {
            stockCheckboxes.forEach(cb => cb.checked = true);
        });
    }
    if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', () => {
            stockCheckboxes.forEach(cb => cb.checked = false);
        });
    }

    /**
     * --- Activate Results Tab if Data Exists ---
     * Automatically switches to the results tab on page load if results are present.
     */
    if (document.body.dataset.hasResults === 'true') {
        const resultsTabButton = document.querySelector('.tab-button[data-tab="results"]');
        if (resultsTabButton) {
            resultsTabButton.click();
        }
    }

});