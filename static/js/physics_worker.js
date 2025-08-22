/**
 * @file physics_worker.js
 * This script runs in a separate thread to handle the physics simulation
 * for the tree visualizer, preventing the main UI thread from freezing.
 */

// --- Worker State ---
let nodes = {};
let nodeList = [];
let isNodeListDirty = true;
let simulationLoopId = null;
const simulationInterval = 0; // Target ~250 ticks per second, matching the main thread
let lastChildCounts = {};

/**
 * Caching accessor for the list of nodes to avoid re-calculating it on every tick.
 * @returns {Array} The flattened list of node objects.
 */
function getNodeList() {
    if (isNodeListDirty) {
        nodeList = Object.values(nodes);
        isNodeListDirty = false;
    }
    return nodeList;
}

/**
 * The core force-directed layout simulation logic.
 * This function is moved directly from TreeVisualizer._runLayoutSimulation.
 * @param {Object} childCounts - An object mapping node IDs to their number of direct children.
 */
function runLayoutSimulation(childCounts) {
    const localNodes = getNodeList();
    if (localNodes.length < 2) return;

    // --- Simulation Constants ---
    const kRepel = 2000;
    const kAttract = 2;
    const kAlign = 1;
    const damping = 0.1;
    const directionalForce = 20;

    // 1. Repulsion Force Calculation (node-to-node)
    // We start at i=1 to skip the root node, which is fixed.
    for (let i = 1; i < localNodes.length; i++) {
        const n1 = localNodes[i];
        for (let j = i + 1; j < localNodes.length; j++) {
            const n2 = localNodes[j];

            const dx = n1.x - n2.x;
            const dy = n1.y - n2.y;
            let distance = Math.sqrt(dx * dx + dy * dy);
            if (distance < 1) distance = 1; // Prevent division by zero

            const force = kRepel / (Math.pow(distance, 2.8));
            const forceX = dx * force;
            const forceY = dy * force;
            
            n1.fx += forceX;
            n1.fy += forceY;
            n2.fx -= forceX;
            n2.fy -= forceY;
        }
    }

    // 2. Attraction, Alignment, and Directional Forces (parent-child relationships)
    const desired_distance = 20;
    for (const node of localNodes) {
        const parent = nodes[node.parent];
        if (parent) {
            // Attraction force (pulls node towards its parent)
            const dx_attract = (parent.x - node.x) * kAttract;
            const dy_attract = (parent.y - node.y) * kAttract;
            if (parent === nodes[0]) {
                // If the parent is the root, we apply a stronger attraction force
                node.fx += dx_attract * 2;
                node.fy += dy_attract * 2;
            } else {
                node.fx += dx_attract;
                node.fy += dy_attract;
                parent.fx -= dx_attract;
                parent.fy -= dy_attract;
            }

            // Alignment force (encourages nodes to flow outwards from their parent)
            const grandparent = nodes[parent.parent];
            if (grandparent) {
                const flow_dx = parent.x - grandparent.x;
                const flow_dy = parent.y - grandparent.y;
                const flow_magnitude = Math.sqrt(flow_dx * flow_dx + flow_dy * flow_dy);

                if (flow_magnitude > 0.1) {
                    const norm_flow_dx = (flow_dx / flow_magnitude) * desired_distance;
                    const norm_flow_dy = (flow_dy / flow_magnitude) * desired_distance;
                    const target_x = parent.x + norm_flow_dx;
                    const target_y = parent.y + norm_flow_dy;
                    const nodeChildCount = childCounts[node.id] || 0;
                    const alignmentStrength = 1 + Math.log1p(nodeChildCount); // Stronger alignment for high-branching nodes
                    node.fx += (target_x - node.x) * kAlign * alignmentStrength;
                    node.fy += (target_y - node.y) * kAlign * alignmentStrength;
                }
            }
        }
        
        // General directional force (pushes all nodes away from the center)
        const dist_from_center = Math.sqrt(node.x * node.x + node.y * node.y);
        if (dist_from_center > 1) {
            node.fx += (node.x / dist_from_center) * directionalForce;
            node.fy += (node.y / dist_from_center) * directionalForce;
        }
    }
    
    // 3. Update Velocities & Positions
    // Apply calculated forces to update node velocities and positions, then reset forces for the next tick.
    for (let i = 1; i < localNodes.length; i++) {
        const node = localNodes[i];

        node.vx = (node.vx || 0) * damping + node.fx * 0.02;
        node.vy = (node.vy || 0) * damping + node.fy * 0.02;
        node.x += node.vx;
        node.y += node.vy;

        node.fx = 0;
        node.fy = 0;
    }
}


/**
 * Main message handler for the worker. Responds to commands from the main thread.
 */
self.onmessage = function(e) {
    const { type, payload } = e.data;

    switch (type) {
        case 'start':
            if (simulationLoopId) clearInterval(simulationLoopId);
            simulationLoopId = setInterval(() => {
                runLayoutSimulation(lastChildCounts);
                // After each tick, send the updated node data back to the main thread for rendering
                postMessage({ type: 'tick', payload: { nodes: nodes } });
            }, simulationInterval);
            break;

        case 'update':
            const { newOrUpdatedNodes, childCounts } = payload;
            if (newOrUpdatedNodes.length > 0) {
                 newOrUpdatedNodes.forEach(nodeData => {
                    // Merge new data, preserving existing physics properties (vx, vy, etc.) if the node already exists
                    nodes[nodeData.id] = {
                        ...nodes[nodeData.id],
                        ...nodeData
                    };
                });
                isNodeListDirty = true; // Mark list as dirty since nodes were added/updated
            }
            lastChildCounts = childCounts; // Always update to the latest child counts from the main thread
            break;

        case 'clear':
            nodes = {};
            nodeList = [];
            lastChildCounts = {};
            isNodeListDirty = true;
            if (simulationLoopId) clearInterval(simulationLoopId);
            simulationLoopId = null;
            break;

        case 'stop':
            if (simulationLoopId) clearInterval(simulationLoopId);
            simulationLoopId = null;
            break;
    }
};