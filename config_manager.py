import json
import os

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
    'w_waste': 1.0, 'w_offcut': 0.5, 'w_cuts': 0.2, 'w_quantity': 0.3,
    'w_sheets': 1.0, 'w_stock_fill': 3.0,'w_unique_layouts': 0.5,
    'standardize_stock_layouts': True,
    'selected_offcuts': [],
    'prioritized_offcuts': [],
    'w_priority_offcut': 15.0,
    'selected_hhos_parts': []
}

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

    if 'offcut_path' in form_data: config['offcut_path'] = form_data.get('offcut_path', '').strip(' "')
    if 'stock_inv_path' in form_data: config['stock_inv_path'] = form_data.get('stock_inv_path', '').strip(' "')
    if 'default_length' in form_data: config['default_length'] = form_data.get('default_length', type=float)
    if 'default_width' in form_data: config['default_width'] = form_data.get('default_width', type=float)
    if 'kerf' in form_data: config['kerf'] = form_data.get('kerf', type=float)
    if 'min_offcut_dim' in form_data: config['min_offcut_dim'] = form_data.get('min_offcut_dim', type=float)
    
    time_limit_val = form_data.get('search_time_limit', type=float)
    config['search_time_limit'] = time_limit_val if time_limit_val is not None else 0.0
    
    if 'branching_factor' in form_data: config['branching_factor'] = form_data.get('branching_factor', type=int)
    
    config['allow_rotation'] = form_data.get('allow_rotation') in ['true', 'on']
    config['use_offcuts'] = form_data.get('use_offcuts') in ['true', 'on']
    config['add_stock_parts'] = form_data.get('add_stock_parts') in ['true', 'on']
    config['dark_mode'] = form_data.get('dark_mode_state') in ['true', 'on']
    config['standardize_stock_layouts'] = form_data.get('standardize_stock_layouts') in ['true', 'on']

    config['selected_stock_parts'] = form_data.getlist('selected_stock_parts')
    config['selected_offcuts'] = form_data.getlist('selected_offcuts')
    config['prioritized_offcuts'] = form_data.getlist('prioritized_offcuts')
    config['selected_hhos_parts'] = form_data.getlist('selected_hhos_parts')

    config['required_stock_quantities'] = {}
    for key, value in form_data.items():
        if key.startswith('required_qty_') and value.isdigit() and int(value) > 0:
            ref_id = key.replace('required_qty_', '')
            config['required_stock_quantities'][ref_id] = int(value)

    for key in ['w_waste', 'w_offcut', 'w_cuts', 'w_quantity', 'w_sheets', 'w_stock_fill', 'w_unique_layouts', 'w_priority_offcut']:
        if key in form_data:
             config[key] = form_data.get(key, type=float)
    
    config.setdefault('part_quantities', {})
    for key, value in form_data.items():
        if key.startswith('part_qty_') and value.isdigit():
            part_id = key.replace('part_qty_', '')
            config['part_quantities'][part_id] = int(value)

    return config