import time
import random
import heapq
import math
from typing import List, Dict, Any, Tuple, Optional
from collections import Counter

# --- NEW: Import Numba for JIT compilation and NumPy for efficient arrays ---
import numba
import numpy as np


# A type alias for a part dictionary for clarity
Part = Dict[str, Any]
Rect = Dict[str, float]

# --- Consolidated & Optimized Helper Functions ---

def get_layout_signature(layout: Dict[str, Any], required_only: bool = False) -> str:
    """
    Creates a signature for a layout. Can be configured for required parts only or all parts.
    """
    sheet_info = layout['sheet_info']
    sig_parts = [f"sheet_{sheet_info['length']:.3f}x{sheet_info['width']:.3f}"]

    parts_to_sign = (
        [p for p in layout['placed_parts'] if not p.get('is_optional_filler', False)]
        if required_only
        else layout['placed_parts']
    )
    
    sorted_parts = sorted(parts_to_sign, key=lambda p: (p['x'], p['y']))

    sig_strings = [
        (
            f"part_{p['x']:.3f},{p['y']:.3f}_"
            f"{p['width'] if p.get('rotated') else p['length']:.3f}x"
            f"{p['length'] if p.get('rotated') else p['width']:.3f}"
        )
        for p in sorted_parts
    ]
    
    return "|".join(sig_parts + sig_strings)


# --- OPTIMIZED: This function is now JIT-compiled by Numba for significant speedup ---
# Note: It now accepts a NumPy array instead of a list of dicts for maximum performance.
@numba.jit(nopython=True)
def find_best_fit(part_length: float, part_width: float, empty_rects_np: np.ndarray) -> int:
    """
    Finds the best empty rectangle to place a part in using a shortest-side-fit heuristic.
    This function is optimized with Numba.
    """
    best_fit_index = -1
    min_short_side_fit = float('inf')

    for i in range(empty_rects_np.shape[0]):
        rect_length = empty_rects_np[i, 0]
        rect_width = empty_rects_np[i, 1]
        
        if part_length <= rect_length and part_width <= rect_width:
            remaining_length = rect_length - part_length
            remaining_width = rect_width - part_width
            short_side_fit = min(remaining_length, remaining_width)
            if short_side_fit < min_short_side_fit:
                min_short_side_fit = short_side_fit
                best_fit_index = i
    
    return best_fit_index


