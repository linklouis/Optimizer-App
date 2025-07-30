import time
import random
from typing import List, Dict, Any, Tuple, Optional

# A type alias for a part dictionary for clarity
Part = Dict[str, Any]
Rect = Dict[str, float]

# --- Consolidated & Optimized Helper Functions ---

def get_layout_signature(layout: Dict[str, Any], required_only: bool = False) -> str:
    """
    Creates a signature for a layout. Can be configured for required parts only or all parts.
    (Consolidates get_required_parts_signature and get_full_layout_signature)
    """
    sheet_info = layout['sheet_info']
    sig_parts = [f"sheet_{sheet_info['length']:.3f}x{sheet_info['width']:.3f}"]

    parts_to_sign = (
        [p for p in layout['placed_parts'] if not p.get('is_stock_part', False)]
        if required_only
        else layout['placed_parts']
    )
    
    # Sorting ensures the signature is consistent regardless of part placement order
    sorted_parts = sorted(parts_to_sign, key=lambda p: (p['x'], p['y']))

    sig_strings = [
        (
            f"part_{p['x']:.3f},{p['y']:.3f}_"
            f"{p['length'] if p.get('rotated') else p['width']:.3f}x"
            f"{p['width'] if p.get('rotated') else p['length']:.3f}"
        )
        for p in sorted_parts
    ]
    
    return "|".join(sig_parts + sig_strings)


