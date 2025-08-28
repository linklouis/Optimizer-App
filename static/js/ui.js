function setupThemeToggle() {
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
    } else if(themeToggle) {
        themeToggle.checked = document.body.classList.contains('dark-mode');
    }

    if (themeToggle) {
        themeToggle.addEventListener('change', function() {
            const isDark = this.checked;
            setTheme(isDark);
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
        });
    }
}

function setupTabs() {
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
}

function setupFormControls() {
    // Reset Stock Quantities Button
    document.getElementById('reset-stock-qty')?.addEventListener('click', () => {
        const card = document.getElementById('reset-stock-qty').closest('.card');
        card?.querySelectorAll('.required-qty-input').forEach(input => {
            input.value = '';
        });
    });
    
    // Unprioritize All Offcuts Button
    document.getElementById('unprioritize-all-offcuts')?.addEventListener('click', () => {
        document.querySelectorAll('.offcut-selector input[name="prioritized_offcuts"]').forEach(cb => {
            cb.checked = false;
        });
    });

    // Reset Scoring Weights Button
    document.getElementById('reset-weights-btn')?.addEventListener('click', () => {
        const weightsDataEl = document.getElementById('default-weights-data');
        if (!weightsDataEl) return;
        
        const defaultWeights = JSON.parse(weightsDataEl.textContent);
        for (const key in defaultWeights) {
            const inputEl = document.getElementById(key);
            if (inputEl) {
                inputEl.value = defaultWeights[key];
            }
        }
    });

    // Optional Time Limit Checkbox
    const timeLimitInput = document.getElementById('search_time_limit');
    const noTimeLimitCb = document.getElementById('no-time-limit-cb');
    if (timeLimitInput && noTimeLimitCb) {
        let previousTimeLimit = timeLimitInput.value;

        if (parseFloat(timeLimitInput.value) <= 0) {
            noTimeLimitCb.checked = true;
            timeLimitInput.disabled = true;
        }

        noTimeLimitCb.addEventListener('change', () => {
            if (noTimeLimitCb.checked) {
                previousTimeLimit = timeLimitInput.value;
                timeLimitInput.value = '0';
                timeLimitInput.disabled = true;
            } else {
                timeLimitInput.value = previousTimeLimit > 0 ? previousTimeLimit : '5.0';
                timeLimitInput.disabled = false;
            }
        });

        timeLimitInput.addEventListener('input', () => {
             if(!noTimeLimitCb.checked) {
                previousTimeLimit = timeLimitInput.value;
             }
        });
    }

    // Selector buttons (Select/Deselect All)
    function setupSelector(type) {
        const idSuffix = type === 'offcut' ? 'offcuts' : type;
        const selectAllBtn = document.getElementById(`select-all-${idSuffix}`);
        const deselectAllBtn = document.getElementById(`deselect-all-${idSuffix}`);
        
        const containerSelector = (type === 'stock') 
            ? '.stock-selector:not(.offcut-selector)' 
            : '.stock-selector.offcut-selector';
        
        const selectorContainer = document.querySelector(containerSelector);

        if (!selectAllBtn || !deselectAllBtn || !selectorContainer) return;
        
        const checkboxSelector = `input[name="selected_${type === 'stock' ? 'stock_parts' : 'offcuts'}"]`;

        selectAllBtn.addEventListener('click', () => {
            selectorContainer.querySelectorAll(checkboxSelector).forEach(cb => {
                if (!cb.disabled) cb.checked = true;
            });
        });
        deselectAllBtn.addEventListener('click', () => {
            selectorContainer.querySelectorAll(checkboxSelector).forEach(cb => {
                if (!cb.disabled) cb.checked = false;
            });
        });
    }
    setupSelector('stock');
    setupSelector('offcut');
    
    // Logic for enabling/disabling stock quantity inputs
    document.querySelector('.stock-selector:not(.offcut-selector)')?.addEventListener('change', (e) => {
        const cb = e.target.closest('input[name="selected_stock_parts"]');
        if (cb) {
            const qtyInput = cb.closest('.stock-item').querySelector('.required-qty-input');
            if (qtyInput) {
                qtyInput.disabled = !cb.checked;
                if (!cb.checked) qtyInput.value = '';
            }
        }
    });
    
    // Select all parts within a loaded file
    document.getElementById('loaded-parts-accordion')?.addEventListener('change', e => {
        const selectAllCheckbox = e.target.closest('.select-all-parts-in-file');
        if (selectAllCheckbox) {
            selectAllCheckbox.closest('table').querySelectorAll('tbody input[name="selected_hhos_parts"]').forEach(cb => {
                cb.checked = selectAllCheckbox.checked;
            });
        }
    });

    // Dynamic file list for HHOS files
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
            if (removeBtn) {
                removeBtn.parentElement.remove();
                document.querySelector('button[value="refresh_files"]')?.click();
            }
        });
    }
}

