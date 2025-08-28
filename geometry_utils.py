def find_all_empty_rectangles(sheet_info: dict, placed_parts: list) -> list:
    """
    Finds a set of non-overlapping rectangles that cover all empty space by
    iteratively subtracting placed parts from the initial sheet area.
    """
    TOLERANCE = 1e-6
    empty_rects = [{
        'x': 0, 'y': 0,
        'width': sheet_info['width'], 'length': sheet_info['length']
    }]

    for part in placed_parts:
        p_len = part['width'] if part.get('rotated') else part['length']
        p_wid = part['length'] if part.get('rotated') else part['width']
        
        next_empty_rects = []
        for rect in empty_rects:
            # Check for intersection between part and rect
            if (part['x'] >= rect['x'] + rect['width'] - TOLERANCE or
                part['x'] + p_wid <= rect['x'] + TOLERANCE or
                part['y'] >= rect['y'] + rect['length'] - TOLERANCE or
                part['y'] + p_len <= rect['y'] + TOLERANCE):
                next_empty_rects.append(rect) # No intersection, keep the rect
                continue
            
            # Top rect (above the part's area within the current rect)
            if rect['y'] < part['y'] - TOLERANCE:
                next_empty_rects.append({'x': rect['x'], 'y': rect['y'], 'width': rect['width'], 'length': part['y'] - rect['y']})
            # Bottom rect (below the part's area within the current rect)
            if rect['y'] + rect['length'] > part['y'] + p_len + TOLERANCE:
                next_empty_rects.append({'x': rect['x'], 'y': part['y'] + p_len, 'width': rect['width'], 'length': (rect['y'] + rect['length']) - (part['y'] + p_len)})
            # Left rect (left of the part's area within the current rect)
            if rect['x'] < part['x'] - TOLERANCE:
                next_empty_rects.append({'x': rect['x'], 'y': part['y'], 'width': part['x'] - rect['x'], 'length': p_len})
            # Right rect (right of the part's area within the current rect)
            if rect['x'] + rect['width'] > part['x'] + p_wid + TOLERANCE:
                next_empty_rects.append({'x': part['x'] + p_wid, 'y': part['y'], 'width': (rect['x'] + rect['width']) - (part['x'] + p_wid), 'length': p_len})

        empty_rects = next_empty_rects
    
    return empty_rects

