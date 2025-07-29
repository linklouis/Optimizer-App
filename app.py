from flask import Flask, request, render_template, flash
from werkzeug.utils import secure_filename
import os
import json
import pandas as pd
import traceback

from data_processing import process_uploaded_files
from optimizer import optimize_multiple_sheets

# --- Constants ---
CONFIG_FILE = 'config.json'
DEFAULT_CONFIG = {
    'default_length': 96, 'default_width': 48, 'hhos_path': '', 'offcut_path': '',
    'stock_inv_path': '', 'kerf': 0.125, 'allow_rotation': True,
    'min_offcut_dim': 4.0, 'use_offcuts': True, 'add_stock_parts': False,
    'search_time_limit': 1.0, 'selected_stock_parts': [], 'dark_mode': False,
    'w_waste': 1.0, 'w_offcut': 0.5, 'w_cuts': 0.2, 'w_quantity': 0.1,
    'w_sheets': 1.0, 'w_stock_fill': 1.0
}

app = Flask(__name__)
app.secret_key = os.urandom(24) # Required for flashing messages

# --- Configuration Handling ---
def load_config():
    """Loads configuration from JSON file, falling back to defaults."""
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
    return config

def save_config(config):
    """Saves configuration to a JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

def update_config_from_form(config, form_data):
    """Updates the config dictionary from form data."""
    # File Paths
    config['hhos_path'] = form_data.get('hhos_path', '').strip(' "')
    config['offcut_path'] = form_data.get('offcut_path', '').strip(' "')
    config['stock_inv_path'] = form_data.get('stock_inv_path', '').strip(' "')

    # General Settings
    config['default_length'] = form_data.get('default_length', type=float)
    config['default_width'] = form_data.get('default_width', type=float)
    config['kerf'] = form_data.get('kerf', type=float)
    config['min_offcut_dim'] = form_data.get('min_offcut_dim', type=float)
    config['search_time_limit'] = form_data.get('search_time_limit', type=float)
    config['allow_rotation'] = form_data.get('allow_rotation') == 'true'
    config['use_offcuts'] = form_data.get('use_offcuts') == 'true'
    config['add_stock_parts'] = form_data.get('add_stock_parts') == 'true'

    # UI & Stock Selection
    config['dark_mode'] = form_data.get('dark_mode_state') == 'on'
    config['selected_stock_parts'] = form_data.getlist('selected_stock_parts')

    # Scoring Weights
    for key in ['w_waste', 'w_offcut', 'w_cuts', 'w_quantity', 'w_sheets', 'w_stock_fill']:
        config[key] = form_data.get(key, type=float)
    
    return config

# --- Main Application Logic ---
def run_optimization(config, hhos_df, offcut_df, stock_inv_df):
    """Orchestrates the optimization process."""
    parts_to_cut = []
    if hhos_df is not None and not hhos_df.empty:
        parts_to_cut = hhos_df.to_dict('records')

    optional_stock_parts = []
    if config['add_stock_parts'] and stock_inv_df is not None:
        selected_mask = stock_inv_df['internal_ref'].isin(config['selected_stock_parts'])
        needy_mask = stock_inv_df['current_stock'] < stock_inv_df['target_stock']
        needy_stock = stock_inv_df[selected_mask & needy_mask].copy()
        if not needy_stock.empty:
            needy_stock['is_stock_part'] = True
            optional_stock_parts = needy_stock.to_dict('records')
            
    is_stock_only_job = not parts_to_cut and optional_stock_parts

    # Prepare stock sheets (Default + Offcuts)
    stock_sheets = [{
        'id': 'default_sheet', 'length': config['default_length'], 
        'width': config['default_width'], 'quantity': float('inf'), 'type': 'Default'
    }]
    if config['use_offcuts'] and offcut_df is not None:
        offcuts_with_id = offcut_df.copy()
        offcuts_with_id['id'] = 'offcut_' + offcuts_with_id.index.astype(str)
        offcuts_with_id['type'] = 'Offcut'
        stock_sheets.extend(offcuts_with_id.to_dict('records'))

    layouts, remaining_parts, final_score = optimize_multiple_sheets(
        parts_to_cut, stock_sheets, config, optional_stock_parts, is_stock_only_job
    )
    return layouts, remaining_parts, final_score

def get_layout_signature(layout):
    """Creates a unique string signature for a layout based on its geometry."""
    sheet_info = layout['sheet_info']
    sig_parts = [f"sheet_{sheet_info['length']:.3f}x{sheet_info['width']:.3f}"]
    
    # Sort parts by position to ensure consistent signature
    sorted_parts = sorted(layout['placed_parts'], key=lambda p: (p['x'], p['y']))
    
    for part in sorted_parts:
        p_length = part['width'] if part.get('rotated') else part['length']
        p_width = part['length'] if part.get('rotated') else part['width']
        sig_parts.append(f"part_{part['x']:.3f},{part['y']:.3f}_{p_width:.3f}x{p_length:.3f}")
        
    return "|".join(sig_parts)

def prepare_results_for_render(layouts, remaining_parts, final_score, config):
    """Processes and groups raw layout data for Jinja rendering."""
    if not layouts:
        return None

    total_placed_area = sum(l['total_placed_area'] for l in layouts)
    total_sheet_area = sum(l['sheet_info']['length'] * l['sheet_info']['width'] for l in layouts)
    overall_yield = (total_placed_area / total_sheet_area) * 100 if total_sheet_area > 0 else 0

    grouped_layouts = {}
    for layout in layouts:
        signature = get_layout_signature(layout)
        if signature not in grouped_layouts:
            sheet_info = layout['sheet_info']
            sheet_area = sheet_info['length'] * sheet_info['width']
            
            # This is the first time we see this layout, so process it
            processed_layout = {
                'placed_parts': layout['placed_parts'],
                'sheet_info': sheet_info,
                'yield_percentage': (layout['total_placed_area'] / sheet_area) * 100 if sheet_area > 0 else 0,
                'usable_offcuts': [r for r in layout['offcuts'] if r['length'] >= config['min_offcut_dim'] and r['width'] >= config['min_offcut_dim']],
                'quantity': 1 # Initialize quantity
            }
            grouped_layouts[signature] = processed_layout
        else:
            # We've seen this layout before, just increment the quantity
            grouped_layouts[signature]['quantity'] += 1

    return {
        'layouts': list(grouped_layouts.values()), 
        'remaining_parts': remaining_parts,
        'final_score': final_score, 
        'overall_yield': overall_yield
    }

# --- Flask Route ---
@app.route('/', methods=['GET', 'POST'])
def home():
    config = load_config()
    optimization_result = None
    all_stock_parts_list = []

    if request.method == 'POST':
        config = update_config_from_form(config, request.form)
        save_config(config)
        flash("Configuration saved successfully!", "success")

    try:
        hhos_df, offcut_df, stock_inv_df = process_uploaded_files(
            config['hhos_path'], config['offcut_path'], config['stock_inv_path'],
            config['default_length'], config['default_width']
        )
        if stock_inv_df is not None:
            all_stock_parts_list = stock_inv_df.to_dict('records')
    except Exception as e:
        flash(f"Error loading files for display: {e}", "danger")
        hhos_df, offcut_df, stock_inv_df = pd.DataFrame(), None, None

    if request.method == 'POST' and (config['hhos_path'] or config['stock_inv_path']):
        try:
            layouts, remaining, score = run_optimization(config, hhos_df, offcut_df, stock_inv_df)
            optimization_result = prepare_results_for_render(layouts, remaining, score, config)
            if optimization_result:
                flash("Optimization complete.", "success")
        except Exception as e:
            flash(f"An error occurred during optimization: {e}", "danger")
            traceback.print_exc()

    return render_template(
        'index.html', 
        config=config, 
        result=optimization_result, 
        all_stock_parts=all_stock_parts_list
    )

if __name__ == '__main__':
    app.run(debug=True, port=5001)