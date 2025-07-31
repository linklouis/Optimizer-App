from flask import Flask, request, render_template, flash, session, jsonify
import os
import json
import pandas as pd
import traceback
import time
from collections import Counter
import math

from data_processing import process_uploaded_files, extract_hhos_data
from optimizer import (
    optimize_multiple_sheets,
    calculate_layout_score,
    get_layout_signature
)

# --- Constants ---
CONFIG_FILE = 'config.json'
DEFAULT_CONFIG = {
    'default_length': 96, 'default_width': 48, 'hhos_files': [], 'offcut_path': '',
    'stock_inv_path': '', 'kerf': 0.125, 'allow_rotation': True,
    'min_offcut_dim': 4.0, 'use_offcuts': True, 'add_stock_parts': False,
    'search_time_limit': 5.0,
    'branching_factor': 1,
    'selected_stock_parts': [],
    'required_stock_quantities': {},
    'dark_mode': False,
    'w_waste': 1.0, 'w_offcut': 0.5, 'w_cuts': 0.2, 'w_quantity': 0.1,
    'w_sheets': 1.0, 'w_stock_fill': 1.0, 'w_unique_layouts': 0.5,
    'standardize_stock_layouts': True,
    'selected_offcuts': [],
    'prioritized_offcuts': [],
    'w_priority_offcut': 10.0,
    'selected_hhos_parts': []
}

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- OFFCUT RECALCULATION LOGIC ---
def find_all_empty_rectangles(sheet_info: dict, placed_parts: list) -> list:
    """
    Finds all empty space on the sheet by creating a grid from all part edges
    and identifying the empty cells.
    """
    TOLERANCE = 1e-6
    # Collect all unique x and y coordinates from the sheet and part boundaries
    all_x = {0, sheet_info['width']}
    all_y = {0, sheet_info['length']}

    for part in placed_parts:
        p_len = part['width'] if part.get('rotated') else part['length']
        p_wid = part['length'] if part.get('rotated') else part['width']
        all_x.add(part['x'])
        all_x.add(part['x'] + p_wid)
        all_y.add(part['y'])
        all_y.add(part['y'] + p_len)
    
    sorted_x = sorted([x for x in all_x if x <= sheet_info['width'] + TOLERANCE])
    sorted_y = sorted([y for y in all_y if y <= sheet_info['length'] + TOLERANCE])

    empty_cells = []
    # Iterate through the grid cells to find empty ones
    for i in range(len(sorted_x) - 1):
        for j in range(len(sorted_y) - 1):
            x, y = sorted_x[i], sorted_y[j]
            w, l = sorted_x[i+1] - x, sorted_y[j+1] - y

            if w < TOLERANCE or l < TOLERANCE:
                continue

            # Check if the center of the cell is occupied by a placed part
            cell_center_x = x + w / 2
            cell_center_y = y + l / 2
            is_occupied = False
            for part in placed_parts:
                p_len = part['width'] if part.get('rotated') else part['length']
                p_wid = part['length'] if part.get('rotated') else part['width']
                if (part['x'] - TOLERANCE <= cell_center_x < part['x'] + p_wid + TOLERANCE and
                    part['y'] - TOLERANCE <= cell_center_y < part['y'] + p_len + TOLERANCE):
                    is_occupied = True
                    break
            
            if not is_occupied:
                empty_cells.append({'x': x, 'y': y, 'width': w, 'length': l})

    return empty_cells