def find_best_fit(part_length: float, part_width: float, empty_rects: List[Rect]) -> int:
    """
    Finds the best empty rectangle to place a part in using a shortest-side-fit heuristic.
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
    """Calculates a score for a given layout. Higher is better."""
    sheet_area = sheet_info['length'] * sheet_info['width']
    if sheet_area == 0 or reference_area == 0:
        return -float('inf')

    used_area = layout['total_placed_area']
    usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
    offcut_area = sum(o['length'] * o['width'] for o in usable_offcuts)
    
    waste_penalty = ((sheet_area - used_area - offcut_area) / reference_area) * weights.get('w_waste', 1.0)
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
                stock_fill_bonus += (deficit / part['target_stock']) * (part_area / sheet_area) * weights.get('w_stock_fill', 1.0)
        else:
            has_required_parts = True

    if not has_required_parts and has_stock_parts and not is_stock_only_job:
        return -float('inf')

    quantity_bonus = (sheet_info.get('quantity', 1) * weights.get('w_quantity', 0.1)) if sheet_info.get('type') != 'Default' else 0
    cuts_penalty = len(layout['placed_parts']) * weights.get('w_cuts', 0.2)

    return area_based_score + stock_fill_bonus + quantity_bonus - cuts_penalty


def _get_split_penalty(rect1: Rect, rect2: Rect) -> float:
    """Calculates a penalty for splitting a rectangle."""
    def get_ratio_penalty(r_len: float, r_wid: float) -> float:
        min_dim = min(r_len, r_wid)
        return max(r_len, r_wid) / min_dim if min_dim > 0 else 0
    return get_ratio_penalty(rect1['length'], rect1['width']) + get_ratio_penalty(rect2['length'], rect2['width'])


# --- Core Placement Logic ---

def _place_parts_in_rects(parts_to_place: List[Part], initial_rects: List[Rect], 
                          kerf: float, allow_rotation: bool) -> Tuple[List[Part], List[Rect]]:
    """Helper function to run the core part placement algorithm."""
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
            
            rect_idx = best_fit['rect_index']
            rect = empty_rects[rect_idx]
            empty_rects[rect_idx] = empty_rects[-1]
            empty_rects.pop()

            placed_part = part.copy()
            placed_part.update({'rotated': rotated, 'x': rect['x'], 'y': rect['y']})
            placed_parts.append(placed_part)

            rect_above = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': rect['width']}
            rect_right_of_part = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': part_l, 'width': rect['width'] - part_w - kerf}
            
            rect_right = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': rect['length'], 'width': rect['width'] - part_w - kerf}
            rect_above_part = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': part_w}
            
            if _get_split_penalty(rect_above, rect_right_of_part) <= _get_split_penalty(rect_right, rect_above_part):
                if rect_above['length'] > 0 and rect_above['width'] > 0: empty_rects.append(rect_above)
                if rect_right_of_part['length'] > 0 and rect_right_of_part['width'] > 0: empty_rects.append(rect_right_of_part)
            else:
                if rect_right['length'] > 0 and rect_right['width'] > 0: empty_rects.append(rect_right)
                if rect_above_part['length'] > 0 and rect_above_part['width'] > 0: empty_rects.append(rect_above_part)
                
    return placed_parts, empty_rects


# --- Optimization Orchestrators ---

def run_single_sheet_optimization(sheet_info: Dict, required_parts: List[Part], config: Dict, 
                                  reference_area: float, optional_stock_parts: Optional[List[Part]] = None) -> Dict:
    """Finds the best layout for a single sheet by running a time-boxed, randomized search."""
    kerf = config['kerf']
    allow_rotation = config['allow_rotation']
    time_limit = config.get('search_time_limit', 1.0)
    is_stock_only_job = not required_parts and optional_stock_parts

    initial_sheet_rect = [{'x': 0, 'y': 0, 'length': sheet_info['length'], 'width': sheet_info['width']}]
    placed_job_parts, remaining_rects_after_job = _place_parts_in_rects(
        required_parts, initial_sheet_rect, kerf, allow_rotation
    )
    
    best_layout = {
        "placed_parts": placed_job_parts, "offcuts": remaining_rects_after_job,
        "sheet_info": sheet_info,
        "total_placed_area": sum(p['length'] * p['width'] for p in placed_job_parts)
    }
    best_score = calculate_layout_score(best_layout, sheet_info, config, config['min_offcut_dim'], reference_area, is_stock_only_job)

    if optional_stock_parts and remaining_rects_after_job:
        sorted_stock_parts = sorted(optional_stock_parts, key=lambda p: p.get('target_stock', 1) - p.get('current_stock', 0), reverse=True)
        
        start_time = time.time()
        iterations = 0
        while time.time() - start_time < time_limit:
            k = min(len(sorted_stock_parts), 10)
            parts_to_attempt = random.sample(sorted_stock_parts, k=k)
            
            placed_stock_parts, final_rects = _place_parts_in_rects(
                parts_to_attempt, remaining_rects_after_job, kerf, allow_rotation
            )
            
            if not placed_stock_parts and iterations > 10: break
            
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

# --- BEAM SEARCH IMPLEMENTATION ---

def optimize_multiple_sheets(parts_to_cut: List[Part], stock_sheets: List[Dict], config: Dict, 
                             optional_stock_parts: Optional[List[Part]] = None, 
                             is_stock_only_job: bool = False) -> Tuple[List[Dict], List[Part], float]:
    """
    Main orchestrator that uses a beam search to find the best overall sequence of layouts.
    This is a pruned tree search approach.
    """
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    beam_width = config.get('beam_width', 1)

    # --- Initial State Setup ---
    part_id_counter = 0
    primary_parts_dict: Dict[int, Part] = {}
    for part in parts_to_cut:
        for _ in range(int(part.get('quantity', 1))):
            p_copy = part.copy()
            p_copy['part_id'] = part_id_counter
            p_copy['is_stock_part'] = False
            primary_parts_dict[part_id_counter] = p_copy
            part_id_counter += 1
            
    if is_stock_only_job and optional_stock_parts:
         for part in optional_stock_parts:
            deficit = int(part['target_stock'] - part['current_stock'])
            for _ in range(deficit):
                p_copy = part.copy()
                p_copy['part_id'] = part_id_counter
                p_copy['is_stock_part'] = True # Ensure this is set correctly
                primary_parts_dict[part_id_counter] = p_copy
                part_id_counter += 1

    # --- Initialize Beams ---
    # Each "beam" is a candidate solution path (a state)
    initial_state = {
        "primary_parts_dict": primary_parts_dict,
        "optional_stock_parts": optional_stock_parts if not is_stock_only_job else [],
        "available_sheets": [s.copy() for s in stock_sheets],
        "layouts": [],
        "layout_signatures": set(),
        "cost": 0.0, # Accumulated cost (lower is better)
    }
    beams = [initial_state]
    
    # --- Main Search Loop ---
    while True:
        next_beams = []
        
        # Expand each beam to generate next possible states
        for beam in beams:
            if not beam["primary_parts_dict"]:
                # This path is complete, add it to the next generation to be considered for the final result
                next_beams.append(beam)
                continue
            
            primary_parts_for_pass = list(beam["primary_parts_dict"].values())
            optional_parts_for_pass = beam["optional_stock_parts"]

            for i, sheet in enumerate(beam["available_sheets"]):
                if sheet.get('quantity', 0) <= 0: continue

                layout_attempt = run_single_sheet_optimization(
                    sheet, primary_parts_for_pass, config, reference_area, optional_parts_for_pass
                )
                
                num_placed_primary = sum(1 for p in layout_attempt['placed_parts'] if p['part_id'] in beam["primary_parts_dict"])
                if num_placed_primary == 0: continue
                
                # --- Create a new state from this layout attempt ---
                new_beam = {
                    "layouts": beam["layouts"] + [layout_attempt],
                    "layout_signatures": beam["layout_signatures"].copy(),
                    "available_sheets": [s.copy() for s in beam["available_sheets"]],
                }

                # Update parts for the new state
                placed_ids = {p['part_id'] for p in layout_attempt['placed_parts']}
                new_beam["primary_parts_dict"] = {k: v for k, v in beam["primary_parts_dict"].items() if k not in placed_ids}
                if new_beam["optional_stock_parts"]:
                    new_beam["optional_stock_parts"] = [p for p in optional_parts_for_pass if p['part_id'] not in placed_ids]
                
                # Update sheet quantity
                new_beam["available_sheets"][i]['quantity'] -= 1

                # Calculate the cost of this *single step*
                layout_score = calculate_layout_score(layout_attempt, sheet, config, config['min_offcut_dim'], reference_area, is_stock_only_job)
                layout_sig = get_layout_signature(layout_attempt, required_only=False)
                is_new_layout = layout_sig not in new_beam["layout_signatures"]
                uniqueness_penalty = config.get('w_unique_layouts', 0.5) if is_new_layout else 0.0
                step_cost = -layout_score + config.get('w_sheets', 1.0) + uniqueness_penalty
                
                # Update signatures and total cost for the new state
                new_beam["layout_signatures"].add(layout_sig)
                new_beam["cost"] = beam["cost"] + step_cost
                
                next_beams.append(new_beam)

        if not next_beams or all(b.get("primary_parts_dict") == beams[i].get("primary_parts_dict") for i,b in enumerate(next_beams)):
            # If no new states were generated, or no progress was made, stop.
            break

        # Pruning step: Keep only the top `beam_width` states
        next_beams.sort(key=lambda b: b['cost'] / (len(initial_state["primary_parts_dict"]) - len(b["primary_parts_dict"]) + 1))
        beams = next_beams[:beam_width]

    # --- Select Best Final Solution ---
    if not beams:
        return [], list(primary_parts_dict.values()), 0.0

    # Find the beam that completed the most parts with the lowest cost
    best_beam = min(beams, key=lambda b: (len(b["primary_parts_dict"]), b["cost"]))
    
    final_layouts = best_beam["layouts"]
    remaining_parts = list(best_beam["primary_parts_dict"].values())

    # Calculate final score for reporting
    total_layout_score = sum(calculate_layout_score(l, l['sheet_info'], config, config['min_offcut_dim'], reference_area, is_stock_only_job) for l in final_layouts)
    sheets_penalty = config.get('w_sheets', 1.0) * len(final_layouts)
    unique_layouts_penalty = config.get('w_unique_layouts', 0.5) * len(best_beam["layout_signatures"])
    final_score = total_layout_score - sheets_penalty - unique_layouts_penalty

    return final_layouts, remaining_parts, final_score