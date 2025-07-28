document.addEventListener('DOMContentLoaded', function() {

    // --- Tabbed Interface ---
    window.openTab = function(evt, tabName) {
        let i, tabcontent, tablinks;
        tabcontent = document.getElementsByClassName("tab-content");
        for (i = 0; i < tabcontent.length; i++) {
            tabcontent[i].style.display = "none";
        }
        tablinks = document.getElementsByClassName("tab-button");
        for (i = 0; i < tablinks.length; i++) {
            tablinks[i].className = tablinks[i].className.replace(" active", "");
        }
        document.getElementById(tabName).style.display = "block";
        evt.currentTarget.className += " active";
    }
    
    // --- Dark Mode Toggle ---
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

    // --- Stock Part Selection ---
    const selectAllBtn = document.getElementById('select-all-stock');
    const deselectAllBtn = document.getElementById('deselect-all-stock');
    const stockCheckboxes = document.querySelectorAll('input[name="selected_stock_parts"]');

    if(selectAllBtn) {
        selectAllBtn.addEventListener('click', () => {
            stockCheckboxes.forEach(cb => cb.checked = true);
        });
    }
    if(deselectAllBtn) {
        deselectAllBtn.addEventListener('click', () => {
            stockCheckboxes.forEach(cb => cb.checked = false);
        });
    }

    // --- Activate Results Tab if Data Exists ---
    if (document.body.dataset.hasResults === 'true') {
        const resultsTabButton = document.querySelector('.tab-button[onclick*="results"]');
        if(resultsTabButton) {
            resultsTabButton.click();
        }
    }

});