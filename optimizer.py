import time
import random
import heapq
import math
import threading
from typing import List, Dict, Any, Tuple, Optional
from collections import Counter
import numba
import numpy as np


# A type alias for a part dictionary for clarity
Part = Dict[str, Any]
Rect = Dict[str, float]


# --- DEBUGGING CLASS (Easily Removable) ---
DEBUG_SCORES = True # Set to False to disable logging

class ScoreDebugger:
    def __init__(self):
        self.true_components = {
            'total_score': [], 'area_score': [], 'stock_bonus': [], 'quantity_bonus': [],
            'priority_bonus': [], 'sheets_penalty': [], 'layouts_penalty': [],
            'cuts_penalty': []
        }
        self.predicted_components = {
            'total_score': [], 'area_score': [], 'stock_bonus': [], 'quantity_bonus': [],
            'priority_bonus': [], 'sheets_penalty': [], 'layouts_penalty': [],
            'cuts_penalty': []
        }

    def _log_component(self, store, key, value):
        if value is not None:
            store[key].append(value)

    def log_true_scores(self, components: Dict[str, float]):
        total_score = sum(v for v in components.values() if v is not None)
        self._log_component(self.true_components, 'total_score', total_score)
        for key, value in components.items():
            self._log_component(self.true_components, key, value)

    def log_predicted_scores(self, components: Dict[str, float]):
        total_score = sum(v for v in components.values() if v is not None)
        self._log_component(self.predicted_components, 'total_score', total_score)
        for key, value in components.items():
            self._log_component(self.predicted_components, key, value)

    def _format_stats(self, key: str, values: List[float]) -> str:
        if not values:
            return f"  - {key:<15}: N/A"
        np_values = np.array(values)
        return (f"  - {key:<15}: "
                f"Min={np.min(np_values):>8.3f}, "
                f"Avg={np.mean(np_values):>8.3f}, "
                f"Max={np.max(np_values):>8.3f}, "
                f"Count={len(values)}")

    def print_stats(self, title: str = "Periodic Score Component Stats"):
        print(f"\n--- {title} ---")
        print("True (Actual) Scores:")
        for key, values in self.true_components.items():
            print(self._format_stats(key, values))
        print("\nPredicted (Bound) Scores:")
        for key, values in self.predicted_components.items():
            print(self._format_stats(key, values))
        print("--- End of Report ---\n")

    def log_context_switch(self, bound: float, score: float, depth: int, remaining_parts: int, node_id: int):
        """Logs the details of the new most promising node being explored."""
        # print(f"[DEBUG] Switching to Node {node_id}: Predicted Score={bound:.3f}, Current Score={score:.3f}, Depth={depth}, Parts Left={remaining_parts}")

# --- End of Debugging Class ---

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
                           is_stock_only_job: bool = False,
                           remaining_parts: Optional[List[Part]] = None,
                           live_stock_counts: Optional[Counter] = None,
                           debugger: Optional['ScoreDebugger'] = None) -> float:
    """Calculates a score for a given sheet layout based on its own merits (waste, offcuts, etc.)."""
    sheet_area = sheet_info['length'] * sheet_info['width']
    if sheet_area == 0 or reference_area == 0:
        return -float('inf')

    used_area = layout['total_placed_area']
    usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
    offcut_area = sum(o['length'] * o['width'] for o in usable_offcuts)
    
    area_based_score = -(((sheet_area - used_area - offcut_area) / reference_area * config.get('w_waste', 1.0)) +
                         ((offcut_area / reference_area) * config.get('w_offcut', 0.5)))

    is_lightly_used = len(layout['placed_parts']) <= 2
    if is_lightly_used and remaining_parts:
        can_fit_more = any(p['length'] * p['width'] < (sheet_area - used_area) for p in remaining_parts)
        if can_fit_more:
            area_based_score = 0.0

    has_required_parts = any(not p.get('is_optional_filler', False) for p in layout['placed_parts'])
    if not has_required_parts and any(p.get('is_optional_filler', False) for p in layout['placed_parts']) and not is_stock_only_job:
        return -float('inf')

    quantity_bonus = 0.0
    if sheet_info.get('type') != 'Default' and max_offcut_quantity > 0:
        quantity_bonus = (float(sheet_info.get('quantity', 1)) / max_offcut_quantity) * config.get('w_quantity', 0.1)

    priority_bonus = 0.0
    if sheet_info.get('type') == 'Offcut':
        offcut_id_str = str(sheet_info.get('id', '')).replace('offcut_', '')
        if offcut_id_str in config.get('prioritized_offcuts', []):
            priority_bonus = config.get('w_priority_offcut', 10.0)
    
    return area_based_score + quantity_bonus + priority_bonus


@numba.jit(nopython=True)
def _get_split_penalty(rect1_len: float, rect1_wid: float, rect2_len: float, rect2_wid: float) -> float:
    """Calculates a penalty for splitting a rectangle. Optimized with Numba."""
    
    def get_ratio_penalty(r_len: float, r_wid: float) -> float:
        min_dim = min(r_len, r_wid)
        return max(r_len, r_wid) / min_dim if min_dim > 0 else 0.0
        
    return get_ratio_penalty(rect1_len, rect1_wid) + get_ratio_penalty(rect2_len, rect2_wid)


