# ... (imports and config setup) ...
from flask import Flask, request, render_template
from data_processing import process_uploaded_files
from optimizer import run_optimization
import os
import json
import pandas as pd

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
    'kerf': 0.125,  # Default kerf, e.g., 1/8 inch
    'allow_rotation': True
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
    
    layout_data = {
        'placed_parts': [],
        'sheet_length': config.get('default_length'),
        'sheet_width': config.get('default_width')
    }

    if request.method == 'POST':
        config['hhos_path'] = request.form.get('hhos_path', '').strip(' "')
        config['offcut_path'] = request.form.get('offcut_path', '').strip(' "')
        config['stock_path'] = request.form.get('stock_path', '').strip(' "')
        config['default_length'] = request.form.get('default_length', type=int, default=96)
        config['default_width'] = request.form.get('default_width', type=int, default=48)
        config['kerf'] = request.form.get('kerf', type=float, default=0.125)
        config['allow_rotation'] = request.form.get('allow_rotation') == 'true'

        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        message = "Configuration saved successfully!"
    
    if all([config.get('hhos_path'), config.get('stock_path')]):
        try:
            offcut_to_use = config.get('offcut_path') or MANAGED_OFFCUT_PATH
            hhos_df, offcut_df, stock_df = process_uploaded_files(
                config['hhos_path'], offcut_to_use, config['stock_path']
            )
            if hhos_df is not None:
                message = message or "Data loaded successfully."

                # --- Run the Optimization ---
                parts_to_cut = hhos_df[['part name', 'label', 'length', 'width', 'quantity']].copy()
                parts_to_cut['length'] = pd.to_numeric(parts_to_cut['length'])
                parts_to_cut['width'] = pd.to_numeric(parts_to_cut['width'])
                parts_list = parts_to_cut.to_dict('records')

                sheet_length = config['default_length']
                sheet_width = config['default_width']
                
                placed_parts, remaining_parts = run_optimization(
                    sheet_length,
                    sheet_width,
                    parts_list,
                    config['kerf'],
                    config['allow_rotation']
                )

                layout_data['placed_parts'] = placed_parts
                layout_data['sheet_length'] = sheet_length
                layout_data['sheet_width'] = sheet_width
                
                print("\n--- OPTIMIZATION RESULTS ---")
                print(f"Placed {len(placed_parts)} parts. {len(remaining_parts)} remaining.")

        except Exception as e:
            error = f"An error occurred: {e}"
            print(f"Error details: {e}")

    return render_template('index.html', message=message, error=error, config=config, layout=layout_data)

if __name__ == '__main__':
    setup_app()
    app.run(debug=True)