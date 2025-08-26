import math
from collections import Counter

# NOTE: The dependency on optimizer.calculate_layout_score for standardization is maintained.
from optimizer import calculate_layout_score, get_layout_signature

# --- NEW HELPERS FOR IDEAL SCORE CALCULATION ---

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
    """
    A simple, single-pass greedy packer to simulate an ideal layout.
    It places parts from the provided list (assumed sorted largest to smallest)
    into the first available space that fits.
    """
    placed_parts = []
    # Start with one empty rectangle representing the whole sheet
    empty_rects = [{'x': 0, 'y': 0, 'length': sheet['length'], 'width': sheet['width']}]
    
    # Get a set of IDs already placed to avoid trying to place them again
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
            
            # Add the part to our list of placed parts for this sheet
            placed_part = part.copy()
            placed_part.update({'rotated': rotated, 'x': rect['x'], 'y': rect['y']})
            placed_parts.append(placed_part)
            placed_ids.add(part['part_id'])

            # Split the remaining empty space into two new rectangles
            rect_above = {'x': rect['x'], 'y': rect['y'] + part_l, 'length': rect['length'] - part_l, 'width': rect['width']}
            rect_right = {'x': rect['x'] + part_w, 'y': rect['y'], 'length': rect['length'], 'width': rect['width'] - part_w}
            
            if rect_above['length'] > kerf and rect_above['width'] > kerf:
                empty_rects.append(rect_above)
            if rect_right['length'] > kerf and rect_right['width'] > kerf:
                empty_rects.append(rect_right)

    return placed_parts

# --- REBUILT IDEAL SCORE CALCULATION ---

def calculate_ideal_score(parts_to_cut: list, optional_stock_parts: list, offcut_df: 'pd.DataFrame', config: dict) -> float:
    """
    Calculates a more realistic ideal score by simulating a greedy packing strategy.
    """
    kerf = config.get('kerf', 0.125)
    allow_rotation = config.get('allow_rotation', True)
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    if reference_area <= 0: return 0.0

    # 1. Prepare a master list of every individual part that needs to be cut
    all_parts = parts_to_cut + optional_stock_parts
    expanded_parts = []
    part_id_counter = 0
    for part in all_parts:
        for _ in range(int(part.get('quantity', 1))):
            p_copy = part.copy()
            p_copy['part_id'] = part_id_counter
            expanded_parts.append(p_copy)
            part_id_counter += 1
    
    # Sort parts by area (descending). This is a crucial heuristic for the greedy packer.
    expanded_parts.sort(key=lambda p: p['length'] * p['width'], reverse=True)
    
    parts_to_place_ids = {p['part_id'] for p in expanded_parts}
    placed_part_details = {} # Store details of where each part was placed

    total_score = 0.0
    num_unique_layouts = 0
    
    def get_parts_to_place():
        """Returns a list of part dicts for parts that still need to be placed."""
        return [p for p in expanded_parts if p['part_id'] in parts_to_place_ids]

    # 2. Simulate placing parts on available offcuts first
    if config.get('use_offcuts') and offcut_df is not None and not offcut_df.empty:
        selected_ids = [int(i) for i in config.get('selected_offcuts', [])]
        prioritized_ids = config.get('prioritized_offcuts', [])
        available_offcuts = offcut_df.loc[offcut_df.index.isin(selected_ids)].copy()
        
        if not available_offcuts.empty:
            available_offcuts['area'] = available_offcuts['length'] * available_offcuts['width']
            available_offcuts['id'] = available_offcuts.index
            # Use largest offcuts first
            sorted_offcuts = available_offcuts.sort_values(by='area', ascending=False).to_dict('records')

            for offcut in sorted_offcuts:
                for _ in range(int(offcut.get('quantity', 1))):
                    if not parts_to_place_ids: break
                    
                    placed_on_this_offcut = _greedy_packer(offcut, get_parts_to_place(), kerf, allow_rotation)
                    
                    if placed_on_this_offcut:
                        num_unique_layouts += 1
                        sheet_area = offcut['length'] * offcut['width']
                        placed_area = sum(p['length'] * p['width'] for p in placed_on_this_offcut)
                        
                        waste_penalty = ((sheet_area - placed_area) / reference_area) * config.get('w_waste', 1.0)
                        cuts_penalty = len(placed_on_this_offcut) * config.get('w_cuts', 0.2)
                        
                        stock_fill_bonus = 0.0
                        for p in placed_on_this_offcut:
                            if p.get('is_stock_part'):
                                deficit = p.get('target_stock', 1) - p.get('current_stock', 0)
                                target = p.get('target_stock', 0)
                                if target > 0 and deficit > 0:
                                    stock_fill_bonus += (1 / target) * ((p['length'] * p['width']) / reference_area) * config.get('w_stock_fill', 1.0)
                            
                            parts_to_place_ids.remove(p['part_id'])

                        quantity_bonus = config.get('w_quantity', 0.1)
                        priority_bonus = config.get('w_priority_offcut', 15.0) if str(offcut['id']) in prioritized_ids else 0.0
                        
                        sheets_penalty = config.get('w_sheets', 1.0)
                        total_score += (stock_fill_bonus + quantity_bonus + priority_bonus) - (waste_penalty + cuts_penalty + sheets_penalty)

    # 3. Simulate placing remaining parts on default sheets
    default_sheet = {'length': config['default_length'], 'width': config['default_width']}
    while parts_to_place_ids:
        num_unique_layouts += 1
        placed_on_this_sheet = _greedy_packer(default_sheet, get_parts_to_place(), kerf, allow_rotation)
        
        if not placed_on_this_sheet:
            # If no parts can be placed on a fresh sheet, they are unplaceable.
            break

        sheet_area = default_sheet['length'] * default_sheet['width']
        placed_area = sum(p['length'] * p['width'] for p in placed_on_this_sheet)
        
        waste_penalty = ((sheet_area - placed_area) / reference_area) * config.get('w_waste', 1.0)
        cuts_penalty = len(placed_on_this_sheet) * config.get('w_cuts', 0.2)
        
        stock_fill_bonus = 0.0
        for p in placed_on_this_sheet:
            if p.get('is_stock_part'):
                deficit = p.get('target_stock', 1) - p.get('current_stock', 0)
                target = p.get('target_stock', 0)
                if target > 0 and deficit > 0:
                    stock_fill_bonus += (1 / target) * ((p['length'] * p['width']) / reference_area) * config.get('w_stock_fill', 1.0)
            
            parts_to_place_ids.remove(p['part_id'])
            
        sheets_penalty = config.get('w_sheets', 1.0)
        total_score += stock_fill_bonus - (waste_penalty + cuts_penalty + sheets_penalty)

    # 4. Apply final global penalties
    total_score -= num_unique_layouts * config.get('w_unique_layouts', 0.5)
    
    return total_score


