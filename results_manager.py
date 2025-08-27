import math
from collections import Counter
import pandas as pd

# NOTE: The dependency on optimizer is now broken. All shared utils are in scoring_utils.
from optimizer import calculate_layout_score
from scoring_utils import get_layout_signature, calculate_solution_score
from geometry_utils import find_all_empty_rectangles, consolidate_empty_space, find_guillotine_cuts

# --- NEW HELPERS FOR NORMALIZED SCORE CALCULATION ---

def _estimate_max_values(all_parts: list, optional_stock_parts: list, offcut_df: pd.DataFrame, config: dict) -> dict:
    """
    Generates a heuristic-based 'max_values' dictionary for normalizing scores,
    mirroring the pre-greedy logic in the optimizer.
    """
    max_values = {}
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    if reference_area <= 0: return {}

    expanded_parts = []
    for part in all_parts:
        for _ in range(int(part.get('quantity', 1))):
            expanded_parts.append(part)

    total_parts_area = sum(p['length'] * p['width'] for p in expanded_parts)
    max_values['total_initial_parts_area'] = total_parts_area
    max_values['sheets'] = (math.ceil(total_parts_area / reference_area) if reference_area > 0 else 1) + 2
    
    total_initial_parts = len(expanded_parts)
    cuts_per_part_heuristic = config.get('cuts_per_part_heuristic', 4.0)
    max_values['cuts'] = (total_initial_parts * cuts_per_part_heuristic) if total_initial_parts > 0 else 1.0
    max_values['layouts'] = max_values['sheets']

    max_raw_stock_bonus = 0.0
    flat_bonus_unit = config.get('w_stock_fill_item_bonus', 0.05)
    temp_counts = Counter()
    
    expanded_optional = []
    for part in optional_stock_parts:
        deficit = int(part.get('target_stock', 1) - part.get('current_stock', 0))
        for _ in range(deficit):
            expanded_optional.append(part)

    for part in sorted(expanded_optional, key=lambda p: p.get('internal_ref', '')):
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

    max_offcut_quantity = 1.0
    if offcut_df is not None and not offcut_df.empty and 'quantity' in offcut_df.columns:
        max_val = offcut_df['quantity'].max()
        if pd.notna(max_val):
            max_offcut_quantity = float(max_val)

    max_values['waste_area'] = max_values['sheets'] * reference_area
    max_values['offcut_area'] = max_values['sheets'] * reference_area
    max_values['quantity_bonus'] = max_values['sheets'] * (max_offcut_quantity if max_offcut_quantity > 0 else 1.0)
    max_values['priority_bonus'] = 1.0
    
    return max_values

# --- REBUILT IDEAL SCORE CALCULATION ---

def _find_best_fit_for_ideal(part_length: float, part_width: float, empty_rects: list) -> int:
    """Finds the best empty rectangle for a part using a shortest-side-fit heuristic."""
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

def _greedy_packer(sheet: dict, parts: list, kerf: float, allow_rotation: bool) -> list:
    placed_parts = []
    empty_rects = [{'x': 0, 'y': 0, 'length': sheet['length'], 'width': sheet['width']}]
    placed_ids = set()

    for part in parts:
        if part.get('part_id') in placed_ids:
            continue

        orientations = [(part['length'], part['width'], False)]
        if allow_rotation and part['length'] != part['width']:
            orientations.append((part['width'], part['length'], True))

        best_fit_index = -1
        best_orientation = None

        for part_l, part_w, rotated in orientations:
            rect_index = _find_best_fit_for_ideal(part_l, part_w, empty_rects)
            if rect_index != -1:
                best_fit_index = rect_index
                best_orientation = (part_l, part_w, rotated)
                break
        
        if best_fit_index != -1:
            part_l, part_w, rotated = best_orientation
            rect = empty_rects.pop(best_fit_index)
            
            placed_part = part.copy()
            placed_part.update({'rotated': rotated, 'x': rect['x'], 'y': rect['y']})
            placed_parts.append(placed_part)
            placed_ids.add(part['part_id'])

            rect_above = {'x': rect['x'], 'y': rect['y'] + part_l, 'length': rect['length'] - part_l, 'width': rect['width']}
            rect_right = {'x': rect['x'] + part_w, 'y': rect['y'], 'length': rect['length'], 'width': rect['width'] - part_w}
            
            if rect_above['length'] > kerf and rect_above['width'] > kerf:
                empty_rects.append(rect_above)
            if rect_right['length'] > kerf and rect_right['width'] > kerf:
                empty_rects.append(rect_right)

    return placed_parts

