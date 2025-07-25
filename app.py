from flask import Flask, request, render_template
from data_processing import process_uploaded_files
from optimizer import run_optimization, calculate_layout_score
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
    'kerf': 0.125,
    'allow_rotation': True,
    'min_offcut_dim': 12.0,
    'multi_pass': True,
    'w_yield': 1.0, 'w_offcut': 0.5, 'w_cuts': 0.2
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
        config['min_offcut_dim'] = request.form.get('min_offcut_dim', type=float, default=4.0)
        config['multi_pass'] = request.form.get('multi_pass') == 'true'
        config['w_yield'] = request.form.get('w_yield', type=float, default=1.0)
        config['w_offcut'] = request.form.get('w_offcut', type=float, default=0.5)
        config['w_cuts'] = request.form.get('w_cuts', type=float, default=0.2)

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

                parts_to_cut = hhos_df[['part name', 'label', 'length', 'width', 'quantity']].copy()
                parts_to_cut['length'] = pd.to_numeric(parts_to_cut['length'])
                parts_to_cut['width'] = pd.to_numeric(parts_to_cut['width'])
                parts_list = parts_to_cut.to_dict('records')

                sheet_length = config['default_length']
                sheet_width = config['default_width']

                best_layout = None
                best_score = -1.0

                if config['multi_pass']:
                    print("--- Running Multi-Pass Optimization ---")
                    sort_strategies = {
                        'area': lambda p: p['length'] * p['width'],
                        'length': lambda p: p['length'],
                        'width': lambda p: p['width'],
                        'perimeter': lambda p: 2 * (p['length'] + p['width'])
                    }

                    for name, sort_key in sort_strategies.items():
                        print(f"Trying strategy: Sort by {name}")
                        sorted_parts = sorted(parts_list, key=sort_key, reverse=True)
                        layout = run_optimization(
                            sheet_length, sheet_width, sorted_parts,
                            config['kerf'], config['allow_rotation']
                        )
                        weights = {k: config[k] for k in ['w_yield', 'w_offcut', 'w_cuts']}
                        score = calculate_layout_score(layout, sheet_length, sheet_width, weights)
                        print(f"  - Score: {score:.2f}")

                        if score > best_score:
                            best_score = score
                            best_layout = layout
                            print(f"  - New best layout found!")
                else:
                    print("--- Running Single-Pass Optimization ---")
                    sorted_parts = sorted(parts_list, key=lambda p: p['length'] * p['width'], reverse=True)
                    best_layout = run_optimization(
                        sheet_length, sheet_width, sorted_parts,
                        config['kerf'], config['allow_rotation']
                    )

                # --- Analyze the final best layout ---
                placed_parts = best_layout['placed_parts']
                offcuts_and_scrap = best_layout['offcuts']

                total_parts_area = sum([(p['length'] * p['width']) for p in placed_parts])
                sheet_area = sheet_length * sheet_width
                yield_percentage = (total_parts_area / sheet_area) * 100 if sheet_area > 0 else 0
                
                min_dim = config['min_offcut_dim']
                usable_offcuts = [r for r in offcuts_and_scrap if r['length'] >= min_dim and r['width'] >= min_dim]

                layout_data.update({
                    'placed_parts': placed_parts,
                    'sheet_length': sheet_length,
                    'sheet_width': sheet_width,
                    'yield_percentage': yield_percentage,
                    'usable_offcuts': usable_offcuts
                })

        except Exception as e:
            error = f"An error occurred: {e}"
            print(f"Error details: {e}")

    return render_template('index.html', message=message, error=error, config=config, layout=layout_data)

if __name__ == '__main__':
    setup_app()
    app.run(debug=True)