def consolidate_empty_space(rects: list, config: dict) -> list:
    """
    Iteratively finds the largest and most square-like rectangular offcuts 
    from a set of tiled empty spaces by finding the maximal empty rectangle 
    in the remaining area in each step.
    """
    TOLERANCE = 1e-6
    min_offcut_dim = config.get('min_offcut_dim', 0)

    if not rects:
        return []

    current_empty_rects = [r for r in rects if r['width'] > TOLERANCE and r['length'] > TOLERANCE]
    final_offcuts = []

    while True:
        if not current_empty_rects:
            break

        # 1. Define a grid from all rectangle vertices
        all_x = set()
        all_y = set()
        for r in current_empty_rects:
            all_x.add(r['x'])
            all_x.add(r['x'] + r['width'])
            all_y.add(r['y'])
            all_y.add(r['y'] + r['length'])
        
        sorted_x = sorted(list(all_x))
        sorted_y = sorted(list(all_y))

        if len(sorted_x) <= 1 or len(sorted_y) <= 1:
            break

        x_map = {val: i for i, val in enumerate(sorted_x)}
        y_map = {val: i for i, val in enumerate(sorted_y)}
        
        # 2. Create a binary grid representing the empty space
        grid_rows = len(sorted_y) - 1
        grid_cols = len(sorted_x) - 1
        grid = [[0] * grid_cols for _ in range(grid_rows)]

        for r in current_empty_rects:
            start_x_idx, end_x_idx = x_map.get(r['x']), x_map.get(r['x'] + r['width'])
            start_y_idx, end_y_idx = y_map.get(r['y']), y_map.get(r['y'] + r['length'])

            for i in range(start_y_idx, end_y_idx):
                for j in range(start_x_idx, end_x_idx):
                    grid[i][j] = 1

        # 3. Find the largest, most-square rectangle in the grid using the histogram method
        heights = [0] * (grid_cols + 1)
        best_rect_info = {'score': -1.0}

        for i in range(grid_rows):
            for j in range(grid_cols):
                heights[j] = heights[j] + 1 if grid[i][j] == 1 else 0
            
            stack = [-1]
            for j, h in enumerate(heights):
                while stack[-1] != -1 and heights[stack[-1]] >= h:
                    H = heights[stack.pop()]
                    W_start_idx = stack[-1] + 1
                    
                    y_start_idx = i - H + 1
                    
                    real_y = sorted_y[y_start_idx]
                    real_x = sorted_x[W_start_idx]
                    real_length = sorted_y[i + 1] - sorted_y[y_start_idx]
                    real_width = sorted_x[j] - sorted_x[W_start_idx]
                    
                    area = real_width * real_length
                    score = 0
                    if real_width > TOLERANCE and real_length > TOLERANCE:
                        min_dim = min(real_width, real_length)
                        max_dim = max(real_width, real_length)
                        # This new score properly balances total area with a bonus for a better
                        # aspect ratio, ensuring larger offcuts are prioritized.
                        score = area * (min_dim / max_dim)**0.5

                    if score > best_rect_info['score']:
                        best_rect_info = {
                            'score': score, 'x': real_x, 'y': real_y, 
                            'width': real_width, 'length': real_length
                        }
                stack.append(j)
        
        if best_rect_info['score'] < 0:
            break

        best_offcut = best_rect_info
        if min(best_offcut['width'], best_offcut['length']) < min_offcut_dim:
            break

        final_offcuts.append({
            'x': best_offcut['x'], 'y': best_offcut['y'],
            'width': best_offcut['width'], 'length': best_offcut['length']
        })

        # 4. Subtract the found offcut from the current empty space
        next_empty_rects = []
        offcut_x2 = best_offcut['x'] + best_offcut['width']
        offcut_y2 = best_offcut['y'] + best_offcut['length']

        for rect in current_empty_rects:
            rect_x2 = rect['x'] + rect['width']
            rect_y2 = rect['y'] + rect['length']

            ix1 = max(rect['x'], best_offcut['x'])
            iy1 = max(rect['y'], best_offcut['y'])
            ix2 = min(rect_x2, offcut_x2)
            iy2 = min(rect_y2, offcut_y2)

            if ix1 >= ix2 - TOLERANCE or iy1 >= iy2 - TOLERANCE:
                next_empty_rects.append(rect)
                continue

            if rect['y'] < iy1 - TOLERANCE:
                next_empty_rects.append({'x': rect['x'], 'y': rect['y'], 'width': rect['width'], 'length': iy1 - rect['y']})
            if rect_y2 > iy2 + TOLERANCE:
                next_empty_rects.append({'x': rect['x'], 'y': iy2, 'width': rect['width'], 'length': rect_y2 - iy2})
            if rect['x'] < ix1 - TOLERANCE:
                next_empty_rects.append({'x': rect['x'], 'y': iy1, 'width': ix1 - rect['x'], 'length': iy2 - iy1})
            if rect_x2 > ix2 + TOLERANCE:
                next_empty_rects.append({'x': ix2, 'y': iy1, 'width': rect_x2 - ix2, 'length': iy2 - iy1})

        current_empty_rects = [r for r in next_empty_rects if r['width'] > TOLERANCE and r['length'] > TOLERANCE]

    return final_offcuts