def calculate_layout_score(layout: Dict[str, Any], sheet_info: Dict[str, Any], config: Dict[str, Any], 
                           min_offcut_dim: float, reference_area: float, max_offcut_quantity: float, 
                           is_stock_only_job: bool = False, tracker: Optional[Dict] = None) -> float:
    """Calculates a score for a given layout. Higher is better."""
    
    def _update_tracker(component_name: str, value: float):
        """Helper to update the statistics for a score component."""
        if tracker is not None:
            stats = tracker.setdefault(component_name, {'min': float('inf'), 'max': -float('inf'), 'sum': 0, 'count': 0})
            stats['min'] = min(stats['min'], value)
            stats['max'] = max(stats['max'], value)
            stats['sum'] += value
            stats['count'] += 1

    sheet_area = sheet_info['length'] * sheet_info['width']
    if sheet_area == 0 or reference_area == 0:
        return -float('inf')

    used_area = layout['total_placed_area']
    usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
    offcut_area = sum(o['length'] * o['width'] for o in usable_offcuts)
    
    # --- Area-Based Components (already normalized by reference_area) ---
    unweighted_waste = (sheet_area - used_area - offcut_area) / reference_area if reference_area > 0 else 0
    unweighted_offcut = offcut_area / reference_area if reference_area > 0 else 0
    _update_tracker('waste_penalty', unweighted_waste)
    _update_tracker('offcut_penalty', unweighted_offcut)

    waste_penalty = unweighted_waste * config.get('w_waste', 1.0)
    offcut_penalty = unweighted_offcut * config.get('w_offcut', 0.5)
    area_based_score = -(waste_penalty + offcut_penalty)
    _update_tracker('area_based_score', -(unweighted_waste + unweighted_offcut))

    # --- Stock Fill Bonus (already normalized by area) ---
    unweighted_stock_fill = 0
    for part in layout['placed_parts']:
        if part.get('is_stock_part', False):
            deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
            if part.get('target_stock', 0) > 0 and deficit > 0:
                part_area = part['length'] * part['width']
                unweighted_stock_fill += (deficit / part['target_stock']) * (part_area / reference_area)

    _update_tracker('stock_fill_bonus', unweighted_stock_fill)
    stock_fill_bonus = unweighted_stock_fill * config.get('w_stock_fill', 1.0)
    
    has_required_parts = any(not p.get('is_optional_filler', False) for p in layout['placed_parts'])
    has_optional_parts = any(p.get('is_optional_filler', False) for p in layout['placed_parts'])
    if not has_required_parts and has_optional_parts and not is_stock_only_job:
        return -float('inf')

    # --- Quantity Bonus (normalized by max quantity) ---
    unweighted_quantity = float(sheet_info.get('quantity', 1)) if sheet_info.get('type') != 'Default' else 0.0
    normalized_quantity = unweighted_quantity / max_offcut_quantity if max_offcut_quantity > 0 else 0.0
    _update_tracker('quantity_bonus', normalized_quantity)
    quantity_bonus = normalized_quantity * config.get('w_quantity', 0.1)

    # --- Cuts Penalty (normalized by expected max cuts) ---
    unweighted_cuts = float(len(layout['placed_parts']))
    expected_max_cuts = config.get('expected_max_cuts_per_sheet', 20.0)
    normalized_cuts = unweighted_cuts / expected_max_cuts if expected_max_cuts > 0 else 0.0
    _update_tracker('cuts_penalty', normalized_cuts)
    cuts_penalty = normalized_cuts * config.get('w_cuts', 0.2)

    # --- Priority Bonus (already normalized as 0 or 1) ---
    unweighted_priority = 0.0
    if sheet_info.get('type') == 'Offcut':
        offcut_id_str = str(sheet_info.get('id', '')).replace('offcut_', '')
        if offcut_id_str in config.get('prioritized_offcuts', []):
            unweighted_priority = 1.0
    _update_tracker('priority_bonus', unweighted_priority)
    priority_bonus = unweighted_priority * config.get('w_priority_offcut', 10.0)
    
    score = area_based_score + stock_fill_bonus + quantity_bonus - cuts_penalty + priority_bonus
    _update_tracker('total_layout_score', score)
    
    return score


# --- OPTIMIZED: This function is now JIT-compiled by Numba for a small speedup ---
@numba.jit(nopython=True)
def _get_split_penalty(rect1_len: float, rect1_wid: float, rect2_len: float, rect2_wid: float) -> float:
    """Calculates a penalty for splitting a rectangle. Optimized with Numba."""
    
    # Inner function for ratio penalty calculation
    def get_ratio_penalty(r_len: float, r_wid: float) -> float:
        min_dim = min(r_len, r_wid)
        return max(r_len, r_wid) / min_dim if min_dim > 0 else 0.0
        
    return get_ratio_penalty(rect1_len, rect1_wid) + get_ratio_penalty(rect2_len, rect2_wid)


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
        
        # --- MODIFIED: Convert list of dicts to NumPy array for the JIT-compiled function ---
        if not empty_rects:
            continue
        empty_rects_np = np.array([[r['length'], r['width']] for r in empty_rects], dtype=np.float64)

        for part_l, part_w, rotated in orientations:
            # --- MODIFIED: Call the optimized find_best_fit function ---
            rect_index = find_best_fit(part_l, part_w, empty_rects_np)
            if rect_index != -1:
                best_fit['rect_index'] = rect_index
                best_fit['orientation'] = (part_l, part_w, rotated)
                break 
        
        if best_fit['rect_index'] != -1:
            part_l, part_w, rotated = best_fit['orientation']
            
            rect_idx = best_fit['rect_index']
            rect = empty_rects[rect_idx]
            
            # --- MODIFIED: More efficient swap and pop for list removal ---
            empty_rects[rect_idx] = empty_rects[-1]
            empty_rects.pop()

            placed_part = part.copy()
            placed_part.update({'rotated': rotated, 'x': rect['x'], 'y': rect['y']})
            placed_parts.append(placed_part)

            rect_above = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': rect['width']}
            rect_right_of_part = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': part_l, 'width': rect['width'] - part_w - kerf}
            rect_right = {'x': rect['x'] + part_w + kerf, 'y': rect['y'], 'length': rect['length'], 'width': rect['width'] - part_w - kerf}
            rect_above_part = {'x': rect['x'], 'y': rect['y'] + part_l + kerf, 'length': rect['length'] - part_l - kerf, 'width': part_w}
            
            # --- MODIFIED: Call the optimized _get_split_penalty function ---
            penalty1 = _get_split_penalty(rect_above['length'], rect_above['width'], rect_right_of_part['length'], rect_right_of_part['width'])
            penalty2 = _get_split_penalty(rect_right['length'], rect_right['width'], rect_above_part['length'], rect_above_part['width'])

            if penalty1 <= penalty2:
                if rect_above['length'] > 0 and rect_above['width'] > 0: empty_rects.append(rect_above)
                if rect_right_of_part['length'] > 0 and rect_right_of_part['width'] > 0: empty_rects.append(rect_right_of_part)
            else:
                if rect_right['length'] > 0 and rect_right['width'] > 0: empty_rects.append(rect_right)
                if rect_above_part['length'] > 0 and rect_above_part['width'] > 0: empty_rects.append(rect_above_part)
                
    return placed_parts, empty_rects