def consolidate_empty_space(rects: list, kerf: float) -> list:
    """
    Consolidates a list of rectangles by merging perfectly aligned adjacent ones.
    This process is repeated until no more merges can be made.
    """
    if len(rects) < 2:
        return rects

    TOLERANCE = 1e-6
    
    while True:
        merged_in_pass = False
        i = 0
        while i < len(rects):
            j = i + 1
            while j < len(rects):
                r1, r2 = rects[i], rects[j]
                
                merged_rect = None

                # Check for vertical merge (must have same width and x-coordinate)
                if abs(r1['x'] - r2['x']) < TOLERANCE and abs(r1['width'] - r2['width']) < TOLERANCE:
                    if abs(r1['y'] + r1['length'] - r2['y']) < TOLERANCE: # r2 is above r1
                        merged_rect = {'x': r1['x'], 'y': r1['y'], 'length': r1['length'] + r2['length'], 'width': r1['width']}
                    elif abs(r2['y'] + r2['length'] - r1['y']) < TOLERANCE: # r1 is above r2
                        merged_rect = {'x': r2['x'], 'y': r2['y'], 'length': r1['length'] + r2['length'], 'width': r1['width']}

                # Check for horizontal merge (must have same length and y-coordinate)
                if not merged_rect and abs(r1['y'] - r2['y']) < TOLERANCE and abs(r1['length'] - r2['length']) < TOLERANCE:
                    if abs(r1['x'] + r1['width'] - r2['x']) < TOLERANCE: # r2 is to the right of r1
                        merged_rect = {'x': r1['x'], 'y': r1['y'], 'length': r1['length'], 'width': r1['width'] + r2['width']}
                    elif abs(r2['x'] + r2['width'] - r1['x']) < TOLERANCE: # r1 is to the right of r2
                        merged_rect = {'x': r2['x'], 'y': r2['y'], 'length': r1['length'], 'width': r1['width'] + r2['width']}
                
                if merged_rect:
                    rects.pop(j)
                    rects[i] = merged_rect
                    merged_in_pass = True
                    j = i + 1 
                else:
                    j += 1
            i += 1

        if not merged_in_pass:
            break
            
    return rects

def find_best_offcut_in_area(area_rect, min_offcut_dim):
    """
    Finds the largest, most square-like rectangle within a given area.
    This simplified version just returns the area if it's large enough,
    as our consolidated areas will be rectangular.
    A more complex version could find inscribed rectangles in non-rectangular polygons.
    """
    if area_rect['width'] >= min_offcut_dim and area_rect['length'] >= min_offcut_dim:
        return area_rect
    return None

def find_largest_inscribed_rectangle(area: dict, min_offcut_dim: float) -> dict | None:
    """
    Finds the largest rectangle that fits inside a potentially non-rectangular area.
    For a simple rectangle, it just returns the rectangle if it's valid.
    This is a placeholder for a more complex algorithm if areas were non-rectangular.
    Since consolidate_empty_space now produces only rectangles, we just validate it.
    """
    if area['width'] >= min_offcut_dim and area['length'] >= min_offcut_dim:
        return area.copy()
    return None

def recalculate_offcuts_from_empty_space(empty_areas: list, min_offcut_dim: float, kerf: float) -> list:
    """
    Given a list of consolidated empty areas, iteratively harvests the best
    possible offcuts until no usable offcuts can be made.
    """
    final_offcuts = []
    
    # A list of areas to be processed. Initially, it's the consolidated empty spaces.
    workable_areas = empty_areas[:]

    while workable_areas:
        area_to_process = workable_areas.pop(0)
        
        # Find the largest possible rectangular offcut in the current area
        best_offcut = find_largest_inscribed_rectangle(area_to_process, min_offcut_dim)
        
        if best_offcut:
            final_offcuts.append(best_offcut)
            
            # After "cutting" the best_offcut, what remains of the area_to_process?
            # Since our `find_largest_inscribed_rectangle` for now handles only rectangular areas
            # and returns the area itself, there are no remainders. If the function was
            # more complex (handling L-shapes), we would calculate the remaining pieces here.
            
            # For example, if we took a piece out of an L-shape, we'd be left with one or two smaller rectangles.
            # remainder1 = {...}
            # remainder2 = {...}
            # if is_valid(remainder1): workable_areas.append(remainder1)
            # if is_valid(remainder2): workable_areas.append(remainder2)

    return final_offcuts

# --- Configuration Handling ---
def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
    config['required_stock_quantities'] = config.get('required_stock_quantities', {})
    return config

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