def find_guillotine_cuts(sheet_info: dict, placed_parts: list) -> list:
    """
    Finds the precise segments of each guillotine cut by recursively subdividing 
    the sheet. This version prioritizes cuts that align with part edges to 
    minimize the total number of cuts.
    """
    if not placed_parts:
        return []
    
    all_cuts = []
    TOLERANCE = 1e-6

    def _get_part_bounds(part):
        p_len = part['width'] if part.get('rotated') else part['length']
        p_wid = part['length'] if part.get('rotated') else part['width']
        return part['x'], part['y'], p_wid, p_len

    def _find_cut_segments_recursive(current_rect, parts_in_rect):
        if not parts_in_rect:
            return

        part_bounds = [_get_part_bounds(p) for p in parts_in_rect]
        
        x_coords, y_coords = set(), set()
        for x, y, w, l in part_bounds:
            if x > current_rect['x'] + TOLERANCE: x_coords.add(x)
            if x + w < current_rect['x'] + current_rect['width'] - TOLERANCE: x_coords.add(x + w)
            if y > current_rect['y'] + TOLERANCE: y_coords.add(y)
            if y + l < current_rect['y'] + current_rect['length'] - TOLERANCE: y_coords.add(y + l)
        
        best_cut = None
        best_score = -1.0

        for cut_x in sorted(list(x_coords)):
            if all(not (b[0] < cut_x - TOLERANCE and b[0] + b[2] > cut_x + TOLERANCE) for b in part_bounds):
                score = sum(1 for b in part_bounds if abs((b[0] + b[2]) - cut_x) < TOLERANCE or abs(b[0] - cut_x) < TOLERANCE)
                if score > best_score:
                    best_score = score
                    best_cut = ('v', cut_x)
        
        for cut_y in sorted(list(y_coords)):
            if all(not (b[1] < cut_y - TOLERANCE and b[1] + b[3] > cut_y + TOLERANCE) for b in part_bounds):
                score = sum(1 for b in part_bounds if abs((b[1] + b[3]) - cut_y) < TOLERANCE or abs(b[1] - cut_y) < TOLERANCE)
                if score > best_score:
                    best_score = score
                    best_cut = ('h', cut_y)

        if not best_cut: return

        cut_type, cut_coord = best_cut
        if cut_type == 'v':
            all_cuts.append({'x1': cut_coord, 'y1': current_rect['y'], 'x2': cut_coord, 'y2': current_rect['y'] + current_rect['length']})
            left_rect = {**current_rect, 'width': cut_coord - current_rect['x']}
            right_rect = {**current_rect, 'x': cut_coord, 'width': current_rect['x'] + current_rect['width'] - cut_coord}
            left_parts = [p for p, b in zip(parts_in_rect, part_bounds) if b[0] + b[2] <= cut_coord + TOLERANCE]
            right_parts = [p for p, b in zip(parts_in_rect, part_bounds) if b[0] >= cut_coord - TOLERANCE]
            _find_cut_segments_recursive(left_rect, left_parts)
            _find_cut_segments_recursive(right_rect, right_parts)
        else: # 'h'
            all_cuts.append({'x1': current_rect['x'], 'y1': cut_coord, 'x2': current_rect['x'] + current_rect['width'], 'y2': cut_coord})
            bottom_rect = {**current_rect, 'length': cut_coord - current_rect['y']}
            top_rect = {**current_rect, 'y': cut_coord, 'length': current_rect['y'] + current_rect['length'] - cut_coord}
            bottom_parts = [p for p, b in zip(parts_in_rect, part_bounds) if b[1] + b[3] <= cut_coord + TOLERANCE]
            top_parts = [p for p, b in zip(parts_in_rect, part_bounds) if b[1] >= cut_coord - TOLERANCE]
            _find_cut_segments_recursive(bottom_rect, bottom_parts)
            _find_cut_segments_recursive(top_rect, top_parts)

    initial_rect = {'x': 0, 'y': 0, 'length': sheet_info['length'], 'width': sheet_info['width']}
    _find_cut_segments_recursive(initial_rect, placed_parts)
    
    return all_cuts