import time
import random
import heapq
import math
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
            'area_score': [], 'stock_bonus': [], 'quantity_bonus': [],
            'priority_bonus': [], 'sheets_penalty': [], 'layouts_penalty': [],
            'cuts_penalty': []
        }
        self.predicted_components = {
            'area_score': [], 'stock_bonus': [], 'quantity_bonus': [],
            'priority_bonus': [], 'sheets_penalty': [], 'layouts_penalty': [],
            'cuts_penalty': []
        }

    def _log_component(self, store, key, value):
        if value is not None:
            store[key].append(value)

    def log_true_scores(self, components: Dict[str, float]):
        for key, value in components.items():
            self._log_component(self.true_components, key, value)

    def log_predicted_scores(self, components: Dict[str, float]):
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

    def check_prediction_monotonicity(self, parent_pred: Dict[str, float], child_pred: Dict[str, float], parent_id: int, child_id: int):
        """Checks if a child's prediction for a score component is more optimistic than its parent's."""
        if not parent_pred:  # Root node has no parent to compare to
            return

        bonuses = {'stock_bonus', 'quantity_bonus', 'priority_bonus'}
        penalties = {'area_score', 'sheets_penalty', 'layouts_penalty', 'cuts_penalty'}

        for key, parent_val in parent_pred.items():
            child_val = child_pred.get(key, 0.0)

            # For bonuses, the child's optimistic prediction should be less than or equal to the parent's.
            if key in bonuses and child_val > parent_val:
                print(f"[DEBUG-WARN] Monotonicity violation on '{key}' for child {child_id} (parent {parent_id}): "
                      f"Child_Pred ({child_val:.4f}) > Parent_Pred ({parent_val:.4f})")

            # For penalties (negative values), the child's prediction should be greater than or equal to the parent's (less negative).
            if key in penalties and child_val < parent_val:
                print(f"[DEBUG-WARN] Monotonicity violation on '{key}' for child {child_id} (parent {parent_id}): "
                      f"Child_Pred ({child_val:.4f}) < Parent_Pred ({parent_val:.4f})")

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

    # This check prevents creating layouts that only have optional filler parts in a mixed job.
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
    
    if debugger:
        debugger.log_true_scores({
            'area_score': area_based_score,
            'quantity_bonus': quantity_bonus,
            'priority_bonus': priority_bonus
        })
    
    return area_based_score + quantity_bonus + priority_bonus


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
        
        if not empty_rects:
            continue
        empty_rects_np = np.array([[r['length'], r['width']] for r in empty_rects], dtype=np.float64)

        for part_l, part_w, rotated in orientations:
            rect_index = find_best_fit(part_l, part_w, empty_rects_np)
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
                                  live_stock_counts: Optional[Counter] = None,
                                  debugger: Optional['ScoreDebugger'] = None) -> Dict:
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
    best_score = calculate_layout_score(best_layout, sheet_info, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job=is_stock_only_job, live_stock_counts=live_stock_counts, debugger=debugger)

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
            candidate_score = calculate_layout_score(candidate_layout, sheet_info, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job=is_stock_only_job, live_stock_counts=live_stock_counts, debugger=debugger)

            if candidate_score > best_score:
                best_score = candidate_score
                best_layout = candidate_layout
            iterations += 1
    
    return best_layout

def _count_guillotine_cuts(layout: Dict[str, Any]) -> int:
    """
    Accurately counts the number of unique guillotine cuts by identifying all
    unique internal horizontal and vertical cut lines.
    """
    sheet_info = layout['sheet_info']
    placed_parts = layout.get('placed_parts', [])
    if not placed_parts:
        return 0

    TOLERANCE = 1e-6
    x_coords, y_coords = set(), set()

    for part in placed_parts:
        p_len = part['width'] if part.get('rotated') else part['length']
        p_wid = part['length'] if part.get('rotated') else part['width']
        x_coords.add(part['x'])
        x_coords.add(part['x'] + p_wid)
        y_coords.add(part['y'])
        y_coords.add(part['y'] + p_len)
        
    # A cut is an internal line, so we filter out the sheet boundaries.
    # We round to merge floating point values that are effectively the same cut.
    unique_x_cuts = {round(x, 4) for x in x_coords if x > TOLERANCE and x < sheet_info['width'] - TOLERANCE}
    unique_y_cuts = {round(y, 4) for y in y_coords if y > TOLERANCE and y < sheet_info['length'] - TOLERANCE}

    return len(unique_x_cuts) + len(unique_y_cuts)


