from flask import Flask, request, render_template, flash
import os
import json
import pandas as pd
import traceback
from collections import Counter

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
    'search_time_limit': 1.0, 'selected_stock_parts': [], 'dark_mode': False,
    'w_waste': 1.0, 'w_offcut': 0.5, 'w_cuts': 0.2, 'w_quantity': 0.1,
    'w_sheets': 1.0, 'w_stock_fill': 1.0, 'w_unique_layouts': 0.5,
    'standardize_stock_layouts': True,
    'beam_width': 1 # MODIFIED: Replaced selection_randomness with beam_width
}

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- Configuration Handling ---
def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
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
    config['beam_width'] = form_data.get('beam_width', type=int) # MODIFIED
    config['allow_rotation'] = form_data.get('allow_rotation') == 'true'
    config['use_offcuts'] = form_data.get('use_offcuts') == 'true'
    config['add_stock_parts'] = form_data.get('add_stock_parts') == 'true'
    config['dark_mode'] = form_data.get('dark_mode_state') == 'on'
    config['selected_stock_parts'] = form_data.getlist('selected_stock_parts')
    config['standardize_stock_layouts'] = form_data.get('standardize_stock_layouts') == 'true'

    for key in ['w_waste', 'w_offcut', 'w_cuts', 'w_quantity', 'w_sheets', 'w_stock_fill', 'w_unique_layouts']:
        config[key] = form_data.get(key, type=float)
    
    return config

# --- Main Application Logic ---
def run_optimization(config, hhos_df, offcut_df, stock_inv_df):
    parts_to_cut = hhos_df.to_dict('records') if hhos_df is not None and not hhos_df.empty else []
    
    optional_stock_parts = []
    if config['add_stock_parts'] and stock_inv_df is not None:
        mask = (stock_inv_df['internal_ref'].isin(config['selected_stock_parts'])) & \
               (stock_inv_df['current_stock'] < stock_inv_df['target_stock'])
        needy_stock = stock_inv_df[mask].copy()
        if not needy_stock.empty:
            needy_stock['is_stock_part'] = True
            optional_stock_parts = needy_stock.to_dict('records')
            
    is_stock_only_job = not parts_to_cut and optional_stock_parts

    stock_sheets = [{'id': 'default_sheet', 'length': config['default_length'], 'width': config['default_width'], 'quantity': float('inf'), 'type': 'Default'}]
    if config['use_offcuts'] and offcut_df is not None:
        offcuts_with_id = offcut_df.copy()
        offcuts_with_id['id'] = 'offcut_' + offcuts_with_id.index.astype(str)
        offcuts_with_id['type'] = 'Offcut'
        stock_sheets.extend(offcuts_with_id.to_dict('records'))

    return optimize_multiple_sheets(parts_to_cut, stock_sheets, config, optional_stock_parts, is_stock_only_job)

def _calculate_live_stock_bonus(layout: dict, live_deficit_tracker: Counter, config: dict) -> float:
    stock_fill_bonus = 0.0
    sheet_area = layout['sheet_info']['length'] * layout['sheet_info']['width']
    if sheet_area == 0:
        return 0.0

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
            for layout in group:
                final_layouts[layout['original_index']] = layout
            continue

        unique_variations = {get_layout_signature(l): l for l in group}.values()
        scored_variations = []
        for v in unique_variations:
            is_stock_only_job = not any(not p.get('is_stock_part', False) for p in v['placed_parts'])
            base_score = calculate_layout_score(v, v['sheet_info'], config, config['min_offcut_dim'], config['default_length'] * config['default_width'], is_stock_only_job)
            stock_bonus_to_remove = _calculate_live_stock_bonus(v, live_deficit_tracker, config)
            scored_variations.append({
                'base_score': base_score - stock_bonus_to_remove,
                'layout': v
            })

        standardized_group = []
        for _ in range(len(group)):
            best_choice = max(
                scored_variations,
                key=lambda item: item['base_score'] + _calculate_live_stock_bonus(item['layout'], live_deficit_tracker, config)
            )
            
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


