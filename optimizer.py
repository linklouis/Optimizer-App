import uuid
import time
import random

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

# MODIFIED: Added 'reference_area' parameter
def calculate_layout_score(layout, sheet_info, weights, min_offcut_dim, reference_area, is_stock_only_job=False):
    """
    Calculates a score by penalizing waste and offcut area, and adding bonuses.
    Waste and offcut penalties are normalized against the reference_area for consistency.
    """
    sheet_area = sheet_info['length'] * sheet_info['width']
    if sheet_area == 0 or reference_area == 0: # NEW: Check reference_area
        return -float('inf')

    # --- Area & Penalty Calculation ---
    used_area = layout['total_placed_area']
    usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
    offcut_area = sum((o['length'] * o['width']) for o in usable_offcuts)
    waste_area = sheet_area - used_area - offcut_area
    
    # MODIFIED: Penalties are now divided by the 'reference_area' instead of the 'sheet_area'.
    offcut_penalty = (offcut_area / reference_area) * weights.get('w_offcut', 0.5)
    waste_penalty = (waste_area / reference_area) * weights.get('w_waste', 1.0)
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
                # NOTE: Stock fill bonus is still based on the current sheet's area. This keeps it
                # as a measure of how efficiently this specific sheet was filled with needed parts.
                stock_fill_bonus += (deficit / part['target_stock']) * (part_area / sheet_area) * weights.get('w_stock_fill', 1.0)
        else:
            has_required_parts = True

    if not has_required_parts and has_stock_parts and not is_stock_only_job:
        return -float('inf')

    # --- Other Adjustments ---
    quantity_bonus = 0
    if sheet_info.get('type') != 'Default':
        quantity_bonus = sheet_info.get('quantity', 1) * weights.get('w_quantity', 0.1)

    cuts_penalty = len(layout['placed_parts']) * weights.get('w_cuts', 0.2)

    # --- Final Score ---
    final_score = area_based_score + stock_fill_bonus + quantity_bonus - cuts_penalty
    
    return final_score

def _get_split_penalty(rect1, rect2):
    """Calculates a penalty for a split based on the aspect ratios of the two resulting rectangles."""
    def get_rect_penalty(r_len, r_wid):
        if min(r_len, r_wid) > 0:
            return max(r_len, r_wid) / min(r_len, r_wid)
        return 0 
    
    return get_rect_penalty(rect1['length'], rect1['width']) + get_rect_penalty(rect2['length'], rect2['width'])


def _place_parts_in_rects(parts_to_place, initial_rects, kerf, allow_rotation):
    """A helper to run the placement algorithm on a set of parts and rectangles."""
    placed_parts = []
    empty_rects = [r.copy() for r in initial_rects]

    for part in parts_to_place:
        best_fit = {'rect_index': -1, 'orientation': None}
        
        orientations = [(part['length'], part['width'], False)]
        if allow_rotation and part['length'] != part['width']:
            orientations.append((part['width'], part['length'], True))

        for part_l, part_w, rotated in orientations:
            rect_index = find_best_fit(part_l, part_w, empty_rects)
            if rect_index != -1:
                best_fit['rect_index'] = rect_index
                best_fit['orientation'] = (part_l, part_w, rotated)
                break 
        
        if best_fit['rect_index'] != -1:
            part_l, part_w, rotated = best_fit['orientation']
            rect = empty_rects.pop(best_fit['rect_index'])
            
            placed_part = part.copy()
            placed_part.update({'rotated': rotated, 'x': rect['x'], 'y': rect['y']})
            placed_parts.append(placed_part)

            rect_above = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': rect['width']}
            rect_right_of_part = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': part_l, 'width': rect['width'] - part_w - kerf}
            rect_right = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': rect['length'], 'width': rect['width'] - part_w - kerf}
            rect_above_part = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': part_w}

            split1_penalty = _get_split_penalty(rect_above, rect_right_of_part)
            split2_penalty = _get_split_penalty(rect_right, rect_above_part)
            
            if split1_penalty <= split2_penalty:
                if rect_above['length'] > 0 and rect_above['width'] > 0: empty_rects.append(rect_above)
                if rect_right_of_part['length'] > 0 and rect_right_of_part['width'] > 0: empty_rects.append(rect_right_of_part)
            else:
                if rect_right['length'] > 0 and rect_right['width'] > 0: empty_rects.append(rect_right)
                if rect_above_part['length'] > 0 and rect_above_part['width'] > 0: empty_rects.append(rect_above_part)

    return placed_parts, empty_rects

