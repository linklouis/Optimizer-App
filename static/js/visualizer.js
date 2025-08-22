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
        this.simulationInterval = 4;  // Target ~250 ticks per second for the simulation

        this.solutionNodeId = null;
        this.lastAddedNodeId = null;
        this.minScore = Infinity;
        this.maxScore = -Infinity;

        this.framesSinceLastSpawn = 0;

        this.childCounts = {};
        this.activePathNodeIds = null;
        this.bestPathNodeIds = null;

        // --- New properties for Zoom and Pan ---
        this.zoom = { x: 0, y: 0, scale: 1.0 };
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
        this._animationLoop();
    }

    stop() {
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
        this.zoom = { x: width / 2, y: height / 2, scale: 1.0 };
    }

    getPendingNodeCount() {
        return this.pendingNodes.length;
    }

    // --- Navigation Handlers (Omitted for brevity, no changes) ---
    _handleZoom(e) { e.preventDefault(); const zoomIntensity = 0.1; const wheel = e.deltaY < 0 ? 1 : -1; const newScale = this.zoom.scale * (1 + wheel * zoomIntensity); const point = new DOMPoint(e.clientX, e.clientY); const transformedPoint = point.matrixTransform(this.svg.getScreenCTM().inverse()); this.zoom.x = transformedPoint.x - (transformedPoint.x - this.zoom.x) * (newScale / this.zoom.scale); this.zoom.y = transformedPoint.y - (transformedPoint.y - this.zoom.y) * (newScale / this.zoom.scale); this.zoom.scale = newScale; }
    _handlePanStart(e) { e.preventDefault(); this.isPanning = true; this.panStart.x = e.clientX; this.panStart.y = e.clientY; this.svg.style.cursor = 'grabbing'; }
    _handlePanMove(e) { if (!this.isPanning) return; e.preventDefault(); const dx = (e.clientX - this.panStart.x) / this.zoom.scale; const dy = (e.clientY - this.panStart.y) / this.zoom.scale; this.zoom.x += dx; this.zoom.y += dy; this.panStart.x = e.clientX; this.panStart.y = e.clientY; }
    _handlePanEnd(e) { this.isPanning = false; this.svg.style.cursor = 'grab'; }


    // --- Loops ---

    _simulationLoop() {
        // This fast loop handles data processing and physics
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
            this.minScore = Math.min(this.minScore, nodeData.score);
            this.maxScore = Math.max(this.maxScore, nodeData.score);
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
            this.nodes[nodeData.id] = { 
                ...existingNode, ...nodeData,
                x: existingNode?.x ?? initialX, 
                y: existingNode?.y ?? initialY,
                vx: 0, vy: 0,
                fx: 0, fy: 0
            };
            
            this.lastAddedNodeId = nodeData.id;

            if (nodeData.status === 'solution') {
                this.solutionNodeId = nodeData.id;
            }

            this.activePathNodeIds = null;
            this.bestPathNodeIds = null;
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

        Object.values(this.nodes).forEach(node => {
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
                circle.setAttribute('style', `fill: ${this._getScoreColor(node.score)}`);
            } else {
                circle.removeAttribute('style');
            }

            g.setAttribute('transform', `translate(${node.x}, ${node.y})`);

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
                link.setAttribute('x1', parent.x);
                link.setAttribute('y1', parent.y);
                link.setAttribute('x2', node.x);
                link.setAttribute('y2', node.y);
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
        for (const node of Object.values(this.nodes)) {
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
        const nodes = Object.values(this.nodes);
        if (nodes.length < 2) return;

        const kRepel = 2000;
        const kAttract = 2;
        const kAlign = 1;
        const damping = 0.1;
        const directionalForce = 20;

        // 1. Repulsion Force Calculation.
        // Assumes forces were reset to 0 at the end of the last tick.
        for (let i = 0; i < nodes.length; i++) {
            const n1 = nodes[i];
            for (let j = i + 1; j < nodes.length; j++) {
                const n2 = nodes[j];

                const dx = n1.x - n2.x;
                const dy = n1.y - n2.y;
                let distance = Math.sqrt(dx * dx + dy * dy);
                if (distance < 1) distance = 1;

                const force = kRepel / (Math.pow(distance, 1.8));
                const forceX = (dx / distance) * force;
                const forceY = (dy / distance) * force;
                
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
                const dx_attract = parent.x - node.x;
                const dy_attract = parent.y - node.y;
                node.fx += dx_attract * kAttract;
                node.fy += dy_attract * kAttract;
                parent.fx -= dx_attract * kAttract;
                parent.fy -= dy_attract * kAttract;

                const grandparent = this.nodes[parent.parent];
                if (grandparent) {
                    const flow_dx = parent.x - grandparent.x;
                    const flow_dy = parent.y - grandparent.y;
                    const flow_magnitude = Math.sqrt(flow_dx * flow_dx + flow_dy * flow_dy);

                    if (flow_magnitude > 0.1) {
                        const desired_distance = 20;
                        const norm_flow_dx = (flow_dx / flow_magnitude) * desired_distance;
                        const norm_flow_dy = (flow_dy / flow_magnitude) * desired_distance;
                        const target_x = parent.x + norm_flow_dx;
                        const target_y = parent.y + norm_flow_dy;
                        const nodeChildCount = childCounts[node.id] || 0;
                        const alignmentStrength = 1 + Math.log1p(nodeChildCount);
                        node.fx += (target_x - node.x) * kAlign * alignmentStrength;
                        node.fy += (target_y - node.y) * kAlign * alignmentStrength;
                    }
                }
            }
            
            const dist_from_center = Math.sqrt(node.x * node.x + node.y * node.y);
            if (dist_from_center > 1) {
                node.fx += (node.x / dist_from_center) * directionalForce;
                node.fy += (node.y / dist_from_center) * directionalForce;
            }
        }
        
        // 3. Update Velocities/Positions AND Reset Forces for the next tick.
        const rootNode = this.nodes[0];
        for (const node of nodes) {
            if (node === rootNode) {
                node.x = 0; 
                node.y = 0; 
            } else {
                node.vx = (node.vx || 0) * damping + node.fx * 0.02;
                node.vy = (node.vy || 0) * damping + node.fy * 0.02;
                node.x += node.vx;
                node.y += node.vy;
            }

            // Reset forces for the next simulation tick.
            node.fx = 0;
            node.fy = 0;
        }
    }
}