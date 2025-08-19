class TreeVisualizer {
    constructor(svgId) {
        this.svg = document.getElementById(svgId);
        this.nodes = {};
        this.links = {};
        this.lastRenderTime = 0;
        this.renderInterval = 250; // Render at most ~4 times per second
        this.solutionNodeId = null;
    }

    addOrUpdateNodes(nodeList) {
        let hasChanges = false;
        nodeList.forEach(nodeData => {
            if (!this.nodes[nodeData.id] || this.nodes[nodeData.id].status !== nodeData.status) {
                this.nodes[nodeData.id] = { ...this.nodes[nodeData.id], ...nodeData };
                if (nodeData.status === 'solution') {
                    this.solutionNodeId = nodeData.id;
                }
                hasChanges = true;
            }
        });

        if (hasChanges) {
            this.scheduleRender();
        }
    }

    scheduleRender() {
        const now = Date.now();
        if (now - this.lastRenderTime > this.renderInterval) {
            this.render();
            this.lastRenderTime = now;
        }
    }

render() {
        if (!this.svg) return;
        this.svg.innerHTML = ''; // Clear previous render

        const { width } = this.svg.parentElement.getBoundingClientRect();
        if (width === 0) return;

        // --- Layout Calculation ---
        const depths = {};
        const calculateDepth = (nodeId) => {
            if (depths[nodeId] !== undefined) return depths[nodeId];
            const node = this.nodes[nodeId];
            if (node.parent === null || node.parent === undefined) {
                depths[nodeId] = 0;
                return 0;
            }
            if (!this.nodes[node.parent]) {
                depths[nodeId] = 0;
                return 0;
            }
            depths[nodeId] = calculateDepth(node.parent) + 1;
            return depths[nodeId];
        };
        Object.keys(this.nodes).forEach(calculateDepth);

        const layers = {};
        let maxLayerSize = 0;
        Object.values(this.nodes).forEach(node => {
            const depth = depths[node.id];
            if (!layers[depth]) layers[depth] = [];
            layers[depth].push(node);
            if (layers[depth].length > maxLayerSize) {
                maxLayerSize = layers[depth].length;
            }
        });

        // --- NEW: Dynamic Height Calculation ---
        const yStep = 18; // Fixed vertical space for each node
        const requiredHeight = Math.max(200, (maxLayerSize + 1) * yStep);
        this.svg.setAttribute('height', requiredHeight);
        this.svg.setAttribute('viewBox', `0 0 ${width} ${requiredHeight}`);
        // --- END NEW ---

        const maxDepth = Math.max(0, ...Object.keys(layers).map(Number));
        const xStep = (width - 60) / Math.max(1, maxDepth);

        Object.values(this.nodes).forEach(node => {
            const depth = depths[node.id];
            node.x = 30 + depth * xStep;
            
            const layer = layers[depth];
            const layerIndex = layer.findIndex(n => n.id === node.id);
            // Use the new fixed yStep instead of dividing container height
            node.y = (layerIndex + 1) * yStep;
        });

        // --- Build Solution Path ---
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

        // --- SVG Rendering (remains the same) ---
        const fragment = document.createDocumentFragment();
        const linkGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        const nodeGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');

        Object.values(this.nodes).forEach(node => {
            const parent = this.nodes[node.parent];
            if (parent) {
                const linkId = `${parent.id}-${node.id}`;
                const link = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                link.setAttribute('class', `link ${bestPathLinkIds.has(linkId) ? 'on-best-path' : ''}`);
                link.setAttribute('x1', parent.x);
                link.setAttribute('y1', parent.y);
                link.setAttribute('x2', node.x);
                link.setAttribute('y2', node.y);
                linkGroup.appendChild(link);
            }

            const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            g.setAttribute('class', `node status-${node.status} ${bestPathNodeIds.has(node.id) ? 'on-best-path' : ''}`);
            g.setAttribute('transform', `translate(${node.x}, ${node.y})`);
            
            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('r', 5);
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
        if (this.svg) this.svg.innerHTML = '';
    }
}