def _predict_leftover_area(remaining_parts: list, reference_area: float, config: Dict) -> List[float]:
    """
    Predicts leftover area by running a fast, optimistic simulation of packing
    the remaining parts. Returns a list of leftover areas, one for each
    simulated sheet, to account for fragmentation.
    """
    if not remaining_parts or reference_area <= 0:
        return []

    kerf = config.get('kerf', 0.125)
    allow_rotation = config.get('allow_rotation', True)
    sheet_sim_info = {'length': config.get('default_length', 96), 'width': config.get('default_width', 48)}

    parts_to_simulate = sorted(remaining_parts, key=lambda p: p['length'] * p['width'], reverse=True)
    hypothetical_leftover_areas = []

    while parts_to_simulate:
        initial_rects = [{'x': 0, 'y': 0, 'length': sheet_sim_info['length'], 'width': sheet_sim_info['width']}]
        
        placed_in_sim, _ = _place_parts_in_rects(
            parts_to_simulate, initial_rects, kerf, allow_rotation
        )

        if not placed_in_sim:
            for part in parts_to_simulate:
                leftover = max(0, reference_area - (part['length'] * part['width']))
                hypothetical_leftover_areas.append(leftover)
            break

        area_used_in_sim = sum(p['length'] * p['width'] for p in placed_in_sim)
        hypothetical_leftover_areas.append(reference_area - area_used_in_sim)

        placed_ids = {p['part_id'] for p in placed_in_sim}
        parts_to_simulate = [p for p in parts_to_simulate if p['part_id'] not in placed_ids]

    return hypothetical_leftover_areas


def _calculate_optimistic_stock_bonus(remaining_parts: list, hypothetical_leftover_areas: List[float],
                                      optional_parts_for_bound: list, reference_area: float,
                                      live_stock_counts: Counter, config: Dict) -> float:
    """
    Calculates the maximum possible stock fill bonus using a flat-rate-per-item
    bonus to avoid bias towards large, un-placeable parts.
    """
    if reference_area <= 0:
        return 0.0
    
    # NEW: Flat bonus per item placed, taken from config
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)

    live_deficit_tracker = Counter()
    all_stock_parts = remaining_parts + optional_parts_for_bound
    for part in all_stock_parts:
        if part.get('is_stock_part'):
            ref = part['internal_ref']
            if ref not in live_deficit_tracker:
                initial_deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
                count_already_made = live_stock_counts.get(ref, 0)
                live_deficit_tracker[ref] = initial_deficit - count_already_made

    # Bonus for required parts that are also stock parts
    required_parts_bonus = 0.0
    for part in remaining_parts:
        if part.get('is_stock_part'):
            ref = part['internal_ref']
            if live_deficit_tracker[ref] > 0:
                target_stock = part.get('target_stock', 0)
                if target_stock <= 0: continue
                
                required_parts_bonus += (live_deficit_tracker[ref] / target_stock) * flat_bonus_unit
                live_deficit_tracker[ref] -= 1

    # Speculative bonus for optional parts
    optional_parts_bonus = 0.0
    if hypothetical_leftover_areas and optional_parts_for_bound:
        optional_parts_by_ref = {p['internal_ref']: p for p in optional_parts_for_bound if p.get('internal_ref')}
        
        all_bonus_items = []
        for ref, part_template in optional_parts_by_ref.items():
            current_deficit = live_deficit_tracker.get(ref, 0)
            if current_deficit <= 0: continue

            part_area = part_template['length'] * part_template['width']
            target_stock = part_template.get('target_stock', 0)
            if target_stock <= 0 or part_area <= 0: continue
            
            for i in range(current_deficit):
                deficit_level = current_deficit - i
                item_bonus = (deficit_level / target_stock) * flat_bonus_unit
                all_bonus_items.append({'bonus': item_bonus, 'area': part_area, 'deficit': deficit_level, 'ref': ref})
        
        # Sort by value density (bonus per unit of area) to prioritize the most valuable parts for the space they occupy.
        all_bonus_items.sort(key=lambda p: p['bonus'] / p['area'] if p['area'] > 0 else float('inf'), reverse=True)
        
        bins = sorted(hypothetical_leftover_areas)

        for item in all_bonus_items:
            best_bin_idx = -1
            # Find smallest bin it fits in
            import bisect
            # Find the insertion point for the item's area in the sorted bins
            insertion_point = bisect.bisect_left(bins, item['area'])
            if insertion_point < len(bins):
                 best_bin_idx = insertion_point
            
            if best_bin_idx != -1:
                optional_parts_bonus += item['bonus']
                bins[best_bin_idx] -= item['area']
                
                # Efficiently update the sorted list of bins
                updated_capacity = bins.pop(best_bin_idx)
                bisect.insort_left(bins, updated_capacity)

    return required_parts_bonus + optional_parts_bonus


