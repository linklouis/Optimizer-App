from flask import Flask, request, render_template, flash
from werkzeug.utils import secure_filename
import os
import json
import pandas as pd
import traceback

from data_processing import process_uploaded_files, extract_hhos_data
from optimizer import (
    optimize_multiple_sheets, 
    calculate_layout_score, 
    get_required_parts_signature, 
    get_full_layout_signature
)

# --- Constants ---
CONFIG_FILE = 'config.json'
DEFAULT_CONFIG = {
    'default_length': 96, 'default_width': 48, 'hhos_files': [], 'offcut_path': '',
    'stock_inv_path': '', 'kerf': 0.125, 'allow_rotation': True,
    'min_offcut_dim': 4.0, 'use_offcuts': True, 'add_stock_parts': False,
    'search_time_limit': 1.0, 'selected_stock_parts': [], 'dark_mode': False,
    'w_waste': 1.0, 'w_offcut': 0.5, 'w_cuts': 0.2, 'w_quantity': 0.1,
    'w_sheets': 1.0, 'w_stock_fill': 1.0,
    'standardize_stock_layouts': True
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
    config['allow_rotation'] = form_data.get('allow_rotation') == 'true'
    config['use_offcuts'] = form_data.get('use_offcuts') == 'true'
    config['add_stock_parts'] = form_data.get('add_stock_parts') == 'true'
    config['dark_mode'] = form_data.get('dark_mode_state') == 'on'
    config['selected_stock_parts'] = form_data.getlist('selected_stock_parts')
    config['standardize_stock_layouts'] = form_data.get('standardize_stock_layouts') == 'true'

    for key in ['w_waste', 'w_offcut', 'w_cuts', 'w_quantity', 'w_sheets', 'w_stock_fill']:
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

def standardize_layouts_post_optimization(layouts, config):
    """
    Finds the best variation for each layout signature and replaces all similar layouts
    with that best variation.
    """
    reference_area = config.get('default_length', 96) * config.get('default_width', 48)
    best_layouts_by_sig = {}

    # First pass: find the best layout for each signature
    for layout in layouts:
        signature = get_required_parts_signature(layout)
        is_stock_only_job = not any(not p.get('is_stock_part', False) for p in layout['placed_parts'])
        score = calculate_layout_score(layout, layout['sheet_info'], config, config['min_offcut_dim'], reference_area, is_stock_only_job)

        if signature not in best_layouts_by_sig or score > best_layouts_by_sig[signature]['score']:
            best_layouts_by_sig[signature] = {'layout': layout, 'score': score}

    # Second pass: build the new standardized list
    standardized_layouts = []
    for layout in layouts:
        signature = get_required_parts_signature(layout)
        standardized_layouts.append(best_layouts_by_sig[signature]['layout'])
    
    return standardized_layouts

def prepare_results_for_render(layouts, remaining_parts, final_score, config):
    """Processes and groups layout data for Jinja rendering."""
    if not layouts: return None

    total_placed_area = sum(l['total_placed_area'] for l in layouts)
    total_sheet_area = sum(l['sheet_info']['length'] * l['sheet_info']['width'] for l in layouts)
    overall_yield = (total_placed_area / total_sheet_area) * 100 if total_sheet_area > 0 else 0

    grouped_layouts = {}
    for layout in layouts:
        # --- MODIFIED: Use the correct signature based on the config setting ---
        if config.get('standardize_stock_layouts', False):
            # If standardized, all layouts with same required parts are identical.
            signature = get_required_parts_signature(layout)
        else:
            # If not standardized, every geometric variation is unique.
            signature = get_full_layout_signature(layout)
            
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
        'final_score': final_score, 'overall_yield': overall_yield
    }

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
            layouts, remaining_parts, _ = run_optimization(config, hhos_df, offcut_df, stock_inv_df)
            
            final_layouts = layouts
            if config.get('standardize_stock_layouts', False):
                final_layouts = standardize_layouts_post_optimization(layouts, config)

            reference_area = config.get('default_length', 96) * config.get('default_width', 48)
            total_score = 0
            for l in final_layouts:
                 is_stock_only_job = not any(not p.get('is_stock_part', False) for p in l['placed_parts'])
                 total_score += calculate_layout_score(l, l['sheet_info'], config, config['min_offcut_dim'], reference_area, is_stock_only_job)
            final_score = total_score - (config.get('w_sheets', 1.0) * len(final_layouts))
            
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