def calculate_ideal_score(parts_to_cut: list, optional_stock_parts: list, offcut_df: 'pd.DataFrame', config: dict) -> float:
    """
    Calculates a realistic ideal score by simulating a greedy packing strategy
    and scoring it with the same normalized method as the optimizer.
    """
    all_parts_for_max = parts_to_cut + optional_stock_parts
    max_values = _estimate_max_values(all_parts_for_max, optional_stock_parts, offcut_df, config)
    if not max_values: return 0.0

    expanded_parts = []
    part_id_counter = 0
    for part in (parts_to_cut + optional_stock_parts):
        for _ in range(int(part.get('quantity', 1))):
            p_copy = part.copy()
            p_copy['part_id'] = part_id_counter
            expanded_parts.append(p_copy)
            part_id_counter += 1
    
    expanded_parts.sort(key=lambda p: p['length'] * p['width'], reverse=True)
    parts_to_place_ids = {p['part_id'] for p in expanded_parts}
    simulated_layouts = []
    
    def get_parts_to_place():
        return [p for p in expanded_parts if p['part_id'] in parts_to_place_ids]

    if config.get('use_offcuts') and offcut_df is not None and not offcut_df.empty:
        selected_ids = [int(i) for i in config.get('selected_offcuts', [])]
        available_offcuts = offcut_df.loc[offcut_df.index.isin(selected_ids)].copy()
        
        if not available_offcuts.empty:
            available_offcuts['area'] = available_offcuts['length'] * available_offcuts['width']
            available_offcuts['id'] = available_offcuts.index
            sorted_offcuts = available_offcuts.sort_values(by='area', ascending=False).to_dict('records')

            for offcut in sorted_offcuts:
                for _ in range(int(offcut.get('quantity', 1))):
                    if not parts_to_place_ids: break
                    
                    placed = _greedy_packer(offcut, get_parts_to_place(), config['kerf'], config['allow_rotation'])
                    if placed:
                        simulated_layouts.append({'placed_parts': placed, 'sheet_info': offcut})
                        for p in placed: parts_to_place_ids.remove(p['part_id'])

    default_sheet = {'length': config['default_length'], 'width': config['default_width'], 'type': 'Default'}
    while parts_to_place_ids:
        placed = _greedy_packer(default_sheet, get_parts_to_place(), config['kerf'], config['allow_rotation'])
        if not placed: break
        simulated_layouts.append({'placed_parts': placed, 'sheet_info': default_sheet})
        for p in placed: parts_to_place_ids.remove(p['part_id'])

    return calculate_solution_score(simulated_layouts, max_values, config)

# --- REBUILT GLOBAL SUMMARY RECALCULATION ---

def recalculate_global_summary(result_obj, config):
    if not result_obj or not result_obj.get('layouts'):
        return result_obj

    layouts = result_obj['layouts']
    max_values = result_obj.get('max_values')
    min_offcut_dim = config.get('min_offcut_dim', 0)

    total_placed_area = 0
    total_sheet_area = 0
    
    for layout in layouts:
        quantity = layout.get('quantity', 1)
        sheet_info = layout['sheet_info']
        sheet_area = sheet_info['length'] * sheet_info['width']
        
        layout['total_placed_area'] = sum(p['length'] * p['width'] for p in layout['placed_parts'])
        layout['yield_percentage'] = (layout['total_placed_area'] / sheet_area) * 100 if sheet_area > 0 else 0
        layout['usable_offcuts'] = [r for r in layout.get('offcuts', []) if r.get('length', 0) >= min_offcut_dim and r.get('width', 0) >= min_offcut_dim]
        
        total_placed_area += layout['total_placed_area'] * quantity
        total_sheet_area += sheet_area * quantity

    # Use the single source of truth for scoring
    result_obj['final_score'] = calculate_solution_score(layouts, max_values, config)
    
    # Update summary stats
    result_obj['overall_yield'] = (total_placed_area / total_sheet_area) * 100 if total_sheet_area > 0 else 0
    result_obj['num_unique_layouts'] = len({get_layout_signature(l) for l in layouts}) if layouts else 0

    ideal_score = result_obj.get('ideal_score', 0)
    final_score = result_obj.get('final_score', -float('inf'))
    score_percentage = 0.0
    if final_score >= ideal_score: score_percentage = 100.0
    elif ideal_score > 0: score_percentage = max(0.0, (final_score / ideal_score) * 100.0)
    elif ideal_score < 0 and final_score < 0: score_percentage = (ideal_score / final_score) * 100.0 if final_score != 0 else 0.0
    result_obj['score_percentage'] = max(0.0, min(score_percentage, 100.0))
    
    return result_obj

# --- STANDARDIZATION & RENDER PREPARATION ---

