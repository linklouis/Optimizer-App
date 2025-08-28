document.addEventListener('DOMContentLoaded', function() {
    let stateHistory = [];
    let currentState = null;
    let treeVisualizer = null;

    function finalizeRender(result) {
        const resultsDiv = document.getElementById('results');
        
        // Clean up old listeners to prevent duplication
        resultsDiv?.removeEventListener('click', handleLayoutClick);
        const oldUndoBtn = document.getElementById('undo-button');
        if (oldUndoBtn) {
            // A simple way to remove listeners is to replace the node
            const newUndoBtn = oldUndoBtn.cloneNode(true);
            oldUndoBtn.parentNode.replaceChild(newUndoBtn, oldUndoBtn);
        }

        renderResults(result); // Render the new state

        // Attach new listeners only if the result is valid
        if (result && !result.error) {
            resultsDiv?.addEventListener('click', handleLayoutClick);
            const undoButton = document.getElementById('undo-button');
            if (undoButton) {
                undoButton.addEventListener('click', handleUndo);
                undoButton.disabled = stateHistory.length === 0;
            }
        }
    }

    // --- State-dependent Handlers ---
    async function handleUndo() {
        if (stateHistory.length === 0) return;

        const statusPanel = document.getElementById('optimization-status-panel');
        let timer = setTimeout(() => statusPanel.style.display = 'block', 200);

        const previousState = stateHistory.pop();

        try {
            const response = await fetch('/restore_state', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                body: JSON.stringify(previousState)
            });

            if (!response.ok) throw new Error('Server failed to restore state.');

            currentState = await response.json();
            finalizeRender(currentState);

        } catch (error) {
            stateHistory.push(previousState); // Push state back on failure
            console.error('Error undoing action:', error);
            alert(`Failed to undo: ${error.message}`);
        } finally {
            clearTimeout(timer);
            if (statusPanel) statusPanel.style.display = 'none';
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
        const statusPanel = document.getElementById('optimization-status-panel');
        let timer = setTimeout(() => statusPanel.style.display = 'block', 200);

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
            finalizeRender(currentState);
        } catch (error) {
            stateHistory.pop();
            console.error('Error updating layout:', error);
            alert(`Failed to update layout: ${error.message}`);
        } finally {
            clearTimeout(timer);
            statusPanel.style.display = 'none';
        }
    }

    async function handleCancelOptimization(taskId, eventSource) {
        if (!taskId) return;

        if (eventSource) eventSource.close();
        if (treeVisualizer) treeVisualizer.stop();
        
        const tickRateValue = document.getElementById('viz-tick-rate');
        if (tickRateValue) tickRateValue.textContent = '0';
        
        const statusPanel = document.getElementById('optimization-status-panel');
        if (statusPanel) statusPanel.style.display = 'none';
        
        const configTabButton = document.querySelector('.tab-button[data-tab="config"]');
        if (configTabButton) configTabButton.click();

        try {
            await fetch(`/cancel_optimization/${taskId}`, { method: 'POST' });
        } catch (error) {
            console.error("Error sending cancellation request:", error);
        }
    }


    function setupResetButtons() {
        const defaultConfigDataEl = document.getElementById('default-config-data');
        if (!defaultConfigDataEl) return;
        const DEFAULT_CONFIG = JSON.parse(defaultConfigDataEl.textContent);

        // --- General Settings Reset ---
        const resetGeneralBtn = document.getElementById('reset-general-btn');
        if (resetGeneralBtn) {
            resetGeneralBtn.addEventListener('click', () => {
                const generalKeys = [
                    'default_length', 'default_width', 'kerf', 'min_offcut_dim', 
                    'search_time_limit', 'branching_factor', 'max_unique_layouts',
                    'allow_rotation', 'use_offcuts', 'add_stock_parts', 'standardize_stock_layouts'
                ];

                generalKeys.forEach(key => {
                    const input = document.getElementById(key);
                    if (input && key in DEFAULT_CONFIG) {
                        const defaultValue = DEFAULT_CONFIG[key];
                        if (input.type === 'checkbox') {
                            input.checked = defaultValue;
                        } else {
                            input.value = defaultValue;
                        }
                    }
                });

                const noTimeLimitCb = document.getElementById('no-time-limit-cb');
                if (noTimeLimitCb) {
                    noTimeLimitCb.checked = (DEFAULT_CONFIG['search_time_limit'] <= 0);
                    noTimeLimitCb.dispatchEvent(new Event('change'));
                }
            });
        }
        
        // --- Scoring Weights Reset ---
        const resetWeightsBtn = document.getElementById('reset-weights-btn');
        if (resetWeightsBtn) {
            const card = resetWeightsBtn.closest('.card');
            resetWeightsBtn.addEventListener('click', () => {
                card.querySelectorAll('input[type="number"]').forEach(input => {
                    if (input.id in DEFAULT_CONFIG) {
                        input.value = DEFAULT_CONFIG[input.id];
                    }
                });
            });
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

    document.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'z') {
            event.preventDefault();
            const undoButton = document.getElementById('undo-button');
            if(undoButton && !undoButton.disabled) {
                handleUndo();
            }
        }
    });

    const configForm = document.querySelector('.config-form');
    if (configForm) {
        configForm.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && (e.target.type === 'text' || e.target.type === 'number')) {
                const parentCard = e.target.closest('.card');
                const cardTitle = parentCard?.querySelector('.card-title')?.textContent || '';
                
                if (cardTitle.includes('General Settings') || cardTitle.includes('Scoring Weights')) {
                } else if (cardTitle.includes('File Paths [?]')) {
                    e.preventDefault();
                    document.querySelector('button[value="refresh_files"]')?.click();
                } else {
                    e.preventDefault();
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

            const statusPanel = document.getElementById('optimization-status-panel');
            const progressBarFill = document.getElementById('progress-bar-fill');
            const partProgressPercent = document.getElementById('part-progress-percent');
            const timeTrackerValue = document.getElementById('time-tracker-value');
            const nodesExploredValue = document.getElementById('nodes-explored-value');
            const bestScoreValue = document.getElementById('best-score-value');
            const sheetsUsedValue = document.getElementById('sheets-used-value');
            const nodesRenderedValue = document.getElementById('viz-nodes-rendered');
            const pendingUpdatesValue = document.getElementById('viz-pending-updates');
            const tickRateValue = document.getElementById('viz-tick-rate');
            const acceptBtn = document.getElementById('accept-solution-btn');
            const cancelBtn = document.getElementById('cancel-optimization-btn');

            if (!statusPanel) return;

            statusPanel.style.display = 'block';
            document.querySelector('.tab-button[data-tab="results"]')?.click();
            document.getElementById('results-container').innerHTML = '';

            const loadingTipElement = document.getElementById('loading-tip-text');
            if (loadingTipElement) {
                const tips = [
                    "Allowing more search time can improve material yield and reduce waste.",
                    "The visualization on the right shows the optimizer's search path in real-time.",
                    "You can stop the optimization early and accept the best solution found so far.",
                    "The 'Branching Factor' setting controls how many layout options are explored at each step. Higher values may yield better results but take longer.",
                    "The 'Max Unique Layouts' setting limits the number of different cutting patterns used. Setting it to 0 removes the limit.",
                    "You can adjust scoring weights to prioritize different aspects of the solution, such as minimizing waste or using offcuts.",
                    "The scoring system adapts as better solutions are found. A new 'best score' might look lower than a previous one because the normalization scale has become stricter. The Solution Log provides a direct, apples-to-apples comparison.",
                    'You can copy a file\'s path by right-clicking it (or Shift+Right-clicking on Windows for "Copy as path") and pasting it into the input fields.',
                    "A negative score is normal! Scores are relative values used to compare options. The Overall Score % in the results provides a more intuitive measure of success.",
                    "The search path visualizer uses color to indicate a path's quality. Greener nodes represent better potential solutions, while redder nodes are less promising.",
                    "Hover over the [?] icons in the results summary for more details about each metric.",
                    "You can remove individual parts or offcuts from a layout by clicking on them.",
                    "Made a change to the results you didn't like? Use the Undo button or press Ctrl+Z to revert your last modification.",
                    "The progress bar indicates how many parts have been placed in the current layout being explored, not the overall optimization progress. Expect it to fluctuate!",
                    "This app runs a faster, but worst algorithm first, so expect an initial startup time. This time will increase with more parts, though the complexity of the job has little effect, unlike with the full algorithm."
                ];
                const randomIndex = Math.floor(Math.random() * tips.length);
                loadingTipElement.innerHTML = `<strong>Tip:</strong> ${tips[randomIndex]}`;
            }

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
            if (acceptBtn) acceptBtn.disabled = true;
            if (cancelBtn) cancelBtn.disabled = false;
            
            const logContainer = document.getElementById('solution-log-entries');
            if(logContainer) logContainer.innerHTML = '';


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

                const newCancelBtn = cancelBtn.cloneNode(true);
                cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
                newCancelBtn.addEventListener('click', () => handleCancelOptimization(task_id, eventSource));

                const newAcceptBtn = acceptBtn.cloneNode(true);
                acceptBtn.parentNode.replaceChild(newAcceptBtn, acceptBtn);
                newAcceptBtn.addEventListener('click', async () => {
                    newAcceptBtn.disabled = true;
                    newCancelBtn.disabled = true;
                    try {
                        await fetch(`/cancel_optimization/${task_id}`, { method: 'POST' });
                    } catch (error) {
                        console.error("Error sending early termination request:", error);
                        newAcceptBtn.disabled = false;
                        newCancelBtn.disabled = false;
                    }
                });

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
                    
                    if (data.sheets_in_best_solution !== undefined && data.sheets_in_best_solution !== null) {
                        sheetsUsedValue.textContent = data.sheets_in_best_solution;
                        if (newAcceptBtn) newAcceptBtn.disabled = false;
                    }

                    if (data.best_solution_preview) {
                        renderResults(data.best_solution_preview); // Render preview (non-interactive)
                    }

if (data.log_entry) {
                        const logContainer = document.getElementById('solution-log-entries');
                        if (logContainer) {
                            const entry = document.createElement('div');
                            entry.className = 'log-entry';
                            
                            const timeStamp = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
                            const newScore = data.log_entry.new_score.toFixed(2);
                            
                            let message;
                            if (data.log_entry.previous_rescored !== undefined) {
                                const prevRescored = data.log_entry.previous_rescored.toFixed(2);
                                message = `
                                    <span class="log-timestamp">[${timeStamp}]</span>
                                    <span>New best found!</span>
                                    <span class="log-score new">Score: <strong>${newScore}</strong></span>
                                    <span class="log-score old">(Prev. rescored: ${prevRescored})</span>
                                `;
                            } else {
                                message = `
                                    <span class="log-timestamp">[${timeStamp}]</span>
                                    <span>Initial solution found.</span>
                                    <span class="log-score new">Score: <strong>${newScore}</strong></span>
                                `;
                            }
                            entry.innerHTML = message.trim();
                            
                            const isScrolledToBottom = logContainer.scrollHeight - logContainer.clientHeight <= logContainer.scrollTop + 1;
                            logContainer.appendChild(entry);
                            if (isScrolledToBottom) {
                                logContainer.scrollTop = logContainer.scrollHeight;
                            }
                        }
                    }

                    if (data.tree_update && treeVisualizer) {
                        treeVisualizer.addOrUpdateNodes(data.tree_update);
                    }
                    if (treeVisualizer) {
                        if (nodesRenderedValue) nodesRenderedValue.textContent = treeVisualizer.getRenderedNodeCount().toLocaleString();
                        if (pendingUpdatesValue) pendingUpdatesValue.textContent = treeVisualizer.getPendingNodeCount().toLocaleString();
                        if (tickRateValue) tickRateValue.textContent = treeVisualizer.getTickRate().toLocaleString();
                    }
                });

                eventSource.addEventListener('done', (event) => {
                    receivedDoneEvent = true;
                    const result = JSON.parse(event.data);

                    if (treeVisualizer) {
                        if (result && result.final_tree_update) {
                            treeVisualizer.addOrUpdateNodes(result.final_tree_update);
                        }
                        treeVisualizer.stop();
                    }

                    currentState = result;
                    stateHistory = [];
                    finalizeRender(result);
                    
                    statusPanel.style.display = 'none';
                    eventSource.close();
                });

                eventSource.onerror = (err) => {
                    if (receivedDoneEvent) {
                        eventSource.close();
                        return;
                    }
                    console.error("EventSource failed:", err);
                    alert("An error occurred while streaming results. Please check the console.");
                    statusPanel.style.display = 'none';
                    eventSource.close();
                };

            } catch (error) {
                console.error("Optimization failed:", error);
                alert(`Error: ${error.message}`);
                statusPanel.style.display = 'none';
            }
        });
    }

    setupThemeToggle();
    setupTabs();
    setupResetButtons();
    setupFormControls();
    setupCollapsibleSections();
    restoreAccordionStates();

    const initialDataElement = document.getElementById('initial-results-data');
    if (initialDataElement) {
        const initialData = JSON.parse(initialDataElement.textContent);
        if (initialData) {
            currentState = initialData;
            finalizeRender(currentState);
            document.querySelector('.tab-button[data-tab="results"]')?.click();
        }
    }
});