def run_single_sheet_optimization(sheet_info: Dict, required_parts: List[Part], config: Dict, 
                                  reference_area: float, max_offcut_quantity: float, 
                                  optional_stock_parts: Optional[List[Part]] = None,
                                  tracker: Optional[Dict] = None) -> Dict:
    """
    Finds the best layout for a single sheet.
    """
    kerf = config['kerf']
    allow_rotation = config['allow_rotation']
    time_limit = 0.1 
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
    best_score = calculate_layout_score(best_layout, sheet_info, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job, tracker)

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
            candidate_score = calculate_layout_score(candidate_layout, sheet_info, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job, tracker)

            if candidate_score > best_score:
                best_score = candidate_score
                best_layout = candidate_layout
            iterations += 1
    
    return best_layout

def _calculate_bound(remaining_parts: list, current_score: float, config: dict, reference_area: float) -> float:
    """Calculates a lower bound on the final score for a partial solution."""
    if not remaining_parts:
        return current_score
    
    remaining_area = sum(p['length'] * p['width'] for p in remaining_parts)
    min_additional_sheets = math.ceil(remaining_area / reference_area) if reference_area > 0 else float('inf')
    
    future_penalty = min_additional_sheets * config.get('w_sheets', 1.0)
    
    return current_score - future_penalty