def prepare_results_for_render(layouts, remaining_parts, final_score, config):
    if not layouts: return None

    total_placed_area = sum(l['total_placed_area'] for l in layouts)
    total_sheet_area = sum(l['sheet_info']['length'] * l['sheet_info']['width'] for l in layouts)
    overall_yield = (total_placed_area / total_sheet_area) * 100 if total_sheet_area > 0 else 0

    grouped_layouts = {}
    for layout in layouts:
        use_required_sig = config.get('standardize_stock_layouts', False)
        signature = get_layout_signature(layout, required_only=use_required_sig)
            
        if signature not in grouped_layouts:
            sheet_info = layout['sheet_info']
            sheet_area = sheet_info['length'] * sheet_info['width']
            grouped_layouts[signature] = {
                'placed_parts': layout['placed_parts'],
                'sheet_info': sheet_info,
                'yield_percentage': (layout['total_placed_area'] / sheet_area) * 100 if sheet_area > 0 else 0,
                'usable_offcuts': [r for r in layout['offcuts'] if r['length'] >= config['min_offcut_dim'] and r['width'] >= config['min_offcut_dim']],
                'quantity': 1
            }
        else:
            grouped_layouts[signature]['quantity'] += 1

    return {
        'layouts': list(grouped_layouts.values()), 'remaining_parts': remaining_parts,
        'final_score': final_score, 'overall_yield': overall_yield,
        'num_unique_layouts': len(grouped_layouts)
    }

def get_initial_optional_parts(stock_inv_df, config):
    if not config['add_stock_parts'] or stock_inv_df is None:
        return []
    mask = (stock_inv_df['internal_ref'].isin(config['selected_stock_parts'])) & \
           (stock_inv_df['current_stock'] < stock_inv_df['target_stock'])
    needy_stock = stock_inv_df[mask].copy()
    if needy_stock.empty:
        return []
    needy_stock['is_stock_part'] = True
    return needy_stock.to_dict('records')


@app.route('/', methods=['GET', 'POST'])
def home():
    config = load_config()
    optimization_result, hhos_file_contents, all_stock_parts_list = None, [], []

    if request.method == 'POST':
        config = update_config_from_form(config, request.form)
        save_config(config)
        flash("Configuration saved successfully!", "success")

    for hhos_file in config.get('hhos_files', []):
        path = hhos_file.get('path')
        content_item = {'path': path, 'parts': [], 'error': None}
        if path and os.path.exists(path):
            try:
                df = extract_hhos_data(pd.read_csv(path, header=None))
                df['quantity'] *= hhos_file.get('multiplier', 1)
                content_item['parts'] = df.to_dict('records')
            except Exception as e:
                content_item['error'] = str(e)
        else:
            content_item['error'] = "File not found."
        hhos_file_contents.append(content_item)

    try:
        hhos_df, offcut_df, stock_inv_df = process_uploaded_files(
            config.get('hhos_files', []), config['offcut_path'], config['stock_inv_path'],
            config['default_length'], config['default_width']
        )
        if stock_inv_df is not None:
            all_stock_parts_list = stock_inv_df.to_dict('records')
    except Exception as e:
        flash(f"Error processing files: {e}", "danger")
        hhos_df, offcut_df, stock_inv_df = pd.DataFrame(), None, None

    if request.method == 'POST' and (config.get('hhos_files') or config['stock_inv_path']):
        try:
            layouts, remaining_parts, final_score = run_optimization(config, hhos_df, offcut_df, stock_inv_df)
            
            final_layouts = layouts
            if config.get('standardize_stock_layouts', False) and layouts:
                initial_optional_parts = get_initial_optional_parts(stock_inv_df, config)
                final_layouts = standardize_layouts(layouts, initial_optional_parts, config)
            
            optimization_result = prepare_results_for_render(final_layouts, remaining_parts, final_score, config)
            if optimization_result:
                flash("Optimization complete.", "success")
        except Exception as e:
            flash(f"An error occurred during optimization: {e}", "danger")
            traceback.print_exc()

    return render_template(
        'index.html', config=config, result=optimization_result, 
        all_stock_parts=all_stock_parts_list, hhos_file_contents=hhos_file_contents
    )

if __name__ == '__main__':
    app.run(debug=True, port=5001)