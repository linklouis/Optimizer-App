import uuid

def find_best_fit(part_l, part_w, empty_rects):
    """Finds the best empty rectangle to place a part in using a shortest-side-fit heuristic."""
    best_fit_index = -1
    min_short_side_fit = float('inf')

    for i, rect in enumerate(empty_rects):
        if part_l <= rect['length'] and part_w <= rect['width']:
            remaining_length = rect['length'] - part_l
            remaining_width = rect['width'] - part_w
            
            # Prioritize leaving the most 'square-like' remaining areas
            short_side_fit = min(remaining_length, remaining_width)

            if short_side_fit < min_short_side_fit:
                min_short_side_fit = short_side_fit
                best_fit_index = i
    
    return best_fit_index

def calculate_layout_score(layout, sheet_info, weights):
    """Calculates a weighted score for a given layout, now including sheet quantity."""
    placed_parts = layout['placed_parts']
    offcuts = layout['offcuts']

    # 1. Calculate Yield Score
    total_parts_area = sum((p['length'] * p['width']) for p in placed_parts)
    sheet_area = sheet_info['length'] * sheet_info['width']
    yield_score = (total_parts_area / sheet_area) * 100 if sheet_area > 0 else 0

    # 2. Calculate Offcut Quality Score
    offcut_score = 0
    for offcut in offcuts:
        if min(offcut['length'], offcut['width']) > 0:
            aspect_ratio = max(offcut['length'], offcut['width']) / min(offcut['length'], offcut['width'])
            offcut_score += (offcut['length'] * offcut['width']) / aspect_ratio

    # 3. Number of Cuts (proxy)
    num_cuts = len(placed_parts)

    # 4. Sheet Quantity Score
    # The score increases for sheets with higher inventory
    quantity_score = sheet_info.get('quantity', 1)

    # 5. Apply Weights
    final_score = (
        (weights.get('w_yield', 1.0) * yield_score) +
        (weights.get('w_offcut', 0.5) * offcut_score) +
        (weights.get('w_quantity', 0.1) * quantity_score) -
        (weights.get('w_cuts', 0.2) * num_cuts)
    )
    
    return final_score

def run_single_sheet_optimization(sheet_info, parts, kerf=0.0, allow_rotation=True):
    """
    Places parts onto a SINGLE sheet. This is the core guillotine-style placement logic.
    """
    parts_to_place = []
    for part in parts:
        for _ in range(int(part.get('quantity', 1))):
            parts_to_place.append({**part, 'rotated': False})

    placed_parts = []
    # Start with one rectangle representing the whole sheet
    empty_rects = [{'x': 0, 'y': 0, 'length': sheet_info['length'], 'width': sheet_info['width']}]

    for part in parts_to_place:
        placed_this_part = False
        
        # Consider both original and rotated orientations
        orientations = [(part['length'], part['width'], False)]
        if allow_rotation and part['length'] != part['width']:
            orientations.append((part['width'], part['length'], True))

        best_fit = {'rect_index': -1, 'orientation': None, 'min_waste': float('inf')}

        for part_l, part_w, rotated in orientations:
            rect_index = find_best_fit(part_l, part_w, empty_rects)
            if rect_index != -1:
                # Simple heuristic: prefer the first fit found for now.
                # More complex logic could evaluate which orientation is 'better'
                best_fit['rect_index'] = rect_index
                best_fit['orientation'] = (part_l, part_w, rotated)
                break 

        if best_fit['rect_index'] != -1:
            part_l, part_w, rotated = best_fit['orientation']
            rect = empty_rects.pop(best_fit['rect_index'])
            
            # Record the placed part's details
            part['rotated'] = rotated
            part['x'], part['y'] = rect['x'], rect['y']
            placed_parts.append(part)

            # Perform guillotine cuts, creating two new empty rectangles
            top_rect = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': rect['width']}
            right_rect = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': part_l, 'width': rect['width'] - part_w - kerf}
            
            # Add new rectangles to the list if they are valid
            if top_rect['length'] > 0 and top_rect['width'] > 0:
                empty_rects.append(top_rect)
            if right_rect['length'] > 0 and right_rect['width'] > 0:
                empty_rects.append(right_rect)

    # Identify which parts from the original list were successfully placed
    placed_part_ids = {id(p) for p in placed_parts}
    remaining_parts = [p for p in parts if id(p) not in placed_part_ids]

    return {
        "placed_parts": placed_parts,
        "remaining_parts": remaining_parts,
        "offcuts": empty_rects,
        "sheet_info": sheet_info
    }

def optimize_multiple_sheets(parts_to_cut, stock_sheets, config):
    """
    Main orchestrator for finding the best combination of sheets.
    Follows a greedy strategy: find the best sheet for the current set of parts,
    fill it, and repeat with the remaining parts.
    """
    # Create a full list of individual parts to be placed
    remaining_parts_list = []
    for part in parts_to_cut:
        for _ in range(int(part.get('quantity', 1))):
            # Give each part a unique ID for tracking
            p_copy = part.copy()
            p_copy['part_id'] = uuid.uuid4()
            remaining_parts_list.append(p_copy)

    available_sheets = [s.copy() for s in stock_sheets]
    final_layouts = []
    
    while remaining_parts_list:
        best_layout_for_pass = None
        best_score_for_pass = -float('inf')
        best_sheet_idx = -1

        # Iterate through available sheet types to find the best one for this pass
        for i, sheet in enumerate(available_sheets):
            if sheet.get('quantity', 0) <= 0:
                continue

            # Run a single-sheet optimization for the current remaining parts
            layout_attempt = run_single_sheet_optimization(
                sheet, remaining_parts_list, config['kerf'], config['allow_rotation']
            )

            if not layout_attempt['placed_parts']:
                continue
            
            # Score this potential layout
            current_score = calculate_layout_score(layout_attempt, sheet, config)
            
            if current_score > best_score_for_pass:
                best_score_for_pass = current_score
                best_layout_for_pass = layout_attempt
                best_sheet_idx = i

        if best_sheet_idx == -1:
            # No available sheet could fit any of the remaining parts
            print(f"Warning: Could not place all parts. {len(remaining_parts_list)} parts remaining.")
            break

        # "Commit" to the best layout found in this pass
        final_layouts.append(best_layout_for_pass)
        
        # Decrement the quantity of the sheet we just used
        available_sheets[best_sheet_idx]['quantity'] -= 1
        
        # Update the list of remaining parts
        placed_part_ids = {p['part_id'] for p in best_layout_for_pass['placed_parts']}
        remaining_parts_list = [p for p in remaining_parts_list if p['part_id'] not in placed_part_ids]

    # Add penalty for the number of sheets used
    num_sheets_used = len(final_layouts)
    total_score = sum(calculate_layout_score(layout, layout['sheet_info'], config) for layout in final_layouts)
    final_score = total_score - (config.get('w_sheets', 1.0) * num_sheets_used)

    return final_layouts, remaining_parts_list, final_score