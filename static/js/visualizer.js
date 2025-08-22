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
        this.simulationLoopId = null; // ID for the fast simulation loop
        this.simulationInterval = 0;  // 4 = target ~250 ticks per second for the simulation

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

        // --- New properties for Zoom and Pan ---
        this.zoom = { x: 0, y: 0, scale: 0.5 };
        this.isPanning = false;
        this.panStart = { x: 0, y: 0 };

        // --- Event Listeners for Navigation ---
        this.svg.addEventListener('wheel', this._handleZoom.bind(this), { passive: false });
        this.svg.addEventListener('mousedown', this._handlePanStart.bind(this));
        this.svg.addEventListener('mousemove', this._handlePanMove.bind(this));
        this.svg.addEventListener('mouseup', this._handlePanEnd.bind(this));
        this.svg.addEventListener('mouseleave', this._handlePanEnd.bind(this));
    }

    // --- Public Methods ---

    start() {
        if (this.animationFrameId) return;
        this.clear();
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
        // if (this.simulationLoopId) {
        //     clearInterval(this.simulationLoopId);
        //     this.simulationLoopId = null;
        // }
        // // It's good practice to stop the animation frame when done.
        // // If you need to debug the final state, you can comment this out.
        // if (this.animationFrameId) {
        //      cancelAnimationFrame(this.animationFrameId);
        //      this.animationFrameId = null;
        // }
        // Process any remaining nodes synchronously
        while (this.pendingNodes.length > 0) {
            const nodeData = this.pendingNodes.shift();
            this._processSingleNode(nodeData);
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

    // --- Navigation Handlers ---
    _handleZoom(e) { e.preventDefault(); const zoomIntensity = 0.1; const wheel = e.deltaY < 0 ? 1 : -1; const newScale = this.zoom.scale * (1 + wheel * zoomIntensity); const point = new DOMPoint(e.clientX, e.clientY); const transformedPoint = point.matrixTransform(this.svg.getScreenCTM().inverse()); this.zoom.x = transformedPoint.x - (transformedPoint.x - this.zoom.x) * (newScale / this.zoom.scale); this.zoom.y = transformedPoint.y - (transformedPoint.y - this.zoom.y) * (newScale / this.zoom.scale); this.zoom.scale = newScale; }
    _handlePanStart(e) { e.preventDefault(); this.isPanning = true; this.panStart.x = e.clientX; this.panStart.y = e.clientY; this.svg.style.cursor = 'grabbing'; }
    _handlePanMove(e) { if (!this.isPanning) return; e.preventDefault(); const dx = (e.clientX - this.panStart.x) / this.zoom.scale; const dy = (e.clientY - this.panStart.y) / this.zoom.scale; this.zoom.x += dx; this.zoom.y += dy; this.panStart.x = e.clientX; this.panStart.y = e.clientY; }
    _handlePanEnd(e) { this.isPanning = false; this.svg.style.cursor = 'grab'; }


    // --- Loops ---

    _simulationLoop() {
        // This fast loop handles data processing and physics
        this.tickCounter++;
        this._introducePendingNodes();
        this.childCounts = this._calculateChildCounts();
        this._runLayoutSimulation(this.childCounts);
    }

    _animationLoop() {
        // This smooth loop only handles rendering
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
                const spawn_distance = Math.random() * 20 + 10; // Random distance between 10 and 30 pixels

                // Calculate spawn direction based on recent ancestor flow
                let total_flow_dx = 0;
                let total_flow_dy = 0;
                let ancestorCount = 0;
                let weight = 1.0;
                const maxAncestors = 20;   // How many generations to look back
                const decayFactor = 1; // How much less influence older ancestors have

                let currentAncestor = parentPos;
                // Traverse up the tree for a few generations
                for (let i = 0; i < maxAncestors && currentAncestor; i++) {
                    const ancestorParent = this.nodes[currentAncestor.parent];
                    if (ancestorParent) {
                        const flow_dx = currentAncestor.x - ancestorParent.x;
                        const flow_dy = currentAncestor.y - ancestorParent.y;

                        total_flow_dx += flow_dx * weight;
                        total_flow_dy += flow_dy * weight;
                        ancestorCount++;
                        weight *= decayFactor;
                    } else {
                        break;
                    }
                    currentAncestor = ancestorParent;
                }

                // Use the averaged flow direction if available, otherwise use a random angle
                const flow_magnitude = Math.sqrt(total_flow_dx * total_flow_dx + total_flow_dy * total_flow_dy);
                if (ancestorCount > 0 && flow_magnitude > 0.1) {
                    initialX = parentPos.x + (total_flow_dx / flow_magnitude) * spawn_distance;
                    initialY = parentPos.y + (total_flow_dy / flow_magnitude) * spawn_distance;
                } else {
                    // Fallback for nodes without a clear ancestor path (e.g., children of root)
                    const angle = Math.random() * 2 * Math.PI;
                    initialX = parentPos.x + Math.cos(angle) * spawn_distance;
                    initialY = parentPos.y + Math.sin(angle) * spawn_distance;
                }
            } else {
                initialX = 0; initialY = 0;
            }

            // Initialize new nodes with all required physics properties.
            this.nodes[nodeData.id] = { 
                ...existingNode, ...nodeData,
                x: existingNode?.x ?? initialX, 
                y: existingNode?.y ?? initialY,
                vx: 0, vy: 0,
                fx: 0, fy: 0,
                cachedColor: (existingNode && existingNode.score === nodeData.score) ? existingNode.cachedColor : null
            };
            
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

    _runLayoutSimulation(childCounts) {
        const nodes = this._getNodeList();
        if (nodes.length < 2) return;

        const kRepel = 2500;
        const kAttract = 0.03;
        const kAlign = 1;
        const damping = 0.01;
        const directionalForce = 7;

        // 1. Repulsion Force Calculation.
        // Assumes forces were reset to 0 at the end of the last tick.
        // We don't need to worry about the root node (id=0) since it's fixed at the center, and a constant repulsion is already applied later.
        for (let i = 1; i < nodes.length; i++) {
            const n1 = nodes[i];
            for (let j = i + 1; j < nodes.length; j++) {
                const n2 = nodes[j];

                const dx = n1.x - n2.x;
                const dy = n1.y - n2.y;
                let distance = Math.sqrt(dx * dx + dy * dy);
                if (distance < 1) distance = 1;

                // const force = kRepel / (Math.pow(distance, 2));
                // const forceX = (dx / distance) * force;
                // const forceY = (dy / distance) * force;
                const force = kRepel / (Math.pow(distance, 3));
                const forceX = dx * force;
                const forceY = dy * force;
                
                n1.fx += forceX;
                n1.fy += forceY;
                n2.fx -= forceX;
                n2.fy -= forceY;
            }
        }

        // 2. Attraction, Alignment, and Directional Forces.
        for (const node of nodes) {
            const parent = this.nodes[node.parent];
            if (parent) {
                // Non-linear attraction force (stronger at longer distances)
                const dx = parent.x - node.x;
                const dy = parent.y - node.y;
                const distance = Math.sqrt(dx * dx + dy * dy) + 1e-6; // Add small epsilon to prevent issues at zero distance

                // Scale the linear force by the distance, making the final force proportional to distance squared.
                const dx_attract = dx * kAttract * distance;
                const dy_attract = dy * kAttract * distance;

                if (parent === nodes[0]) {
                    node.fx += dx_attract * 2;
                    node.fy += dy_attract * 2;
                } else {
                    node.fx += dx_attract;
                    node.fy += dy_attract;
                    parent.fx -= dx_attract;
                    parent.fy -= dy_attract;
                }


                // Alignment force (based on recent ancestor flow)
                let total_flow_dx = 0;
                let total_flow_dy = 0;
                let weight = 1.0;
                const maxAncestors = 15;    // How many generations to look back for flow
                const decayFactor = 1;  // Influence of older ancestors
                let currentAncestor = parent;

                // Traverse up the tree for a few generations to get an average flow direction
                for (let i = 0; i < maxAncestors && currentAncestor; i++) {
                    const ancestorParent = this.nodes[currentAncestor.parent];
                    if (ancestorParent) {
                        const dx = currentAncestor.x - ancestorParent.x;
                        const dy = currentAncestor.y - ancestorParent.y;

                        total_flow_dx += dx * weight;
                        total_flow_dy += dy * weight;
                        weight *= decayFactor; // Older ancestors have less influence
                    } else {
                        break; // Stop if we hit the root
                    }
                    currentAncestor = ancestorParent;
                }

                const flow_magnitude = Math.sqrt(total_flow_dx * total_flow_dx + total_flow_dy * total_flow_dy);

                if (flow_magnitude > 0.1) {
                    // Normalize the averaged flow vector to get a pure direction.
                    const norm_flow_dx = total_flow_dx / flow_magnitude;
                    const norm_flow_dy = total_flow_dy / flow_magnitude;

                    // Get the child's current distance from its parent.
                    const current_relative_dx = node.x - parent.x;
                    const current_relative_dy = node.y - parent.y;
                    const current_distance = Math.sqrt(current_relative_dx * current_relative_dx + current_relative_dy * current_relative_dy) * 0.9;

                    // Calculate the ideal target position for rotation.
                    const target_x = parent.x + norm_flow_dx * current_distance;
                    const target_y = parent.y + norm_flow_dy * current_distance;

                    // Apply a force pulling the node towards this rotational target.
                    const nodeChildCount = childCounts[node.id] || 0;
                    const alignmentStrength = 1 + Math.log1p(nodeChildCount);
                    node.fx += (target_x - node.x) * kAlign * alignmentStrength;
                    node.fy += (target_y - node.y) * kAlign * alignmentStrength;
                }
            }
            
            const dist_from_center = Math.sqrt(node.x * node.x + node.y * node.y);
            if (dist_from_center > 1) {
                node.fx += (node.x / dist_from_center) * directionalForce;
                node.fy += (node.y / dist_from_center) * directionalForce;
            }
        }
        
        // 3. Update Velocities/Positions AND Reset Forces for the next tick.
        for (let i = 1; i < nodes.length; i++) {
            const node = nodes[i];

            node.vx = (node.vx || 0) * damping + node.fx * 0.015;
            node.vy = (node.vy || 0) * damping + node.fy * 0.015;
            if (Math.abs(node.vx) <= 70 && Math.abs(node.vy) <= 70) {
                node.x += node.vx;
                node.y += node.vy;
            } else {
                node.vx = 70 * node.vx / Math.abs(node.vx);
                node.vy = 70 * node.vy / Math.abs(node.vy);
            }

            node.fx = 0;
            node.fy = 0;
        }
    }
}