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


def _calculate_optimistic_stock_bonus(remaining_parts: list, optional_parts_for_bound: list, 
                                     live_stock_counts: Counter, config: Dict[str, Any], 
                                     reference_area: float) -> float:
    """
    Calculates a realistic upper bound for the stock fill bonus by estimating
    the space leftover after placing required parts and filling it greedily
    with the most "bonus-dense" optional stock parts.
    """
    # 1. Triage: If there are no optional parts to place, no bonus is possible.
    # Also, if this is a mixed job (not stock-only) and no required parts are left,
    # we can't create new sheets with only optional parts, so the bonus is 0.
    is_mixed_job = any(not p.get('is_stock_part') for p in remaining_parts)
    if not optional_parts_for_bound or not remaining_parts and is_mixed_job:
        return 0.0

    # 2. Estimate future sheets and available area
    estimated_sheets = _get_min_sheets_for_parts(remaining_parts, reference_area)
    total_future_area = estimated_sheets * reference_area
    required_parts_area = sum(p['length'] * p['width'] for p in remaining_parts)
    optimistic_available_area = max(0, total_future_area - required_parts_area)

    if optimistic_available_area <= 0:
        return 0.0

    # 3. Prioritize optional parts by bonus density
    w_stock_fill = config.get('w_stock_fill', 1.0)
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)
    
    candidate_parts = []
    temp_counts = live_stock_counts.copy() # Use a temporary counter for this prediction

    for part in sorted(optional_parts_for_bound, key=lambda p: p['part_id']):
        ref = part['internal_ref']
        initial_deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
        target_stock = part.get('target_stock', 1)
        if target_stock <= 0: continue

        # Calculate bonus for this specific instance of the part
        count_already_made = temp_counts.get(ref, 0)
        live_deficit = initial_deficit - count_already_made
        
        if live_deficit <= 0:
            potential_bonus = 0.0
        else:
            potential_bonus = (live_deficit / target_stock) * flat_bonus_unit * w_stock_fill
        
        part_area = part['length'] * part['width']
        if part_area > 0:
            bonus_density = potential_bonus / part_area
            candidate_parts.append({
                'part': part,
                'bonus': potential_bonus,
                'area': part_area,
                'density': bonus_density
            })
        
        # Increment the temp counter to model diminishing returns for the next part of this type
        temp_counts[ref] += 1
    
    # Sort candidates by how efficiently they provide a bonus
    sorted_candidates = sorted(candidate_parts, key=lambda x: x['density'], reverse=True)

    # 4. Perform Greedy Fill
    predicted_bonus = 0.0
    area_filled = 0.0
    for cand in sorted_candidates:
        if area_filled + cand['area'] <= optimistic_available_area:
            predicted_bonus += cand['bonus']
            area_filled += cand['area']
        else:
            # For the last part, we can add a fractional bonus for the remaining space
            fractional_fit = (optimistic_available_area - area_filled) / cand['area']
            predicted_bonus += cand['bonus'] * fractional_fit
            break # The space is now full

    return predicted_bonus


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