def update_config_from_form(config, form_data):
    hhos_paths = form_data.getlist('hhos_paths')
    hhos_multipliers = form_data.getlist('hhos_multipliers')
    config['hhos_files'] = []
    for path, mult_str in zip(hhos_paths, hhos_multipliers):
        path = path.strip(' "')
        if path:
            multiplier = int(mult_str) if mult_str.isdigit() else 1
            config['hhos_files'].append({'path': path, 'multiplier': multiplier})

    config['offcut_path'] = form_data.get('offcut_path', '').strip(' "')
    config['stock_inv_path'] = form_data.get('stock_inv_path', '').strip(' "')
    config['default_length'] = form_data.get('default_length', type=float)
    config['default_width'] = form_data.get('default_width', type=float)
    config['kerf'] = form_data.get('kerf', type=float)
    config['min_offcut_dim'] = form_data.get('min_offcut_dim', type=float)
    config['search_time_limit'] = form_data.get('search_time_limit', type=float)
    config['branching_factor'] = form_data.get('branching_factor', type=int)
    config['allow_rotation'] = form_data.get('allow_rotation') == 'true'
    config['use_offcuts'] = form_data.get('use_offcuts') == 'true'
    config['add_stock_parts'] = form_data.get('add_stock_parts') == 'true'
    config['dark_mode'] = form_data.get('dark_mode_state') == 'on'
    config['selected_stock_parts'] = form_data.getlist('selected_stock_parts')
    config['standardize_stock_layouts'] = form_data.get('standardize_stock_layouts') == 'true'
    
    config['required_stock_quantities'] = {}
    for key, value in form_data.items():
        if key.startswith('required_qty_') and value.isdigit() and int(value) > 0:
            ref_id = key.replace('required_qty_', '')
            config['required_stock_quantities'][ref_id] = int(value)

    config['selected_offcuts'] = form_data.getlist('selected_offcuts')
    config['prioritized_offcuts'] = form_data.getlist('prioritized_offcuts')
    config['selected_hhos_parts'] = form_data.getlist('selected_hhos_parts')

    for key in ['w_waste', 'w_offcut', 'w_cuts', 'w_quantity', 'w_sheets', 'w_stock_fill', 'w_unique_layouts', 'w_priority_offcut']:
        if form_data.get(key) is not None:
             config[key] = form_data.get(key, type=float)
    
    config.setdefault('part_quantities', {})
    for key, value in form_data.items():
        if key.startswith('part_qty_') and value.isdigit():
            part_id = key.replace('part_qty_', '')
            config['part_quantities'][part_id] = int(value)

    return config

