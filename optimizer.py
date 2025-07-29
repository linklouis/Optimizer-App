import uuid
import time
import random
from typing import List, Dict, Any, Tuple, Optional

# A type alias for a part dictionary for clarity
Part = Dict[str, Any]
Rect = Dict[str, float]

def find_best_fit(part_length: float, part_width: float, empty_rects: List[Rect]) -> int:
    """
    Finds the best empty rectangle to place a part in using a shortest-side-fit heuristic.
    This heuristic aims to leave larger, more usable rectangles behind.
    
    Args:
        part_length: The length of the part to place.
        part_width: The width of the part to place.
        empty_rects: A list of available empty rectangles on the sheet.

    Returns:
        The index of the best-fitting rectangle in empty_rects, or -1 if no fit is found.
    """
    best_fit_index = -1
    min_short_side_fit = float('inf')

    for i, rect in enumerate(empty_rects):
        if part_length <= rect['length'] and part_width <= rect['width']:
            remaining_length = rect['length'] - part_length
            remaining_width = rect['width'] - part_width
            
            short_side_fit = min(remaining_length, remaining_width)

            if short_side_fit < min_short_side_fit:
                min_short_side_fit = short_side_fit
                best_fit_index = i
    
    return best_fit_index

def calculate_layout_score(layout: Dict[str, Any], sheet_info: Dict[str, Any], weights: Dict[str, float], 
                           min_offcut_dim: float, reference_area: float, is_stock_only_job: bool = False) -> float:
    """
    Calculates a score for a given layout.

    The score penalizes waste and offcuts (normalized against a reference area) and adds
    bonuses for using existing inventory (offcuts) and filling needed stock parts.
    
    Args:
        layout: The layout dictionary containing placed parts and offcuts.
        sheet_info: Information about the sheet used for the layout.
        weights: A dictionary of weights for different scoring components.
        min_offcut_dim: The minimum dimension for an offcut to be considered usable.
        reference_area: A standard area (e.g., of a default sheet) to normalize penalties against.
        is_stock_only_job: Flag indicating if the job is only for creating stock parts.

    Returns:
        The calculated score for the layout. A higher score is better.
    """
    sheet_area = sheet_info['length'] * sheet_info['width']
    if sheet_area == 0 or reference_area == 0:
        return -float('inf')

    used_area = layout['total_placed_area']
    usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
    offcut_area = sum(o['length'] * o['width'] for o in usable_offcuts)
    waste_area = sheet_area - used_area - offcut_area
    
    # Penalties are normalized by 'reference_area' for consistent scoring across different sheet sizes.
    waste_penalty = (waste_area / reference_area) * weights.get('w_waste', 1.0)
    offcut_penalty = (offcut_area / reference_area) * weights.get('w_offcut', 0.5)
    area_based_score = -(waste_penalty + offcut_penalty)

    stock_fill_bonus = 0
    has_required_parts = False
    has_stock_parts = False

    for part in layout['placed_parts']:
        if part.get('is_stock_part', False):
            has_stock_parts = True
            deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
            if part.get('target_stock', 0) > 0 and deficit > 0:
                part_area = part['length'] * part['width']
                # Stock fill bonus is relative to the current sheet's area to measure its efficiency.
                stock_fill_bonus += (deficit / part['target_stock']) * (part_area / sheet_area) * weights.get('w_stock_fill', 1.0)
        else:
            has_required_parts = True

    # Heavily penalize layouts that use a sheet only for stock parts when required parts are still needed.
    if not has_required_parts and has_stock_parts and not is_stock_only_job:
        return -float('inf')

    # Bonus for using a non-default sheet (like an offcut), encouraging inventory use.
    quantity_bonus = 0
    if sheet_info.get('type') != 'Default':
        quantity_bonus = sheet_info.get('quantity', 1) * weights.get('w_quantity', 0.1)

    cuts_penalty = len(layout['placed_parts']) * weights.get('w_cuts', 0.2)

    return area_based_score + stock_fill_bonus + quantity_bonus - cuts_penalty

def _get_split_penalty(rect1: Rect, rect2: Rect) -> float:
    """
    Calculates a penalty for splitting a rectangle, favoring splits that result in more 'squarish'
    and thus more versatile leftover rectangles.
    """
    def get_ratio_penalty(r_len: float, r_wid: float) -> float:
        if min(r_len, r_wid) > 0:
            return max(r_len, r_wid) / min(r_len, r_wid)
        return 0 
    
    return get_ratio_penalty(rect1['length'], rect1['width']) + get_ratio_penalty(rect2['length'], rect2['width'])