def _calculate_bound(remaining_parts: list, current_score: float, config: Dict[str, Any], reference_area: float, used_signatures: frozenset,
                     max_values: Dict[str, float],
                     optional_parts_for_bound: list, live_stock_counts: Counter,
                     greedy_learned_rates: Dict[str, float],
                     debugger: Optional['ScoreDebugger'] = None) -> float:
    """
    Calculates a sophisticated upper bound using data-driven rates learned from the greedy solution.
    """
    if not remaining_parts:
        return current_score

    # --- 1. Predict future values using learned rates from the greedy solution ---
    remaining_area = sum(p['length'] * p['width'] for p in remaining_parts)
    
    # Use learned packing density to predict sheets needed (more realistic than 100% yield)
    packing_density = greedy_learned_rates.get('packing_density', 0.9) * 1.1 # Default to 9% if not available
    if packing_density == 0: packing_density = 0.9
    predicted_sheets = math.ceil(remaining_area / (packing_density * reference_area)) if reference_area > 0 else float('inf')

    # Use learned waste rate to predict future waste area
    waste_rate = 1.0 - packing_density
    predicted_waste_area = predicted_sheets * reference_area * waste_rate
    
    # Use learned cuts-per-part ratio to predict future cuts
    cuts_per_part = greedy_learned_rates.get('cuts_per_part', 1.2) # Default to 1.2 if not available
    predicted_cuts = len(remaining_parts) * cuts_per_part

    # Other predictions remain the same
    optimistic_new_layouts = _predict_number_of_new_sheet_layouts(remaining_parts, used_signatures, reference_area, config)
    raw_stock_bonus = _calculate_optimistic_stock_bonus(
        remaining_parts, optional_parts_for_bound, live_stock_counts, config, reference_area
    )

    # --- 2. Normalize and weight the predictions ---
    area_penalty = (predicted_waste_area / max_values['waste_area']) * config.get('w_waste', 1.0)
    stock_bonus = (raw_stock_bonus / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
    sheets_penalty = (predicted_sheets / max_values['sheets']) * config.get('w_sheets', 1.0)
    layouts_penalty = (optimistic_new_layouts / max_values['layouts']) * config.get('w_unique_layouts', 0.5)
    cuts_penalty = (predicted_cuts / max_values['cuts']) * config.get('w_cuts', 0.2)

    upper_bound = (
        current_score
        + stock_bonus
        - area_penalty
        - sheets_penalty
        - layouts_penalty
        - cuts_penalty
    )

    if debugger:
        debugger.log_predicted_scores({
            'area_score': -area_penalty,
            'stock_bonus': stock_bonus,
            'quantity_bonus': 0.0,
            'priority_bonus': 0.0,
            'sheets_penalty': -sheets_penalty,
            'layouts_penalty': -layouts_penalty,
            'cuts_penalty': -cuts_penalty
        })

    return upper_bound


def _run_greedy_seed(initial_parts_dict: Dict[int, Part], stock_sheets: List[Dict], config: Dict,
                     optional_parts_list: List[Part], is_stock_only_job: bool, reference_area: float,
                     max_offcut_quantity: float, max_values: Dict[str, float],
                     debugger: Optional['ScoreDebugger']):
    """
    Runs a fast, greedy optimization using the new min-max normalization scoring.
    """
    greedy_layouts = []
    current_parts = initial_parts_dict.copy()
    current_score = 0.0
    live_stock_counts = Counter()
    used_signatures = frozenset()
    greedy_raw_components = Counter()

    while current_parts:
        candidate_actions = []
        unique_sheets = {f"{s['length']}x{s['width']}_{s.get('type', 'Default')}": s for s in stock_sheets}.values()

        for sheet_tpl in unique_sheets:
            layout = run_single_sheet_optimization(
                sheet_tpl, list(current_parts.values()), config, reference_area, max_offcut_quantity,
                optional_parts_list, live_stock_counts, None
            )
            placed_ids = {p['part_id'] for p in layout['placed_parts'] if p['part_id'] in current_parts}
            if not placed_ids:
                continue
            
            # --- CALCULATE SCORE DELTA FOR THIS LAYOUT ---
            # 1. Get raw values for the layout
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

            # 2. Normalize, weight, and calculate score delta components
            component_deltas = {}
            component_deltas['area_score'] = - (
                (raw_components['waste_area'] / max_values['waste_area']) * config.get('w_waste', 1.0) +
                (raw_components['offcut_area'] / max_values['offcut_area']) * config.get('w_offcut', 0.5)
            )
            component_deltas['stock_bonus'] = (raw_components['stock_bonus'] / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
            component_deltas['quantity_bonus'] = (raw_components['quantity_bonus'] / max_values['quantity_bonus']) * config.get('w_quantity', 0.1)
            component_deltas['priority_bonus'] = (raw_components['priority_bonus'] / max_values['priority_bonus']) * config.get('w_priority_offcut', 10.0)
            component_deltas['sheets_penalty'] = - (1.0 / max_values['sheets']) * config.get('w_sheets', 1.0)
            component_deltas['cuts_penalty'] = - (raw_components['cuts'] / max_values['cuts']) * config.get('w_cuts', 0.2)
            
            layout_sig = get_layout_signature(layout, required_only=False)
            component_deltas['layouts_penalty'] = 0.0
            if layout_sig not in used_signatures:
                component_deltas['layouts_penalty'] = - (1.0 / max_values['layouts']) * config.get('w_unique_layouts', 0.5)

            score_delta = sum(component_deltas.values())

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

    # --- 1. ESTIMATE MAX VALUES (PRE-GREEDY) ---
    max_values = {}
    total_parts_area = sum(p['length'] * p['width'] for p in initial_parts_dict.values())
    # Estimate max sheets needed. Add a buffer for non-optimal packing.
    max_values['sheets'] = (math.ceil(total_parts_area / reference_area) if reference_area > 0 else 1) + 2
    cuts_per_part_heuristic = config.get('cuts_per_part_heuristic', 4.0)
    max_values['cuts'] = (total_initial_parts * cuts_per_part_heuristic) if total_initial_parts > 0 else 1.0
    max_values['layouts'] = max_values['sheets'] # Assume one new layout per sheet as a worst-case

    # Estimate max raw stock bonus by summing the potential bonus for ALL available optional parts
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

    # Define max values for components, scaling by the estimated number of sheets where appropriate.
    # The total waste/offcut can't exceed the total area of all sheets used.
    max_values['waste_area'] = max_values['sheets'] * reference_area
    max_values['offcut_area'] = max_values['sheets'] * reference_area
    # The total quantity bonus is similarly scaled.
    max_values['quantity_bonus'] = max_values['sheets'] * (max_offcut_quantity if max_offcut_quantity > 0 else 1.0)
    # Priority bonus is for using one prioritized offcut; its theoretical max is using one on every sheet,
    # but a max of 1.0 is more stable as it's a rare event.
    max_values['priority_bonus'] = 1.0

    # --- 2. RUN GREEDY SEED ---
    greedy_layouts, greedy_score, greedy_raw_components = _run_greedy_seed(
        initial_parts_dict, stock_sheets, config, optional_parts_list, is_stock_only_job,
        reference_area, max_offcut_quantity, max_values, debugger
    )
    
    best_solution = ([], list(initial_parts_dict.values()))
    best_score = -float('inf')
    greedy_learned_rates = {}

    # --- 3. LEARN FROM GREEDY SOLUTION (if successful) ---
    if greedy_layouts:
        print("\n--- Greedy Seed Solution (Initial Score) ---")
        print(f"  - Final Greedy Score: {greedy_score:.3f}")
        print("------------------------------------------\n")

        print("[INFO] Refining max values with data-driven heuristics from the greedy solution.")

        total_greedy_parts = sum(len(l['placed_parts']) for l in greedy_layouts)
        greedy_total_cuts = greedy_raw_components.get('cuts', 0)
        cuts_per_part_ratio = 2.5
        if total_greedy_parts > 0:
            cuts_per_part_ratio = greedy_total_cuts / total_greedy_parts
            absolute_max_parts = total_initial_parts + len(optional_parts_list)
            projected_max_cuts = (absolute_max_parts * cuts_per_part_ratio) * 1.25
            max_values['cuts'] = max(max_values['cuts'], projected_max_cuts)

        greedy_sheet_count = len(greedy_layouts)
        total_greedy_area = sum(p['length'] * p['width'] for l in greedy_layouts for p in l['placed_parts'])
        total_sheet_area = greedy_sheet_count * reference_area
        packing_density = 0.75
        if total_sheet_area > 0:
            packing_density = total_greedy_area / total_sheet_area
            total_possible_parts_area = total_parts_area + sum(p['length'] * p['width'] for p in optional_parts_list)
            projected_density = max(0.5, packing_density * 0.9)
            projected_max_sheets = math.ceil(total_possible_parts_area / (projected_density * reference_area))
            max_values['sheets'] = max(max_values['sheets'], projected_max_sheets)
        
        greedy_layout_count = len({get_layout_signature(l, required_only=False) for l in greedy_layouts})
        layouts_per_sheet_ratio = greedy_layout_count / greedy_sheet_count if greedy_sheet_count > 0 else 1
        projected_max_layouts = math.ceil(max_values['sheets'] * layouts_per_sheet_ratio) + 1
        max_values['layouts'] = max(max_values['layouts'], projected_max_layouts)

        greedy_learned_rates = {
            'packing_density': packing_density,
            'cuts_per_part': cuts_per_part_ratio
        }

        renormalized_components = Counter()
        renormalized_components['area_score'] = - (
            (greedy_raw_components.get('waste_area', 0) / max_values['waste_area']) * config.get('w_waste', 1.0) +
            (greedy_raw_components.get('offcut_area', 0) / max_values['offcut_area']) * config.get('w_offcut', 0.5)
        )
        renormalized_components['stock_fill_bonus'] = (greedy_raw_components.get('stock_bonus', 0) / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
        renormalized_components['quantity_bonus'] = (greedy_raw_components.get('quantity_bonus', 0) / max_values['quantity_bonus']) * config.get('w_quantity', 0.1)
        renormalized_components['priority_bonus'] = (greedy_raw_components.get('priority_bonus', 0) / max_values['priority_bonus']) * config.get('w_priority_offcut', 10.0)
        renormalized_components['sheets_penalty'] = - (greedy_sheet_count / max_values['sheets']) * config.get('w_sheets', 1.0)
        renormalized_components['layouts_penalty'] = - (greedy_layout_count / max_values['layouts']) * config.get('w_unique_layouts', 0.5)
        renormalized_components['cuts_penalty'] = - (greedy_total_cuts / max_values['cuts']) * config.get('w_cuts', 0.2)
        
        renormalized_score = sum(renormalized_components.values())
        
        print("\n--- Greedy Solution (Renormalized for B&B) ---")
        print(f"  - Final Greedy Score (Renormalized): {renormalized_score:.3f}")
        print("  - Score Components:")
        print_order = ['area_score', 'stock_fill_bonus', 'quantity_bonus', 'priority_bonus', 'sheets_penalty', 'layouts_penalty', 'cuts_penalty']
        for key in print_order:
            value = renormalized_components.get(key, 0.0)
            print(f"    - {key:<20}: {value:>8.3f}")
        print("--------------------------------------------\n")

        best_solution = (greedy_layouts, [])
        best_score = renormalized_score

        if preview_callback:
            preview_callback(greedy_layouts, [], best_score, time.time() - start_time)
            
    else:
        print("\n[WARN] Greedy seed did not find a complete solution. Using heuristic normalization.\n")

    # --- 4. INITIALIZE THE MAIN B&B SEARCH ---
    initial_used_signatures = frozenset()
    initial_stock_counts = Counter()
    initial_bound = -_calculate_bound(
        list(initial_parts_dict.values()), 0.0, config, reference_area, initial_used_signatures,
        max_values, optional_parts_list, initial_stock_counts, greedy_learned_rates, debugger=debugger
    )
    
    node_counter = 1
    pq = [(initial_bound, 0.0, 0, initial_parts_dict, [], initial_used_signatures, initial_stock_counts)]
    
    # --- 5. RUN MAIN B&B LOOP ---
    # This loop pops the best global option to start a "deep dive".
    while pq and (time_limit <= 0 or (time.time() - start_time) < time_limit):
        if cancellation_event and cancellation_event.is_set(): break

        # Pop the most promising path from the global queue to start a deep dive
        (
            _bound, current_score, parent_node_id, current_parts, 
            used_layouts, used_signatures, live_stock_counts
        ) = heapq.heappop(pq)
        
        # Start a "deep dive" from this node, exploring descendants preferentially
        diving = True
        while diving and (time_limit <= 0 or (time.time() - start_time) < time_limit):
            diving = False # A dive ends unless a promising child is chosen to continue

            if debugger:
                debugger.log_context_switch(-_bound, current_score, len(used_layouts), len(current_parts), parent_node_id)
            
            tree_nodes[parent_node_id]["status"] = "explored"
            tree_update_batch[parent_node_id] = tree_nodes[parent_node_id].copy()

            # --- Progress and Debugging ---
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
            
            # --- Pruning ---
            bound_check = _calculate_bound(
                list(current_parts.values()), current_score, config, reference_area, used_signatures,
                max_values, optional_parts_list, live_stock_counts, greedy_learned_rates, debugger=debugger
            )
            if bound_check < best_score:
                continue # End this dive path, go back to outer loop to pop a new path

            # --- Generate Child Nodes ---
            remaining_primary_parts = list(current_parts.values())
            candidate_actions = []
            unique_sheets = {f"{s['length']}x{s['width']}_{s.get('type', 'Default')}": s for s in stock_sheets}.values()
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

            potential_next_nodes = []
            for action in candidate_actions:
                layout = action['layout']
                placed_ids = {p['part_id'] for p in layout['placed_parts'] if p['part_id'] in current_parts}
                if not placed_ids: continue
                
                layout_sig = get_layout_signature(layout, required_only=False)
                is_new_signature = layout_sig not in used_signatures
                if is_new_signature and max_layouts > 0 and len(used_signatures) >= max_layouts: continue
                
                # --- Calculate Score Delta ---
                sheet_info = layout['sheet_info']
                sheet_area = sheet_info['length'] * sheet_info['width']
                used_area = layout['total_placed_area']
                min_offcut_dim = config['min_offcut_dim']
                usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
                offcut_area = sum(o['length'] * o['width'] for o in usable_offcuts)
                
                raw_waste, raw_offcuts = sheet_area - used_area - offcut_area, offcut_area
                raw_quantity = float(sheet_info.get('quantity', 1)) if sheet_info.get('type') != 'Default' else 0.0
                raw_priority = 1.0 if sheet_info.get('type') == 'Offcut' and str(sheet_info.get('id', '')).replace('offcut_', '') in config.get('prioritized_offcuts', []) else 0.0
                
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
                raw_cuts = _count_guillotine_cuts(layout)

                component_deltas = {}
                component_deltas['area_score'] = -((raw_waste / max_values['waste_area']) * config.get('w_waste', 1.0) + (raw_offcuts / max_values['offcut_area']) * config.get('w_offcut', 0.5))
                component_deltas['stock_bonus'] = (raw_stock_bonus / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
                component_deltas['quantity_bonus'] = (raw_quantity / max_values['quantity_bonus']) * config.get('w_quantity', 0.1)
                component_deltas['priority_bonus'] = (raw_priority / max_values['priority_bonus']) * config.get('w_priority_offcut', 10.0)
                component_deltas['sheets_penalty'] = -(1.0 / max_values['sheets']) * config.get('w_sheets', 1.0)
                component_deltas['cuts_penalty'] = -(raw_cuts / max_values['cuts']) * config.get('w_cuts', 0.2)
                component_deltas['layouts_penalty'] = -(1.0 / max_values['layouts']) * config.get('w_unique_layouts', 0.5) if is_new_signature else 0.0
                score_delta = sum(component_deltas.values())
                if debugger: debugger.log_true_scores(component_deltas)

                # --- Create Child Node State ---
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

                if not child_parts: # Found a solution
                    if child_score > best_score:
                        best_score, best_solution = child_score, (child_layouts, [])
                        
                        if preview_callback:
                            preview_callback(child_layouts, [], child_score, time.time() - start_time)

                        tree_nodes[child_node_id]["status"] = "solution"
                        tree_update_batch[child_node_id] = tree_nodes[child_node_id].copy()
                        if last_best_sol_node is not None and last_best_sol_node in tree_nodes:
                            tree_nodes[last_best_sol_node]["status"] = "explored"
                            tree_update_batch[last_best_sol_node] = tree_nodes[last_best_sol_node].copy()
                        last_best_sol_node = child_node_id
                else: # Not a solution, check if it's a promising path
                    child_bound = _calculate_bound(
                        list(child_parts.values()), child_score, config, reference_area, child_signatures,
                        max_values, optional_parts_list, child_stock_counts, greedy_learned_rates, debugger=debugger
                    )
                    if child_bound > best_score:
                        node_data = (child_bound, child_score, child_node_id, child_parts, child_layouts, child_signatures, child_stock_counts)
                        potential_next_nodes.append(node_data)
                    else:
                        tree_nodes[child_node_id]["status"] = "pruned_bound"
                        tree_update_batch[child_node_id] = tree_nodes[child_node_id].copy()

            if potential_next_nodes:
                # Sort potential children by bound to find the best one to dive into
                potential_next_nodes.sort(key=lambda x: x[0], reverse=True)
                
                # Best child continues the dive
                best_child_data = potential_next_nodes.pop(0)
                (
                    _bound, current_score, parent_node_id, current_parts, 
                    used_layouts, used_signatures, live_stock_counts
                ) = best_child_data
                diving = True # Signal to continue the inner loop with the new node's state

                # Push other promising children to the global queue
                for other_node in potential_next_nodes:
                    other_bound, *node_tuple = other_node
                    heapq.heappush(pq, (-other_bound, *node_tuple))
    
    if debugger:
        debugger.print_stats("Final Score Component Stats")

    final_layouts, remaining_parts = best_solution
    
    if greedy_layouts and not final_layouts:
        print("[DEBUG] Main search did not find a better solution; returning greedy solution.")
        final_layouts, remaining_parts = greedy_layouts, []
        best_score = renormalized_score

    return final_layouts, remaining_parts, best_score, list(tree_update_batch.values())