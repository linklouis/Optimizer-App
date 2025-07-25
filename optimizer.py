def find_best_fit(part_l, part_w, empty_rects):
    """Finds the best empty rectangle to place a part in."""
    best_fit_index = -1
    min_waste = float('inf')

    for i, rect in enumerate(empty_rects):
        # Check if the part fits
        if part_l <= rect['length'] and part_w <= rect['width']:
            waste = (rect['length'] * rect['width']) - (part_l * part_w)
            if waste < min_waste:
                min_waste = waste
                best_fit_index = i
    
    return best_fit_index

def calculate_layout_score(layout, sheet_length, sheet_width, weights):
    """Calculates a weighted score for a given layout."""
    placed_parts = layout['placed_parts']
    offcuts = layout['offcuts']

    # 1. Calculate Yield Score
    total_parts_area = sum([(p['length'] * p['width']) for p in placed_parts])
    sheet_area = sheet_length * sheet_width
    yield_score = (total_parts_area / sheet_area) * 100 if sheet_area > 0 else 0

    # 2. Calculate Offcut Quality Score
    offcut_score = 0
    for offcut in offcuts:
        # This rewards squarer offcuts over long, thin ones [cite: 37]
        aspect_ratio = max(offcut['length'], offcut['width']) / min(offcut['length'], offcut['width']) if min(offcut['length'], offcut['width']) > 0 else float('inf')
        offcut_score += (offcut['length'] * offcut['width']) / aspect_ratio

    # 3. Calculate Number of Cuts (a simple proxy)
    # Every placed part requires at least one new cut line
    num_cuts = len(placed_parts)

    # 4. Apply Weights to get Final Score [cite: 33]
    final_score = (weights['w_yield'] * yield_score) + \
                  (weights['w_offcut'] * offcut_score) - \
                  (weights['w_cuts'] * num_cuts)
    
    return final_score

def run_optimization(sheet_length, sheet_width, parts, kerf=0.0, allow_rotation=True):
    """
    Places parts using a best-fit algorithm that tracks empty spaces.
    This function performs a single pass with a pre-sorted list of parts.
    """
    parts_to_place = []
    for part in parts:
        for _ in range(int(part['quantity'])):
            parts_to_place.append({**part, 'rotated': False})

    placed_parts = []
    empty_rects = [{'x': 0, 'y': 0, 'length': sheet_length, 'width': sheet_width}]

    for part in parts_to_place:
        placed = False
        
        orientations = [(part['length'], part['width'], False)]
        if allow_rotation and part['length'] != part['width']:
            orientations.append((part['width'], part['length'], True))

        for part_l, part_w, rotated in orientations:
            best_rect_index = find_best_fit(part_l, part_w, empty_rects)

            if best_rect_index != -1:
                rect = empty_rects.pop(best_rect_index)
                part['rotated'] = rotated
                placed_parts.append({**part, 'x': rect['x'], 'y': rect['y']})

                top_rect = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': rect['width']}
                right_rect = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': part_l, 'width': rect['width'] - part_w - kerf}

                if top_rect['length'] > 0 and top_rect['width'] > 0:
                    empty_rects.append(top_rect)
                if right_rect['length'] > 0 and right_rect['width'] > 0:
                    empty_rects.append(right_rect)

                placed = True
                break
    
    remaining_parts_list = [p for p in parts_to_place if p not in placed_parts]
    
    # Return a dictionary of results for easy handling
    return {
        "placed_parts": placed_parts,
        "remaining_parts": remaining_parts_list,
        "offcuts": empty_rects
    }