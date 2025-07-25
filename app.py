from flask import Flask, request, render_template
from data_processing import process_uploaded_files
from optimizer import run_optimization # <-- Import the new function
import os
import json
import pandas as pd

app = Flask(__name__)

# --- (Keep your existing CONFIG, setup_app, and load_config functions) ---
CONFIG_FILE = 'config.json'
UPLOAD_FOLDER = 'uploads'
MANAGED_OFFCUT_PATH = os.path.join(UPLOAD_FOLDER, 'managed_offcut_list.csv')

DEFAULT_CONFIG = {
    'default_length': 96,
    'default_width': 48,
    'hhos_path': '',
    'offcut_path': '',
    'stock_path': ''
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
# --- (End of existing functions) ---

@app.route('/', methods=['GET', 'POST'])
def home():
    message = None
    error = None
    config = load_config()

    if request.method == 'POST':
        # (Keep your existing POST logic for saving the config)
        config['hhos_path'] = request.form.get('hhos_path', '').strip(' "')
        config['offcut_path'] = request.form.get('offcut_path', '').strip(' "')
        config['stock_path'] = request.form.get('stock_path', '').strip(' "')
        config['default_length'] = request.form.get('default_length', type=int, default=96)
        config['default_width'] = request.form.get('default_width', type=int, default=48)
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
                # 1. Prepare parts list from the DataFrame
                parts_to_cut = hhos_df[['part name', 'length', 'width', 'quantity']].copy()
                parts_to_cut['length'] = pd.to_numeric(parts_to_cut['length'])
                parts_to_cut['width'] = pd.to_numeric(parts_to_cut['width'])
                parts_list = parts_to_cut.to_dict('records')

                # 2. Get sheet dimensions from config
                sheet_length = config['default_length']
                sheet_width = config['default_width']
                
                # 3. Call the optimizer
                placed_parts, remaining_parts = run_optimization(sheet_length, sheet_width, parts_list)

                # 4. Print results to console (for now)
                print("\n--- OPTIMIZATION RESULTS ---")
                print(f"Placed {len(placed_parts)} parts on the sheet.")
                for part in placed_parts:
                    print(f"  - {part['part name']}: at (x={part['x']:.2f}, y={part['y']:.2f})")
                
                if remaining_parts:
                    print(f"\nCould not place {len(remaining_parts)} parts.")

        except Exception as e:
            error = f"An error occurred: {e}"
            print(f"Error details: {e}")

    return render_template('index.html', message=message, error=error, config=config)

if __name__ == '__main__':
    setup_app()
    app.run(debug=True)