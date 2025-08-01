document.addEventListener('DOMContentLoaded', function() {
    let stateHistory = [];
    let currentState = null;

    // --- State-dependent Handlers ---
    async function handleUndo() {
        if (stateHistory.length === 0) return;

        const loadingOverlay = document.getElementById('loading-overlay');
        loadingOverlay.style.display = 'flex';

        const previousState = stateHistory.pop();

        try {
            const response = await fetch('/restore_state', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                body: JSON.stringify(previousState)
            });

            if (!response.ok) throw new Error('Server failed to restore state.');

            currentState = await response.json();
            renderResults(currentState);
            document.getElementById('undo-button')?.addEventListener('click', handleUndo);

        } catch (error) {
            stateHistory.push(previousState); // Push state back on failure
            console.error('Error undoing action:', error);
            alert(`Failed to undo: ${error.message}`);
        } finally {
            if (loadingOverlay) loadingOverlay.style.display = 'none';
        }
    }

    async function handleLayoutClick(e) {
        const item = e.target.closest('.interactive-item');
        if (!item) return;

        const layoutCard = item.closest('.layout-card');
        const layoutSignature = layoutCard.dataset.layoutSignature;
        const itemIndex = parseInt(item.dataset.itemIndex, 10);
        const itemType = item.dataset.itemType;
        let action = null;

        if (itemType === 'stock_part') action = 'remove_stock_part';
        else if (itemType === 'job_part') action = 'remove_job_part';
        else if (itemType === 'offcut') action = 'remove_offcut';
        
        if (!action) return;

        stateHistory.push(JSON.parse(JSON.stringify(currentState)));
        const loadingOverlay = document.getElementById('loading-overlay');
        loadingOverlay.style.display = 'flex';

        try {
            const response = await fetch('/update_layout', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                body: JSON.stringify({ 
                    action, 
                    layout_signature: layoutSignature, 
                    item_index: itemIndex,
                    current_state: currentState
                })
            });
            if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`);

            currentState = await response.json();
            renderResults(currentState);
            document.getElementById('undo-button')?.addEventListener('click', handleUndo);
        } catch (error) {
            stateHistory.pop();
            console.error('Error updating layout:', error);
            alert(`Failed to update layout: ${error.message}`);
        } finally {
            loadingOverlay.style.display = 'none';
        }
    }
        
    // --- Event Listeners and Initial Setup ---
    
    // Global keydown for Undo
    document.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'z') {
            event.preventDefault();
            handleUndo();
        }
    });

    // Main optimization form submission
    const configForm = document.querySelector('.config-form');
    if (configForm) {
        configForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const loadingOverlay = document.getElementById('loading-overlay');
            const progressBarFill = document.getElementById('progress-bar-fill');
            const partProgressPercent = document.getElementById('part-progress-percent');
            const timeTrackerValue = document.getElementById('time-tracker-value');
            const nodesExploredValue = document.getElementById('nodes-explored-value');
            const bestScoreValue = document.getElementById('best-score-value');
            const sheetsUsedValue = document.getElementById('sheets-used-value');

            if (!loadingOverlay) return;

            loadingOverlay.style.display = 'flex';
            document.querySelector('.tab-button[data-tab="results"]')?.click();
            document.getElementById('results-container').innerHTML = '';

            progressBarFill.style.width = '0%';
            partProgressPercent.textContent = '0%';
            timeTrackerValue.textContent = '00:00';
            nodesExploredValue.textContent = '0';
            bestScoreValue.textContent = '-';
            sheetsUsedValue.textContent = '-';

            try {
                const formData = new FormData(configForm);
                const startResponse = await fetch('/start_optimization', {
                    method: 'POST',
                    body: formData
                });

                if (!startResponse.ok) throw new Error('Failed to start optimization task.');
                
                const { task_id } = await startResponse.json();
                const eventSource = new EventSource(`/stream/${task_id}`);

                eventSource.addEventListener('progress', (event) => {
                    const data = JSON.parse(event.data);
                    
                    if (data.part_progress !== undefined) {
                        progressBarFill.style.width = `${data.part_progress}%`;
                        partProgressPercent.textContent = `${data.part_progress}%`;
                    }

                    if (data.elapsed_time !== undefined) {
                        const formatTime = (seconds) => {
                            const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
                            const secs = Math.floor(seconds % 60).toString().padStart(2, '0');
                            return `${mins}:${secs}`;
                        };
                        let timeText = formatTime(data.elapsed_time);
                        if (data.time_limit > 0) {
                            timeText += ` / ${formatTime(data.time_limit)}`;
                        }
                        timeTrackerValue.textContent = timeText;
                    }

                    if (data.nodes_explored !== undefined) nodesExploredValue.textContent = data.nodes_explored.toLocaleString();
                    if (data.current_best_score !== undefined && data.current_best_score !== null) bestScoreValue.textContent = data.current_best_score.toFixed(2);
                    if (data.sheets_in_best_solution !== undefined && data.sheets_in_best_solution !== null) sheetsUsedValue.textContent = data.sheets_in_best_solution;
                });

                eventSource.addEventListener('done', (event) => {
                    const result = JSON.parse(event.data);
                    currentState = result;
                    stateHistory = [];
                    renderResults(result);
                    document.getElementById('undo-button')?.addEventListener('click', handleUndo);
                    loadingOverlay.style.display = 'none';
                    eventSource.close();
                });

                eventSource.onerror = (err) => {
                    console.error("EventSource failed:", err);
                    alert("An error occurred while streaming results. Please check the console.");
                    loadingOverlay.style.display = 'none';
                    eventSource.close();
                };

            } catch (error) {
                console.error("Optimization failed:", error);
                alert(`Error: ${error.message}`);
                loadingOverlay.style.display = 'none';
            }
        });
    }

    // Attach listener for layout interaction
    document.getElementById('results')?.addEventListener('click', handleLayoutClick);
    
    // Initial UI Setup
    setupThemeToggle();
    setupTabs();
    setupFormControls();
    setupCollapsibleSections();

    // Render initial data if it exists
    const initialDataElement = document.getElementById('initial-results-data');
    if (initialDataElement) {
        const initialData = JSON.parse(initialDataElement.textContent);
        if (initialData) {
            currentState = initialData;
            renderResults(currentState);
            document.getElementById('undo-button')?.addEventListener('click', handleUndo);
            document.querySelector('.tab-button[data-tab="results"]')?.click();
        }
    }
});