def _place_randomized_stock(stock_parts_to_place, initial_rects, kerf, allow_rotation):
    """
    Places stock parts using a randomized greedy approach to explore different layout configurations.
    """
    parts_to_attempt = list(stock_parts_to_place)
    random.shuffle(parts_to_attempt) # Randomize the order to explore different paths

    # Prioritize placing more needed parts by only considering a random subset of the most-needed parts
    # This adds another layer of exploration to find better solutions.
    k = min(len(parts_to_attempt), 10) # Consider the top 10 most needed parts, or fewer if not available
    parts_to_attempt = random.sample(parts_to_attempt, k=k)

    return _place_parts_in_rects(parts_to_attempt, initial_rects, kerf, allow_rotation)

# MODIFIED: Added 'reference_area' parameter
def run_single_sheet_optimization(sheet_info, required_parts, config, reference_area, optional_stock_parts=None):
    """
    Finds the best layout for a single sheet by comparing a version with no stock parts
    against multiple, randomized configurations of stock parts over a time limit.
    """
    kerf = config['kerf']
    allow_rotation = config['allow_rotation']
    time_limit = config.get('search_time_limit', 1.0)
    is_stock_only_job = not required_parts and optional_stock_parts

    # --- Step 1: Establish Baseline (No Stock Parts) ---
    placed_job_parts, remaining_rects_after_job = _place_parts_in_rects(
        required_parts, [{'x': 0, 'y': 0, 'length': sheet_info['length'], 'width': sheet_info['width']}], kerf, allow_rotation
    )
    
    baseline_layout = {
        "placed_parts": placed_job_parts,
        "offcuts": remaining_rects_after_job,
        "sheet_info": sheet_info,
        "total_placed_area": sum(p['length'] * p['width'] for p in placed_job_parts)
    }
    
    best_layout_so_far = baseline_layout
    # MODIFIED: Pass 'reference_area' to the scoring function
    best_score_so_far = calculate_layout_score(best_layout_so_far, sheet_info, config, config['min_offcut_dim'], reference_area, is_stock_only_job)

    # --- Step 2: Time-boxed Search for Stock Part Configurations ---
    if optional_stock_parts and remaining_rects_after_job:
        sorted_stock_parts = sorted(optional_stock_parts, key=lambda p: p['target_stock'] - p['current_stock'], reverse=True)
        
        start_time = time.time()
        iterations = 0
        while time.time() - start_time < time_limit:
            # Generate a new candidate layout by placing a randomized set of stock parts
            placed_stock_parts, final_rects = _place_randomized_stock(
                sorted_stock_parts, remaining_rects_after_job, kerf, allow_rotation
            )
            
            if not placed_stock_parts: # No need to score if we couldn't place any stock parts
                iterations += 1
                if iterations > 10: # If we can't place anything after a few tries, stop.
                    break
                continue

            candidate_placed_parts = placed_job_parts + placed_stock_parts
            candidate_layout = {
                "placed_parts": candidate_placed_parts,
                "offcuts": final_rects,
                "sheet_info": sheet_info,
                "total_placed_area": sum(p['length'] * p['width'] for p in candidate_placed_parts)
            }
            
            # MODIFIED: Pass 'reference_area' to the scoring function
            candidate_score = calculate_layout_score(candidate_layout, sheet_info, config, config['min_offcut_dim'], reference_area, is_stock_only_job)

            if candidate_score > best_score_so_far:
                best_score_so_far = candidate_score
                best_layout_so_far = candidate_layout
            
            iterations += 1
    
    return best_layout_so_far


