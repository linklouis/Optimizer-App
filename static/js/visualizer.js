class TreeVisualizer {
    constructor(svgId) {
        this.svg = document.getElementById(svgId);
        this.linkGroup = this.svg.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'g'));
        this.nodeGroup = this.svg.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'g'));
        
        this.nodes = {};
        this.pendingNodes = [];
        this.svgNodes = {};
        this.svgLinks = {};
        
        this.animationFrameId = null;
        this.solutionNodeId = null;
        this.activePathIds = new Set();
        this.minScore = Infinity;
        this.maxScore = -Infinity;

        this.framesSinceLastSpawn = 0;
        this.spawnInterval = 5; // Introduce a node every 5 frames
    }

    // --- Public Methods ---

    start() {
        if (this.animationFrameId) return; // Already running
        this.clear();
        this._animationLoop();
    }

    stop() {
        // TODO uncomment this line to stop the animation
        // if (this.animationFrameId) {
        //     cancelAnimationFrame(this.animationFrameId);
        //     this.animationFrameId = null;
        // }
        this._introducePendingNodes(true); // Introduce all remaining nodes
        this._updatePositions(); // Final position update
    }
    
    addOrUpdateNodes(nodeList, activePathIds = []) {
        this.activePathIds = new Set(activePathIds);
        this.pendingNodes.push(...nodeList);
    }

    clear() {
        this.nodes = {};
        this.pendingNodes = [];
        this.svgNodes = {};
        this.svgLinks = {};
        this.solutionNodeId = null;
        this.activePathIds = new Set();
        this.minScore = Infinity;
        this.maxScore = -Infinity;
        this.nodeGroup.innerHTML = '';
        this.linkGroup.innerHTML = '';
        this.framesSinceLastSpawn = 0;
    }

    // --- Animation and Drawing Loop ---

    _animationLoop() {
        const { width, height } = this.svg.parentElement.getBoundingClientRect();
        if (width > 0 && height > 0) {
            this.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
            this._introducePendingNodes();
            this._runLayoutSimulation(width, height);
            this._updatePositions();
        }
        this.animationFrameId = requestAnimationFrame(() => this._animationLoop());
    }

    _introducePendingNodes(introduceAll = false) {
        if (introduceAll) {
            // When stopping, introduce all remaining nodes at once
            this.pendingNodes.forEach(this._processSingleNode.bind(this));
            this.pendingNodes = [];
            return;
        }

        this.framesSinceLastSpawn++;
        if (this.pendingNodes.length > 0 && this.framesSinceLastSpawn >= this.spawnInterval) {
            const nodeData = this.pendingNodes.shift(); // Get one node from the front
            this._processSingleNode(nodeData);
            this.framesSinceLastSpawn = 0;
        }
    }

    // This new helper contains the logic for processing one node
    _processSingleNode(nodeData) {
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
                const { height } = this.svg.parentElement.getBoundingClientRect();
                initialX = 30;
                initialY = height > 0 ? height / 2 : 150;
            } else {
                const fallbackX = this.svg.clientWidth > 0 ? this.svg.clientWidth / 2 : 200;
                initialX = parentPos ? parentPos.x + 10 : fallbackX;
                initialY = parentPos ? parentPos.y + (Math.random() - 0.5) * 10 : 30;
            }

            this.nodes[nodeData.id] = { 
                ...existingNode, ...nodeData,
                x: existingNode?.x ?? initialX, 
                y: existingNode?.y ?? initialY,
                vx: 0, vy: 0
            };

            if (nodeData.status === 'solution') {
                this.solutionNodeId = nodeData.id;
            }
        }
    }

    _updatePositions() {
        const childCounts = this._calculateChildCounts();
        const bestPathNodeIds = this._getBestPath();

        Object.values(this.nodes).forEach(node => {
            // Update or create node SVG element
            let g = this.svgNodes[node.id];
            if (!g) {
                g = this._createNodeElement();
                this.svgNodes[node.id] = g;
                this.nodeGroup.appendChild(g);
            }
            
            // Update node classes and styles
            const isRoot = node.id === 0;
            const isBest = bestPathNodeIds.has(node.id);
            const isActive = this.activePathIds.has(node.id);
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

            // Update or create link SVG element
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
                const isActiveLink = isActive && this.activePathIds.has(parent.id);
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
    
    // --- Helper Methods ---

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

_runLayoutSimulation(width, height) {
        const nodes = Object.values(this.nodes);
        if (nodes.length < 2) return;

        // --- Tuned Physics Parameters ---
        const kRepel = 2000;
        const kAttract = 0.1;
        const kAlign = 0.05; // Strength of the new directional alignment force
        const damping = 0.3;

        // 1. Reset forces for this frame
        for (const n1 of nodes) {
            n1.fx = 0; n1.fy = 0;
            // 2. Repulsion Force (pushes all nodes away from each other)
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

        // 3. Attraction and Alignment Forces
        for (const node of nodes) {
            const parent = this.nodes[node.parent];
            if (!parent) continue;

            // --- Attraction Force (pulls node to parent) ---
            const dx_attract = parent.x - node.x;
            const dy_attract = parent.y - node.y;
            node.fx += dx_attract * kAttract;
            node.fy += dy_attract * kAttract;
            // The parent is pushed in the opposite direction
            parent.fx -= dx_attract * kAttract;
            parent.fy -= dy_attract * kAttract;

            // --- NEW: Directional Alignment Force ---
            const grandparent = this.nodes[parent.parent];
            if (grandparent) {
                // Get the vector from grandparent to parent (the "flow" direction)
                const flow_dx = parent.x - grandparent.x;
                const flow_dy = parent.y - grandparent.y;

                // Nudge the current node to follow that same direction from its parent
                const target_x = parent.x + flow_dx;
                const target_y = parent.y + flow_dy;
                
                node.fx += (target_x - node.x) * kAlign;
                node.fy += (target_y - node.y) * kAlign;
            }
        }
        
        // 4. Update Velocities and Positions
        const rootNode = this.nodes[0];
        for (const node of nodes) {
            if (node === rootNode) {
                node.x = 30; node.y = height / 2; continue;
            }
            
            node.fx += 0.1; // Constant rightward force

            node.vx = (node.vx || 0) * damping + node.fx * 0.02;
            node.vy = (node.vy || 0) * damping + node.fy * 0.02;
            node.x += node.vx;
            node.y += node.vy;

            node.x = Math.max(10, Math.min(width - 10, node.x));
            node.y = Math.max(10, Math.min(height - 10, node.y));
        }
    }
}