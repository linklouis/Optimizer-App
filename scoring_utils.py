import numba
import numpy as np
from collections import Counter
from typing import Dict, Any

# --- Layout Utility Functions (Moved from Optimizer) ---

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
def _count_guillotine_cuts_jit(placed_parts_data: np.ndarray, sheet_width: float, sheet_length: float) -> int:
    """
    Numba-optimized core function to count guillotine cuts from NumPy array data.
    """
    if placed_parts_data.shape[0] == 0:
        return 0

    TOLERANCE = 1e-6
    
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

def count_guillotine_cuts(layout: Dict[str, Any]) -> int:
    """
    Wrapper function to count guillotine cuts. Extracts data for the jitted core.
    """
    placed_parts = layout.get('placed_parts', [])
    if not placed_parts:
        return 0
        
    placed_parts_data = np.array([
        [
            p['x'], p['y'], p['length'], p['width'], 
            1.0 if p.get('rotated') else 0.0
        ] 
        for p in placed_parts
    ], dtype=np.float64)
    
    sheet_info = layout['sheet_info']
    return _count_guillotine_cuts_jit(placed_parts_data, sheet_info['width'], sheet_info['length'])

# --- Master Scoring Function ---

def calculate_solution_score(layouts: list, max_values: dict, config: dict) -> float:
    """
    Calculates the total score of a complete solution using a specific set of normalization parameters.
    This is the single source of truth for scoring.
    """
    if not layouts or not max_values:
        return -float('inf')

    raw = Counter()
    live_stock_counts = Counter()
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)
    min_offcut_dim = config.get('min_offcut_dim', 0)

    for layout in layouts:
        sheet_info = layout['sheet_info']
        sheet_area = sheet_info['length'] * sheet_info['width']
        
        usable_offcuts = [o for o in layout.get('offcuts', []) if o.get('length', 0) >= min_offcut_dim and o.get('width', 0) >= min_offcut_dim]
        offcut_area = sum(o['length'] * o['width'] for o in usable_offcuts)
        
        placed_area = sum(p['length'] * p['width'] for p in layout['placed_parts'])
        raw['waste_area'] += sheet_area - placed_area - offcut_area
        raw['offcut_area'] += offcut_area
        raw['cuts'] += count_guillotine_cuts(layout)
        
        if sheet_info.get('type') != 'Default':
            raw['quantity_bonus'] += float(sheet_info.get('quantity', 1))
            offcut_id_str = str(sheet_info.get('id', '')).replace('offcut_', '')
            if offcut_id_str in config.get('prioritized_offcuts', []):
                raw['priority_bonus'] += 1.0
        
        for part in sorted(layout['placed_parts'], key=lambda p: p.get('part_id', 0)):
            if not part.get('is_stock_part'): continue
            ref = part['internal_ref']
            initial_deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
            target = part.get('target_stock', 0)
            if target <= 0: continue
            
            count_already_made = live_stock_counts.get(ref, 0)
            live_deficit = initial_deficit - count_already_made
            if live_deficit > 0:
                raw['stock_bonus'] += (live_deficit / target) * flat_bonus_unit
            live_stock_counts[ref] += 1

    # Normalize and sum components
    norm = Counter()
    if max_values.get('waste_area', 0) > 0:
        norm['area_score'] = -(
            (raw['waste_area'] / max_values['waste_area']) * config.get('w_waste', 1.0) +
            (raw['offcut_area'] / max_values['offcut_area']) * config.get('w_offcut', 0.5)
        )
    if max_values.get('stock_bonus', 0) > 0:
        norm['stock_bonus'] = (raw['stock_bonus'] / max_values['stock_bonus']) * config.get('w_stock_fill', 1.0)
    if max_values.get('quantity_bonus', 0) > 0:
        norm['quantity_bonus'] = (raw['quantity_bonus'] / max_values['quantity_bonus']) * config.get('w_quantity', 0.1)
    if max_values.get('priority_bonus', 0) > 0:
        norm['priority_bonus'] = (raw['priority_bonus'] / max_values['priority_bonus']) * config.get('w_priority_offcut', 10.0)
    if max_values.get('sheets', 0) > 0:
        norm['sheets_penalty'] = - (len(layouts) / max_values['sheets']) * config.get('w_sheets', 1.0)
    if max_values.get('layouts', 0) > 0:
        # BUG FIX: Correctly count unique layouts using their signatures.
        num_unique_layouts = len({get_layout_signature(l) for l in layouts})
        norm['layouts_penalty'] = - (num_unique_layouts / max_values['layouts']) * config.get('w_unique_layouts', 0.5)
    if max_values.get('cuts', 0) > 0:
        norm['cuts_penalty'] = - (raw['cuts'] / max_values['cuts']) * config.get('w_cuts', 0.2)
    
    return sum(norm.values())