def _get_min_cuts_for_parts(num_parts: int) -> int:
    """
    Calculates the mathematical minimum number of guillotine cuts required
    to produce a given number of rectangular parts from a single sheet.
    """
    if num_parts <= 1:
        return 0
    rows = math.ceil(math.sqrt(num_parts))
    cols = math.ceil(num_parts / rows)
    return (rows - 1) + (cols - 1)


def _get_min_sheets_for_parts(remaining_parts: list, reference_area: float) -> int:
    """ Calculates the minimum number of sheets required to produce the remaining parts,
    assuming perfect packing and no waste. """
    remaining_area = sum(p['length'] * p['width'] for p in remaining_parts)
    return math.ceil(remaining_area / reference_area) if reference_area > 0 else int('inf')


def _predict_number_of_new_sheet_layouts(remaining_parts: list, used_signatures: frozenset, 
                                        reference_area: float, config: Dict) -> int:
    """
    Analyzes remaining part demand vs. production from existing layouts and runs a fast,
    optimistic simulation to predict how many new, unique layouts will be required.
    """
    # 1. Tally the demand for each unique part dimension among remaining parts.
    remaining_demand = Counter()
    dim_to_part_map = {} # Store a representative part for each dimension
    for part in remaining_parts:
        p_len, p_wid = (part['length'], part['width'])
        dim_tuple = tuple(sorted((f"{p_len:.3f}", f"{p_wid:.3f}")))
        remaining_demand[dim_tuple] += 1
        if dim_tuple not in dim_to_part_map:
            dim_to_part_map[dim_tuple] = part

    # 2. Analyze the production "recipe" of each existing layout.
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

    # 3. Calculate how many parts can be fulfilled by reusing existing layouts.
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

    # 4. Get a list of unique, unfulfilled "problem parts".
    unique_problem_parts = []
    for dim, total_needed in remaining_demand.items():
        if total_needed > fulfilled_parts.get(dim, 0):
            unique_problem_parts.append(dim_to_part_map[dim])

    # 5. Run a greedy simulation to pack the unique problem parts.
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
            
            if not placed_in_sim: # Could not place even one part on a fresh sheet
                # This case is tricky; it implies a part is larger than the sheet.
                # A safe bet is to assume one layout per remaining unique part.
                return layouts_needed + len(parts_to_simulate)

            placed_dims = {tuple(sorted((f"{p['length']:.3f}", f"{p['width']:.3f}"))) for p in placed_in_sim}
            parts_to_simulate = [p for p in parts_to_simulate if tuple(sorted((f"{p['length']:.3f}", f"{p['width']:.3f}"))) not in placed_dims]
            
        return layouts_needed
    
    return 0


def _calculate_worst_case_leftover_area(remaining_parts: List[Part], stock_sheets: List[Dict]) -> float:
    """
    Calculates the guaranteed optimistic leftover area by assuming the absolute worst-case
    packing scenario: one sheet per remaining part. This ensures admissibility.
    """
    if not stock_sheets or not remaining_parts:
        return 0.0

    total_remaining_parts_area = sum(p['length'] * p['width'] for p in remaining_parts)
    
    # The maximum number of sheets that could possibly be used is one per part.
    num_sheets_for_bonus_calc = len(remaining_parts)
    
    max_optimistic_leftover_area = 0
    for sheet in stock_sheets:
        sheet_area = sheet['length'] * sheet['width']
        if sheet_area > 0:
            # Total area provided by the worst-case number of sheets of this size.
            total_sheet_area_for_bonus = num_sheets_for_bonus_calc * sheet_area
            current_leftover = max(0, total_sheet_area_for_bonus - total_remaining_parts_area)
            
            # We take the max leftover from among all available sheet sizes.
            if current_leftover > max_optimistic_leftover_area:
                max_optimistic_leftover_area = current_leftover
    
    return max_optimistic_leftover_area