# --- Scoring Logic ---
def calculate_ideal_score(parts_to_cut, optional_stock_parts, offcut_df, config):
    ideal_part_list = parts_to_cut + optional_stock_parts
    if not ideal_part_list:
        return 0.0

    total_ideal_part_area = sum(p['length'] * p['width'] * p.get('quantity', 1) for p in ideal_part_list)
    total_ideal_part_count = sum(p.get('quantity', 1) for p in ideal_part_list)

    ideal_sheets_penalty = 0.0
    ideal_quantity_bonus = 0.0
    remaining_area_to_place = total_ideal_part_area
    num_unique_layouts = 0
    unavoidable_waste_area = 0.0
    
    if config.get('use_offcuts') and offcut_df is not None and not offcut_df.empty:
        selected_ids = [int(i) for i in config.get('selected_offcuts', [])]
        available_offcuts = offcut_df.loc[offcut_df.index.isin(selected_ids)]
        
        if not available_offcuts.empty:
            offcuts = available_offcuts.copy()
            offcuts['area'] = offcuts['length'] * offcuts['width']
            offcuts = offcuts.sort_values(by='area', ascending=False).to_dict('records')
            
            num_used_offcuts = 0
            for offcut in offcuts:
                if remaining_area_to_place <= 0: break
                for _ in range(int(offcut.get('quantity', 1))):
                    if remaining_area_to_place <= 0: break
                    remaining_area_to_place -= offcut['area']
                    num_used_offcuts += 1
                    ideal_quantity_bonus += offcut.get('quantity', 1) * config.get('w_quantity', 0.1)
            
            if num_used_offcuts > 0:
                ideal_sheets_penalty += num_used_offcuts * config.get('w_sheets', 1.0)
                num_unique_layouts += 1 

    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    if reference_area > 0 and remaining_area_to_place > 0:
        min_default_sheets = math.ceil(remaining_area_to_place / reference_area)
        ideal_sheets_penalty += min_default_sheets * config.get('w_sheets', 1.0)
        unavoidable_waste_area = (min_default_sheets * reference_area) - remaining_area_to_place
        if min_default_sheets > 0:
            num_unique_layouts += 1
            
    ideal_waste_penalty = (unavoidable_waste_area / reference_area) * config.get('w_waste', 1.0) if reference_area > 0 else 0
    ideal_offcut_penalty = 0.0
    ideal_cuts_penalty = total_ideal_part_count * config.get('w_cuts', 0.2)
    ideal_unique_layouts_penalty = num_unique_layouts * config.get('w_unique_layouts', 0.5)
    total_ideal_penalty = ideal_waste_penalty + ideal_offcut_penalty + ideal_cuts_penalty + ideal_sheets_penalty + ideal_unique_layouts_penalty

    ideal_stock_fill_bonus = 0.0
    if reference_area > 0:
        for part in ideal_part_list:
            if part.get('is_stock_part'):
                deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
                target = part.get('target_stock', 0)
                if target > 0 and deficit > 0:
                    part_area = part['length'] * part['width']
                    bonus_per_item = (deficit / target) * (part_area / reference_area) * config.get('w_stock_fill', 1.0)
                    ideal_stock_fill_bonus += bonus_per_item * part.get('quantity', 1)

    total_ideal_bonus = ideal_stock_fill_bonus + ideal_quantity_bonus
    
    return total_ideal_bonus - total_ideal_penalty

def recalculate_global_summary(result_obj, config):
    """Recalculates all summary metrics based on the current list of layouts."""
    if not result_obj or not result_obj.get('layouts'):
        return result_obj

    layouts = result_obj['layouts']
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)

    total_score_from_layouts = 0
    total_placed_area = 0
    total_sheet_area = 0
    total_sheets = 0
    
    # Check if this is a job that ONLY contains optional stock parts to begin with
    is_stock_only_job = all(
        all(p.get('is_optional_filler', False) for p in layout['placed_parts'])
        for layout in layouts
    )

    for layout in layouts:
        sheet_info = layout['sheet_info']
        quantity = layout.get('quantity', 1)
        layout_sheet_area = sheet_info['length'] * sheet_info['width']
        
        layout['total_placed_area'] = sum(p['length'] * p['width'] for p in layout['placed_parts'])
        layout['yield_percentage'] = (layout['total_placed_area'] / layout_sheet_area) * 100 if layout_sheet_area > 0 else 0
        layout['usable_offcuts'] = [r for r in layout.get('offcuts', []) if r['length'] >= config['min_offcut_dim'] and r['width'] >= config['min_offcut_dim']]
        
        # --- Start of fix ---
        # Check for layouts that might now be invalid (e.g., only contain optional parts)
        is_now_stock_only = all(p.get('is_optional_filler', False) for p in layout['placed_parts'])

        # The `is_stock_only_job` parameter tells the score function how to treat this layout
        layout_score = calculate_layout_score(layout, sheet_info, config, config['min_offcut_dim'], reference_area, is_stock_only_job or is_now_stock_only)
        
        # Intercept -inf and replace it with a large negative number for safe JSON conversion
        if layout_score == -float('inf'):
            layout_score = -1e9 
        # --- End of fix ---
        
        total_score_from_layouts += layout_score * quantity

        total_placed_area += layout['total_placed_area'] * quantity
        total_sheet_area += layout_sheet_area * quantity
        total_sheets += quantity

    sheets_penalty = total_sheets * config.get('w_sheets', 1.0)
    unique_layouts_penalty = len(layouts) * config.get('w_unique_layouts', 0.5)
    final_score = total_score_from_layouts - sheets_penalty - unique_layouts_penalty
    result_obj['final_score'] = final_score

    result_obj['overall_yield'] = (total_placed_area / total_sheet_area) * 100 if total_sheet_area > 0 else 0
    result_obj['num_unique_layouts'] = len(layouts)

    ideal_score = result_obj.get('ideal_score', 0)
    score_percentage = 0.0
    if final_score >= ideal_score: score_percentage = 100.0
    elif ideal_score > 0: score_percentage = max(0.0, (final_score / ideal_score) * 100.0)
    elif ideal_score < 0: score_percentage = (ideal_score / final_score) * 100.0 if final_score != 0 else 0.0
    result_obj['score_percentage'] = max(0.0, min(score_percentage, 100.0))
    
    return result_obj

