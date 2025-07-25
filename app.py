from flask import Flask, request, render_template
from data_processing import process_uploaded_files
from optimizer import optimize_multiple_sheets
import os
import json
import pandas as pd
import numpy as np
import uuid

app = Flask(__name__)

CONFIG_FILE = 'config.json'
UPLOAD_FOLDER = 'uploads'
MANAGED_OFFCUT_PATH = os.path.join(UPLOAD_FOLDER, 'managed_offcut_list.csv')

DEFAULT_CONFIG = {
    'default_length': 96,
    'default_width': 48,
    'hhos_path': '',
    'offcut_path': '',
    'stock_path': '',
    'kerf': 0.125,
    'allow_rotation': True,
    'min_offcut_dim': 12.0,
    'use_offcuts': True,
    'w_waste': 1.0, 
    'w_offcut': 0.5, 
    'w_cuts': 0.2,
    'w_quantity': 0.1,
    'w_sheets': 1.0
}

def setup_app():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    if not os.path.exists(MANAGED_OFFCUT_PATH):
        pd.DataFrame(columns=['length', 'width', 'quantity']).to_csv(MANAGED_OFFCUT_PATH, index=False)

def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
    return config

@app.route('/', methods=['GET', 'POST'])
def home():
    message = None
    error = None
    config = load_config()
    optimization_result = None

    if request.method == 'POST':
        config['hhos_path'] = request.form.get('hhos_path', '').strip(' "')
        config['offcut_path'] = request.form.get('offcut_path', '').strip(' "')
        config['stock_path'] = request.form.get('stock_path', '').strip(' "')
        
        config['default_length'] = request.form.get('default_length', type=float, default=96)
        config['default_width'] = request.form.get('default_width', type=float, default=48)
        config['kerf'] = request.form.get('kerf', type=float, default=0.125)
        config['allow_rotation'] = request.form.get('allow_rotation') == 'true'
        config['min_offcut_dim'] = request.form.get('min_offcut_dim', type=float, default=4.0)
        config['use_offcuts'] = request.form.get('use_offcuts') == 'true'

        config['w_waste'] = request.form.get('w_waste', type=float, default=1.0)
        config['w_offcut'] = request.form.get('w_offcut', type=float, default=0.5)
        config['w_cuts'] = request.form.get('w_cuts', type=float, default=0.2)
        config['w_quantity'] = request.form.get('w_quantity', type=float, default=0.1)
        config['w_sheets'] = request.form.get('w_sheets', type=float, default=1.0)


        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        message = "Configuration saved successfully!"
    
    if all([config.get('hhos_path'), config.get('stock_path')]):
        try:
            hhos_df, offcut_df, stock_df = process_uploaded_files(
                config['hhos_path'], 
                config.get('offcut_path') or MANAGED_OFFCUT_PATH, 
                config['stock_path'],
                config['default_length'],
                config['default_width']
            )
            
            parts_to_cut = hhos_df[['part name', 'label', 'length', 'width', 'quantity']].to_dict('records')

            stock_sheets = []
            
            stock_sheets.append({
                'id': 'default_sheet',
                'length': config['default_length'],
                'width': config['default_width'],
                'quantity': float('inf'),
                'type': 'Default'
            })
            
            if stock_df is not None and not stock_df.empty:
                stock_df['id'] = 'stock_' + stock_df.index.astype(str)
                stock_df['type'] = 'Stock'
                stock_sheets.extend(stock_df.to_dict('records'))

            if config.get('use_offcuts') and offcut_df is not None and not offcut_df.empty:
                offcut_df['id'] = 'offcut_' + offcut_df.index.astype(str)
                offcut_df['type'] = 'Offcut'
                stock_sheets.extend(offcut_df.to_dict('records'))

            layouts, remaining_parts, final_score = optimize_multiple_sheets(
                parts_to_cut, stock_sheets, config
            )
            
            if layouts:
                message = message or "Optimization complete."
                processed_layouts = []
                for layout in layouts:
                    sheet_info = layout['sheet_info']
                    sheet_area = sheet_info['length'] * sheet_info['width']
                    yield_percentage = (layout['total_placed_area'] / sheet_area) * 100 if sheet_area > 0 else 0
                    
                    min_dim = config['min_offcut_dim']
                    usable_offcuts = [r for r in layout['offcuts'] if r['length'] >= min_dim and r['width'] >= min_dim]

                    processed_layouts.append({
                        'placed_parts': layout['placed_parts'],
                        'sheet_info': sheet_info,
                        'yield_percentage': yield_percentage,
                        'usable_offcuts': usable_offcuts,
                    })
                
                optimization_result = {
                    'layouts': processed_layouts,
                    'remaining_parts': remaining_parts,
                    'final_score': final_score
                }

        except Exception as e:
            error = f"An error occurred: {e}"
            print(f"Error details: {e}")

    return render_template('index.html', message=message, error=error, config=config, result=optimization_result)

if __name__ == '__main__':
    setup_app()
    app.run(debug=True)