def _place_parts_in_rects(parts_to_place: List[Part], initial_rects: List[Rect], 
                          kerf: float, allow_rotation: bool) -> Tuple[List[Part], List[Rect]]:
    """
    Helper function to run the core part placement algorithm.
    It iterates through parts and places them in the best available rectangle.
    """
    placed_parts: List[Part] = []
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
            
            placed_part = part.copy()
            placed_part.update({'rotated': rotated, 'x': rect['x'], 'y': rect['y']})
            placed_parts.append(placed_part)

            # Define the two possible ways to split the remaining area after placing a part.
            rect_above = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': rect['width']}
            rect_right_of_part = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': part_l, 'width': rect['width'] - part_w - kerf}

            rect_right = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': rect['length'], 'width': rect['width'] - part_w - kerf}
            rect_above_part = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': part_w}
            
            # Choose the split method that results in better-shaped leftover rectangles.
            if _get_split_penalty(rect_above, rect_right_of_part) <= _get_split_penalty(rect_right, rect_above_part):
                if rect_above['length'] > 0 and rect_above['width'] > 0: empty_rects.append(rect_above)
                if rect_right_of_part['length'] > 0 and rect_right_of_part['width'] > 0: empty_rects.append(rect_right_of_part)
            else:
                if rect_right['length'] > 0 and rect_right['width'] > 0: empty_rects.append(rect_right)
                if rect_above_part['length'] > 0 and rect_above_part['width'] > 0: empty_rects.append(rect_above_part)

    return placed_parts, empty_rects

def _place_randomized_stock(stock_parts_to_place: List[Part], initial_rects: List[Rect], 
                            kerf: float, allow_rotation: bool) -> Tuple[List[Part], List[Rect]]:
    """
    Places optional stock parts using a randomized greedy approach. This exploration helps find
    better overall layouts than a single deterministic pass.
    """
    parts_to_attempt = list(stock_parts_to_place)
    random.shuffle(parts_to_attempt)

    k = min(len(parts_to_attempt), 10) # Consider a random subset to vary the attempts.
    parts_to_attempt = random.sample(parts_to_attempt, k=k)

    return _place_parts_in_rects(parts_to_attempt, initial_rects, kerf, allow_rotation)

def run_single_sheet_optimization(sheet_info: Dict, required_parts: List[Part], config: Dict, 
                                  reference_area: float, optional_stock_parts: Optional[List[Part]] = None) -> Dict:
    """
    Finds the best layout for a single sheet by running a time-boxed, randomized search.

    It first establishes a baseline layout with only required parts. Then, it repeatedly
    tries to add randomized sets of optional stock parts to find a higher-scoring layout
    within the given time limit.
    """
    kerf = config['kerf']
    allow_rotation = config['allow_rotation']
    time_limit = config.get('search_time_limit', 1.0)
    is_stock_only_job = not required_parts and optional_stock_parts

    # --- Step 1: Establish baseline layout with only required parts ---
    initial_sheet_rect = [{'x': 0, 'y': 0, 'length': sheet_info['length'], 'width': sheet_info['width']}]
    placed_job_parts, remaining_rects_after_job = _place_parts_in_rects(
        required_parts, initial_sheet_rect, kerf, allow_rotation
    )
    
    best_layout = {
        "placed_parts": placed_job_parts,
        "offcuts": remaining_rects_after_job,
        "sheet_info": sheet_info,
        "total_placed_area": sum(p['length'] * p['width'] for p in placed_job_parts)
    }
    best_score = calculate_layout_score(best_layout, sheet_info, config, config['min_offcut_dim'], reference_area, is_stock_only_job)

    # --- Step 2: Time-boxed search to add optional stock parts ---
    if optional_stock_parts and remaining_rects_after_job:
        sorted_stock_parts = sorted(optional_stock_parts, key=lambda p: p['target_stock'] - p['current_stock'], reverse=True)
        
        start_time = time.time()
        iterations = 0
        while time.time() - start_time < time_limit:
            placed_stock_parts, final_rects = _place_randomized_stock(
                sorted_stock_parts, remaining_rects_after_job, kerf, allow_rotation
            )
            
            if not placed_stock_parts and iterations > 10:
                break # Stop if we fail to place any stock parts after several tries.
            
            candidate_placed_parts = placed_job_parts + placed_stock_parts
            candidate_layout = {
                "placed_parts": candidate_placed_parts, "offcuts": final_rects,
                "sheet_info": sheet_info,
                "total_placed_area": sum(p['length'] * p['width'] for p in candidate_placed_parts)
            }
            candidate_score = calculate_layout_score(candidate_layout, sheet_info, config, config['min_offcut_dim'], reference_area, is_stock_only_job)

            if candidate_score > best_score:
                best_score = candidate_score
                best_layout = candidate_layout
            iterations += 1
    
    return best_layout

