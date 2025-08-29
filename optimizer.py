import time
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

# Import the new consolidated scoring and utility functions
from scoring_utils import get_layout_signature, count_guillotine_cuts, calculate_solution_score
from geometry_utils import find_all_empty_rectangles, consolidate_empty_space
from config_manager import DEFAULT_CONFIG


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

# --- End of Debugging Class ---

# --- Consolidated & Optimized Helper Functions ---

def _finalize_layout_offcuts(layout: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Takes a layout with placed parts and correctly calculates and attaches the final,
    consolidated offcuts.
    """
    if 'placed_parts' not in layout or 'sheet_info' not in layout:
        layout['offcuts'] = []
        return layout

    tiled_empty_space = find_all_empty_rectangles(layout['sheet_info'], layout['placed_parts'])
    consolidated_offcuts = consolidate_empty_space(tiled_empty_space, config)
    layout['offcuts'] = consolidated_offcuts
    
    return layout


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
                           remaining_parts: Optional[List[Part]] = None) -> float:
    """Calculates a score for a given sheet layout based on its own merits (waste, offcuts, etc.)."""
    sheet_area = sheet_info['length'] * sheet_info['width']
    if sheet_area == 0 or reference_area == 0:
        return -float('inf')

    used_area = layout['total_placed_area']
    usable_offcuts = [o for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim]
    offcut_area = sum(o['length'] * o['width'] for o in usable_offcuts)
    
    area_based_score = -(((sheet_area - used_area - offcut_area) / reference_area * config.get('w_waste', DEFAULT_CONFIG['w_waste'])) +
                         ((offcut_area / reference_area) * config.get('w_offcut', DEFAULT_CONFIG['w_offcut'])))

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
        quantity_bonus = (float(sheet_info.get('quantity', 1)) / max_offcut_quantity) * config.get('w_quantity', DEFAULT_CONFIG['w_quantity'])

    priority_bonus = 0.0
    if sheet_info.get('type') == 'Offcut':
        offcut_id_str = str(sheet_info.get('id', '')).replace('offcut_', '')
        if offcut_id_str in config.get('prioritized_offcuts', DEFAULT_CONFIG['prioritized_offcuts']):
            priority_bonus = config.get('w_priority_offcut', DEFAULT_CONFIG['w_priority_offcut'])
    
    return area_based_score + quantity_bonus + priority_bonus


@numba.jit(nopython=True)
def _place_parts_in_rects_jit(parts_to_place_np: np.ndarray, initial_rects_np: np.ndarray,
                              kerf: float, allow_rotation: bool) -> Tuple[np.ndarray, np.ndarray]:
    """
    Core part placement algorithm, fully optimized with Numba to work on NumPy arrays.
    """
    empty_rects = np.zeros((initial_rects_np.shape[0] + parts_to_place_np.shape[0] * 2, 4), dtype=np.float64)
    empty_rects[:initial_rects_np.shape[0], :] = initial_rects_np
    num_empty_rects = initial_rects_np.shape[0]

    placed_parts = np.zeros((parts_to_place_np.shape[0], 6), dtype=np.float64)
    num_placed_parts = 0

    for i in range(parts_to_place_np.shape[0]):
        part_id, part_length, part_width = parts_to_place_np[i]

        best_rect_idx = -1
        is_rotated = False
        min_short_side_fit = float('inf')

        for j in range(num_empty_rects):
            rect_len, rect_wid = empty_rects[j, 2], empty_rects[j, 3]
            if part_length <= rect_len and part_width <= rect_wid:
                short_side_fit = min(rect_len - part_length, rect_wid - part_width)
                if short_side_fit < min_short_side_fit:
                    min_short_side_fit = short_side_fit
                    best_rect_idx = j
                    is_rotated = False

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
            
            rect_x, rect_y, rect_len, rect_wid = empty_rects[best_rect_idx]

            placed_parts[num_placed_parts, 0] = part_id
            placed_parts[num_placed_parts, 1] = rect_x
            placed_parts[num_placed_parts, 2] = rect_y
            placed_parts[num_placed_parts, 3] = part_length
            placed_parts[num_placed_parts, 4] = part_width
            placed_parts[num_placed_parts, 5] = 1.0 if is_rotated else 0.0
            num_placed_parts += 1

            empty_rects[best_rect_idx] = empty_rects[num_empty_rects - 1]
            num_empty_rects -= 1

            rect_above_len = rect_len - part_l - kerf
            rect_right_of_part_wid = rect_wid - part_w - kerf
            
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

    return placed_parts[:num_placed_parts], empty_rects[:num_empty_rects]


def _place_parts_in_rects(parts_to_place: List[Part], initial_rects: List[Rect], 
                          kerf: float, allow_rotation: bool) -> Tuple[List[Part], List[Rect]]:
    """
    Wrapper for the JIT-compiled placement function.
    """
    if not parts_to_place or not initial_rects:
        return [], initial_rects

    parts_np = np.array([[p['part_id'], p['length'], p['width']] for p in parts_to_place], dtype=np.float64)
    rects_np = np.array([[r['x'], r['y'], r['length'], r['width']] for r in initial_rects], dtype=np.float64)

    placed_parts_np, final_rects_np = _place_parts_in_rects_jit(parts_np, rects_np, kerf, allow_rotation)

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
                                  optional_stock_parts: Optional[List[Part]] = None) -> Dict:
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
    best_score = calculate_layout_score(best_layout, sheet_info, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job=is_stock_only_job)

    if optional_stock_parts and remaining_rects_after_job:
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
            candidate_score = calculate_layout_score(candidate_layout, sheet_info, config, config['min_offcut_dim'], reference_area, max_offcut_quantity, is_stock_only_job=is_stock_only_job)

            if candidate_score > best_score:
                best_score = candidate_score
                best_layout = candidate_layout
    
    # Finalize the offcuts for the chosen best layout before returning.
    best_layout = _finalize_layout_offcuts(best_layout, config)

    return best_layout

@numba.jit(nopython=True)
def _calculate_made_counts_jit(refs: np.ndarray, unique_refs: np.ndarray, live_counts_arr: np.ndarray) -> np.ndarray:
    """Calculates the cumulative count of each part reference, optimized with Numba."""
    num_parts = refs.shape[0]
    made_counts = np.zeros(num_parts, dtype=np.float64)
    
    temp_counts = live_counts_arr.copy()

    for i in range(num_parts):
        ref = refs[i]
        idx = np.searchsorted(unique_refs, ref)
        
        if idx < len(unique_refs) and unique_refs[idx] == ref:
            made_counts[i] = temp_counts[idx]
            temp_counts[idx] += 1
            
    return made_counts


def _calculate_optimistic_stock_bonus(remaining_parts: list, optional_parts_data: np.ndarray, 
                                      live_stock_counts: Counter, config: Dict[str, Any],
                                      learned_rates: Dict[str, float]) -> float:
    """
    Calculates a realistic upper bound for the stock fill bonus using vectorized NumPy operations.
    """
    is_mixed_job = any(not p.get('is_stock_part') for p in remaining_parts)
    if optional_parts_data.shape[0] == 0 or (not remaining_parts and is_mixed_job):
        return 0.0

    packing_density_for_stock = learned_rates.get('packing_density', 0.75)
    required_parts_area = sum(p['length'] * p['width'] for p in remaining_parts)
    total_future_area = (required_parts_area / packing_density_for_stock) if packing_density_for_stock > 0 else 0

    optimistic_available_area = max(0, total_future_area - required_parts_area)

    if optimistic_available_area <= 0:
        return 0.0

    w_stock_fill = config.get('w_stock_fill', DEFAULT_CONFIG['w_stock_fill'])
    # This flat bonus provides a direct, explicit incentive to make specific stock parts
    # that are below their target quantities, separate from the implicit bonus of reducing waste.
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)
    
    part_data = optional_parts_data
    refs = part_data[:, 0]
    unique_refs, counts = np.unique(refs, return_counts=True)
    live_counts_arr = np.array([live_stock_counts.get(ref_hash, 0) for ref_hash in unique_refs], dtype=np.float64)
    
    made_counts = _calculate_made_counts_jit(refs, unique_refs, live_counts_arr)

    target_stock = part_data[:, 1]
    current_stock = part_data[:, 2]
    part_areas = part_data[:, 3]
    
    initial_deficit = target_stock - current_stock
    live_deficit = initial_deficit - made_counts
    live_deficit[live_deficit < 0] = 0
    target_stock[target_stock <= 0] = 1.0
    
    potential_bonus = (live_deficit / target_stock) * flat_bonus_unit * w_stock_fill
    bonus_density = potential_bonus / (part_areas + 1e-9)

    sorted_indices = np.argsort(bonus_density)[::-1]
    sorted_bonuses = potential_bonus[sorted_indices]
    sorted_areas = part_areas[sorted_indices]
    
    cumulative_areas = np.cumsum(sorted_areas)
    full_fit_idx = np.searchsorted(cumulative_areas, optimistic_available_area, side='right')
    predicted_bonus = np.sum(sorted_bonuses[:full_fit_idx])
    
    if full_fit_idx < part_data.shape[0]:
        area_filled = cumulative_areas[full_fit_idx - 1] if full_fit_idx > 0 else 0
        remaining_area = optimistic_available_area - area_filled
        fractional_fit = remaining_area / sorted_areas[full_fit_idx]
        predicted_bonus += sorted_bonuses[full_fit_idx] * fractional_fit

    return predicted_bonus


def _predict_number_of_new_sheet_layouts(remaining_parts: list, used_signatures: frozenset, config: Dict) -> int:
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
            initial_rects = [{'x': 0, 'y': 0, 'length': config['default_length'], 'width': config['default_width']}]
            
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

    predicted_total_sheet_area = predicted_sheets * reference_area
    predicted_unused_area = max(0, predicted_total_sheet_area - remaining_area)
    offcut_ratio = learned_rates.get('offcut_ratio', 0.1)
    
    predicted_offcut_area = predicted_unused_area * offcut_ratio
    predicted_waste_area = predicted_unused_area * (1 - offcut_ratio)
    
    cuts_per_part = learned_rates.get('cuts_per_part', 1.2)
    predicted_cuts = len(remaining_parts) * cuts_per_part

    avg_quantity_bonus_per_sheet = learned_rates.get('avg_quantity_bonus_per_sheet', 0.0)
    raw_quantity_bonus = avg_quantity_bonus_per_sheet * predicted_sheets

    optimistic_new_layouts = _predict_number_of_new_sheet_layouts(remaining_parts, used_signatures, config)
    raw_stock_bonus = _calculate_optimistic_stock_bonus(remaining_parts, optional_parts_data_for_bound, live_stock_counts, config, learned_rates)

    area_penalty = (predicted_waste_area / max_values['waste_area']) * config.get('w_waste', DEFAULT_CONFIG['w_waste'])
    offcut_penalty = (predicted_offcut_area / max_values['offcut_area']) * config.get('w_offcut', DEFAULT_CONFIG['w_offcut'])
    stock_bonus = (raw_stock_bonus / max_values['stock_bonus']) * config.get('w_stock_fill', DEFAULT_CONFIG['w_stock_fill'])
    quantity_bonus = (raw_quantity_bonus / max_values['quantity_bonus']) * config.get('w_quantity', DEFAULT_CONFIG['w_quantity'])
    sheets_penalty = (predicted_sheets / max_values['sheets']) * config.get('w_sheets', DEFAULT_CONFIG['w_sheets'])
    layouts_penalty = (optimistic_new_layouts / max_values['layouts']) * config.get('w_unique_layouts', DEFAULT_CONFIG['w_unique_layouts'])
    cuts_penalty = (predicted_cuts / max_values['cuts']) * config.get('w_cuts', DEFAULT_CONFIG['w_cuts'])

    upper_bound = (current_score + stock_bonus + quantity_bonus - area_penalty - offcut_penalty - sheets_penalty - layouts_penalty - cuts_penalty)

    if debugger:
        debugger.log_predicted_scores({
            'area_score': -area_penalty - offcut_penalty,
            'stock_bonus': stock_bonus, 'quantity_bonus': quantity_bonus,
            'priority_bonus': 0.0, 'sheets_penalty': -sheets_penalty,
            'layouts_penalty': -layouts_penalty, 'cuts_penalty': -cuts_penalty
        })

    return upper_bound


def _evaluate_layout_action(layout: Dict, current_parts_ids: set, live_stock_counts: Counter, 
                            used_signatures: frozenset, max_values: Dict, config: Dict, 
                            debugger: Optional['ScoreDebugger']) -> Optional[Dict]:
    """
    Evaluates a single layout action, calculating its score delta and other metrics.
    """
    placed_ids = {p['part_id'] for p in layout['placed_parts'] if p['part_id'] in current_parts_ids}
    if not placed_ids:
        return None

    layout_sig = get_layout_signature(layout, required_only=False)
    is_new_signature = layout_sig not in used_signatures
    max_layouts = config.get('max_unique_layouts', DEFAULT_CONFIG['max_unique_layouts'])
    if is_new_signature and max_layouts > 0 and len(used_signatures) >= max_layouts:
        return None

    sheet_info = layout['sheet_info']
    min_offcut_dim = config.get('min_offcut_dim', DEFAULT_CONFIG['min_offcut_dim'])
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)   # Not actually in the config at the moment

    raw_components = Counter()
    usable_offcuts_area = sum(o['length'] * o['width'] for o in layout['offcuts'] if o['length'] >= min_offcut_dim and o['width'] >= min_offcut_dim)
    raw_components['waste_area'] = sheet_info['length'] * sheet_info['width'] - layout['total_placed_area'] - usable_offcuts_area
    raw_components['offcut_area'] = usable_offcuts_area
    raw_components['cuts'] = count_guillotine_cuts(layout)

    if sheet_info.get('type') != 'Default':
        raw_components['quantity_bonus'] = float(sheet_info.get('quantity', 1))
        offcut_id_str = str(sheet_info.get('id', '')).replace('offcut_', '')
        if offcut_id_str in config.get('prioritized_offcuts', DEFAULT_CONFIG['prioritized_offcuts']):
            raw_components['priority_bonus'] = 1.0

    raw_stock_bonus = 0.0
    temp_counts_for_layout = live_stock_counts.copy()
    for part in sorted(layout['placed_parts'], key=lambda p: p.get('part_id', 0)):
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

    component_deltas = {}
    if max_values.get('waste_area', 0) > 0:
        component_deltas['area_score'] = -(
            (raw_components['waste_area'] / max_values['waste_area']) * config.get('w_waste', DEFAULT_CONFIG['w_waste']) +
            (raw_components['offcut_area'] / max_values['offcut_area']) * config.get('w_offcut', DEFAULT_CONFIG['w_offcut'])
        )
    if max_values.get('stock_bonus', 0) > 0:
        component_deltas['stock_bonus'] = (raw_components['stock_bonus'] / max_values['stock_bonus']) * config.get('w_stock_fill', DEFAULT_CONFIG['w_stock_fill'])
    if max_values.get('quantity_bonus', 0) > 0:
        component_deltas['quantity_bonus'] = (raw_components['quantity_bonus'] / max_values['quantity_bonus']) * config.get('w_quantity', DEFAULT_CONFIG['w_quantity'])
    if max_values.get('priority_bonus', 0) > 0:
        component_deltas['priority_bonus'] = (raw_components['priority_bonus'] / max_values['priority_bonus']) * config.get('w_priority_offcut', DEFAULT_CONFIG['w_priority_offcut'])
    if max_values.get('sheets', 0) > 0:
        component_deltas['sheets_penalty'] = -(1.0 / max_values['sheets']) * config.get('w_sheets', DEFAULT_CONFIG['w_sheets'])
    if max_values.get('cuts', 0) > 0:
        component_deltas['cuts_penalty'] = -(raw_components['cuts'] / max_values['cuts']) * config.get('w_cuts', DEFAULT_CONFIG['w_cuts'])
    if is_new_signature and max_values.get('layouts', 0) > 0:
        component_deltas['layouts_penalty'] = -(1.0 / max_values['layouts']) * config.get('w_unique_layouts', DEFAULT_CONFIG['w_unique_layouts'])

    score_delta = sum(v for v in component_deltas.values() if v is not None)

    if debugger:
        debugger.log_true_scores(component_deltas)

    return {
        'layout': layout, 'placed_ids': placed_ids, 'score_delta': score_delta,
        'raw_components': raw_components, 'sig': layout_sig, 'is_new_signature': is_new_signature
    }


# TODO We're not using is_stock_only_job here (or anywhere?) Do we still need it? If a job is stock only, those parts would probably be listed as required, so we wouldn't need special handling?
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
    live_stock_counts = Counter()
    used_signatures = frozenset()

    while current_parts:
        if cancellation_event and cancellation_event.is_set():
            return None, -float('inf')
            
        candidate_actions = []
        unique_sheets = {f"{s['length']}x{s['width']}_{s.get('type', 'Default')}": s for s in stock_sheets}.values()
        current_parts_ids = set(current_parts.keys())

        for sheet_tpl in unique_sheets:
            if cancellation_event and cancellation_event.is_set():
                return None, -float('inf')

            layout = run_single_sheet_optimization(sheet_tpl, list(current_parts.values()), config, reference_area, max_offcut_quantity, optional_parts_list)
            
            eval_result = _evaluate_layout_action(
                layout, current_parts_ids, live_stock_counts, used_signatures, max_values, config, debugger
            )
            
            if eval_result:
                efficiency = eval_result['score_delta'] / len(eval_result['placed_ids']) if eval_result['placed_ids'] else -float('inf')
                eval_result['efficiency'] = efficiency
                candidate_actions.append(eval_result)

        if not candidate_actions:
            break

        best_action = max(candidate_actions, key=lambda x: x['efficiency'])
        
        greedy_layouts.append(best_action['layout'])
        current_parts = {pid: p for pid, p in current_parts.items() if pid not in best_action['placed_ids']}
        used_signatures = used_signatures.union({best_action['sig']})
        for part in best_action['layout']['placed_parts']:
            if part.get('is_stock_part'):
                live_stock_counts[part['internal_ref']] += 1

    if current_parts:
        return None, -float('inf')

    greedy_score = calculate_solution_score(greedy_layouts, max_values, config)
    return greedy_layouts, greedy_score


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
    total_cuts_in_solution = sum(count_guillotine_cuts(l) for l in solution_layouts)
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
    Implements an adaptive, iterative re-scoring of the top nodes in the priority queue.
    """
    if not pq:
        return []
    
    new_pq_list = []
    for node in pq:
        _stale_bound, score, _id, parts, layouts, sigs, stock_counts = node
        new_bound = _calculate_bound(
            list(parts.values()), score, config, reference_area, sigs,
            max_values, optional_parts_data, stock_counts, learned_rates, debugger
        )
        new_pq_list.append((-new_bound, score, _id, parts, layouts, sigs, stock_counts))
    
    heapq.heapify(new_pq_list)
    return new_pq_list


def optimize_multiple_sheets(parts_to_cut: List[Part], stock_sheets: List[Dict], config: Dict,
                             optional_stock_parts: Optional[List[Part]] = None,
                             is_stock_only_job: bool = False,
                             progress_callback=None,
                             max_offcut_quantity: float = 1.0,
                             cancellation_event: Optional['threading.Event'] = None,
                             preview_callback=None) -> Tuple[List[Dict], List[Part], float, list, dict]:
    """
    Optimizes cutting stock using a Branch and Bound search with a "deep dive" strategy.
    """
    start_time = time.time()
    time_limit = config.get('search_time_limit', DEFAULT_CONFIG['search_time_limit'])
    branching_factor = config.get('branching_factor', DEFAULT_CONFIG['branching_factor'])
    reference_area = config.get('default_length', DEFAULT_CONFIG['default_length']) * config.get('default_width', DEFAULT_CONFIG['default_width'])
    last_update_time = start_time
    
    debugger = ScoreDebugger() if DEBUG_SCORES else None
    last_debug_print_time = start_time
    debug_print_interval = 2.0

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
        
        optional_parts_list.sort(key=lambda p: p.get('target_stock', 1) - p.get('current_stock', 0), reverse=True)

    optional_parts_np = np.array([
        [
            hash(p['internal_ref']),
            p.get('target_stock', 1),
            p.get('current_stock', 0),
            p['length'] * p['width']
        ] for p in optional_parts_list
    ], dtype=np.float64) if optional_parts_list else np.empty((0, 4), dtype=np.float64)

    max_values = {}
    total_parts_area = sum(p['length'] * p['width'] for p in initial_parts_dict.values())
    max_values['total_initial_parts_area'] = total_parts_area
    max_values['sheets'] = (math.ceil(total_parts_area / reference_area) if reference_area > 0 else 1) + 2
    cuts_per_part_heuristic = config.get('cuts_per_part_heuristic', 4.0)    # Not actually in the config at the moment
    max_values['cuts'] = (total_initial_parts * cuts_per_part_heuristic) if total_initial_parts > 0 else 1.0
    max_values['layouts'] = max_values['sheets']

    max_raw_stock_bonus = 0.0
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)   # Not actually in the config at the moment
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

    greedy_layouts, greedy_score = _run_greedy_seed(
        initial_parts_dict, stock_sheets, config, optional_parts_list, is_stock_only_job,
        reference_area, max_offcut_quantity, max_values, debugger, cancellation_event
    )
    
    best_solution = ([], list(initial_parts_dict.values()))
    best_score = -float('inf')
    learned_rates = {}

    if greedy_layouts:
        max_values, learned_rates = _update_bounding_parameters_from_solution(
            greedy_layouts, config, total_initial_parts, optional_parts_list, reference_area, max_values
        )
        renormalized_score = calculate_solution_score(greedy_layouts, max_values, config)
        
        best_solution = (greedy_layouts, [])
        best_score = renormalized_score

        if preview_callback:
            preview_callback(greedy_layouts, [], best_score, time.time() - start_time, max_values, None)
            
    else:
        print("\n[WARN] Greedy seed did not find a complete solution. Using heuristic normalization.\n")

    initial_used_signatures = frozenset()
    initial_stock_counts = Counter()
    initial_bound = -_calculate_bound(
        list(initial_parts_dict.values()), 0.0, config, reference_area, initial_used_signatures,
        max_values, optional_parts_np, initial_stock_counts, learned_rates, debugger=debugger
    )
    
    node_counter = 1
    pq = [(initial_bound, 0.0, 0, initial_parts_dict, [], initial_used_signatures, initial_stock_counts)]
    
    while pq and (time_limit <= 0 or (time.time() - start_time) < time_limit):
        if cancellation_event and cancellation_event.is_set(): break

        (
            _bound, current_score, parent_node_id, current_parts, 
            used_layouts, used_signatures, live_stock_counts
        ) = heapq.heappop(pq)
        
        diving = True
        while diving and (time_limit <= 0 or (time.time() - start_time) < time_limit):
            if cancellation_event and cancellation_event.is_set(): break
            
            diving = False
            
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
                        sheet_tpl, remaining_primary_parts, config, reference_area, max_offcut_quantity, optional_parts_list)
                    if any(p.get('part_id') in current_parts for p in layout_attempt['placed_parts']):
                        candidate_actions.append({'type': 'generate', 'layout': layout_attempt})
            
            unique_layouts_in_path = {get_layout_signature(l): l for l in used_layouts}.values()
            for existing_layout in unique_layouts_in_path:
                if any(p['part_id'] in current_parts for p in existing_layout['placed_parts'] if 'part_id' in p):
                    candidate_actions.append({'type': 'reuse', 'layout': existing_layout})

            potential_next_nodes = []
            current_parts_ids = set(current_parts.keys())
            for action in candidate_actions:
                layout = action['layout']

                eval_result = _evaluate_layout_action(
                    layout, current_parts_ids, live_stock_counts, used_signatures, max_values, config, debugger
                )
                if not eval_result:
                    continue

                child_score = current_score + eval_result['score_delta']
                child_parts = {pid: p for pid, p in current_parts.items() if pid not in eval_result['placed_ids']}
                child_layouts = used_layouts + [layout]
                child_signatures = used_signatures.union({eval_result['sig']})
                
                child_stock_counts = live_stock_counts.copy()
                for part in layout['placed_parts']:
                    if part.get('is_stock_part'):
                        child_stock_counts[part['internal_ref']] += 1

                child_node_id = node_counter
                new_node = {"id": child_node_id, "parent": parent_node_id, "score": child_score, "status": "exploring"}
                tree_nodes[child_node_id] = new_node
                tree_update_batch[child_node_id] = new_node.copy()
                node_counter += 1

                if not child_parts:
                    if child_score > best_score:
                        previous_best_solution_layouts = best_solution[0] if best_solution[0] else None
                        best_solution = (child_layouts, [])
                        
                        if preview_callback:
                            preview_callback(child_layouts, [], child_score, time.time() - start_time, max_values, previous_best_solution_layouts)

                        tree_nodes[child_node_id]["status"] = "solution"
                        tree_update_batch[child_node_id] = new_node.copy()
                        if last_best_sol_node is not None and last_best_sol_node in tree_nodes:
                            tree_nodes[last_best_sol_node]["status"] = "explored"
                            tree_update_batch[last_best_sol_node] = tree_nodes[last_best_sol_node].copy()
                        last_best_sol_node = child_node_id

                        max_values, learned_rates = _update_bounding_parameters_from_solution(
                            child_layouts, config, total_initial_parts, optional_parts_list, reference_area, max_values
                        )
                        best_score = calculate_solution_score(child_layouts, max_values, config)
                        
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