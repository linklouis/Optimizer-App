class TreeVisualizer {
    constructor(svgId) {
        this.svg = document.getElementById(svgId);
        this.nodes = {};
        this.lastRenderTime = 0;
        this.renderInterval = 250; 
        this.solutionNodeId = null;
        this.activePathIds = new Set();
        this.minScore = Infinity;
        this.maxScore = -Infinity;
    }

    addOrUpdateNodes(nodeList, activePathIds = []) {
        let hasChanges = false;
        this.activePathIds = new Set(activePathIds);

        nodeList.forEach(nodeData => {
            // Update min/max scores for the color scale
            if (nodeData.score !== undefined) {
                this.minScore = Math.min(this.minScore, nodeData.score);
                this.maxScore = Math.max(this.maxScore, nodeData.score);
            }

            if (this.solutionNodeId === nodeData.id && nodeData.status !== 'solution') {
                this.solutionNodeId = null;
                hasChanges = true;
            }

            const existingNode = this.nodes[nodeData.id];
            if (!existingNode || existingNode.status !== nodeData.status) {
                const parentPos = existingNode?.parent ? this.nodes[existingNode.parent] : this.nodes[nodeData.parent];
                const initialX = parentPos ? parentPos.x + (Math.random() - 0.5) * 20 : this.svg.clientWidth / 2;
                const initialY = parentPos ? parentPos.y + 20 : 30;

                this.nodes[nodeData.id] = { 
                    ...existingNode, ...nodeData,
                    x: existingNode?.x ?? initialX, y: existingNode?.y ?? initialY
                };

                if (nodeData.status === 'solution') {
                    this.solutionNodeId = nodeData.id;
                }
                hasChanges = true;
            }
        });
        if (hasChanges) this.scheduleRender();
    }

    scheduleRender() {
        const now = Date.now();
        if (now - this.lastRenderTime > this.renderInterval) {
            this.render();
            this.lastRenderTime = now;
        }
    }

    _getScoreColor(score) {
        if (score === undefined || this.minScore === Infinity || this.maxScore === -Infinity) {
            return 'var(--bg-secondary)'; // Default color if score is not available
        }

        const range = this.maxScore - this.minScore;
        if (range < 1e-6) { // If all scores are effectively the same
            return 'hsl(60, 60%, 50%)'; // Return a neutral yellow
        }

        // Normalize score to be between 0 (worst) and 1 (best)
        const normalized = (score - this.minScore) / range;

        // Map normalized value to a hue from red (0) to green (120)
        const hue = normalized * 120;
        
        // Use HSL for easy color interpolation
        return `hsl(${hue}, 70%, 50%)`;
    }

    _calculateChildCounts() {
        const childCounts = {};
        for (const nodeId in this.nodes) {
            childCounts[nodeId] = 0;
        }
        for (const node of Object.values(this.nodes)) {
            if (node.parent !== null && childCounts.hasOwnProperty(node.parent)) {
                childCounts[node.parent]++;
            }
        }
        return childCounts;
    }

    _runLayoutSimulation(width, height) {
        const nodes = Object.values(this.nodes);
        if (nodes.length < 2) return;

        const kRepel = 1000;
        const kAttract = 0.5;
        const leftRepel = 100;
        const damping = 0.9;
        const iterations = 50;

        const rootNode = this.nodes[0];
        if (rootNode) {
            rootNode.x = 30;
            rootNode.y = height / 2;
        }

        for (let i = 0; i < iterations; i++) {
            for (const n1 of nodes) {
                n1.fx = 0; n1.fy = 0;
                for (const n2 of nodes) {
                    if (n1 === n2) continue;
                    const dx = n1.x - n2.x;
                    const dy = n1.y - n2.y;
                    const distanceSq = dx * dx + dy * dy;
                    if (distanceSq > 0) {
                        const force = kRepel / distanceSq;
                        n1.fx += (dx / Math.sqrt(distanceSq)) * force;
                        n1.fy += (dy / Math.sqrt(distanceSq)) * force;
                    } else {
                        n1.fx += (Math.random() - 0.5) * kRepel;
                        n1.fy += (Math.random() - 0.5) * kRepel;
                    }
                }
                n1.fx += leftRepel / (n1.x + 1);
            }

            for (const node of nodes) {
                const parent = this.nodes[node.parent];
                if (parent) {
                    const dx = parent.x - node.x;
                    const dy = parent.y - node.y;
                    node.fx += dx * kAttract;
                    node.fy += dy * kAttract;
                    parent.fx -= dx * kAttract;
                    parent.fy -= dy * kAttract;
                }
            }
            
            for (const node of nodes) {
                if (node.id === 0) continue;
                node.vx = (node.vx || 0) * damping + node.fx * 0.01;
                node.vy = (node.vy || 0) * damping + node.fy * 0.01;
                node.x += node.vx;
                node.y += node.vy;

                node.x = Math.max(10, Math.min(width - 10, node.x));
                node.y = Math.max(10, Math.min(height - 10, node.y));
            }
        }
    }

    render() {
        if (!this.svg) return;
        
        const { width, height } = this.svg.parentElement.getBoundingClientRect();
        if (width === 0 || height === 0) return;
        
        this.svg.innerHTML = '';
        this.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

        this._runLayoutSimulation(width, height);
        const childCounts = this._calculateChildCounts();

        const bestPathNodeIds = new Set();
        const bestPathLinkIds = new Set();
        if (this.solutionNodeId !== null) {
            let currentNode = this.nodes[this.solutionNodeId];
            while (currentNode) {
                bestPathNodeIds.add(currentNode.id);
                if (currentNode.parent !== null && this.nodes[currentNode.parent]) {
                     bestPathLinkIds.add(`${currentNode.parent}-${currentNode.id}`);
                }
                currentNode = this.nodes[currentNode.parent];
            }
        }

        const fragment = document.createDocumentFragment();
        const linkGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        const nodeGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');

        Object.values(this.nodes).forEach(node => {
            const parent = this.nodes[node.parent];
            if (parent) {
                const linkId = `${parent.id}-${node.id}`;
                const isBest = bestPathLinkIds.has(linkId);
                const isActive = this.activePathIds.has(node.id) && this.activePathIds.has(parent.id);
                
                const link = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                link.setAttribute('class', `link ${isBest ? 'on-best-path' : ''} ${isActive ? 'on-active-path' : ''}`);
                link.setAttribute('x1', parent.x);
                link.setAttribute('y1', parent.y);
                link.setAttribute('x2', node.x);
                link.setAttribute('y2', node.y);
                linkGroup.appendChild(link);
            }

            const isBest = bestPathNodeIds.has(node.id);
            const isActive = this.activePathIds.has(node.id);
            const isHighBranching = (childCounts[node.id] || 0) > 3;
            const isRoot = node.id === 0;

            const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            const classList = ['node', `status-${node.status}`];
            if (isBest) classList.push('on-best-path');
            if (isActive) classList.push('on-active-path');
            if (isHighBranching) classList.push('high-branching');
            if (isRoot) classList.push('is-root');
            
            g.setAttribute('class', classList.join(' '));
            g.setAttribute('transform', `translate(${node.x}, ${node.y})`);
            
            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('r', 5);

            // Apply score color only to non-special nodes
            if (!isRoot && !isHighBranching && node.status !== 'solution') {
                const color = this._getScoreColor(node.score);
                circle.setAttribute('style', `fill: ${color}`);
            }
            
            g.appendChild(circle);
            nodeGroup.appendChild(g);
        });

        fragment.appendChild(linkGroup);
        fragment.appendChild(nodeGroup);
        this.svg.appendChild(fragment);
    }

    clear() {
        this.nodes = {};
        this.solutionNodeId = null;
        this.activePathIds = new Set();
        this.minScore = Infinity;
        this.maxScore = -Infinity;
        if (this.svg) this.svg.innerHTML = '';
    }
}