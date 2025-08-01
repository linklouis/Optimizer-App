import math
from collections import Counter
from optimizer import calculate_layout_score, get_layout_signature

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
    if not result_obj or not result_obj.get('layouts'):
        return result_obj

    layouts = result_obj['layouts']
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)

    total_score_from_layouts = 0
    total_placed_area = 0
    total_sheet_area = 0
    total_sheets = 0
    
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
        
        is_now_stock_only = all(p.get('is_optional_filler', False) for p in layout['placed_parts'])
        layout_score = calculate_layout_score(layout, sheet_info, config, config['min_offcut_dim'], reference_area, is_stock_only_job or is_now_stock_only)
        
        if layout_score == -float('inf'):
            layout_score = -1e9 
        
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