document.addEventListener('DOMContentLoaded', function() {
    let stateHistory = [];
    let currentState = null;
    let treeVisualizer = null;

    // --- State-dependent Handlers ---
    async function handleUndo() {
        if (stateHistory.length === 0) return;

        const loadingOverlay = document.getElementById('loading-overlay');
        // Prevent loading flicker on fast actions
        let timer = setTimeout(() => loadingOverlay.style.display = 'flex', 200);

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
            if(currentState && !currentState.error) {
                document.getElementById('undo-button')?.addEventListener('click', handleUndo);
            }

        } catch (error) {
            stateHistory.push(previousState); // Push state back on failure
            console.error('Error undoing action:', error);
            alert(`Failed to undo: ${error.message}`);
        } finally {
            clearTimeout(timer);
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
        // Prevent loading flicker on fast actions
        let timer = setTimeout(() => loadingOverlay.style.display = 'flex', 200);

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
             if(currentState && !currentState.error) {
                document.getElementById('undo-button')?.addEventListener('click', handleUndo);
            }
        } catch (error) {
            stateHistory.pop();
            console.error('Error updating layout:', error);
            alert(`Failed to update layout: ${error.message}`);
        } finally {
            clearTimeout(timer);
            loadingOverlay.style.display = 'none';
        }
    }

    async function handleCancelOptimization(taskId, eventSource) {
        if (!taskId) return;

        // 1. Immediately stop all client-side activity
        if (eventSource) {
            eventSource.close();
        }
        if (treeVisualizer) {
            treeVisualizer.stop();
        }
        
        // 2. Hide the loading overlay and switch back to the config tab
        const loadingOverlay = document.getElementById('loading-overlay');
        if (loadingOverlay) loadingOverlay.style.display = 'none';
        
        const configTabButton = document.querySelector('.tab-button[data-tab="config"]');
        if (configTabButton) {
            configTabButton.click();
        }

        // 3. Send the cancellation request to the server in the background
        try {
            await fetch(`/cancel_optimization/${taskId}`, { method: 'POST' });
        } catch (error) {
            console.error("Error sending cancellation request:", error);
        }
    }
        
    // --- Event Listeners and Initial Setup ---
    
    function restoreAccordionStates() {
        document.querySelectorAll('details.collapsible-section').forEach(details => {
            const key = details.querySelector('.card-title')?.textContent.trim();
            const wasOpen = localStorage.getItem(`accordion-${key}`);
            if (key && wasOpen === 'true') {
                details.open = true;
            }
        });
    }

    // Global keydown for Undo
    document.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'z') {
            event.preventDefault();
            const undoButton = document.getElementById('undo-button');
            if(undoButton && !undoButton.disabled) {
                handleUndo();
            }
        }
    });

    // Main optimization form submission
    const configForm = document.querySelector('.config-form');
    if (configForm) {
        // Control form submission via Enter key
        configForm.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && (e.target.type === 'text' || e.target.type === 'number')) {
                const parentCard = e.target.closest('.card');
                const cardTitle = parentCard?.querySelector('.card-title')?.textContent || '';
                
                if (cardTitle.includes('General Settings') || cardTitle.includes('Scoring Weights')) {
                    // Do nothing, allow submission to bubble up to the form
                } else if (cardTitle.includes('File Paths')) {
                    e.preventDefault(); // Prevent default form submit
                    document.querySelector('button[value="refresh_files"]')?.click();
                } else {
                    e.preventDefault(); // Prevent submission from other cards
                }
            }
        });

        configForm.addEventListener('submit', async (e) => {
            const action = e.submitter?.name === 'action' ? e.submitter.value : 'optimize';
            
            if (action === 'refresh_files') {
                document.querySelectorAll('details.collapsible-section').forEach(details => {
                    const key = details.querySelector('.card-title')?.textContent.trim();
                    if (key) {
                        localStorage.setItem(`accordion-${key}`, details.open);
                    }
                });
                return; 
            }

            e.preventDefault();

            const loadingOverlay = document.getElementById('loading-overlay');
            const progressBarFill = document.getElementById('progress-bar-fill');
            const partProgressPercent = document.getElementById('part-progress-percent');
            const timeTrackerValue = document.getElementById('time-tracker-value');
            const nodesExploredValue = document.getElementById('nodes-explored-value');
            const bestScoreValue = document.getElementById('best-score-value');
            const sheetsUsedValue = document.getElementById('sheets-used-value');
            const pendingUpdatesValue = document.getElementById('pending-updates-value');

            if (!loadingOverlay) return;

            loadingOverlay.style.display = 'flex';
            document.querySelector('.tab-button[data-tab="results"]')?.click();
            document.getElementById('results-container').innerHTML = '';

            // --- Initialize Visualizer ---
            const visualizerSvgId = 'path-visualization-svg'; 
            treeVisualizer = new TreeVisualizer(visualizerSvgId);
            treeVisualizer.start();

            progressBarFill.style.width = '0%';
            partProgressPercent.textContent = '0%';
            timeTrackerValue.textContent = '00:00';
            nodesExploredValue.textContent = '0';
            bestScoreValue.textContent = '-';
            sheetsUsedValue.textContent = '-';
            if (pendingUpdatesValue) pendingUpdatesValue.textContent = '0';

            try {
                const formData = new FormData(configForm);
                const startResponse = await fetch('/start_optimization', {
                    method: 'POST',
                    body: formData
                });

                if (!startResponse.ok) throw new Error('Failed to start optimization task.');
                
                const { task_id } = await startResponse.json();
                const eventSource = new EventSource(`/stream/${task_id}`);
                let receivedDoneEvent = false;

                // --- Attach Cancel Button Handler ---
                const cancelBtn = document.getElementById('cancel-optimization-btn');
                if (cancelBtn) {
                    // Clone and replace to remove any old listeners from previous runs
                    const newCancelBtn = cancelBtn.cloneNode(true);
                    cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
                    newCancelBtn.addEventListener('click', () => handleCancelOptimization(task_id, eventSource));
                }

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
                        if (data.time_limit > 0) timeText += ` / ${formatTime(data.time_limit)}`;
                        timeTrackerValue.textContent = timeText;
                    }
                    if (data.nodes_explored !== undefined) nodesExploredValue.textContent = data.nodes_explored.toLocaleString();
                    if (data.current_best_score !== undefined && data.current_best_score !== null) bestScoreValue.textContent = data.current_best_score.toFixed(2);
                    if (data.sheets_in_best_solution !== undefined && data.sheets_in_best_solution !== null) sheetsUsedValue.textContent = data.sheets_in_best_solution;
                    // --- Update Visualizer ---
                    if (data.tree_update && treeVisualizer) {
                        treeVisualizer.addOrUpdateNodes(data.tree_update);
                    }
                    // --- Update Pending Count ---
                    if (treeVisualizer && pendingUpdatesValue) {
                        pendingUpdatesValue.textContent = treeVisualizer.getPendingNodeCount().toLocaleString();
                    }
                });

                eventSource.addEventListener('done', (event) => {
                    receivedDoneEvent = true;
                    const result = JSON.parse(event.data); // Parse result first

                    if (treeVisualizer) {
                        // Process any final node updates that arrived with the 'done' event
                        if (result && result.final_tree_update) {
                            treeVisualizer.addOrUpdateNodes(result.final_tree_update);
                        }
                        treeVisualizer.stop(); // Stop the animation and draw the final state
                    }

                    if (treeVisualizer && pendingUpdatesValue) {
                        pendingUpdatesValue.textContent = treeVisualizer.getPendingNodeCount().toLocaleString();
                    }

                    currentState = result;
                    stateHistory = [];
                    // Only render if the result is not null
                    if (result) { 
                        renderResults(result);
                        if(!result.error) {
                            document.getElementById('undo-button')?.addEventListener('click', handleUndo);
                        }
                    } else {
                        // If result is null, display a placeholder
                        renderResults(null); 
                    }
                    // --- DEBUG: Keep visualizer open. To revert, uncomment the line below. ---
                    // TODO: Remove this line in production
                    // loadingOverlay.style.display = 'none';
                    eventSource.close();
                });

                eventSource.onerror = (err) => {
                    if (receivedDoneEvent) { // If we're already done, ignore the error
                        eventSource.close();
                        return;
                    }
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
    restoreAccordionStates(); // This was the missing call

    // Render initial data if it exists
    const initialDataElement = document.getElementById('initial-results-data');
    if (initialDataElement) {
        const initialData = JSON.parse(initialDataElement.textContent);
        currentState = initialData;
        renderResults(currentState); // Always render, even if data is null
        
        if (currentState && !currentState.error) {
            document.getElementById('undo-button')?.addEventListener('click', handleUndo);
            // Only switch to results tab if there's actual data
            document.querySelector('.tab-button[data-tab="results"]')?.click();
        }
    }
});