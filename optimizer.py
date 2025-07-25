import uuid

def find_best_fit(part_l, part_w, empty_rects):
    """Finds the best empty rectangle to place a part in using a shortest-side-fit heuristic."""
    best_fit_index = -1
    min_short_side_fit = float('inf')

    for i, rect in enumerate(empty_rects):
        if part_l <= rect['length'] and part_w <= rect['width']:
            remaining_length = rect['length'] - part_l
            remaining_width = rect['width'] - part_w
            
            short_side_fit = min(remaining_length, remaining_width)

            if short_side_fit < min_short_side_fit:
                min_short_side_fit = short_side_fit
                best_fit_index = i
    
    return best_fit_index

def calculate_layout_score(layout, sheet_info, weights, min_offcut_dim):
    """
    Calculates a score by penalizing waste and offcut area. The goal is to get a score
    as close to zero as possible.
    """
    sheet_area = sheet_info['length'] * sheet_info['width']
    if sheet_area == 0:
        return -float('inf')

    # 1. Calculate the area of parts, offcuts, and waste.
    used_area = layout['total_placed_area']
    
    usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
    offcut_area = sum((o['length'] * o['width']) for o in usable_offcuts)
    
    waste_area = sheet_area - used_area - offcut_area

    # 2. Calculate the penalty score for leftover material.
    offcut_penalty = (offcut_area ** 0.6) * weights.get('w_offcut', 0.5)
    waste_penalty = (waste_area ** 0.6) * weights.get('w_waste', 1.0)
    
    area_based_score = -(offcut_penalty + waste_penalty)
    
    # 3. Adjust score based on other factors (inventory and cuts)
    quantity_bonus = 0
    if sheet_info.get('type') != 'Default':
        quantity_bonus = sheet_info.get('quantity', 1) * weights.get('w_quantity', 0.1)

    cuts_penalty = len(layout['placed_parts']) * weights.get('w_cuts', 0.2)

    # 4. Combine all scores
    final_score = area_based_score + quantity_bonus - cuts_penalty
    
    return final_score

def run_single_sheet_optimization(sheet_info, parts, kerf=0.0, allow_rotation=True):
    """
    Places parts onto a SINGLE sheet and returns the layout, including the total area of placed parts.
    """
    parts_to_place = []
    for part in parts:
        for _ in range(int(part.get('quantity', 1))):
            parts_to_place.append({**part, 'rotated': False})

    placed_parts = []
    empty_rects = [{'x': 0, 'y': 0, 'length': sheet_info['length'], 'width': sheet_info['width']}]

    for part in parts_to_place:
        orientations = [(part['length'], part['width'], False)]
        if allow_rotation and part['length'] != part['width']:
            orientations.append((part['width'], part['length'], True))

        best_fit = {'rect_index': -1, 'orientation': None}

        for part_l, part_w, rotated in orientations:
            rect_index = find_best_fit(part_l, part_w, empty_rects)
            if rect_index != -1:
                best_fit['rect_index'] = rect_index
                best_fit['orientation'] = (part_l, part_w, rotated)
                break 

        if best_fit['rect_index'] != -1:
            part_l, part_w, rotated = best_fit['orientation']
            rect = empty_rects.pop(best_fit['rect_index'])
            
            part['rotated'] = rotated
            part['x'], part['y'] = rect['x'], rect['y']
            placed_parts.append(part)

            top_rect = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': rect['width']}
            right_rect = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': part_l, 'width': rect['width'] - part_w - kerf}
            
            if top_rect['length'] > 0 and top_rect['width'] > 0:
                empty_rects.append(top_rect)
            if right_rect['length'] > 0 and right_rect['width'] > 0:
                empty_rects.append(right_rect)

    total_placed_area = sum(p['length'] * p['width'] for p in placed_parts)

    return {
        "placed_parts": placed_parts,
        "offcuts": empty_rects,
        "sheet_info": sheet_info,
        "total_placed_area": total_placed_area
    }

def optimize_multiple_sheets(parts_to_cut, stock_sheets, config):
    """
    Main orchestrator that finds the best combination of sheets by minimizing
    the "cost-per-part" in each pass.
    """
    remaining_parts_list = []
    for part in parts_to_cut:
        for _ in range(int(part.get('quantity', 1))):
            p_copy = part.copy()
            p_copy['part_id'] = uuid.uuid4()
            remaining_parts_list.append(p_copy)

    available_sheets = [s.copy() for s in stock_sheets]
    final_layouts = []
    
    while remaining_parts_list:
        best_layout_for_pass = None
        min_cost_per_part = float('inf')
        best_sheet_idx = -1

        for i, sheet in enumerate(available_sheets):
            if sheet.get('quantity', 0) <= 0:
                continue

            layout_attempt = run_single_sheet_optimization(
                sheet, remaining_parts_list, config['kerf'], config['allow_rotation']
            )
            
            num_placed = len(layout_attempt['placed_parts'])
            if num_placed == 0:
                continue
            
            # Calculate the score for this single layout (it will be negative or zero)
            layout_score = calculate_layout_score(
                layout_attempt, sheet, config, config['min_offcut_dim']
            )
            
            # Calculate the total "cost" of this pass, including the sheet penalty.
            # We invert the score to think of it as a positive cost.
            total_pass_cost = -layout_score + config.get('w_sheets', 1.0)
            
            # Find the cost per part to determine the most "productive" layout.
            cost_per_part = total_pass_cost / num_placed

            if cost_per_part < min_cost_per_part:
                min_cost_per_part = cost_per_part
                best_layout_for_pass = layout_attempt
                best_sheet_idx = i
        
        if best_sheet_idx == -1:
            print(f"Warning: Could not place all parts. {len(remaining_parts_list)} parts remaining.")
            break

        final_layouts.append(best_layout_for_pass)
        available_sheets[best_sheet_idx]['quantity'] -= 1
        
        placed_part_ids = {p['part_id'] for p in best_layout_for_pass['placed_parts']}
        remaining_parts_list = [p for p in remaining_parts_list if p['part_id'] not in placed_part_ids]

    # Calculate the final, overall score for display
    total_score = sum(calculate_layout_score(layout, layout['sheet_info'], config, config['min_offcut_dim']) for layout in final_layouts)
    final_score = total_score - (config.get('w_sheets', 1.0) * len(final_layouts))

    return final_layouts, remaining_parts_list, final_score