def _calculate_bound(remaining_parts: list, current_score: float, config: Dict[str, Any], reference_area: float, used_signatures: frozenset,
                     normalized_sheet_penalty: float, normalized_layouts_penalty: float, total_cuts_normalizer: float,
                     optional_parts_for_bound: list, live_stock_counts: Counter, stock_sheets: List[Dict],
                     debugger: Optional['ScoreDebugger'] = None) -> Tuple[float, Dict[str, float]]:
    """
    Calculates a sophisticated upper bound that is aware of the live stock part deficit.
    """
    if not remaining_parts:
        return current_score, {}

    # --- START: GUARANTEED ADMISSIBLE HEURISTIC ---
    # To ensure the bound is a true upper bound, we calculate leftover space based on the
    # absolute worst-case packing scenario, now encapsulated in a helper function.
    max_optimistic_leftover_area = _calculate_worst_case_leftover_area(remaining_parts, stock_sheets)
    hypothetical_leftover_areas = [max_optimistic_leftover_area]
    # --- END: GUARANTEED ADMISSIBLE HEURISTIC ---

    raw_optimistic_stock_fill = _calculate_optimistic_stock_bonus(
        remaining_parts, hypothetical_leftover_areas, optional_parts_for_bound, reference_area, live_stock_counts, config
    )
    optimistic_future_cuts = _get_min_cuts_for_parts(len(remaining_parts))
    # The penalty must still be based on the absolute minimum sheets, using the reference area for consistency.
    min_additional_sheets = _get_min_sheets_for_parts(remaining_parts, reference_area)
    optimistic_new_layouts = _predict_number_of_new_sheet_layouts(remaining_parts, used_signatures, reference_area, config)
    
    # Apply weights to the raw values
    optimistic_area_score = 0.0 # Assume perfect packing for the bound
    optimistic_quantity_bonus = 0.0 # Too complex to predict
    optimistic_priority_bonus = 0.0 # Same as above
    optimistic_stock_fill_bonus = raw_optimistic_stock_fill * config.get('w_stock_fill', 1.0)
    future_cuts_penalty = (optimistic_future_cuts / total_cuts_normalizer) * config.get('w_cuts', 0.2) if total_cuts_normalizer > 0 else 0
    future_sheets_penalty = min_additional_sheets * normalized_sheet_penalty
    future_layouts_penalty = optimistic_new_layouts * normalized_layouts_penalty
    
    upper_bound = (
        current_score
        + optimistic_area_score
        + optimistic_stock_fill_bonus
        - future_cuts_penalty
        - future_sheets_penalty
        - future_layouts_penalty
    )
    
    predicted_components = {
        'area_score': optimistic_area_score,
        'stock_bonus': optimistic_stock_fill_bonus,
        'quantity_bonus': optimistic_quantity_bonus,
        'priority_bonus': optimistic_priority_bonus,
        'sheets_penalty': -future_sheets_penalty,
        'layouts_penalty': -future_layouts_penalty,
        'cuts_penalty': -future_cuts_penalty
    }

    if debugger:
        debugger.log_predicted_scores(predicted_components)

    return upper_bound, predicted_components