# --- Main Application Logic ---
def run_optimization(config, hhos_df, offcut_df, stock_inv_df):
    if hhos_df is not None and not hhos_df.empty:
        part_quantities = config.get('part_quantities', {})
        if part_quantities:
            hhos_df['quantity'] = hhos_df.apply(lambda r: part_quantities.get(r['unique_id'], r['quantity']), axis=1)
        selected_hhos_ids = config.get('selected_hhos_parts', [])
        hhos_df = hhos_df[hhos_df['unique_id'].isin(selected_hhos_ids)]

    hhos_parts = hhos_df.to_dict('records') if hhos_df is not None and not hhos_df.empty else []
    for part in hhos_parts: part['is_optional_filler'] = False

    required_stock_parts, optional_stock_parts = [], []
    if stock_inv_df is not None:
        required_qtys = config.get('required_stock_quantities', {})
        for ref_id, qty in required_qtys.items():
            part_series = stock_inv_df[stock_inv_df['internal_ref'] == ref_id]
            if not part_series.empty:
                part_dict = part_series.iloc[0].to_dict()
                part_dict.update({'quantity': qty, 'is_stock_part': True, 'is_optional_filler': False, 'label': part_dict.get('part_name_clean', 'STOCK')})
                required_stock_parts.append(part_dict)
                
        if config['add_stock_parts']:
            mask = (stock_inv_df['internal_ref'].isin(config['selected_stock_parts'])) & (stock_inv_df['current_stock'] < stock_inv_df['target_stock']) & (~stock_inv_df['internal_ref'].isin(required_qtys.keys()))
            needy_stock = stock_inv_df[mask].copy()
            if not needy_stock.empty:
                needy_stock['is_stock_part'] = True
                needy_stock['is_optional_filler'] = True
                optional_stock_parts = needy_stock.to_dict('records')

    parts_to_cut = hhos_parts + required_stock_parts
    is_stock_only_job = not parts_to_cut and optional_stock_parts
    stock_sheets = [{'id': 'default_sheet', 'length': config['default_length'], 'width': config['default_width'], 'quantity': float('inf'), 'type': 'Default'}]
    
    if config.get('use_offcuts') and offcut_df is not None and not offcut_df.empty:
        selected_ids = [int(i) for i in config.get('selected_offcuts', [])]
        filtered_offcuts = offcut_df.loc[offcut_df.index.isin(selected_ids)] if selected_ids else offcut_df
        if not filtered_offcuts.empty:
            offcuts_with_id = filtered_offcuts.copy()
            offcuts_with_id['id'] = 'offcut_' + offcuts_with_id.index.astype(str)
            offcuts_with_id['type'] = 'Offcut'
            stock_sheets.extend(offcuts_with_id.to_dict('records'))

    layouts, remaining, score = optimize_multiple_sheets(parts_to_cut, stock_sheets, config, optional_stock_parts, is_stock_only_job)
    return layouts, remaining, score, parts_to_cut

def _calculate_live_stock_bonus(layout: dict, live_deficit_tracker: Counter, config: dict) -> float:
    stock_fill_bonus = 0.0
    sheet_area = layout['sheet_info']['length'] * layout['sheet_info']['width']
    if sheet_area == 0: return 0.0
    w_stock_fill = config.get('w_stock_fill', 1.0)
    for part in layout.get('placed_parts', []):
        if part.get('is_stock_part') and live_deficit_tracker.get(part.get('internal_ref'), 0) > 0:
            part_area = part.get('length', 0) * part.get('width', 0)
            initial_deficit = part.get('target_stock', 1) - part.get('current_stock', 0)
            if initial_deficit > 0:
                stock_fill_bonus += (1 / initial_deficit) * (part_area / sheet_area) * w_stock_fill
    return stock_fill_bonus