def optimize_multiple_sheets(parts_to_cut: List[Part], stock_sheets: List[Dict], config: Dict, 
                             optional_stock_parts: Optional[List[Part]] = None, 
                             is_stock_only_job: bool = False,
                             progress_callback=None,
                             max_offcut_quantity: float = 1.0) -> Tuple[List[Dict], List[Part], float]:
    """
    Optimizes cutting stock using a time-limited Branch and Bound search.
    """
    start_time = time.time()
    time_limit = config.get('search_time_limit', 1.0)
    branching_factor = config.get('branching_factor', 1)
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    last_update_time = start_time
    
    score_component_tracker = {}

    def _track_final_penalty(component_name: str, value: float):
        """Helper to update stats for final score components."""
        stats = score_component_tracker.setdefault(component_name, {'min': float('inf'), 'max': -float('inf'), 'sum': 0, 'count': 0})
        stats['min'] = min(stats['min'], value)
        stats['max'] = max(stats['max'], value)
        stats['sum'] += value
        stats['count'] += 1

    # --- Initial State Setup ---
    part_id_counter = 0
    initial_parts_dict: Dict[int, Part] = {}
    primary_parts_list = parts_to_cut if not is_stock_only_job else (optional_stock_parts or [])
    
    for part in primary_parts_list:
        qty = int(part.get('quantity', 1))
        for _ in range(qty):
            p_copy = part.copy()
            p_copy['part_id'] = part_id_counter
            initial_parts_dict[part_id_counter] = p_copy
            part_id_counter += 1

    total_initial_parts = len(initial_parts_dict)

    optional_parts_list = []
    if not is_stock_only_job and optional_stock_parts:
        for part in optional_stock_parts:
            deficit = int(part.get('target_stock', 1) - part.get('current_stock', 0))
            for _ in range(deficit):
                p_copy = part.copy()
                p_copy['part_id'] = part_id_counter
                optional_parts_list.append(p_copy)
                part_id_counter += 1

    initial_bound = -_calculate_bound(list(initial_parts_dict.values()), 0, config, reference_area)
    
    node_counter = 0
    pq = [ (initial_bound, 0.0, node_counter, initial_parts_dict, [], frozenset()) ]
    node_counter += 1
    
    best_solution = ([], list(initial_parts_dict.values()))
    best_score = -float('inf')

    # --- Branch and Bound Loop ---
    while pq and (time_limit <= 0 or (time.time() - start_time) < time_limit):
        _, current_score, _, current_parts, used_layouts, used_signatures = heapq.heappop(pq)
        
        current_time = time.time()
        if progress_callback and (current_time - last_update_time > 0.1):
            part_progress = ((total_initial_parts - len(current_parts)) / total_initial_parts) * 100 if total_initial_parts > 0 else 100
            progress_callback({
                "part_progress": int(part_progress), "elapsed_time": current_time - start_time,
                "time_limit": time_limit, "nodes_explored": node_counter,
                "current_best_score": best_score if best_score > -float('inf') else None,
                "sheets_in_best_solution": len(best_solution[0]) if best_solution[0] else None
            })
            last_update_time = current_time

        if -_calculate_bound(list(current_parts.values()), current_score, config, reference_area) < best_score:
            continue

        remaining_primary_parts = list(current_parts.values())
        unique_sheets = {f"{s['length']}x{s['width']}": s for s in stock_sheets}.values()

        for sheet_tpl in unique_sheets:
            for _ in range(branching_factor):
                layout_attempt = run_single_sheet_optimization(
                    sheet_tpl, remaining_primary_parts, config, reference_area, max_offcut_quantity, optional_parts_list, tracker=score_component_tracker
                )
                
                placed_part_ids = {p['part_id'] for p in layout_attempt['placed_parts'] if p.get('part_id') in current_parts}
                if not placed_part_ids:
                    continue

                child_parts = {pid: p for pid, p in current_parts.items() if pid not in placed_part_ids}
                child_layouts = used_layouts + [layout_attempt]
                
                layout_sig = get_layout_signature(layout_attempt, required_only=False)
                child_signatures = used_signatures.union({layout_sig})

                layout_score = calculate_layout_score(layout_attempt, sheet_tpl, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job, tracker=score_component_tracker)
                child_score = current_score + layout_score
                
                if not child_parts:
                    # Normalize against the number of parts to make the penalty scale-invariant
                    normalized_sheets_penalty = len(child_layouts) / total_initial_parts if total_initial_parts > 0 else 0
                    # Normalize against the number of sheets to penalize complexity relative to solution size
                    normalized_layouts_penalty = len(child_signatures) / len(child_layouts) if len(child_layouts) > 0 else 0

                    sheets_penalty_val = config.get('w_sheets', 1.0) * normalized_sheets_penalty
                    layouts_penalty_val = config.get('w_unique_layouts', 0.5) * normalized_layouts_penalty
                    
                    _track_final_penalty('sheets_penalty', normalized_sheets_penalty)
                    _track_final_penalty('layouts_penalty', normalized_layouts_penalty)

                    final_score = child_score - sheets_penalty_val - layouts_penalty_val
                    if final_score > best_score:
                        best_score = final_score
                        best_solution = (child_layouts, [])
                else:
                    child_bound = _calculate_bound(list(child_parts.values()), child_score, config, reference_area)
                    if -child_bound > best_score:
                        heapq.heappush(pq, (-child_bound, child_score, node_counter, child_parts, child_layouts, child_signatures))
                        node_counter += 1
    
    if progress_callback:
        progress_callback({
            "part_progress": 100, "elapsed_time": time.time() - start_time,
            "time_limit": time_limit, "nodes_explored": node_counter,
            "current_best_score": best_score if best_score > -float('inf') else None,
            "sheets_in_best_solution": len(best_solution[0]) if best_solution[0] else None
        })

    # --- Print the analysis of score components ---
    print("\n--- Optimization Score Component Analysis ---")
    if not score_component_tracker:
        print("No scoring data was collected.")
    else:
        for component, stats in sorted(score_component_tracker.items()):
            if stats['count'] > 0:
                average = stats['sum'] / stats['count']
                print(f"Component: {component:<20} | Min: {stats['min']:>10.4f} | Max: {stats['max']:>10.4f} | Avg: {average:>10.4f}")
            else:
                print(f"Component: {component:<20} | No data collected.")
    print("-------------------------------------------\n")

    final_layouts, remaining_parts = best_solution
    return final_layouts, remaining_parts, best_score