function setupCollapsibleSections() {
    // Accordions
    document.querySelectorAll('details.animated-accordion').forEach((accordion) => {
        const content = accordion.querySelector('.collapsible-content');
        if (!content) return;
        
        if (accordion.open) {
            content.style.maxHeight = content.scrollHeight + 'px';
        }
        accordion.addEventListener('toggle', () => {
            content.style.maxHeight = accordion.open ? content.scrollHeight + 'px' : '0px';
        });
    });

    // Resizable cards
    document.querySelectorAll('.resizable-card .resize-handle').forEach(handle => {
        const collapsibleContent = handle.closest('.collapsible-content');
        if (!collapsibleContent) return;
        
        const isJobPartsCard = !!handle.parentElement.querySelector('#loaded-parts-accordion');

        handle.addEventListener('mousedown', function(e) {
            e.preventDefault();
            const accordion = handle.closest('details.animated-accordion');
            if (!accordion || !accordion.open) return;

            // Determine the target(s) for resizing based on the card type
            const resizeTargets = isJobPartsCard
                ? collapsibleContent.querySelectorAll('.accordion-item[open] .table-container')
                : collapsibleContent.querySelectorAll('.stock-selector');
            
            if (resizeTargets.length === 0) return;

            const startY = e.clientY;
            const startHeight = resizeTargets[0].offsetHeight;
            const originalTransition = collapsibleContent.style.transition;
            collapsibleContent.style.transition = 'none';
            
            const doDrag = (e) => {
                const newHeight = Math.max(50, startHeight + e.clientY - startY);
                resizeTargets.forEach(target => {
                    // Set height on the specific targets
                    target.style.height = `${newHeight}px`;
                });
                // Update the main container to ensure it fits the new content
                collapsibleContent.style.maxHeight = `${collapsibleContent.scrollHeight}px`;
            };

            const stopDrag = () => {
                collapsibleContent.style.transition = originalTransition;
                document.documentElement.removeEventListener('mousemove', doDrag);
                document.documentElement.removeEventListener('mouseup', stopDrag);
            };

            document.documentElement.addEventListener('mousemove', doDrag);
            document.documentElement.addEventListener('mouseup', stopDrag, { once: true });
        });
    });

    // Logic for conditional resize handle and auto-resizing of Job Parts card
    const jobPartsAccordion = document.getElementById('loaded-parts-accordion');
    if (jobPartsAccordion) {
        const mainCollapsibleContent = jobPartsAccordion.closest('.animated-accordion > .collapsible-content');
        const resizeHandle = jobPartsAccordion.closest('.resizable-card')?.querySelector('.resize-handle');

        const updateParentContainer = () => {
            // Update resize handle visibility
            if (resizeHandle) {
                const anyAccordionOpen = jobPartsAccordion.querySelector('details[open]');
                resizeHandle.style.display = anyAccordionOpen ? 'flex' : 'none';
            }
            // Adjust parent container height to fit new content if it's open
            if (mainCollapsibleContent) {
                const parentAccordion = mainCollapsibleContent.closest('details.animated-accordion');
                if (parentAccordion && parentAccordion.open) {
                    mainCollapsibleContent.style.maxHeight = mainCollapsibleContent.scrollHeight + 'px';
                }
            }
        };

        jobPartsAccordion.addEventListener('toggle', updateParentContainer, true);
        updateParentContainer(); // Initial check on page load
    }
}


function setupVisualizationToggle() {
    const vizToggle = document.getElementById('enable_visualization');
    if (!vizToggle) return;

    // Set initial state from localStorage (defaults to true/enabled)
    const isEnabled = localStorage.getItem('visualizationEnabled') !== 'false';
    vizToggle.checked = isEnabled;

    // Save state when changed
    vizToggle.addEventListener('change', () => {
        localStorage.setItem('visualizationEnabled', vizToggle.checked);
    });
}