def standardize_layouts(layouts: list, initial_optional_parts: list, config: dict) -> list:
    live_deficit_tracker = Counter({p['internal_ref']: int(p['target_stock'] - p['current_stock']) for p in initial_optional_parts})
    grouped_layouts = {}
    for i, layout in enumerate(layouts):
        layout['original_index'] = i
        signature = get_layout_signature(layout, required_only=True)
        grouped_layouts.setdefault(signature, []).append(layout)
    final_layouts = [None] * len(layouts)
    for signature, group in grouped_layouts.items():
        if len(group) <= 1:
            for layout in group: final_layouts[layout['original_index']] = layout
            continue
        unique_variations = {get_layout_signature(l): l for l in group}.values()
        scored_variations = []
        for v in unique_variations:
            is_stock_only_job = not any(not p.get('is_optional_filler', False) for p in v['placed_parts'])
            base_score = calculate_layout_score(v, v['sheet_info'], config, config['min_offcut_dim'], config['default_length'] * config['default_width'], is_stock_only_job)
            stock_bonus_to_remove = _calculate_live_stock_bonus(v, live_deficit_tracker, config)
            scored_variations.append({'base_score': base_score - stock_bonus_to_remove, 'layout': v})
        standardized_group = []
        for _ in range(len(group)):
            best_choice = max(scored_variations, key=lambda item: item['base_score'] + _calculate_live_stock_bonus(item['layout'], live_deficit_tracker, config))
            chosen_layout = best_choice['layout']
            standardized_group.append(chosen_layout)
            for part in chosen_layout.get('placed_parts', []):
                if part.get('is_stock_part'):
                    ref = part.get('internal_ref')
                    if live_deficit_tracker[ref] > 0: live_deficit_tracker[ref] -= 1
        for original_layout, standardized_layout in zip(group, standardized_group):
            final_layouts[original_layout['original_index']] = standardized_layout
    return final_layouts

def prepare_results_for_render(layouts, remaining_parts, final_score, config, elapsed_time, ideal_score, score_percentage):
    if not layouts: return None
    total_placed_area = sum(l['total_placed_area'] for l in layouts)
    total_sheet_area = sum(l['sheet_info']['length'] * l['sheet_info']['width'] for l in layouts if l['sheet_info']['length'] * l['sheet_info']['width'] > 0)
    overall_yield = (total_placed_area / total_sheet_area) * 100 if total_sheet_area > 0 else 0
    
    grouped_layouts = {}
    for layout in layouts:
        use_required_sig = config.get('standardize_stock_layouts', False)
        signature = get_layout_signature(layout, required_only=use_required_sig)
        if signature not in grouped_layouts:
            sheet_info = layout['sheet_info']
            
            if sheet_info.get('quantity') == float('inf'):
                sheet_info['quantity'] = None

            sheet_area = sheet_info['length'] * sheet_info['width']
            grouped_layouts[signature] = {
                'signature': signature,
                'placed_parts': layout['placed_parts'],
                'sheet_info': sheet_info,
                'yield_percentage': (layout['total_placed_area'] / sheet_area) * 100 if sheet_area > 0 else 0,
                'usable_offcuts': [r for r in layout.get('offcuts', []) if r['length'] >= config['min_offcut_dim'] and r['width'] >= config['min_offcut_dim']],
                'quantity': 1,
                'offcuts': layout.get('offcuts', [])
            }
        else:
            grouped_layouts[signature]['quantity'] += 1
            
    return {
        'layouts': list(grouped_layouts.values()), 'remaining_parts': remaining_parts,
        'final_score': final_score, 'ideal_score': ideal_score, 'score_percentage': score_percentage,
        'overall_yield': overall_yield, 'num_unique_layouts': len(grouped_layouts),
        'elapsed_time': elapsed_time,
        'config': {'default_width': config['default_width'], 'default_length': config['default_length']}
    }