@numba.jit(nopython=True)
def _place_parts_in_rects_jit(
    parts_to_place_np: np.ndarray,
    initial_rects_np: np.ndarray,
    kerf: float,
    allow_rotation: bool,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Core part placement algorithm, fully optimized with Numba to work on NumPy arrays.
    - parts_to_place_np columns: part_id, length, width
    - rects_np columns: x, y, length, width
    - placed_parts_np columns: part_id, x, y, length, width, rotated_flag
    """
    # Pre-allocate a generous array for empty rectangles.
    # This avoids resizing, which is slow.
    empty_rects = np.zeros((initial_rects_np.shape[0] + parts_to_place_np.shape[0] * 2, 4), dtype=np.float64)
    empty_rects[:initial_rects_np.shape[0], :] = initial_rects_np
    num_empty_rects = initial_rects_np.shape[0]

    # Pre-allocate for placed parts
    placed_parts = np.zeros((parts_to_place_np.shape[0], 6), dtype=np.float64)
    num_placed_parts = 0

    for i in range(parts_to_place_np.shape[0]):
        part_id, part_length, part_width = parts_to_place_np[i]

        best_rect_idx = -1
        is_rotated = False

        # --- Find Best Fit ---
        min_short_side_fit = float('inf')

        # Try without rotation
        for j in range(num_empty_rects):
            rect_len, rect_wid = empty_rects[j, 2], empty_rects[j, 3]
            if part_length <= rect_len and part_width <= rect_wid:
                short_side_fit = min(rect_len - part_length, rect_wid - part_width)
                if short_side_fit < min_short_side_fit:
                    min_short_side_fit = short_side_fit
                    best_rect_idx = j
                    is_rotated = False

        # Try with rotation if allowed
        if allow_rotation and part_length != part_width:
            for j in range(num_empty_rects):
                rect_len, rect_wid = empty_rects[j, 2], empty_rects[j, 3]
                if part_width <= rect_len and part_length <= rect_wid:
                    short_side_fit = min(rect_len - part_width, rect_wid - part_length)
                    if short_side_fit < min_short_side_fit:
                        min_short_side_fit = short_side_fit
                        best_rect_idx = j
                        is_rotated = True
        
        if best_rect_idx != -1:
            part_l = part_width if is_rotated else part_length
            part_w = part_length if is_rotated else part_width
            
            # Get the chosen rectangle
            rect_x, rect_y, rect_len, rect_wid = empty_rects[best_rect_idx]

            # --- Update placed_parts array ---
            placed_parts[num_placed_parts, 0] = part_id
            placed_parts[num_placed_parts, 1] = rect_x
            placed_parts[num_placed_parts, 2] = rect_y
            placed_parts[num_placed_parts, 3] = part_length # Store original dims
            placed_parts[num_placed_parts, 4] = part_width
            placed_parts[num_placed_parts, 5] = 1.0 if is_rotated else 0.0
            num_placed_parts += 1

            # --- Update empty_rects array ---
            # "Pop" the used rectangle by swapping with the last one
            empty_rects[best_rect_idx] = empty_rects[num_empty_rects - 1]
            num_empty_rects -= 1

            # Define the two possible split strategies
            rect_above_len = rect_len - part_l - kerf
            rect_right_of_part_wid = rect_wid - part_w - kerf
            
            rect_right_wid = rect_wid - part_w - kerf
            rect_above_part_len = rect_len - part_l - kerf

            # --- Simplified Split Decision (Guillotine) ---
            # Add the two new rectangles from the split
            if rect_above_len > 1e-6 and rect_wid > 1e-6:
                empty_rects[num_empty_rects, 0] = rect_x
                empty_rects[num_empty_rects, 1] = rect_y + part_l + kerf
                empty_rects[num_empty_rects, 2] = rect_above_len
                empty_rects[num_empty_rects, 3] = rect_wid
                num_empty_rects += 1
            
            if part_l > 1e-6 and rect_right_of_part_wid > 1e-6:
                empty_rects[num_empty_rects, 0] = rect_x + part_w + kerf
                empty_rects[num_empty_rects, 1] = rect_y
                empty_rects[num_empty_rects, 2] = part_l
                empty_rects[num_empty_rects, 3] = rect_right_of_part_wid
                num_empty_rects += 1

    # Return only the active portions of the arrays
    return placed_parts[:num_placed_parts], empty_rects[:num_empty_rects]


def _place_parts_in_rects(parts_to_place: List[Part], initial_rects: List[Rect], 
                          kerf: float, allow_rotation: bool) -> Tuple[List[Part], List[Rect]]:
    """
    Wrapper for the JIT-compiled placement function.
    Handles conversion between Python dicts and NumPy arrays.
    """
    if not parts_to_place or not initial_rects:
        return [], initial_rects

    # Convert inputs to NumPy arrays
    parts_np = np.array([[p['part_id'], p['length'], p['width']] for p in parts_to_place], dtype=np.float64)
    rects_np = np.array([[r['x'], r['y'], r['length'], r['width']] for r in initial_rects], dtype=np.float64)

    # Call the fast JIT function
    placed_parts_np, final_rects_np = _place_parts_in_rects_jit(parts_np, rects_np, kerf, allow_rotation)

    # Convert results back to Python dicts
    # This is done only once at the end, not in a hot loop
    part_map = {p['part_id']: p for p in parts_to_place}
    placed_parts_list = []
    for i in range(placed_parts_np.shape[0]):
        part_id, x, y, length, width, rotated_flag = placed_parts_np[i]
        original_part = part_map[part_id]
        
        placed_part = original_part.copy()
        placed_part.update({
            'x': x,
            'y': y,
            'rotated': bool(rotated_flag)
        })
        placed_parts_list.append(placed_part)

    final_rects_list = [
        {'x': r[0], 'y': r[1], 'length': r[2], 'width': r[3]}
        for r in final_rects_np
    ]
    
    return placed_parts_list, final_rects_list


def run_single_sheet_optimization(sheet_info: Dict, required_parts: List[Part], config: Dict, 
                                  reference_area: float, max_offcut_quantity: float, 
                                  optional_stock_parts: Optional[List[Part]] = None,
                                  live_stock_counts: Optional[Counter] = None,
                                  debugger: Optional['ScoreDebugger'] = None,
                                  cancellation_event: Optional['threading.Event'] = None) -> Dict:
    """
    Finds the best layout for a single sheet.
    """
    kerf = config['kerf']
    allow_rotation = config['allow_rotation']
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
    best_score = calculate_layout_score(best_layout, sheet_info, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job=is_stock_only_job, live_stock_counts=live_stock_counts, debugger=debugger)

    if optional_stock_parts and remaining_rects_after_job:
        # PERF: Replace the iterative random sampling with a single, deterministic greedy pass.
        # This is much faster as it avoids the expensive random.sample and reduces
        # the number of calls to the JIT-compiled _place_parts_in_rects function.
        # Sort parts by area to prioritize placing larger, more difficult ones first.
        fittable_parts = sorted(optional_stock_parts, key=lambda p: p['length'] * p['width'], reverse=True)

        placed_stock_parts, final_rects = _place_parts_in_rects(
            fittable_parts, remaining_rects_after_job, kerf, allow_rotation
        )

        if placed_stock_parts:
            candidate_placed_parts = placed_job_parts + placed_stock_parts
            candidate_layout = {
                "placed_parts": candidate_placed_parts, "offcuts": final_rects,
                "sheet_info": sheet_info,
                "total_placed_area": sum(p['length'] * p['width'] for p in candidate_placed_parts)
            }
            candidate_score = calculate_layout_score(candidate_layout, sheet_info, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job=is_stock_only_job, live_stock_counts=live_stock_counts, debugger=debugger)

            if candidate_score > best_score:
                best_score = candidate_score
                best_layout = candidate_layout
    
    return best_layout

@numba.jit(nopython=True)
def _count_guillotine_cuts_jit(placed_parts_data: np.ndarray, sheet_width: float, sheet_length: float) -> int:
    """
    Numba-optimized core function to count guillotine cuts from NumPy array data.
    """
    if placed_parts_data.shape[0] == 0:
        return 0

    TOLERANCE = 1e-6
    
    # Pre-allocate arrays for all x and y coordinates
    all_x_coords = np.empty(placed_parts_data.shape[0] * 2, dtype=np.float64)
    all_y_coords = np.empty(placed_parts_data.shape[0] * 2, dtype=np.float64)

    for i in range(placed_parts_data.shape[0]):
        part_x, part_y, part_len, part_wid, rotated = placed_parts_data[i]
        
        p_len = part_wid if rotated else part_len
        p_wid = part_len if rotated else part_wid
        
        all_x_coords[i*2] = part_x
        all_x_coords[i*2 + 1] = part_x + p_wid
        all_y_coords[i*2] = part_y
        all_y_coords[i*2 + 1] = part_y + p_len

    # --- Count unique internal X cuts ---
    np.sort(all_x_coords)
    unique_x_cuts = 0
    if all_x_coords.shape[0] > 0:
        last_x = all_x_coords[0]
        if last_x > TOLERANCE and last_x < sheet_width - TOLERANCE:
            unique_x_cuts += 1
        for i in range(1, all_x_coords.shape[0]):
            current_x = all_x_coords[i]
            if current_x - last_x > TOLERANCE:
                if current_x > TOLERANCE and current_x < sheet_width - TOLERANCE:
                    unique_x_cuts += 1
                last_x = current_x
    
    # --- Count unique internal Y cuts ---
    np.sort(all_y_coords)
    unique_y_cuts = 0
    if all_y_coords.shape[0] > 0:
        last_y = all_y_coords[0]
        if last_y > TOLERANCE and last_y < sheet_length - TOLERANCE:
            unique_y_cuts += 1
        for i in range(1, all_y_coords.shape[0]):
            current_y = all_y_coords[i]
            if current_y - last_y > TOLERANCE:
                if current_y > TOLERANCE and current_y < sheet_length - TOLERANCE:
                    unique_y_cuts += 1
                last_y = current_y

    return unique_x_cuts + unique_y_cuts

def _count_guillotine_cuts(layout: Dict[str, Any]) -> int:
    """
    Wrapper function to count guillotine cuts. Extracts data for the jitted core.
    """
    placed_parts = layout.get('placed_parts', [])
    if not placed_parts:
        return 0
        
    # Prepare data in a NumPy array format for Numba
    # Columns: x, y, length, width, rotated
    placed_parts_data = np.array([
        [
            p['x'], p['y'], p['length'], p['width'], 
            1.0 if p.get('rotated') else 0.0
        ] 
        for p in placed_parts
    ], dtype=np.float64)
    
    sheet_info = layout['sheet_info']
    return _count_guillotine_cuts_jit(placed_parts_data, sheet_info['width'], sheet_info['length'])


@numba.jit(nopython=True)
def _calculate_made_counts_jit(refs: np.ndarray, unique_refs: np.ndarray, live_counts_arr: np.ndarray) -> np.ndarray:
    """Calculates the cumulative count of each part reference, optimized with Numba."""
    num_parts = refs.shape[0]
    made_counts = np.zeros(num_parts, dtype=np.float64)
    
    # Create a temporary count array indexed the same as unique_refs.
    # This acts as our hash map in the Numba world.
    temp_counts = live_counts_arr.copy()

    for i in range(num_parts):
        ref = refs[i]
        # Find the index of the current part's ref in the unique_refs array.
        # np.searchsorted is very fast on sorted arrays.
        idx = np.searchsorted(unique_refs, ref)
        
        # This check is for safety, in case a ref is not in unique_refs, though it should be.
        if idx < len(unique_refs) and unique_refs[idx] == ref:
            made_counts[i] = temp_counts[idx]
            temp_counts[idx] += 1
            
    return made_counts


def _calculate_optimistic_stock_bonus(remaining_parts: list, optional_parts_data: np.ndarray, 
                                     live_stock_counts: Counter, config: Dict[str, Any], 
                                     reference_area: float) -> float:
    """
    Calculates a realistic upper bound for the stock fill bonus using vectorized NumPy operations.
    """
    is_mixed_job = any(not p.get('is_stock_part') for p in remaining_parts)
    if optional_parts_data.shape[0] == 0 or (not remaining_parts and is_mixed_job):
        return 0.0

    # Be more generous about future available space for stock parts by assuming a more
    # moderate overall packing density (e.g., 75%) for the estimate.
    packing_density_for_stock = 0.75 
    required_parts_area = sum(p['length'] * p['width'] for p in remaining_parts)
    total_future_area = (required_parts_area / packing_density_for_stock) if packing_density_for_stock > 0 else 0

    optimistic_available_area = max(0, total_future_area - required_parts_area)

    if optimistic_available_area <= 0:
        return 0.0

    w_stock_fill = config.get('w_stock_fill', 1.0)
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)
    
    # --- Vectorized Calculation ---
    # PERF: The expensive array conversion has been moved outside this hot function.
    part_data = optional_parts_data

    # Quickly count how many of each part we have already made
    refs = part_data[:, 0]
    unique_refs, counts = np.unique(refs, return_counts=True)
    live_counts_arr = np.array([live_stock_counts.get(ref_hash, 0) for ref_hash in unique_refs], dtype=np.float64)
    
    # Calculate how many parts of each type have been made so far in the simulation
    # This uses a fast, JIT-compiled function to replace a very slow Python loop with dict lookups.
    made_counts = _calculate_made_counts_jit(refs, unique_refs, live_counts_arr)

    # Vectorized bonus calculation
    target_stock = part_data[:, 1]
    current_stock = part_data[:, 2]
    part_areas = part_data[:, 3]
    
    initial_deficit = target_stock - current_stock
    live_deficit = initial_deficit - made_counts
    live_deficit[live_deficit < 0] = 0 # Cannot have negative deficit
    target_stock[target_stock <= 0] = 1.0 # Avoid division by zero
    
    potential_bonus = (live_deficit / target_stock) * flat_bonus_unit * w_stock_fill
    
    # Calculate bonus density (bonus per unit of area)
    # Add a small epsilon to area to prevent division by zero for zero-area parts
    bonus_density = potential_bonus / (part_areas + 1e-9)

    # --- Knapsack-like Filling ---
    # Sort candidates by bonus density in descending order
    sorted_indices = np.argsort(bonus_density)[::-1]
    
    sorted_bonuses = potential_bonus[sorted_indices]
    sorted_areas = part_areas[sorted_indices]
    
    # Use cumulative sum to find how many parts fit
    cumulative_areas = np.cumsum(sorted_areas)
    
    # Find index of the last part that fits completely
    # searchsorted is very fast
    full_fit_idx = np.searchsorted(cumulative_areas, optimistic_available_area, side='right')
    
    predicted_bonus = np.sum(sorted_bonuses[:full_fit_idx])
    
    # Add the fractional bonus for the one part that might fit partially
    if full_fit_idx < part_data.shape[0]:
        area_filled = cumulative_areas[full_fit_idx - 1] if full_fit_idx > 0 else 0
        remaining_area = optimistic_available_area - area_filled
        fractional_fit = remaining_area / sorted_areas[full_fit_idx]
        predicted_bonus += sorted_bonuses[full_fit_idx] * fractional_fit

    return predicted_bonus


def _get_min_sheets_for_parts(remaining_parts: list, reference_area: float) -> int:
    """ Calculates the minimum number of sheets required."""
    remaining_area = sum(p['length'] * p['width'] for p in remaining_parts)
    return math.ceil(remaining_area / reference_area) if reference_area > 0 else int('inf')


def _predict_number_of_new_sheet_layouts(remaining_parts: list, used_signatures: frozenset, 
                                        reference_area: float, config: Dict) -> int:
    """
    Predicts how many new, unique layouts will be required.
    """
    remaining_demand = Counter()
    dim_to_part_map = {}
    for part in remaining_parts:
        p_len, p_wid = (part['length'], part['width'])
        dim_tuple = tuple(sorted((f"{p_len:.3f}", f"{p_wid:.3f}")))
        remaining_demand[dim_tuple] += 1
        if dim_tuple not in dim_to_part_map:
            dim_to_part_map[dim_tuple] = part

    layout_production = {}
    for sig in used_signatures:
        production_counter = Counter()
        for part_str in sig.split('|'):
            if part_str.startswith('part_'):
                dims_str = part_str.split('_')[2]
                p_len_str, p_wid_str = dims_str.split('x')
                dim_tuple = tuple(sorted((p_len_str, p_wid_str)))
                production_counter[dim_tuple] += 1
        layout_production[sig] = production_counter

    fulfilled_parts = Counter()
    for sig, production in layout_production.items():
        if not production: continue
        usability = float('inf')
        for dim, count_produced in production.items():
            if remaining_demand[dim] > 0:
                usability = min(usability, remaining_demand[dim] // count_produced)
        if usability == float('inf') or usability == 0: continue
        for dim, count_produced in production.items():
            fulfilled_parts[dim] += usability * count_produced

    unique_problem_parts = []
    for dim, total_needed in remaining_demand.items():
        if total_needed > fulfilled_parts.get(dim, 0):
            unique_problem_parts.append(dim_to_part_map[dim])

    if unique_problem_parts:
        parts_to_simulate = sorted(unique_problem_parts, key=lambda p: p['length'] * p['width'], reverse=True)
        layouts_needed = 0
        
        while parts_to_simulate:
            layouts_needed += 1
            sheet_sim = {'length': config['default_length'], 'width': config['default_width']}
            initial_rects = [{'x': 0, 'y': 0, 'length': sheet_sim['length'], 'width': sheet_sim['width']}]
            
            placed_in_sim, _ = _place_parts_in_rects(
                parts_to_simulate, initial_rects, config['kerf'], config['allow_rotation']
            )
            
            if not placed_in_sim:
                return layouts_needed + len(parts_to_simulate)

            placed_dims = {tuple(sorted((f"{p['length']:.3f}", f"{p['width']:.3f}"))) for p in placed_in_sim}
            parts_to_simulate = [p for p in parts_to_simulate if tuple(sorted((f"{p['length']:.3f}", f"{p['width']:.3f}"))) not in placed_dims]
            
        return layouts_needed
    
    return 0


def _calculate_bound(remaining_parts: list, current_score: float, config: Dict[str, Any], reference_area: float, used_signatures: frozenset,
                     max_values: Dict[str, float],
                     optional_parts_data_for_bound: np.ndarray, live_stock_counts: Counter,
                     learned_rates: Dict[str, float],
                     debugger: Optional['ScoreDebugger'] = None) -> float:
    """
    Calculates a sophisticated upper bound using data-driven rates learned from a solution.
    """
    if not remaining_parts:
        return current_score

    remaining_area = sum(p['length'] * p['width'] for p in remaining_parts)
    
    packing_density = learned_rates.get('packing_density', 0.9)
    if packing_density == 0: packing_density = 0.9
    predicted_sheets = math.ceil(remaining_area / (packing_density * reference_area)) if reference_area > 0 else float('inf')

    # Predict future waste AND offcuts based on learned rates
    predicted_total_sheet_area = predicted_sheets * reference_area
    predicted_unused_area = max(0, predicted_total_sheet_area - remaining_area)
    offcut_ratio = learned_rates.get('offcut_ratio', 0.1)
    
    predicted_offcut_area = predicted_unused_area * offcut_ratio
    predicted_waste_area = predicted_unused_area * (1 - offcut_ratio)
    
    cuts_per_part = learned_rates.get('cuts_per_part', 1.2)
    predicted_cuts = len(remaining_parts) * cuts_per_part

    # Predict future quantity_bonus based on a learned rate
    avg_quantity_bonus_per_sheet = learned_rates.get('avg_quantity_bonus_per_sheet', 0.0)
    raw_quantity_bonus = avg_quantity_bonus_per_sheet * predicted_sheets

    optimistic_new_layouts = _predict_number_of_new_sheet_layouts(remaining_parts, used_signatures, reference_area, config)
    raw_stock_bonus = _calculate_optimistic_stock_bonus(
        remaining_parts, optional_parts_data_for_bound, live_stock_counts, config, reference_area
    )

    # Calculate all score components
    area_penalty = (predicted_waste_area / max_values['waste_area']) * config.get('w_waste', 1.0)
    offcut_penalty = (predicted_offcut_area / max_values['offcut_area']) * config.get('w_offcut', 0.5)
    stock_bonus = (raw_stock_bonus / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
    quantity_bonus = (raw_quantity_bonus / max_values['quantity_bonus']) * config.get('w_quantity', 0.1)
    sheets_penalty = (predicted_sheets / max_values['sheets']) * config.get('w_sheets', 1.0)
    layouts_penalty = (optimistic_new_layouts / max_values['layouts']) * config.get('w_unique_layouts', 0.5)
    cuts_penalty = (predicted_cuts / max_values['cuts']) * config.get('w_cuts', 0.2)

    upper_bound = (current_score + stock_bonus + quantity_bonus - area_penalty - offcut_penalty - sheets_penalty - layouts_penalty - cuts_penalty)

    if debugger:
        debugger.log_predicted_scores({
            'area_score': -area_penalty - offcut_penalty,
            'stock_bonus': stock_bonus, 'quantity_bonus': quantity_bonus,
            'priority_bonus': 0.0, 'sheets_penalty': -sheets_penalty,
            'layouts_penalty': -layouts_penalty, 'cuts_penalty': -cuts_penalty
        })

    return upper_bound


def _run_greedy_seed(initial_parts_dict: Dict[int, Part], stock_sheets: List[Dict], config: Dict,
                     optional_parts_list: List[Part], is_stock_only_job: bool, reference_area: float,
                     max_offcut_quantity: float, max_values: Dict[str, float],
                     debugger: Optional['ScoreDebugger'],
                     cancellation_event: Optional['threading.Event'] = None):
    """
    Runs a fast, greedy optimization to find a good starting solution.
    """
    greedy_layouts = []
    current_parts = initial_parts_dict.copy()
    current_score = 0.0
    live_stock_counts = Counter()
    used_signatures = frozenset()
    greedy_raw_components = Counter()

    while current_parts:
        if cancellation_event and cancellation_event.is_set():
            return None, -float('inf'), Counter()
            
        candidate_actions = []
        unique_sheets = {f"{s['length']}x{s['width']}_{s.get('type', 'Default')}": s for s in stock_sheets}.values()

        for sheet_tpl in unique_sheets:
            layout = run_single_sheet_optimization(
                sheet_tpl, list(current_parts.values()), config, reference_area, max_offcut_quantity,
                optional_parts_list, live_stock_counts, None, cancellation_event
            )
            placed_ids = {p['part_id'] for p in layout['placed_parts'] if p['part_id'] in current_parts}
            if not placed_ids:
                continue
            
            sheet_info = layout['sheet_info']
            sheet_area = sheet_info['length'] * sheet_info['width']
            used_area = layout['total_placed_area']
            min_offcut_dim = config['min_offcut_dim']
            usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
            offcut_area = sum(o['length'] * o['width'] for o in usable_offcuts)
            
            raw_components = Counter()
            raw_components['waste_area'] = sheet_area - used_area - offcut_area
            raw_components['offcut_area'] = offcut_area
            raw_components['quantity_bonus'] = float(sheet_info.get('quantity', 1)) if sheet_info.get('type') != 'Default' else 0.0
            if sheet_info.get('type') == 'Offcut':
                offcut_id_str = str(sheet_info.get('id', '')).replace('offcut_', '')
                if offcut_id_str in config.get('prioritized_offcuts', []):
                    raw_components['priority_bonus'] = 1.0
            
            raw_stock_bonus = 0.0
            flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)
            temp_counts_for_layout = live_stock_counts.copy()
            for part in sorted(layout['placed_parts'], key=lambda p: p['part_id']):
                if not part.get('is_stock_part'): continue
                ref = part['internal_ref']
                initial_deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
                target_stock = part.get('target_stock', 0)
                if target_stock <= 0: continue
                count_already_made = temp_counts_for_layout.get(ref, 0)
                live_deficit = initial_deficit - count_already_made
                if live_deficit > 0:
                    raw_stock_bonus += (live_deficit / target_stock) * flat_bonus_unit
                temp_counts_for_layout[ref] += 1
            raw_components['stock_bonus'] = raw_stock_bonus
            raw_components['cuts'] = _count_guillotine_cuts(layout)

            component_deltas = {}
            component_deltas['area_score'] = - ((raw_components['waste_area'] / max_values['waste_area']) * config.get('w_waste', 1.0) + (raw_components['offcut_area'] / max_values['offcut_area']) * config.get('w_offcut', 0.5))
            component_deltas['stock_bonus'] = (raw_components['stock_bonus'] / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
            component_deltas['quantity_bonus'] = (raw_components['quantity_bonus'] / max_values['quantity_bonus']) * config.get('w_quantity', 0.1)
            component_deltas['priority_bonus'] = (raw_components['priority_bonus'] / max_values['priority_bonus']) * config.get('w_priority_offcut', 10.0)
            component_deltas['sheets_penalty'] = - (1.0 / max_values['sheets']) * config.get('w_sheets', 1.0)
            component_deltas['cuts_penalty'] = - (raw_components['cuts'] / max_values['cuts']) * config.get('w_cuts', 0.2)
            
            layout_sig = get_layout_signature(layout, required_only=False)
            if layout_sig not in used_signatures:
                component_deltas['layouts_penalty'] = - (1.0 / max_values['layouts']) * config.get('w_unique_layouts', 0.5)

            score_delta = sum(v for v in component_deltas.values() if v is not None)

            if debugger:
                debugger.log_true_scores(component_deltas)
            
            efficiency = score_delta / len(placed_ids) if placed_ids else -float('inf')
            candidate_actions.append({'layout': layout, 'efficiency': efficiency, 'placed_ids': placed_ids, 'score_delta': score_delta, 'raw_components': raw_components, 'sig': layout_sig})

        if not candidate_actions:
            break

        best_action = max(candidate_actions, key=lambda x: x['efficiency'])
        
        greedy_layouts.append(best_action['layout'])
        current_score += best_action['score_delta']
        greedy_raw_components.update(best_action['raw_components'])
        
        current_parts = {pid: p for pid, p in current_parts.items() if pid not in best_action['placed_ids']}
        used_signatures = used_signatures.union({best_action['sig']})
        for part in best_action['layout']['placed_parts']:
            if part.get('is_stock_part'):
                live_stock_counts[part['internal_ref']] += 1

    if current_parts:
        return None, -float('inf'), Counter()

    return greedy_layouts, current_score, greedy_raw_components

def _update_bounding_parameters_from_solution(solution_layouts: list, config: dict, 
                                              total_initial_parts: int, optional_parts_list: list,
                                              reference_area: float, current_max_values: dict) -> Tuple[dict, dict]:
    """
    Analyzes a complete solution to learn its performance rates and updates the
    max_values dictionary used for score normalization and bounding.
    """
    updated_max_values = current_max_values.copy()
    min_offcut_dim = config['min_offcut_dim']
    
    total_parts_in_solution = sum(len(l['placed_parts']) for l in solution_layouts)
    total_cuts_in_solution = sum(_count_guillotine_cuts(l) for l in solution_layouts)
    cuts_per_part_ratio = total_cuts_in_solution / total_parts_in_solution * 0.9 if total_parts_in_solution > 0 else 1.2

    sheet_count = len(solution_layouts)
    total_placed_area = sum(p['length'] * p['width'] for l in solution_layouts for p in l['placed_parts'])
    total_sheet_area = sheet_count * reference_area
    
    base_density = total_placed_area / total_sheet_area if total_sheet_area > 0 else 0.85
    stable_density = (base_density * 0.7) + (0.85 * 0.3)
    packing_density = min(stable_density + 0.1, 0.98)
    
    total_offcut_area = sum(o['length'] * o['width'] for l in solution_layouts for o in l['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim)
    total_unused_area = total_sheet_area - total_placed_area
    offcut_ratio = total_offcut_area / total_unused_area if total_unused_area > 0 else 0.0
    
    # Learn the average raw quantity bonus achieved per sheet
    raw_quantity_bonus = sum(float(l['sheet_info'].get('quantity', 1)) for l in solution_layouts if l['sheet_info'].get('type') != 'Default')
    avg_quantity_bonus_per_sheet = raw_quantity_bonus / sheet_count if sheet_count > 0 else 0.0

    layout_count = len({get_layout_signature(l, required_only=False) for l in solution_layouts})
    layouts_per_sheet_ratio = layout_count / sheet_count if sheet_count > 0 else 1.0

    new_learned_rates = {
        'packing_density': packing_density,
        'cuts_per_part': cuts_per_part_ratio,
        'offcut_ratio': offcut_ratio,
        'avg_quantity_bonus_per_sheet': avg_quantity_bonus_per_sheet
    }

    absolute_max_parts = total_initial_parts + len(optional_parts_list)
    projected_max_cuts = (absolute_max_parts * cuts_per_part_ratio)
    updated_max_values['cuts'] = max(current_max_values.get('cuts', 0), projected_max_cuts)
    
    total_possible_parts_area = sum(p['length'] * p['width'] for p in (optional_parts_list or []))
    if 'total_initial_parts_area' in current_max_values:
        total_possible_parts_area += current_max_values['total_initial_parts_area']
    
    projected_density = packing_density
    if projected_density > 0 and reference_area > 0:
        projected_max_sheets = math.ceil(total_possible_parts_area / (projected_density * reference_area))
        updated_max_values['sheets'] = max(current_max_values.get('sheets', 0), projected_max_sheets)

    projected_max_layouts = math.ceil(updated_max_values['sheets'] * layouts_per_sheet_ratio) + 1
    updated_max_values['layouts'] = max(current_max_values.get('layouts', 0), projected_max_layouts)

    updated_max_values['waste_area'] = max(current_max_values.get('waste_area', 0), updated_max_values['sheets'] * reference_area)
    updated_max_values['offcut_area'] = max(current_max_values.get('offcut_area', 0), updated_max_values['sheets'] * reference_area)

    return updated_max_values, new_learned_rates

def _rescore_top_nodes(pq: list, config: dict, reference_area: float, max_values: dict,
                       optional_parts_data: np.ndarray, learned_rates: dict, 
                       debugger: Optional['ScoreDebugger']) -> list:
    """
    Implements an adaptive, iterative re-scoring of the top nodes in the priority queue
    until the top of the queue stabilizes.
    """
    if not pq:
        return []
    
    already_rescored_ids = set()
    MAX_RECALC = 50000
    STABILITY_THRESHOLD = 85.0 # Percentage

    for i in range(10): # Max 10 passes to prevent infinite loops
        n_to_check = int(max(100, min(len(pq) * 0.5, 2000)))
        if n_to_check >= len(pq): n_to_check = len(pq)

        current_top_nodes = heapq.nsmallest(n_to_check, pq)
        original_top_ids = {node[2] for node in current_top_nodes} # node[2] is the node_id
        
        nodes_to_rescore_this_pass = {node[2]: node for node in current_top_nodes if node[2] not in already_rescored_ids}

        if not nodes_to_rescore_this_pass or len(already_rescored_ids) >= MAX_RECALC:
            if not nodes_to_rescore_this_pass:
                print(f"[DEBUG] Re-score stabilized after {i} passes.")
            break

        new_pq_list = []
        for node in pq:
            node_id = node[2]
            if node_id in nodes_to_rescore_this_pass:
                _stale_bound, score, _id, parts, layouts, sigs, stock_counts = node
                new_bound = _calculate_bound(
                    list(parts.values()), score, config, reference_area, sigs,
                    max_values, optional_parts_data, stock_counts, learned_rates, debugger
                )
                new_pq_list.append((-new_bound, score, _id, parts, layouts, sigs, stock_counts))
                already_rescored_ids.add(_id)
            else:
                new_pq_list.append(node)
        
        heapq.heapify(new_pq_list)
        pq = new_pq_list
        
        new_top_nodes = heapq.nsmallest(n_to_check, pq)
        new_top_ids = {node[2] for node in new_top_nodes}
        
        overlap_count = len(original_top_ids.intersection(new_top_ids))
        overlap_percentage = (overlap_count / n_to_check) * 100 if n_to_check > 0 else 100
        
        print(f"[DEBUG] Re-score Pass {i+1}: Processed {len(nodes_to_rescore_this_pass)} new nodes. Total processed: {len(already_rescored_ids)}. Top nodes overlap: {overlap_percentage:.1f}%.")

        if overlap_percentage >= STABILITY_THRESHOLD:
            print(f"[DEBUG] Re-score stabilized after {i+1} passes.")
            break
    
    if len(already_rescored_ids) >= MAX_RECALC:
        print(f"[DEBUG] Re-score pass limit ({MAX_RECALC} nodes) reached.")

    return pq


def optimize_multiple_sheets(parts_to_cut: List[Part], stock_sheets: List[Dict], config: Dict,
                             optional_stock_parts: Optional[List[Part]] = None,
                             is_stock_only_job: bool = False,
                             progress_callback=None,
                             max_offcut_quantity: float = 1.0,
                             cancellation_event: Optional['threading.Event'] = None,
                             preview_callback=None) -> Tuple[List[Dict], List[Part], float, list]:
    """
    Optimizes cutting stock using a Branch and Bound search with a "deep dive" strategy.
    """
    start_time = time.time()
    time_limit = config.get('search_time_limit', 1.0)
    branching_factor = config.get('branching_factor', 1)
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    last_update_time = start_time
    
    debugger = ScoreDebugger() if DEBUG_SCORES else None
    last_debug_print_time = start_time
    debug_print_interval = 2.0

    max_layouts = config.get('max_unique_layouts', 0)

    tree_nodes = {0: {"id": 0, "parent": None, "score": 0, "status": "exploring"}}
    tree_update_batch = {0: tree_nodes[0].copy()}
    last_best_sol_node = None

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
        
        # Pre-sort the list here, ONCE.
        optional_parts_list.sort(key=lambda p: p.get('target_stock', 1) - p.get('current_stock', 0), reverse=True)

    # PERF: Pre-convert optional parts to a NumPy array to avoid repeated conversions.
    optional_parts_np = np.array([
        [
            hash(p['internal_ref']),
            p.get('target_stock', 1),
            p.get('current_stock', 0),
            p['length'] * p['width']
        ] for p in optional_parts_list
    ], dtype=np.float64) if optional_parts_list else np.empty((0, 4), dtype=np.float64)

    # --- 1. ESTIMATE MAX VALUES (PRE-GREEDY) ---
    max_values = {}
    total_parts_area = sum(p['length'] * p['width'] for p in initial_parts_dict.values())
    max_values['total_initial_parts_area'] = total_parts_area
    max_values['sheets'] = (math.ceil(total_parts_area / reference_area) if reference_area > 0 else 1) + 2
    cuts_per_part_heuristic = config.get('cuts_per_part_heuristic', 4.0)
    max_values['cuts'] = (total_initial_parts * cuts_per_part_heuristic) if total_initial_parts > 0 else 1.0
    max_values['layouts'] = max_values['sheets']

    max_raw_stock_bonus = 0.0
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)
    temp_counts = Counter()
    for part in sorted(optional_parts_list, key=lambda p: p['internal_ref']):
        ref = part['internal_ref']
        initial_deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
        target_stock = part.get('target_stock', 0)
        if target_stock <= 0: continue
        count_already_made = temp_counts.get(ref, 0)
        live_deficit = initial_deficit - count_already_made
        if live_deficit > 0:
            max_raw_stock_bonus += (live_deficit / target_stock) * flat_bonus_unit
        temp_counts[ref] += 1
    max_values['stock_bonus'] = max_raw_stock_bonus if max_raw_stock_bonus > 0 else 1.0

    max_values['waste_area'] = max_values['sheets'] * reference_area
    max_values['offcut_area'] = max_values['sheets'] * reference_area
    max_values['quantity_bonus'] = max_values['sheets'] * (max_offcut_quantity if max_offcut_quantity > 0 else 1.0)
    max_values['priority_bonus'] = 1.0

    # --- 2. RUN GREEDY SEED ---
    greedy_layouts, greedy_score, greedy_raw_components = _run_greedy_seed(
        initial_parts_dict, stock_sheets, config, optional_parts_list, is_stock_only_job,
        reference_area, max_offcut_quantity, max_values, debugger, cancellation_event
    )
    
    best_solution = ([], list(initial_parts_dict.values()))
    best_score = -float('inf')
    learned_rates = {}

    # --- 3. LEARN FROM GREEDY SOLUTION (if successful) ---
    if greedy_layouts:
        max_values, learned_rates = _update_bounding_parameters_from_solution(
            greedy_layouts, config, total_initial_parts, optional_parts_list, reference_area, max_values
        )

        renormalized_components = Counter()
        renormalized_components['area_score'] = - (
            (greedy_raw_components.get('waste_area', 0) / max_values['waste_area']) * config.get('w_waste', 1.0) +
            (greedy_raw_components.get('offcut_area', 0) / max_values['offcut_area']) * config.get('w_offcut', 0.5)
        )
        renormalized_components['stock_fill_bonus'] = (greedy_raw_components.get('stock_bonus', 0) / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
        renormalized_components['quantity_bonus'] = (greedy_raw_components.get('quantity_bonus', 0) / max_values['quantity_bonus']) * config.get('w_quantity', 0.1)
        renormalized_components['priority_bonus'] = (greedy_raw_components.get('priority_bonus', 0) / max_values['priority_bonus']) * config.get('w_priority_offcut', 10.0)
        renormalized_components['sheets_penalty'] = - (len(greedy_layouts) / max_values['sheets']) * config.get('w_sheets', 1.0)
        renormalized_components['layouts_penalty'] = - (len({get_layout_signature(l, required_only=False) for l in greedy_layouts}) / max_values['layouts']) * config.get('w_unique_layouts', 0.5)
        renormalized_components['cuts_penalty'] = - (greedy_raw_components.get('cuts', 0) / max_values['cuts']) * config.get('w_cuts', 0.2)
        
        renormalized_score = sum(renormalized_components.values())
        
        print("\n--- Greedy Solution (Renormalized for B&B) ---")
        print(f"  - Final Greedy Score (Renormalized): {renormalized_score:.3f}")
        # --- START: Added Diagnostic Printout ---
        print("  - Score Breakdown:")
        for key, value in sorted(renormalized_components.items()):
            if value != 0:
                print(f"    - {key:<20}: {value:.3f}")
        # --- END: Added Diagnostic Printout ---
        print("--------------------------------------------\n")

        best_solution = (greedy_layouts, [])
        best_score = renormalized_score

        if preview_callback:
            preview_callback(greedy_layouts, [], best_score, time.time() - start_time, max_values)
            
    else:
        print("\n[WARN] Greedy seed did not find a complete solution. Using heuristic normalization.\n")

    # --- 4. INITIALIZE THE MAIN B&B SEARCH ---
    initial_used_signatures = frozenset()
    initial_stock_counts = Counter()
    initial_bound = -_calculate_bound(
        list(initial_parts_dict.values()), 0.0, config, reference_area, initial_used_signatures,
        max_values, optional_parts_np, initial_stock_counts, learned_rates, debugger=debugger
    )
    
    node_counter = 1
    pq = [(initial_bound, 0.0, 0, initial_parts_dict, [], initial_used_signatures, initial_stock_counts)]
    
    # --- 5. RUN MAIN B&B LOOP ---
    while pq and (time_limit <= 0 or (time.time() - start_time) < time_limit):
        if cancellation_event and cancellation_event.is_set(): break

        (
            _bound, current_score, parent_node_id, current_parts, 
            used_layouts, used_signatures, live_stock_counts
        ) = heapq.heappop(pq)
        
        diving = True
        while diving and (time_limit <= 0 or (time.time() - start_time) < time_limit):
            diving = False

            if debugger:
                debugger.log_context_switch(-_bound, current_score, len(used_layouts), len(current_parts), parent_node_id)
            
            tree_nodes[parent_node_id]["status"] = "explored"
            tree_update_batch[parent_node_id] = tree_nodes[parent_node_id].copy()

            current_time = time.time()
            if debugger and (current_time - last_debug_print_time > debug_print_interval):
                debugger.print_stats()
                last_debug_print_time = current_time

            batch_size_limit = 500
            if (current_time - last_update_time > 0.2) or len(tree_update_batch) >= batch_size_limit:
                if progress_callback:
                    progress_data = {
                        "part_progress": int(((total_initial_parts - len(current_parts)) / total_initial_parts) * 100 if total_initial_parts > 0 else 100),
                        "elapsed_time": current_time - start_time,
                        "time_limit": time_limit, "nodes_explored": node_counter,
                        "current_best_score": best_score if best_score > -float('inf') else None,
                        "sheets_in_best_solution": len(best_solution[0]) if best_solution[0] else None,
                        "tree_update": list(tree_update_batch.values())
                    }
                    progress_callback(progress_data)
                tree_update_batch.clear()
                last_update_time = current_time
            
            bound_check = _calculate_bound(
                list(current_parts.values()), current_score, config, reference_area, used_signatures,
                max_values, optional_parts_np, live_stock_counts, learned_rates, debugger=debugger
            )
            if bound_check < best_score:
                continue

            remaining_primary_parts = list(current_parts.values())
            candidate_actions = []
            unique_sheets = {f"{s['length']}x{s['width']}_{s.get('type', 'Default')}": s for s in stock_sheets}.values()
            for sheet_tpl in unique_sheets:
                for _ in range(branching_factor):
                    layout_attempt = run_single_sheet_optimization(
                        sheet_tpl, remaining_primary_parts, config, reference_area, max_offcut_quantity,
                        optional_parts_list, live_stock_counts, debugger, cancellation_event
                    )
                    if any(p.get('part_id') in current_parts for p in layout_attempt['placed_parts']):
                        candidate_actions.append({'type': 'generate', 'layout': layout_attempt})
            
            unique_layouts_in_path = {get_layout_signature(l): l for l in used_layouts}.values()
            for existing_layout in unique_layouts_in_path:
                if any(p['part_id'] in current_parts for p in existing_layout['placed_parts'] if 'part_id' in p):
                    candidate_actions.append({'type': 'reuse', 'layout': existing_layout})

            potential_next_nodes = []
            for action in candidate_actions:
                layout = action['layout']
                placed_ids = {p['part_id'] for p in layout['placed_parts'] if p['part_id'] in current_parts}
                if not placed_ids: continue
                
                layout_sig = get_layout_signature(layout, required_only=False)
                is_new_signature = layout_sig not in used_signatures
                if is_new_signature and max_layouts > 0 and len(used_signatures) >= max_layouts: continue
                
                sheet_info = layout['sheet_info']
                raw_stock_bonus = 0.0
                temp_counts_for_layout = live_stock_counts.copy()
                for part in sorted(layout['placed_parts'], key=lambda p: p['part_id']):
                    if not part.get('is_stock_part'): continue
                    ref = part['internal_ref']
                    initial_deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
                    target_stock = part.get('target_stock', 0)
                    if target_stock <= 0: continue
                    count_already_made = temp_counts_for_layout.get(ref, 0)
                    live_deficit = initial_deficit - count_already_made
                    if live_deficit > 0: raw_stock_bonus += (live_deficit / target_stock) * flat_bonus_unit
                    temp_counts_for_layout[ref] += 1
                
                component_deltas = {}
                raw_components = Counter()
                raw_components['waste_area'] = sheet_info['length'] * sheet_info['width'] - layout['total_placed_area'] - sum(o['length']*o['width'] for o in layout['offcuts'] if o['length'] >= config['min_offcut_dim'] and o['width'] >= config['min_offcut_dim'])
                raw_components['offcut_area'] = sum(o['length']*o['width'] for o in layout['offcuts'] if o['length'] >= config['min_offcut_dim'] and o['width'] >= config['min_offcut_dim'])
                raw_components['quantity_bonus'] = float(sheet_info.get('quantity', 1)) if sheet_info.get('type') != 'Default' else 0.0
                raw_components['priority_bonus'] = 1.0 if sheet_info.get('type') == 'Offcut' and str(sheet_info.get('id', '')).replace('offcut_', '') in config.get('prioritized_offcuts', []) else 0.0
                raw_components['cuts'] = _count_guillotine_cuts(layout)
                raw_components['stock_bonus'] = raw_stock_bonus

                component_deltas['area_score'] = -((raw_components['waste_area'] / max_values['waste_area']) * config.get('w_waste', 1.0) + (raw_components['offcut_area'] / max_values['offcut_area']) * config.get('w_offcut', 0.5))
                component_deltas['stock_bonus'] = (raw_components['stock_bonus'] / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
                component_deltas['quantity_bonus'] = (raw_components['quantity_bonus'] / max_values['quantity_bonus']) * config.get('w_quantity', 0.1)
                component_deltas['priority_bonus'] = (raw_components['priority_bonus'] / max_values['priority_bonus']) * config.get('w_priority_offcut', 10.0)
                component_deltas['sheets_penalty'] = -(1.0 / max_values['sheets']) * config.get('w_sheets', 1.0)
                component_deltas['cuts_penalty'] = -(raw_components['cuts'] / max_values['cuts']) * config.get('w_cuts', 0.2)
                component_deltas['layouts_penalty'] = -(1.0 / max_values['layouts']) * config.get('w_unique_layouts', 0.5) if is_new_signature else 0.0
                score_delta = sum(component_deltas.values())
                if debugger: debugger.log_true_scores(component_deltas)

                child_score = current_score + score_delta
                child_parts = {pid: p for pid, p in current_parts.items() if pid not in placed_ids}
                child_layouts = used_layouts + [layout]
                child_signatures = used_signatures.union({layout_sig})
                child_stock_counts = live_stock_counts.copy()
                for part in layout['placed_parts']:
                    if part.get('is_stock_part'): child_stock_counts[part['internal_ref']] += 1

                child_node_id = node_counter
                new_node = {"id": child_node_id, "parent": parent_node_id, "score": child_score, "status": "exploring"}
                tree_nodes[child_node_id] = new_node
                tree_update_batch[child_node_id] = new_node.copy()
                node_counter += 1

                if not child_parts:
                    if child_score > best_score:
                        best_score, best_solution = child_score, (child_layouts, [])
                        
                        if preview_callback:
                            preview_callback(child_layouts, [], child_score, time.time() - start_time, max_values)

                        tree_nodes[child_node_id]["status"] = "solution"
                        tree_update_batch[child_node_id] = new_node.copy()
                        if last_best_sol_node is not None and last_best_sol_node in tree_nodes:
                            tree_nodes[last_best_sol_node]["status"] = "explored"
                            tree_update_batch[last_best_sol_node] = tree_nodes[last_best_sol_node].copy()
                        last_best_sol_node = child_node_id

                        max_values, learned_rates = _update_bounding_parameters_from_solution(
                            child_layouts, config, total_initial_parts, optional_parts_list, reference_area, max_values
                        )
                        pq = _rescore_top_nodes(
                            pq, config, reference_area, max_values, optional_parts_np, learned_rates, debugger
                        )
                else:
                    child_bound = _calculate_bound(
                        list(child_parts.values()), child_score, config, reference_area, child_signatures,
                        max_values, optional_parts_np, child_stock_counts, learned_rates, debugger=debugger
                    )
                    if child_bound > best_score:
                        node_data = (child_bound, child_score, child_node_id, child_parts, child_layouts, child_signatures, child_stock_counts)
                        potential_next_nodes.append(node_data)
                    else:
                        tree_nodes[child_node_id]["status"] = "pruned_bound"
                        tree_update_batch[child_node_id] = new_node.copy()

            if potential_next_nodes:
                potential_next_nodes.sort(key=lambda x: x[0], reverse=True)
                
                best_child_data = potential_next_nodes.pop(0)
                (
                    _bound, current_score, parent_node_id, current_parts, 
                    used_layouts, used_signatures, live_stock_counts
                ) = best_child_data
                diving = True

                for other_node in potential_next_nodes:
                    other_bound, *node_tuple = other_node
                    heapq.heappush(pq, (-other_bound, *node_tuple))
    
    if debugger:
        debugger.print_stats("Final Score Component Stats")

    final_layouts, remaining_parts = best_solution
    
    if greedy_layouts and not final_layouts:
        final_layouts, remaining_parts = greedy_layouts, []
        best_score = renormalized_score

    return final_layouts, remaining_parts, best_score, list(tree_update_batch.values()), max_values