def _calculate_live_stock_bonus(layout: dict, live_deficit_tracker: Counter, config: dict) -> float:
    """
    Calculates the stock fill bonus for a layout variation during standardization.
    """
    stock_fill_bonus = 0.0
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    if reference_area == 0: return 0.0
    
    w_stock_fill = config.get('w_stock_fill', 1.0)

    for part in layout.get('placed_parts', []):
        if part.get('is_stock_part') and live_deficit_tracker.get(part.get('internal_ref'), 0) > 0:
            part_area = part.get('length', 0) * part.get('width', 0)
            target_stock = part.get('target_stock', 1)
            
            if target_stock > 0:
                bonus = (1 / target_stock) * (part_area / reference_area) * w_stock_fill
                stock_fill_bonus += bonus

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
            reference_area = config['default_length'] * config['default_width']
            base_score = calculate_layout_score(v, v['sheet_info'], config, config['min_offcut_dim'], reference_area, 1.0, is_stock_only_job)
            
            stock_bonus_to_remove = 0.0
            if reference_area > 0:
                for p in v.get('placed_parts', []):
                    if p.get('is_stock_part'):
                        deficit = p.get('target_stock', 1) - p.get('current_stock', 0)
                        target_stock = p.get('target_stock', 0)
                        if target_stock > 0 and deficit > 0:
                            part_area = p['length'] * p['width']
                            stock_bonus_to_remove += (1 / target_stock) * (part_area / reference_area) * config.get('w_stock_fill', 1.0)

            scored_variations.append({'base_score': base_score - stock_bonus_to_remove, 'layout': v})
        
        standardized_group = []
        for _ in range(len(group)):
            best_choice = max(scored_variations, key=lambda item: item['base_score'] + _calculate_live_stock_bonus(item['layout'], live_deficit_tracker, config))
            chosen_layout = best_choice['layout']
            standardized_group.append(chosen_layout)
            
            for part in chosen_layout.get('placed_parts', []):
                if part.get('is_stock_part'):
                    ref = part.get('internal_ref')
                    if live_deficit_tracker[ref] > 0: 
                        live_deficit_tracker[ref] -= 1

        for original_layout, standardized_layout in zip(group, standardized_group):
            final_layouts[original_layout['original_index']] = standardized_layout
            
    return final_layouts

def prepare_results_for_render(layouts, remaining_parts, final_score, config, elapsed_time, ideal_score, score_percentage, max_values=None):
    if not layouts: return None
    
    grouped_layouts = {}
    for layout in layouts:
        use_required_sig = config.get('standardize_stock_layouts', False)
        signature = get_layout_signature(layout, required_only=use_required_sig)
        if signature not in grouped_layouts:
            sheet_info = layout['sheet_info']
            
            if sheet_info.get('quantity') == float('inf'):
                sheet_info['quantity'] = None

            sheet_area = sheet_info['length'] * sheet_info['width']
            total_placed_area = sum(p['length'] * p['width'] for p in layout['placed_parts'])
            min_offcut_dim = config.get('min_offcut_dim', 0)
            
            layout['cuts'] = find_guillotine_cuts(sheet_info, layout['placed_parts'])
            
            grouped_layouts[signature] = {
                'signature': signature,
                'placed_parts': layout['placed_parts'],
                'sheet_info': sheet_info,
                'yield_percentage': (total_placed_area / sheet_area) * 100 if sheet_area > 0 else 0,
                'usable_offcuts': [r for r in layout.get('offcuts', []) if r['length'] >= min_offcut_dim and r['width'] >= min_offcut_dim],
                'quantity': 1,
                'offcuts': layout.get('offcuts', []),
                'cuts': layout.get('cuts', [])
            }
        else:
            grouped_layouts[signature]['quantity'] += 1
    
    # Convert to list and sort by sheet area (smallest to largest)
    layouts_list = list(grouped_layouts.values())
    layouts_list.sort(key=lambda l: l['sheet_info']['length'] * l['sheet_info']['width'])
    
    total_placed_area_final = sum(l['quantity'] * sum(p['length'] * p['width'] for p in l['placed_parts']) for l in layouts_list)
    total_sheet_area_final = sum(l['quantity'] * l['sheet_info']['length'] * l['sheet_info']['width'] for l in layouts_list)
    overall_yield = (total_placed_area_final / total_sheet_area_final) * 100 if total_sheet_area_final > 0 else 0
            
    return {
        'layouts': layouts_list, 'remaining_parts': remaining_parts,
        'final_score': final_score, 'ideal_score': ideal_score, 'score_percentage': score_percentage,
        'overall_yield': overall_yield, 'num_unique_layouts': len(grouped_layouts),
        'elapsed_time': elapsed_time,
        'config': {'default_width': config['default_width'], 'default_length': config['default_length']},
        'max_values': max_values or {}
    }