def optimize_multiple_sheets(parts_to_cut: List[Part], stock_sheets: List[Dict], config: Dict,
                             optional_stock_parts: Optional[List[Part]] = None,
                             is_stock_only_job: bool = False,
                             progress_callback=None,
                             max_offcut_quantity: float = 1.0,
                             cancellation_event: Optional['threading.Event'] = None) -> Tuple[List[Dict], List[Part], float]:
    """
    Optimizes cutting stock using a time-limited Branch and Bound search.
    """
    start_time = time.time()
    time_limit = config.get('search_time_limit', 1.0)
    branching_factor = config.get('branching_factor', 1)
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    last_update_time = start_time
    
    debugger = ScoreDebugger() if DEBUG_SCORES else None
    last_debug_print_time = start_time
    debug_print_interval = 2.0  # Print debug stats every 2 seconds

    max_layouts = config.get('max_unique_layouts', 0)
    layout_score_cache = {}

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

    total_parts_area = sum(p['length'] * p['width'] for p in initial_parts_dict.values())
    expected_sheets = math.ceil(total_parts_area / reference_area) if reference_area > 0 else 1
    penalty_normalizer = 1.0 / expected_sheets if expected_sheets > 0 else 1.0
    
    normalized_sheet_penalty = config.get('w_sheets', 1.0) * penalty_normalizer
    normalized_layouts_penalty = config.get('w_unique_layouts', 0.5) * penalty_normalizer
    
    cuts_per_part_heuristic = config.get('cuts_per_part_heuristic', 1.5) # Reduced from 4.0
    total_cuts_normalizer = total_initial_parts * cuts_per_part_heuristic

    # Get a list of unique sheet templates to pass to the bound calculator
    unique_sheets = list({f"{s['length']}x{s['width']}_{s.get('type', 'Default')}": s for s in stock_sheets}.values())

    initial_used_signatures = frozenset()
    initial_stock_counts = Counter()
    initial_bound, initial_pred_components = _calculate_bound(
        list(initial_parts_dict.values()), 0, config, reference_area, initial_used_signatures,
        normalized_sheet_penalty, normalized_layouts_penalty, total_cuts_normalizer, optional_parts_list,
        initial_stock_counts, unique_sheets, debugger=debugger
    )
    
    node_counter = 1
    # The priority queue now stores the prediction components for each node
    pq = [(-initial_bound, 0.0, 0, initial_parts_dict, [], initial_used_signatures, initial_stock_counts, initial_pred_components)]
    
    best_solution = ([], list(initial_parts_dict.values()))
    best_score = -float('inf')

    while pq and (time_limit <= 0 or (time.time() - start_time) < time_limit):
        if cancellation_event and cancellation_event.is_set():
            break
        
        # Unpack the parent node's prediction components
        bound, current_score, parent_node_id, current_parts, used_layouts, used_signatures, live_stock_counts, parent_pred_components = heapq.heappop(pq)
        
        if debugger:
            debugger.log_context_switch(-bound, current_score, len(used_layouts), len(current_parts), parent_node_id)
        
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
        
        # Pass unique_sheets to the bound check
        bound_check, _ = _calculate_bound(
            list(current_parts.values()), current_score, config, reference_area, used_signatures,
            normalized_sheet_penalty, normalized_layouts_penalty, total_cuts_normalizer, optional_parts_list,
            live_stock_counts, unique_sheets, debugger=debugger
        )
        if bound_check < best_score:
            continue

        remaining_primary_parts = list(current_parts.values())
        candidate_actions = []

        for sheet_tpl in unique_sheets:
            for _ in range(branching_factor):
                layout_attempt = run_single_sheet_optimization(
                    sheet_tpl, remaining_primary_parts, config, reference_area, max_offcut_quantity,
                    optional_parts_list, live_stock_counts, debugger
                )
                if any(p.get('part_id') in current_parts for p in layout_attempt['placed_parts']):
                    candidate_actions.append({'type': 'generate', 'layout': layout_attempt})
        
        unique_layouts_in_path = {get_layout_signature(l): l for l in used_layouts}.values()
        for existing_layout in unique_layouts_in_path:
            if any(p['part_id'] in current_parts for p in existing_layout['placed_parts'] if 'part_id' in p):
                candidate_actions.append({'type': 'reuse', 'layout': existing_layout})

        for action in candidate_actions:
            layout = action['layout']
            layout_sig = get_layout_signature(layout, required_only=False)
            
            is_new_signature = layout_sig not in used_signatures
            if is_new_signature and max_layouts > 0 and len(used_signatures) >= max_layouts:
                continue

            if layout_sig not in layout_score_cache:
                layout_score_cache[layout_sig] = calculate_layout_score(
                    layout, layout['sheet_info'], config, config['min_offcut_dim'], reference_area,
                    max_offcut_quantity, is_stock_only_job,
                    remaining_parts=list(current_parts.values()),
                    live_stock_counts=live_stock_counts,
                    debugger=debugger
                )
            layout_score = layout_score_cache[layout_sig]
            
            placed_ids = {p['part_id'] for p in layout['placed_parts'] if p['part_id'] in current_parts}
            if not placed_ids: continue

            # --- Calculate True Stock Bonus Here ---
            stock_fill_bonus = 0.0
            flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)
            w_stock_fill = config.get('w_stock_fill', 1.0) # Global weight for the component
            
            parts_on_this_layout = Counter(p['internal_ref'] for p in layout['placed_parts'] if p.get('is_stock_part'))
            if parts_on_this_layout:
                for ref, count_on_sheet in parts_on_this_layout.items():
                    part_template = next(p for p in layout['placed_parts'] if p.get('internal_ref') == ref)
                    initial_deficit = part_template.get('target_stock', 1) - part_template.get('current_stock', 0)
                    target_stock = part_template.get('target_stock', 0)
                    if target_stock <= 0: continue

                    count_already_made = live_stock_counts.get(ref, 0)

                    for i in range(count_on_sheet):
                        live_deficit = initial_deficit - (count_already_made + i)
                        if live_deficit <= 0: continue
                        stock_fill_bonus += (live_deficit / target_stock) * flat_bonus_unit * w_stock_fill
            
            layouts_penalty = normalized_layouts_penalty if action['type'] == 'generate' and is_new_signature else 0
            unweighted_cuts = float(_count_guillotine_cuts(layout))
            cuts_penalty = (unweighted_cuts / total_cuts_normalizer) * config.get('w_cuts', 0.2) if total_cuts_normalizer > 0 else 0
            
            if debugger:
                debugger.log_true_scores({
                    'stock_bonus': stock_fill_bonus,
                    'sheets_penalty': -normalized_sheet_penalty,
                    'layouts_penalty': -layouts_penalty,
                    'cuts_penalty': -cuts_penalty
                })
            
            child_score = current_score + layout_score + stock_fill_bonus - normalized_sheet_penalty - layouts_penalty - cuts_penalty
            child_parts = {pid: p for pid, p in current_parts.items() if pid not in placed_ids}
            child_layouts = used_layouts + [layout]
            child_signatures = used_signatures.union({layout_sig})

            child_stock_counts = live_stock_counts.copy()
            for part in layout['placed_parts']:
                if part.get('is_stock_part'):
                    child_stock_counts[part['internal_ref']] += 1

            current_node_id = node_counter
            new_node = {"id": current_node_id, "parent": parent_node_id, "score": child_score, "status": "exploring"}
            tree_nodes[current_node_id] = new_node
            tree_update_batch[current_node_id] = new_node.copy()
            node_counter += 1

            if not child_parts:
                if child_score > best_score:
                    best_score = child_score
                    best_solution = (child_layouts, [])
                    tree_nodes[current_node_id]["status"] = "solution"
                    tree_update_batch[current_node_id] = tree_nodes[current_node_id].copy()
                    if last_best_sol_node is not None and last_best_sol_node in tree_nodes:
                        tree_nodes[last_best_sol_node]["status"] = "explored"
                        tree_update_batch[last_best_sol_node] = tree_nodes[last_best_sol_node].copy()
                    last_best_sol_node = current_node_id
            else:
                # Pass unique_sheets to calculate the child's bound
                child_bound, child_pred_components = _calculate_bound(
                    list(child_parts.values()), child_score, config, reference_area, child_signatures,
                    normalized_sheet_penalty, normalized_layouts_penalty, total_cuts_normalizer, optional_parts_list,
                    child_stock_counts, unique_sheets, debugger=debugger
                )
                if debugger:
                    debugger.check_prediction_monotonicity(parent_pred_components, child_pred_components, parent_node_id, current_node_id)
                
                if child_bound > best_score:
                    # Push the child's prediction components to the queue
                    heapq.heappush(pq, (-child_bound, child_score, current_node_id, child_parts, child_layouts, child_signatures, child_stock_counts, child_pred_components))
                else:
                    tree_nodes[current_node_id]["status"] = "pruned_bound"
                    tree_update_batch[current_node_id] = tree_nodes[current_node_id].copy()
    
    if debugger:
        debugger.print_stats("Final Score Component Stats")

    final_layouts, remaining_parts = best_solution
    return final_layouts, remaining_parts, best_score, list(tree_update_batch.values())