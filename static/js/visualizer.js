const RENDER_THRESHOLD = 0.1; // Min pixels a node must move to trigger a DOM update
class TreeVisualizer {
    constructor(svgId) {
        this.svg = document.getElementById(svgId);
        this.svg.innerHTML = ''; // Wipes the SVG clean from any previous runs
        
        // Create a wrapper group for zoom and pan
        this.zoomWrapper = this.svg.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'g'));
        this.linkGroup = this.zoomWrapper.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'g'));
        this.nodeGroup = this.zoomWrapper.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'g'));
        
        this.nodes = {};
        this.pendingNodes = [];
        this.svgNodes = {};
        this.svgLinks = {};
        this.processedNodeUpdates = new Set();
        
        this.animationFrameId = null;
        this.simulationLoopId = null; // ID for the main thread's data processing loop
        this.simulationInterval = 0;  // Target ~250 ticks per second for the simulation

        this.solutionNodeId = null;
        this.lastAddedNodeId = null;
        this.minScore = Infinity;
        this.maxScore = -Infinity;

        this.framesSinceLastSpawn = 0;

        this.childCounts = {};
        this.activePathNodeIds = null;
        this.bestPathNodeIds = null;
        this.needsGlobalColorUpdate = true;

        this.tickCounter = 0;
        this.tickRate = 0;
        this.tickRateIntervalId = null;

        this.nodeList = [];
        this.isNodeListDirty = true;

        // --- Properties for Zoom and Pan ---
        this.zoom = { x: 0, y: 0, scale: 0.5 };
        this.isPanning = false;
        this.panStart = { x: 0, y: 0 };

        // --- Event Listeners for Navigation ---
        this.svg.addEventListener('wheel', this._handleZoom.bind(this), { passive: false });
        this.svg.addEventListener('mousedown', this._handlePanStart.bind(this));
        this.svg.addEventListener('mousemove', this._handlePanMove.bind(this));
        this.svg.addEventListener('mouseup', this._handlePanEnd.bind(this));
        this.svg.addEventListener('mouseleave', this._handlePanEnd.bind(this));

        // --- Web Worker for Physics ---
        this.physicsWorker = new Worker('static/js/physics_worker.js');
        this.physicsWorker.onmessage = this._handleWorkerMessage.bind(this);
        this.nodesToSendToWorker = [];
    }
    
    /**
     * Handles messages received from the physics worker thread.
     * @param {MessageEvent} e - The event object from the worker.
     */
    _handleWorkerMessage(e) {
        const { type, payload } = e.data;
        if (type === 'tick') {
            const workerNodes = payload.nodes;
            // Update main thread node positions from worker data.
            // This is the only place physics state should be written to on the main thread.
            for (const id in workerNodes) {
                if (this.nodes[id]) {
                    const workerNode = workerNodes[id];
                    this.nodes[id].x = workerNode.x;
                    this.nodes[id].y = workerNode.y;
                    this.nodes[id].vx = workerNode.vx;
                    this.nodes[id].vy = workerNode.vy;
                }
            }
        }
    }

    // --- Public Methods ---

    start() {
        if (this.animationFrameId) return;
        this.clear();
        this.physicsWorker.postMessage({ type: 'start' }); // Start the worker's simulation
        this.simulationLoopId = setInterval(this._simulationLoop.bind(this), this.simulationInterval);
        this.tickRateIntervalId = setInterval(() => {
            this.tickRate = this.tickCounter; // Ticks per second
            this.tickCounter = 0;
        }, 1000);
        this._animationLoop();
    }

    stop() {
        if (this.tickRateIntervalId) {
            clearInterval(this.tickRateIntervalId);
            this.tickRateIntervalId = null;
        }
        if (this.simulationLoopId) {
            clearInterval(this.simulationLoopId);
            this.simulationLoopId = null;
        }
        this.physicsWorker.postMessage({ type: 'stop' }); // Stop the worker's simulation

        // Process any remaining nodes synchronously
        while (this.pendingNodes.length > 0) {
            const nodeData = this.pendingNodes.shift();
            this._processSingleNode(nodeData);
        }
        
        // Send one final update to the worker to ensure it has the complete final state
        if (this.nodesToSendToWorker.length > 0) {
            const childCounts = this._calculateChildCounts();
            this.physicsWorker.postMessage({
                type: 'update',
                payload: { newOrUpdatedNodes: this.nodesToSendToWorker, childCounts: childCounts }
            });
            this.nodesToSendToWorker = [];
        }
        
        this._updatePositions(this._calculateChildCounts());
    }
    
    addOrUpdateNodes(nodeList) {
        this.pendingNodes.push(...nodeList);
    }

    clear() {
        if (this.simulationLoopId) {
            clearInterval(this.simulationLoopId);
            this.simulationLoopId = null;
        }
        if (this.tickRateIntervalId) {
            clearInterval(this.tickRateIntervalId);
            this.tickRateIntervalId = null;
        }
        this.physicsWorker.postMessage({ type: 'clear' }); // Clear the worker's state
        this.nodesToSendToWorker = [];

        this.tickCounter = 0;
        this.tickRate = 0;
        this.nodes = {};
        this.pendingNodes = [];
        this.svgNodes = {};
        this.svgLinks = {};
        this.processedNodeUpdates = new Set();
        this.solutionNodeId = null;
        this.lastAddedNodeId = null;
        this.minScore = Infinity;
        this.maxScore = -Infinity;
        this.nodeGroup.innerHTML = '';
        this.linkGroup.innerHTML = '';
        this.framesSinceLastSpawn = 0;
        const { width, height } = this.svg.parentElement.getBoundingClientRect();
        this.zoom = { x: width / 2, y: height / 2, scale: 0.5 };
    }

    getPendingNodeCount() {
        return this.pendingNodes.length;
    }

    getRenderedNodeCount() {
        return Object.keys(this.nodes).length;
    }

    getTickRate() {
        return this.tickRate;
    }

    _getNodeList() {
        if (this.isNodeListDirty) {
            this.nodeList = Object.values(this.nodes);
            this.isNodeListDirty = false;
        }
        return this.nodeList;
    }

    // --- Navigation Handlers (Unchanged) ---
    _handleZoom(e) { e.preventDefault(); const zoomIntensity = 0.1; const wheel = e.deltaY < 0 ? 1 : -1; const newScale = this.zoom.scale * (1 + wheel * zoomIntensity); const point = new DOMPoint(e.clientX, e.clientY); const transformedPoint = point.matrixTransform(this.svg.getScreenCTM().inverse()); this.zoom.x = transformedPoint.x - (transformedPoint.x - this.zoom.x) * (newScale / this.zoom.scale); this.zoom.y = transformedPoint.y - (transformedPoint.y - this.zoom.y) * (newScale / this.zoom.scale); this.zoom.scale = newScale; }
    _handlePanStart(e) { e.preventDefault(); this.isPanning = true; this.panStart.x = e.clientX; this.panStart.y = e.clientY; this.svg.style.cursor = 'grabbing'; }
    _handlePanMove(e) { if (!this.isPanning) return; e.preventDefault(); const dx = (e.clientX - this.panStart.x) / this.zoom.scale; const dy = (e.clientY - this.panStart.y) / this.zoom.scale; this.zoom.x += dx; this.zoom.y += dy; this.panStart.x = e.clientX; this.panStart.y = e.clientY; }
    _handlePanEnd(e) { this.isPanning = false; this.svg.style.cursor = 'grab'; }


    // --- Loops ---

    _simulationLoop() {
        // This fast loop now handles data processing and COMMUNICATION with the worker.
        this.tickCounter++;
        this._introducePendingNodes(); // This may call _processSingleNode, which queues new nodes for the worker.

        // Always calculate the latest child counts and send them along with any newly processed nodes.
        const childCounts = this._calculateChildCounts();
        this.physicsWorker.postMessage({
            type: 'update',
            payload: {
                newOrUpdatedNodes: this.nodesToSendToWorker,
                childCounts: childCounts
            }
        });
        this.nodesToSendToWorker = []; // Clear the queue after sending
    }

    _animationLoop() {
        // This smooth loop only handles rendering. It is unchanged.
        const { width, height } = this.svg.parentElement.getBoundingClientRect();
        if (width > 0 && height > 0) {
            this.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
            this.zoomWrapper.setAttribute('transform', `translate(${this.zoom.x}, ${this.zoom.y}) scale(${this.zoom.scale})`);
            
            this._updatePositions(this.childCounts);
        }
        this.animationFrameId = requestAnimationFrame(() => this._animationLoop());
    }

    // --- Processing and Drawing ---

    _introducePendingNodes() {
        const pendingCount = this.pendingNodes.length;
        if (pendingCount === 0) {
            return;
        }

        const maxInterval = 10;
        const minInterval = 0;
        const lowerThreshold = 50;
        const upperThreshold = 500;

        let dynamicInterval;
        if (pendingCount <= lowerThreshold) {
            dynamicInterval = maxInterval;
        } else if (pendingCount >= upperThreshold) {
            dynamicInterval = minInterval;
        } else {
            // Calculate linear progress from 0.0 to 1.0
            const linearProgress = (pendingCount - lowerThreshold) / (upperThreshold - lowerThreshold);
            // Apply a cubic easing curve to create an exponential effect
            const exponentialProgress = 1 - Math.pow(1 - linearProgress, 3);
            dynamicInterval = maxInterval - exponentialProgress * (maxInterval - minInterval);
        }
        
        this.framesSinceLastSpawn++;
        if (this.framesSinceLastSpawn >= dynamicInterval) {
            const nodeData = this.pendingNodes.shift();
            this._processSingleNode(nodeData);
            this.framesSinceLastSpawn = 0;
        }
    }

    _processSingleNode(nodeData) {
        const updateSignature = `${nodeData.id}-${nodeData.status}`;
        if (this.processedNodeUpdates.has(updateSignature)) {
            const existingNode = this.nodes[nodeData.id];
            console.warn(`Visualizer skipped redundant update for Node ${nodeData.id}.`, { 
                "Incoming Update": { status: nodeData.status, score: nodeData.score },
                "Existing Node State": existingNode ? { ...existingNode } : '!! NODE DOES NOT EXIST IN VISUALIZER YET !!' 
            });
            return;
        }

        if (nodeData.score !== undefined) {
            const scoreChanged = this.minScore > nodeData.score || this.maxScore < nodeData.score;
            if (scoreChanged) {
                this.minScore = Math.min(this.minScore, nodeData.score);
                this.maxScore = Math.max(this.maxScore, nodeData.score);
                this.needsGlobalColorUpdate = true;
            }
        }

        if (this.solutionNodeId === nodeData.id && nodeData.status !== 'solution') {
            this.solutionNodeId = null;
            this.bestPathNodeIds = null;
        }

        const existingNode = this.nodes[nodeData.id];
        if (!existingNode || existingNode.status !== nodeData.status) {
            const parentPos = this.nodes[nodeData.parent];
            const isRoot = nodeData.id === 0;

            let initialX, initialY;

            if (isRoot) {
                initialX = 0;
                initialY = 0;
            } else if (parentPos) {
                const grandparentPos = this.nodes[parentPos.parent];
                const spawn_distance = 20;

                if (grandparentPos) {
                    let flow_dx = parentPos.x - grandparentPos.x;
                    let flow_dy = parentPos.y - grandparentPos.y;
                    const magnitude = Math.sqrt(flow_dx * flow_dx + flow_dy * flow_dy);
                    if (magnitude > 0.1) {
                        initialX = parentPos.x + (flow_dx / magnitude) * spawn_distance;
                        initialY = parentPos.y + (flow_dy / magnitude) * spawn_distance;
                    } else {
                        const angle = Math.random() * 2 * Math.PI;
                        initialX = parentPos.x + Math.cos(angle) * spawn_distance;
                        initialY = parentPos.y + Math.sin(angle) * spawn_distance;
                    }
                } else {
                    const angle = Math.random() * 2 * Math.PI;
                    initialX = parentPos.x + Math.cos(angle) * spawn_distance + (Math.random() * 30 + 7);
                    initialY = parentPos.y + Math.sin(angle) * spawn_distance;
                }
            } else {
                initialX = 0; initialY = 0;
            }

            // Initialize new nodes with all required physics properties.
            const newNode = { 
                ...existingNode, ...nodeData,
                x: existingNode?.x ?? initialX, 
                y: existingNode?.y ?? initialY,
                vx: 0, vy: 0,
                fx: 0, fy: 0,
                cachedColor: (existingNode && existingNode.score === nodeData.score) ? existingNode.cachedColor : null
            };
            
            this.nodes[nodeData.id] = newNode;
            // Queue this new/updated node to be sent to the worker in the next simulation tick.
            this.nodesToSendToWorker.push(newNode);

            this.lastAddedNodeId = nodeData.id;

            if (nodeData.status === 'solution') {
                this.solutionNodeId = nodeData.id;
            }

            this.activePathNodeIds = null;
            this.bestPathNodeIds = null;
            this.isNodeListDirty = true;
        }
        
        this.processedNodeUpdates.add(updateSignature);
    }

    _getActivePath() {
        const activePath = new Set();
        if (this.lastAddedNodeId !== null) {
            let currentNode = this.nodes[this.lastAddedNodeId];
            while (currentNode) {
                activePath.add(currentNode.id);
                currentNode = this.nodes[currentNode.parent];
            }
        }
        return activePath;
    }

    _updatePositions(childCounts) {
        if (!this.bestPathNodeIds) {
            this.bestPathNodeIds = this._getBestPath();
        }
        if (!this.activePathNodeIds) {
            this.activePathNodeIds = this._getActivePath();
        }

        if (this.needsGlobalColorUpdate) {
            for (const node of this._getNodeList()) {
                node.cachedColor = null;
            }
            this.needsGlobalColorUpdate = false;
        }

        this._getNodeList().forEach(node => {
            let g = this.svgNodes[node.id];
            if (!g) {
                g = this._createNodeElement();
                this.svgNodes[node.id] = g;
                this.nodeGroup.appendChild(g);
            }
            
            const isRoot = node.id === 0;
            const isBest = this.bestPathNodeIds.has(node.id);
            const isActive = this.activePathNodeIds.has(node.id);
            const isHighBranching = (childCounts[node.id] || 0) > 3;

            const classList = ['node', `status-${node.status}`];
            if (isBest) classList.push('on-best-path');
            if (isActive) classList.push('on-active-path');
            if (isHighBranching) classList.push('high-branching');
            if (isRoot) classList.push('is-root');
            
            const newClassName = classList.join(' ');
            if (g.dataset.currentClass !== newClassName) {
                g.setAttribute('class', newClassName);
                g.dataset.currentClass = newClassName;
            }
            
            const circle = g.querySelector('circle');
            if (!isRoot && !isHighBranching && node.status !== 'solution') {
                if (!node.cachedColor) {
                    node.cachedColor = this._getScoreColor(node.score);
                }
                if (circle.style.fill !== node.cachedColor) {
                    circle.style.fill = node.cachedColor;
                }
            } else {
                circle.removeAttribute('style');
            }

            const lastX = parseFloat(g.dataset.x || -9999);
            const lastY = parseFloat(g.dataset.y || -9999);
            if (Math.abs(node.x - lastX) > RENDER_THRESHOLD || Math.abs(node.y - lastY) > RENDER_THRESHOLD) {
                g.setAttribute('transform', `translate(${node.x}, ${node.y})`);
                g.dataset.x = node.x;
                g.dataset.y = node.y;
            }

            const parent = this.nodes[node.parent];
            if (parent) {
                const linkId = `${parent.id}-${node.id}`;
                let link = this.svgLinks[linkId];
                if (!link) {
                    link = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                    this.svgLinks[linkId] = link;
                    this.linkGroup.appendChild(link);
                }
                const isBestLink = isBest && this.bestPathNodeIds.has(parent.id);
                const isActiveLink = isActive && this.activePathNodeIds.has(parent.id);
                const newLinkClassName = `link ${isBestLink ? 'on-best-path' : ''} ${isActiveLink ? 'on-active-path' : ''}`;
                if (link.dataset.currentClass !== newLinkClassName) {
                    link.setAttribute('class', newLinkClassName);
                    link.dataset.currentClass = newLinkClassName;
                }
                
                const lastP_X = parseFloat(link.dataset.x1 || -9999);
                const lastP_Y = parseFloat(link.dataset.y1 || -9999);
                const lastN_X = parseFloat(link.dataset.x2 || -9999);
                const lastN_Y = parseFloat(link.dataset.y2 || -9999);
                if (Math.abs(parent.x - lastP_X) > RENDER_THRESHOLD || Math.abs(parent.y - lastP_Y) > RENDER_THRESHOLD ||
                    Math.abs(node.x - lastN_X) > RENDER_THRESHOLD || Math.abs(node.y - lastN_Y) > RENDER_THRESHOLD) {
                    link.setAttribute('x1', parent.x);
                    link.setAttribute('y1', parent.y);
                    link.setAttribute('x2', node.x);
                    link.setAttribute('y2', node.y);
                    link.dataset.x1 = parent.x;
                    link.dataset.y1 = parent.y;
                    link.dataset.x2 = node.x;
                    link.dataset.y2 = node.y;
                }
            }
        });
    }

    _createNodeElement() {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('r', 2.5);
        g.appendChild(circle);
        return g;
    }
    
    _getScoreColor(score) {
        if (score === undefined || this.minScore === Infinity || this.maxScore === -Infinity) {
            return 'var(--bg-secondary)';
        }
        const range = this.maxScore - this.minScore;
        if (range < 1e-6) return 'hsl(60, 60%, 50%)';
        const normalized = (score - this.minScore) / range;
        const hue = normalized * 120;
        return `hsl(${hue}, 70%, 50%)`;
    }

    _calculateChildCounts() {
        const childCounts = {};
        for (const nodeId in this.nodes) childCounts[nodeId] = 0;
        for (const node of this._getNodeList()) {
            if (node.parent !== null && childCounts.hasOwnProperty(node.parent)) {
                childCounts[node.parent]++;
            }
        }
        return childCounts;
    }

    _getBestPath() {
        const bestPath = new Set();
        if (this.solutionNodeId !== null) {
            let currentNode = this.nodes[this.solutionNodeId];
            while (currentNode) {
                bestPath.add(currentNode.id);
                currentNode = this.nodes[currentNode.parent];
            }
        }
        return bestPath;
    }
}