def optimize_multiple_sheets(parts_to_cut, stock_sheets, config, optional_stock_parts=None, is_stock_only_job=False):
    """
    Main orchestrator that finds the best combination of sheets by minimizing
    the "cost-per-part" in each pass, using the time-boxed search for each sheet.
    """
    # NEW: Define the reference area from the default sheet dimensions in the config.
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)

    required_parts_list = []
    for part in parts_to_cut:
        for _ in range(int(part.get('quantity', 1))):
            p_copy = part.copy()
            p_copy['part_id'] = uuid.uuid4()
            p_copy['is_stock_part'] = False
            required_parts_list.append(p_copy)
    
    live_optional_stock_parts = []
    if optional_stock_parts:
        for part in optional_stock_parts:
            deficit = int(part['target_stock'] - part['current_stock'])
            if deficit > 0:
                for _ in range(deficit):
                    p_copy = part.copy()
                    p_copy['part_id'] = uuid.uuid4()
                    live_optional_stock_parts.append(p_copy)

    available_sheets = [s.copy() for s in stock_sheets]
    final_layouts = []
    
    loop_condition = required_parts_list or (is_stock_only_job and live_optional_stock_parts)
    while loop_condition:
        best_layout_for_pass = None
        min_cost_per_part = float('inf')
        best_sheet_idx = -1

        parts_for_this_pass = required_parts_list if not is_stock_only_job else live_optional_stock_parts

        for i, sheet in enumerate(available_sheets):
            if sheet.get('quantity', 0) <= 0:
                continue

            # MODIFIED: Pass 'reference_area' to the optimization function
            layout_attempt = run_single_sheet_optimization(
                sheet, parts_for_this_pass, config, reference_area, live_optional_stock_parts if not is_stock_only_job else []
            )
            
            num_placed_primary = sum(1 for p in layout_attempt['placed_parts'] if p['part_id'] in {part['part_id'] for part in parts_for_this_pass})
            if num_placed_primary == 0:
                continue
            
            # MODIFIED: Pass 'reference_area' to the scoring function
            layout_score = calculate_layout_score(
                layout_attempt, sheet, config, config['min_offcut_dim'], reference_area, is_stock_only_job
            )
            
            total_pass_cost = -layout_score + config.get('w_sheets', 1.0)
            cost_per_part = total_pass_cost / num_placed_primary

            if cost_per_part < min_cost_per_part:
                min_cost_per_part = cost_per_part
                best_layout_for_pass = layout_attempt
                best_sheet_idx = i
        
        if best_sheet_idx == -1:
            print(f"Warning: Could not place all primary parts. {len(parts_for_this_pass)} parts remaining.")
            break

        final_layouts.append(best_layout_for_pass)
        available_sheets[best_sheet_idx]['quantity'] -= 1
        
        placed_ids = {p['part_id'] for p in best_layout_for_pass['placed_parts']}
        required_parts_list = [p for p in required_parts_list if p['part_id'] not in placed_ids]
        live_optional_stock_parts = [p for p in live_optional_stock_parts if p['part_id'] not in placed_ids]
        
        loop_condition = required_parts_list or (is_stock_only_job and live_optional_stock_parts)

    # MODIFIED: Pass 'reference_area' to the final scoring calculation
    total_score = sum(calculate_layout_score(layout, layout['sheet_info'], config, config['min_offcut_dim'], reference_area, is_stock_only_job) for layout in final_layouts)
    final_score = total_score - (config.get('w_sheets', 1.0) * len(final_layouts))

    return final_layouts, required_parts_list, final_score