# --- EXISTING FUNCTIONS (UNCHANGED) ---

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
    """
    Calculates the stock fill bonus for a layout variation during standardization.
    This ensures the best variation is chosen based on its ability to fill needed stock parts.
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
            base_score = calculate_layout_score(v, v['sheet_info'], config, config['min_offcut_dim'], reference_area, is_stock_only_job)
            
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


def prepare_preview_summary(layouts: list, config: dict) -> dict:
    """Creates a lightweight summary of a solution for real-time preview."""
    if not layouts:
        return None

    grouped_layouts = {}
    for layout in layouts:
        sig = get_layout_signature(layout, required_only=False)
        if sig not in grouped_layouts:
            grouped_layouts[sig] = {
                'layout_obj': layout,
                'quantity': 1
            }
        else:
            grouped_layouts[sig]['quantity'] += 1
            
    total_sheets = len(layouts)
    num_unique_layouts = len(grouped_layouts)
    
    total_placed_area = sum(l.get('total_placed_area', 0) for l in layouts)
    total_sheet_area = sum(l['sheet_info']['length'] * l['sheet_info']['width'] for l in layouts)
    overall_yield = (total_placed_area / total_sheet_area) * 100 if total_sheet_area > 0 else 0
    
    first_group = next(iter(grouped_layouts.values()), None)
    if not first_group: return None

    first_layout = first_group['layout_obj']
    sheet_info = first_layout['sheet_info']
    sheet_area = sheet_info['length'] * sheet_info['width']
    
    first_layout_summary = {
        'signature': get_layout_signature(first_layout, required_only=config.get('standardize_stock_layouts', False)),
        'placed_parts': first_layout['placed_parts'],
        'sheet_info': sheet_info,
        'yield_percentage': (first_layout.get('total_placed_area', 0) / sheet_area) * 100 if sheet_area > 0 else 0,
        'usable_offcuts': [r for r in first_layout.get('offcuts', []) if r['length'] >= config['min_offcut_dim'] and r['width'] >= config['min_offcut_dim']],
        'quantity': first_group['quantity']
    }
    
    return {
        'total_sheets': total_sheets,
        'num_unique_layouts': num_unique_layouts,
        'overall_yield': overall_yield,
        'first_layout': first_layout_summary,
        'config': {'default_width': config.get('default_width', 48)}
    }