class TreeVisualizer {
    constructor(svgId) {
        this.svg = document.getElementById(svgId);
        
        // Create a wrapper group for zoom and pan
        this.zoomWrapper = this.svg.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'g'));
        this.linkGroup = this.zoomWrapper.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'g'));
        this.nodeGroup = this.zoomWrapper.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'g'));
        
        this.nodes = {};
        this.pendingNodes = [];
        this.svgNodes = {};
        this.svgLinks = {};
        this.processedNodeUpdates = new Set(); // Track processed updates
        
        this.animationFrameId = null;
        this.solutionNodeId = null;
        this.lastAddedNodeId = null;
        this.minScore = Infinity;
        this.maxScore = -Infinity;

        this.framesSinceLastSpawn = 0;
        this.spawnInterval = 5;

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
        this._animationLoop();
    }

    stop() {
        // if (this.animationFrameId) {
        //      cancelAnimationFrame(this.animationFrameId);
        //      this.animationFrameId = null;
        // }
        this._introducePendingNodes(true);
        this._updatePositions(this._calculateChildCounts());
    }
    
    addOrUpdateNodes(nodeList) {
        this.pendingNodes.push(...nodeList);
    }

    clear() {
        this.nodes = {};
        this.pendingNodes = [];
        this.svgNodes = {};
        this.svgLinks = {};
        this.processedNodeUpdates = new Set(); // Reset tracker
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

    // --- Navigation Handlers ---

    _handleZoom(e) {
        e.preventDefault();
        const zoomIntensity = 0.1;
        const wheel = e.deltaY < 0 ? 1 : -1;
        const newScale = this.zoom.scale * (1 + wheel * zoomIntensity);

        const point = new DOMPoint(e.clientX, e.clientY);
        const transformedPoint = point.matrixTransform(this.svg.getScreenCTM().inverse());

        this.zoom.x = transformedPoint.x - (transformedPoint.x - this.zoom.x) * (newScale / this.zoom.scale);
        this.zoom.y = transformedPoint.y - (transformedPoint.y - this.zoom.y) * (newScale / this.zoom.scale);
        this.zoom.scale = newScale;
    }

    _handlePanStart(e) {
        e.preventDefault();
        this.isPanning = true;
        this.panStart.x = e.clientX;
        this.panStart.y = e.clientY;
        this.svg.style.cursor = 'grabbing';
    }

    _handlePanMove(e) {
        if (!this.isPanning) return;
        e.preventDefault();
        const dx = (e.clientX - this.panStart.x) / this.zoom.scale;
        const dy = (e.clientY - this.panStart.y) / this.zoom.scale;
        this.zoom.x += dx;
        this.zoom.y += dy;
        this.panStart.x = e.clientX;
        this.panStart.y = e.clientY;
    }

    _handlePanEnd(e) {
        this.isPanning = false;
        this.svg.style.cursor = 'grab';
    }

    // --- Animation and Drawing Loop ---

    _animationLoop() {
        const { width, height } = this.svg.parentElement.getBoundingClientRect();
        if (width > 0 && height > 0) {
            this.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
            this.zoomWrapper.setAttribute('transform', `translate(${this.zoom.x}, ${this.zoom.y}) scale(${this.zoom.scale})`);
            
            const childCounts = this._calculateChildCounts();
            
            this._introducePendingNodes();
            this._runLayoutSimulation(childCounts);
            this._updatePositions(childCounts);
        }
        this.animationFrameId = requestAnimationFrame(() => this._animationLoop());
    }

    _introducePendingNodes(introduceAll = false) {
        if (introduceAll) {
            this.pendingNodes.forEach(this._processSingleNode.bind(this));
            this.pendingNodes = [];
            return;
        }

        this.framesSinceLastSpawn++;
        if (this.pendingNodes.length > 0 && this.framesSinceLastSpawn >= this.spawnInterval) {
            const nodeData = this.pendingNodes.shift();
            this._processSingleNode(nodeData);
            this.framesSinceLastSpawn = 0;
        }
    }

    _processSingleNode(nodeData) {
        const updateSignature = `${nodeData.id}-${nodeData.status}`;
        if (this.processedNodeUpdates.has(updateSignature)) {
            return; // Already processed this exact update, skip to prevent stalling.
        }

        if (nodeData.score !== undefined) {
            this.minScore = Math.min(this.minScore, nodeData.score);
            this.maxScore = Math.max(this.maxScore, nodeData.score);
        }

        if (this.solutionNodeId === nodeData.id && nodeData.status !== 'solution') {
            this.solutionNodeId = null;
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
                    initialX = parentPos.x + Math.cos(angle) * spawn_distance;
                    initialY = parentPos.y + Math.sin(angle) * spawn_distance;
                }
            } else {
                initialX = 0; initialY = 0;
            }

            this.nodes[nodeData.id] = { 
                ...existingNode, ...nodeData,
                x: existingNode?.x ?? initialX, 
                y: existingNode?.y ?? initialY,
                vx: 0, vy: 0
            };
            
            this.lastAddedNodeId = nodeData.id;

            if (nodeData.status === 'solution') {
                this.solutionNodeId = nodeData.id;
            }
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
        const bestPathNodeIds = this._getBestPath();
        const activePathNodeIds = this._getActivePath();

        Object.values(this.nodes).forEach(node => {
            let g = this.svgNodes[node.id];
            if (!g) {
                g = this._createNodeElement();
                this.svgNodes[node.id] = g;
                this.nodeGroup.appendChild(g);
            }
            
            const isRoot = node.id === 0;
            const isBest = bestPathNodeIds.has(node.id);
            const isActive = activePathNodeIds.has(node.id);
            const isHighBranching = (childCounts[node.id] || 0) > 3;

            const classList = ['node', `status-${node.status}`];
            if (isBest) classList.push('on-best-path');
            if (isActive) classList.push('on-active-path');
            if (isHighBranching) classList.push('high-branching');
            if (isRoot) classList.push('is-root');
            g.setAttribute('class', classList.join(' '));
            
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
                const isBestLink = isBest && bestPathNodeIds.has(parent.id);
                const isActiveLink = isActive && activePathNodeIds.has(parent.id);
                link.setAttribute('class', `link ${isBestLink ? 'on-best-path' : ''} ${isActiveLink ? 'on-active-path' : ''}`);
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
        const damping = 0.3; // Higher values mean less damping. 1 is no damping, 0 is full stop each frame.

        for (const n1 of nodes) {
            n1.fx = 0; n1.fy = 0;
            for (const n2 of nodes) {
                if (n1 === n2) continue;
                const dx = n1.x - n2.x;
                const dy = n1.y - n2.y;
                let distance = Math.sqrt(dx * dx + dy * dy);
                if (distance < 1) distance = 1;
                const force = kRepel / (distance * distance);
                n1.fx += (dx / distance) * force;
                n1.fy += (dy / distance) * force;
            }
        }

        for (const node of nodes) {
            const parent = this.nodes[node.parent];
            if (!parent) continue;

            const dx_attract = parent.x - node.x;
            const dy_attract = parent.y - node.y;
            node.fx += dx_attract * kAttract;
            node.fy += dy_attract * kAttract;
            parent.fx -= dx_attract * kAttract;
            parent.fy -= dy_attract * kAttract;

            node.fx += 1; // Encourages the tree to grow to the right

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
        
        const rootNode = this.nodes[0];
        for (const node of nodes) {
            if (node === rootNode) {
                node.x = 0; 
                node.y = 0; 
                continue;
            }

            node.vx = (node.vx || 0) * damping + node.fx * 0.02;
            node.vy = (node.vy || 0) * damping + node.fy * 0.02;
            node.x += node.vx;
            node.y += node.vy;
        }
    }
}