def optimize_multiple_sheets(parts_to_cut: List[Part], stock_sheets: List[Dict], config: Dict, 
                             optional_stock_parts: Optional[List[Part]] = None, 
                             is_stock_only_job: bool = False) -> Tuple[List[Dict], List[Part], float]:
    """
    Main orchestrator that selects the best sheets and layouts to fulfill the part requirements.
    
    It iteratively selects the best sheet to use by minimizing the "cost-per-part" for
    each available sheet, ensuring the most efficient use of materials over the entire job.
    """
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)

    # Create a unique list of all individual parts needed.
    required_parts_list: List[Part] = []
    for part in parts_to_cut:
        for _ in range(int(part.get('quantity', 1))):
            p_copy = part.copy()
            p_copy['part_id'] = uuid.uuid4()
            p_copy['is_stock_part'] = False
            required_parts_list.append(p_copy)
    
    live_optional_stock_parts: List[Part] = []
    if optional_stock_parts:
        for part in optional_stock_parts:
            deficit = int(part['target_stock'] - part['current_stock'])
            for _ in range(deficit):
                p_copy = part.copy()
                p_copy['part_id'] = uuid.uuid4()
                live_optional_stock_parts.append(p_copy)

    available_sheets = [s.copy() for s in stock_sheets]
    final_layouts = []
    
    while required_parts_list or (is_stock_only_job and live_optional_stock_parts):
        best_layout_for_pass = None
        min_cost_per_part = float('inf')
        best_sheet_idx = -1

        # Determine which parts are the primary target for this pass.
        primary_parts_for_pass = required_parts_list if not is_stock_only_job else live_optional_stock_parts

        for i, sheet in enumerate(available_sheets):
            if sheet.get('quantity', 0) <= 0:
                continue

            layout_attempt = run_single_sheet_optimization(
                sheet, primary_parts_for_pass, config, reference_area, 
                live_optional_stock_parts if not is_stock_only_job else []
            )
            
            primary_part_ids = {p['part_id'] for p in primary_parts_for_pass}
            num_placed_primary = sum(1 for p in layout_attempt['placed_parts'] if p['part_id'] in primary_part_ids)
            if num_placed_primary == 0:
                continue
            
            layout_score = calculate_layout_score(layout_attempt, sheet, config, config['min_offcut_dim'], reference_area, is_stock_only_job)
            
            # The "cost" of using this sheet is its negative score plus a penalty for using a sheet.
            total_pass_cost = -layout_score + config.get('w_sheets', 1.0)
            cost_per_part = total_pass_cost / num_placed_primary

            if cost_per_part < min_cost_per_part:
                min_cost_per_part = cost_per_part
                best_layout_for_pass = layout_attempt
                best_sheet_idx = i
        
        if best_sheet_idx == -1:
            # If no sheet can place any of the remaining parts, break the loop.
            print(f"Warning: Could not place any more primary parts. {len(primary_parts_for_pass)} parts remaining.")
            break

        final_layouts.append(best_layout_for_pass)
        available_sheets[best_sheet_idx]['quantity'] -= 1
        
        placed_ids = {p['part_id'] for p in best_layout_for_pass['placed_parts']}
        required_parts_list = [p for p in required_parts_list if p['part_id'] not in placed_ids]
        live_optional_stock_parts = [p for p in live_optional_stock_parts if p['part_id'] not in placed_ids]

    # Calculate final score for the entire job.
    total_score = sum(calculate_layout_score(l, l['sheet_info'], config, config['min_offcut_dim'], reference_area, is_stock_only_job) for l in final_layouts)
    final_score = total_score - (config.get('w_sheets', 1.0) * len(final_layouts))

    remaining_parts = required_parts_list if not is_stock_only_job else live_optional_stock_parts
    return final_layouts, remaining_parts, final_score