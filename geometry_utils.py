def find_all_empty_rectangles(sheet_info: dict, placed_parts: list) -> list:
    """
    Finds all empty space on the sheet by creating a grid from all part edges
    and identifying the empty cells.
    """
    TOLERANCE = 1e-6
    # Collect all unique x and y coordinates from the sheet and part boundaries
    all_x = {0, sheet_info['width']}
    all_y = {0, sheet_info['length']}

    for part in placed_parts:
        p_len = part['width'] if part.get('rotated') else part['length']
        p_wid = part['length'] if part.get('rotated') else part['width']
        all_x.add(part['x'])
        all_x.add(part['x'] + p_wid)
        all_y.add(part['y'])
        all_y.add(part['y'] + p_len)
    
    sorted_x = sorted([x for x in all_x if x <= sheet_info['width'] + TOLERANCE])
    sorted_y = sorted([y for y in all_y if y <= sheet_info['length'] + TOLERANCE])

    empty_cells = []
    # Iterate through the grid cells to find empty ones
    for i in range(len(sorted_x) - 1):
        for j in range(len(sorted_y) - 1):
            x, y = sorted_x[i], sorted_y[j]
            w, l = sorted_x[i+1] - x, sorted_y[j+1] - y

            if w < TOLERANCE or l < TOLERANCE:
                continue

            # Check if the center of the cell is occupied by a placed part
            cell_center_x = x + w / 2
            cell_center_y = y + l / 2
            is_occupied = False
            for part in placed_parts:
                p_len = part['width'] if part.get('rotated') else part['length']
                p_wid = part['length'] if part.get('rotated') else part['width']
                if (part['x'] - TOLERANCE <= cell_center_x < part['x'] + p_wid + TOLERANCE and
                    part['y'] - TOLERANCE <= cell_center_y < part['y'] + p_len + TOLERANCE):
                    is_occupied = True
                    break
            
            if not is_occupied:
                empty_cells.append({'x': x, 'y': y, 'width': w, 'length': l})

    return empty_cells

def consolidate_empty_space(rects: list, kerf: float) -> list:
    """
    Consolidates a list of rectangles by merging perfectly aligned adjacent ones.
    This process is repeated until no more merges can be made.
    """
    if len(rects) < 2:
        return rects

    TOLERANCE = 1e-6
    
    while True:
        merged_in_pass = False
        i = 0
        while i < len(rects):
            j = i + 1
            while j < len(rects):
                r1, r2 = rects[i], rects[j]
                
                merged_rect = None

                # Check for vertical merge (must have same width and x-coordinate)
                if abs(r1['x'] - r2['x']) < TOLERANCE and abs(r1['width'] - r2['width']) < TOLERANCE:
                    if abs(r1['y'] + r1['length'] - r2['y']) < TOLERANCE: # r2 is above r1
                        merged_rect = {'x': r1['x'], 'y': r1['y'], 'length': r1['length'] + r2['length'], 'width': r1['width']}
                    elif abs(r2['y'] + r2['length'] - r1['y']) < TOLERANCE: # r1 is above r2
                        merged_rect = {'x': r2['x'], 'y': r2['y'], 'length': r1['length'] + r2['length'], 'width': r1['width']}

                # Check for horizontal merge (must have same length and y-coordinate)
                if not merged_rect and abs(r1['y'] - r2['y']) < TOLERANCE and abs(r1['length'] - r2['length']) < TOLERANCE:
                    if abs(r1['x'] + r1['width'] - r2['x']) < TOLERANCE: # r2 is to the right of r1
                        merged_rect = {'x': r1['x'], 'y': r1['y'], 'length': r1['length'], 'width': r1['width'] + r2['width']}
                    elif abs(r2['x'] + r2['width'] - r1['x']) < TOLERANCE: # r1 is to the right of r2
                        merged_rect = {'x': r2['x'], 'y': r2['y'], 'length': r1['length'], 'width': r1['width'] + r2['width']}
                
                if merged_rect:
                    rects.pop(j)
                    rects[i] = merged_rect
                    merged_in_pass = True
                    j = i + 1 
                else:
                    j += 1
            i += 1

        if not merged_in_pass:
            break
            
    return rects

def find_guillotine_cuts(sheet_info: dict, placed_parts: list) -> list:
    """
    Finds the precise segments of each guillotine cut by recursively subdividing the sheet,
    ensuring each cut is valid and including final trim cuts around single parts.
    This version prioritizes cuts that produce larger, more square-shaped offcuts.
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
        BONUS_WEIGHT = 10.0
        rect_x, rect_y, rect_w, rect_l = current_rect['x'], current_rect['y'], current_rect['width'], current_rect['length']

        for cut_x in sorted(list(x_coords)):
            if all(not (b[0] < cut_x - TOLERANCE and b[0] + b[2] > cut_x + TOLERANCE) for b in part_bounds):
                w1 = cut_x - rect_x
                w2 = rect_w - w1
                base_score = min(min(w1, rect_l), min(w2, rect_l))
                bonus = sum(1 for b in part_bounds if abs((b[0] + b[2]) - cut_x) < TOLERANCE or abs(b[0] - cut_x) < TOLERANCE)
                score = base_score + (bonus * BONUS_WEIGHT)
                if score > best_score:
                    best_score = score
                    best_cut = ('v', cut_x)
        
        for cut_y in sorted(list(y_coords)):
            if all(not (b[1] < cut_y - TOLERANCE and b[1] + b[3] > cut_y + TOLERANCE) for b in part_bounds):
                l1 = cut_y - rect_y
                l2 = rect_l - l1
                base_score = min(min(rect_w, l1), min(rect_w, l2))
                bonus = sum(1 for b in part_bounds if abs((b[1] + b[3]) - cut_y) < TOLERANCE or abs(b[1] - cut_y) < TOLERANCE)
                score = base_score + (bonus * BONUS_WEIGHT)
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