def get_initial_optional_parts(stock_inv_df, config):
    if not config['add_stock_parts'] or stock_inv_df is None: return []
    required_refs = config.get('required_stock_quantities', {}).keys()
    mask = (stock_inv_df['internal_ref'].isin(config['selected_stock_parts'])) & (stock_inv_df['current_stock'] < stock_inv_df['target_stock']) & (~stock_inv_df['internal_ref'].isin(required_refs))
    needy_stock = stock_inv_df[mask].copy()
    if needy_stock.empty: return []
    needy_stock['is_stock_part'] = True
    return needy_stock.to_dict('records')

@app.route('/', methods=['GET', 'POST'])
def home():
    config = load_config()
    optimization_result, hhos_file_contents, all_stock_parts_list, all_offcuts_list = None, [], [], []
    
    if request.method == 'POST':
        session.pop('optimization_result', None)
        config = update_config_from_form(config, request.form)
        save_config(config)
        flash("Configuration saved successfully!", "success")

    all_hhos_part_ids = []
    for hhos_file in config.get('hhos_files', []):
        path, content_item = hhos_file.get('path'), {'path': hhos_file.get('path'), 'parts': [], 'error': None}
        if path and os.path.exists(path):
            try:
                temp_df = extract_hhos_data(pd.read_csv(path, header=None))
                temp_df['quantity'] *= hhos_file.get('multiplier', 1)
                temp_df['unique_id'] = [f"{path}_{i}" for i in range(len(temp_df))]
                all_hhos_part_ids.extend(temp_df['unique_id'].tolist())
                part_quantities = config.get('part_quantities', {})
                if part_quantities: temp_df['quantity'] = temp_df.apply(lambda r: part_quantities.get(r['unique_id'], r['quantity']), axis=1)
                content_item['parts'] = temp_df.to_dict('records')
            except Exception as e: content_item['error'] = str(e)
        else: content_item['error'] = "File not found."
        hhos_file_contents.append(content_item)

    if request.method == 'GET' and all_hhos_part_ids and not config.get('selected_hhos_parts'):
        config['selected_hhos_parts'] = all_hhos_part_ids

    try:
        hhos_df, offcut_df, stock_inv_df = process_uploaded_files(config.get('hhos_files', []), config['offcut_path'], config['stock_inv_path'], config['default_length'], config['default_width'])
        if stock_inv_df is not None: all_stock_parts_list = stock_inv_df.to_dict('records')
        if offcut_df is not None:
            if not config.get('selected_offcuts') and config.get('offcut_path'): config['selected_offcuts'] = [str(i) for i in offcut_df.index]
            offcut_df_for_template = offcut_df.copy()
            offcut_df_for_template['id'] = offcut_df_for_template.index.astype(str)
            all_offcuts_list = offcut_df_for_template.to_dict('records')
    except Exception as e:
        flash(f"Error processing files: {e}", "danger")
        traceback.print_exc()
        hhos_df, offcut_df, stock_inv_df = pd.DataFrame(), None, None

    if request.method == 'POST' and (config.get('hhos_files') or config['stock_inv_path'] or config.get('required_stock_quantities')):
        try:
            opt_start_time = time.time()
            layouts, remaining_parts, final_score, parts_to_cut = run_optimization(config, hhos_df, offcut_df, stock_inv_df)
            opt_elapsed_time = time.time() - opt_start_time
            initial_optional_parts = get_initial_optional_parts(stock_inv_df, config)
            final_layouts = standardize_layouts(layouts, initial_optional_parts, config) if config.get('standardize_stock_layouts', False) and layouts else layouts
            ideal_score = calculate_ideal_score(parts_to_cut, initial_optional_parts, offcut_df, config)
            score_percentage = 0.0
            if final_score >= ideal_score: score_percentage = 100.0
            elif ideal_score > 0: score_percentage = max(0.0, (final_score / ideal_score) * 100.0)
            elif ideal_score < 0: score_percentage = (ideal_score / final_score) * 100.0 if final_score != 0 else 0.0
            score_percentage = max(0.0, min(score_percentage, 100.0))
            optimization_result = prepare_results_for_render(final_layouts, remaining_parts, final_score, config, opt_elapsed_time, ideal_score, score_percentage)
            if optimization_result:
                session['optimization_result'] = optimization_result
                flash("Optimization complete.", "success")
            elif not layouts:
                 flash("Optimization timed out. Try increasing the search time.", "danger")
        except Exception as e:
            flash(f"An error occurred during optimization: {e}", "danger")
            traceback.print_exc()
    elif request.method == 'GET' and 'optimization_result' in session:
        optimization_result = session['optimization_result']

    return render_template('index.html', config=config, result=optimization_result, all_stock_parts=all_stock_parts_list, hhos_file_contents=hhos_file_contents, all_offcuts=all_offcuts_list)

