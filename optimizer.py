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

def run_optimization(sheet_length, sheet_width, parts, kerf=0.0, allow_rotation=True):
    """
    Places parts using a best-fit algorithm that tracks empty spaces.
    Returns placed parts, remaining parts, and all leftover empty rectangles.
    """
    parts_to_place = []
    for part in parts:
        for _ in range(int(part['quantity'])):
            parts_to_place.append({**part, 'rotated': False})

    parts_to_place.sort(key=lambda p: p['length'] * p['width'], reverse=True)

    placed_parts = []
    empty_rects = [{'x': 0, 'y': 0, 'length': sheet_length, 'width': sheet_width}]

    for part in parts_to_place:
        placed = False
        
        # Determine possible orientations
        orientations = [(part['length'], part['width'], False)]
        if allow_rotation and part['length'] != part['width']:
            orientations.append((part['width'], part['length'], True))

        for part_l, part_w, rotated in orientations:
            best_rect_index = find_best_fit(part_l, part_w, empty_rects)

            if best_rect_index != -1:
                # A spot was found, place the part
                rect = empty_rects.pop(best_rect_index)
                
                # Add the part to the placed list
                part['rotated'] = rotated
                placed_parts.append({**part, 'x': rect['x'], 'y': rect['y']})

                # --- Split the remaining space into two new empty rectangles ---
                # New rectangle above the placed part
                top_rect = {
                    'x': rect['x'],
                    'y': rect['y'] + part_l + kerf,
                    'length': rect['length'] - part_l - kerf,
                    'width': rect['width']
                }
                # New rectangle to the right of the placed part
                right_rect = {
                    'x': rect['x'] + part_w + kerf,
                    'y': rect['y'],
                    'length': part_l, # Only as long as the part itself
                    'width': rect['width'] - part_w - kerf
                }

                # Add the new rectangles to the list if they are valid
                if top_rect['length'] > 0 and top_rect['width'] > 0:
                    empty_rects.append(top_rect)
                if right_rect['length'] > 0 and right_rect['width'] > 0:
                    empty_rects.append(right_rect)

                placed = True
                break # Stop trying orientations for this part
        
        if not placed:
            # This part could not be placed in any orientation
            # For simplicity, we'll just drop it. A more complex algorithm could try again later.
            pass

    remaining_parts_list = [p for p in parts_to_place if p not in placed_parts]
    return placed_parts, remaining_parts_list, empty_rects