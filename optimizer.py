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

def calculate_layout_score(layout, sheet_info, weights, min_offcut_dim, is_stock_only_job=False):
    """
    Calculates a score by penalizing waste and offcut area, and adding bonuses.
    """
    sheet_area = sheet_info['length'] * sheet_info['width']
    if sheet_area == 0:
        return -float('inf')

    # --- Area & Penalty Calculation ---
    used_area = layout['total_placed_area']
    usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
    offcut_area = sum((o['length'] * o['width']) for o in usable_offcuts)
    waste_area = sheet_area - used_area - offcut_area
    
    offcut_penalty = offcut_area * weights.get('w_offcut', 0.5)
    waste_penalty = waste_area * weights.get('w_waste', 1.0)
    area_based_score = -(offcut_penalty + waste_penalty)

    # --- Bonus & Constraint Calculation ---
    stock_fill_bonus = 0
    has_required_parts = False
    has_stock_parts = False

    for part in layout['placed_parts']:
        if part.get('is_stock_part', False):
            has_stock_parts = True
            deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
            if part.get('target_stock', 0) > 0 and deficit > 0:
                part_area = part['length'] * part['width']
                # Bonus is proportional to the inventory deficit
                stock_fill_bonus += (deficit / part['target_stock']) * part_area * weights.get('w_stock_fill', 1.0)
        else:
            has_required_parts = True

    # --- Constraint: Penalize sheets with only stock parts (unless it's a stock-only job) ---
    if not has_required_parts and has_stock_parts and not is_stock_only_job:
        return -float('inf') # Disqualify this layout completely

    # --- Other Adjustments ---
    quantity_bonus = 0
    if sheet_info.get('type') != 'Default':
        quantity_bonus = sheet_info.get('quantity', 1) * weights.get('w_quantity', 0.1)

    cuts_penalty = len(layout['placed_parts']) * weights.get('w_cuts', 0.2)

    # --- Final Score ---
    final_score = area_based_score + stock_fill_bonus + quantity_bonus - cuts_penalty
    
    return final_score

def _place_parts_in_rects(parts_to_place, initial_rects, kerf, allow_rotation):
    """A helper to run the placement algorithm on a set of parts and rectangles."""
    placed_parts = []
    empty_rects = [r.copy() for r in initial_rects]

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
            
            # Create a copy to avoid modifying the original part dict
            placed_part = part.copy()
            placed_part.update({'rotated': rotated, 'x': rect['x'], 'y': rect['y']})
            placed_parts.append(placed_part)

            top_rect = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': rect['width']}
            right_rect = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': part_l, 'width': rect['width'] - part_w - kerf}
            
            if top_rect['length'] > 0 and top_rect['width'] > 0:
                empty_rects.append(top_rect)
            if right_rect['length'] > 0 and right_rect['width'] > 0:
                empty_rects.append(right_rect)

    return placed_parts, empty_rects

def run_single_sheet_optimization(sheet_info, required_parts, config, optional_stock_parts=None):
    """
    Places required parts and then fills remaining space with optional stock parts.
    """
    kerf = config['kerf']
    allow_rotation = config['allow_rotation']

    # Phase 1: Place required parts for the job
    placed_job_parts, remaining_rects = _place_parts_in_rects(required_parts, 
        [{'x': 0, 'y': 0, 'length': sheet_info['length'], 'width': sheet_info['width']}], 
        kerf, allow_rotation)

    # Phase 2: Fill remaining space with optional stock parts
    placed_stock_parts = []
    if optional_stock_parts and remaining_rects:
        # Sort stock parts to prioritize smaller ones first to fill small gaps
        sorted_stock_parts = sorted(optional_stock_parts, key=lambda p: p['length'] * p['width'])
        placed_stock_parts, remaining_rects = _place_parts_in_rects(sorted_stock_parts, remaining_rects, kerf, allow_rotation)
    
    all_placed_parts = placed_job_parts + placed_stock_parts
    total_placed_area = sum(p['length'] * p['width'] for p in all_placed_parts)

    return {
        "placed_parts": all_placed_parts,
        "offcuts": remaining_rects,
        "sheet_info": sheet_info,
        "total_placed_area": total_placed_area
    }

def optimize_multiple_sheets(parts_to_cut, stock_sheets, config, optional_stock_parts=None, is_stock_only_job=False):
    """
    Main orchestrator that finds the best combination of sheets by minimizing
    the "cost-per-part" in each pass.
    """
    # Prepare the list of required parts, expanding by quantity
    required_parts_list = []
    for part in parts_to_cut:
        for _ in range(int(part.get('quantity', 1))):
            p_copy = part.copy()
            p_copy['part_id'] = uuid.uuid4()
            p_copy['is_stock_part'] = False
            required_parts_list.append(p_copy)

    available_sheets = [s.copy() for s in stock_sheets]
    final_layouts = []
    
    while required_parts_list:
        best_layout_for_pass = None
        min_cost_per_part = float('inf')
        best_sheet_idx = -1

        for i, sheet in enumerate(available_sheets):
            if sheet.get('quantity', 0) <= 0:
                continue

            layout_attempt = run_single_sheet_optimization(
                sheet, required_parts_list, config, optional_stock_parts
            )
            
            num_placed_required = sum(1 for p in layout_attempt['placed_parts'] if not p.get('is_stock_part'))
            if num_placed_required == 0:
                continue
            
            layout_score = calculate_layout_score(
                layout_attempt, sheet, config, config['min_offcut_dim'], is_stock_only_job
            )
            
            total_pass_cost = -layout_score + config.get('w_sheets', 1.0)
            cost_per_part = total_pass_cost / num_placed_required

            if cost_per_part < min_cost_per_part:
                min_cost_per_part = cost_per_part
                best_layout_for_pass = layout_attempt
                best_sheet_idx = i
        
        if best_sheet_idx == -1:
            print(f"Warning: Could not place all required parts. {len(required_parts_list)} parts remaining.")
            break

        final_layouts.append(best_layout_for_pass)
        available_sheets[best_sheet_idx]['quantity'] -= 1
        
        placed_part_ids = {p['part_id'] for p in best_layout_for_pass['placed_parts'] if not p.get('is_stock_part')}
        required_parts_list = [p for p in required_parts_list if p['part_id'] not in placed_part_ids]

    # Handle case where the job is ONLY to make stock parts
    if not parts_to_cut and optional_stock_parts:
        # This is a simplified loop for stock-only jobs
        layout_attempt = run_single_sheet_optimization(stock_sheets[0], [], config, optional_stock_parts)
        if layout_attempt['placed_parts']:
            final_layouts.append(layout_attempt)

    # Calculate the final, overall score for display
    total_score = sum(calculate_layout_score(layout, layout['sheet_info'], config, config['min_offcut_dim'], is_stock_only_job) for layout in final_layouts)
    final_score = total_score - (config.get('w_sheets', 1.0) * len(final_layouts))

    return final_layouts, required_parts_list, final_score