@app.route('/update_layout', methods=['POST'])
def update_layout():
    if 'optimization_result' not in session: 
        return jsonify({"error": "No result found in session."}), 400
    
    data = request.get_json()
    action, layout_sig, item_index = data.get('action'), data.get('layout_signature'), data.get('item_index')
    if not all([action, layout_sig, item_index is not None]): 
        return jsonify({"error": "Missing required data."}), 400
    
    config = load_config()
    result = session['optimization_result']
    
    layout_to_modify = next((g for g in result['layouts'] if g.get('signature') == layout_sig), None)
            
    if not layout_to_modify: 
        return jsonify({"error": "Layout group not found."}), 404

    if action in ['remove_stock_part', 'remove_job_part']:
        if item_index < 0 or item_index >= len(layout_to_modify['placed_parts']): 
            return jsonify({"error": "Part index out of bounds."}), 400
        
        # Get the quantity of the entire layout group BEFORE modification
        quantity = layout_to_modify.get('quantity', 1)
        
        # Remove the part from the layout object
        part_to_remove = layout_to_modify['placed_parts'].pop(item_index)

        # If it's a job part, add one part back for each sheet in the group
        if action == 'remove_job_part':
            parts_to_add_back = [part_to_remove.copy() for _ in range(quantity)]
            result.setdefault('remaining_parts', []).extend(parts_to_add_back)

        # Check if that was the last part on the layout
        if not layout_to_modify['placed_parts']:
            # If yes, remove the entire layout group from the results
            result['layouts'] = [lo for lo in result['layouts'] if lo.get('signature') != layout_sig]
        else:
            # If no, update the existing layout group
            tiled_empty_space = find_all_empty_rectangles(layout_to_modify['sheet_info'], layout_to_modify['placed_parts'])
            new_offcuts = consolidate_empty_space(tiled_empty_space, config['kerf'])
            layout_to_modify['offcuts'] = new_offcuts
            
            use_req_sig = config.get('standardize_stock_layouts', False)
            new_signature = get_layout_signature(layout_to_modify, required_only=use_req_sig)
            layout_to_modify['signature'] = new_signature

    elif action == 'remove_offcut':
        # This logic remains unchanged
        usable_offcuts = [r for r in layout_to_modify.get('offcuts', []) if r['length'] >= config['min_offcut_dim'] and r['width'] >= config['min_offcut_dim']]
        
        if item_index < 0 or item_index >= len(usable_offcuts): 
            return jsonify({"error": "Offcut index out of bounds."}), 400
        
        offcut_to_remove = usable_offcuts[item_index]
        original_offcuts = layout_to_modify['offcuts']
        for i in range(len(original_offcuts) - 1, -1, -1):
            oc = original_offcuts[i]
            if all(abs(oc[k] - offcut_to_remove[k]) < 1e-6 for k in ['x', 'y', 'length', 'width']):
                original_offcuts.pop(i)
                break
    else: 
        return jsonify({"error": "Invalid action."}), 400
    
    # Recalculate all summary scores and update the result object
    updated_result = recalculate_global_summary(result, config)

    session['optimization_result'] = updated_result
    return jsonify(updated_result)


if __name__ == '__main__':
    app.run(debug=True, port=5001)