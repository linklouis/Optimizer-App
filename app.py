from flask import Flask, request, render_template
from data_processing import process_uploaded_files
import os
import json
import pandas as pd # Import pandas to create the initial file

app = Flask(__name__)

CONFIG_FILE = 'config.json'
UPLOAD_FOLDER = 'uploads'
# Correct the file extension here
MANAGED_OFFCUT_PATH = os.path.join(UPLOAD_FOLDER, 'managed_offcut_list.csv')

# Establish the base configuration with your default size
DEFAULT_CONFIG = {
    'default_length': 96,
    'default_width': 48,
    'hhos_path': '',
    'offcut_path': '', # This is now the optional override path
    'stock_path': ''
}

def setup_app():
    # Ensure the upload folder exists
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    # Create a default, empty offcut file if it doesn't exist
    if not os.path.exists(MANAGED_OFFCUT_PATH):
        print(f"Creating default offcut file at: {MANAGED_OFFCUT_PATH}")
        # Create an empty DataFrame with the correct columns and save it
        empty_offcuts = pd.DataFrame(columns=['length', 'width', 'quantity'])
        empty_offcuts.to_csv(MANAGED_OFFCUT_PATH, index=False)

def load_config():
    # Start with the defaults
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            # Update with saved values
            config.update(json.load(f))
    return config

@app.route('/', methods=['GET', 'POST'])
def home():
    message = None
    error = None
    config = load_config()

    if request.method == 'POST':
        # Update config from form data
        config['hhos_path'] = request.form.get('hhos_path', '').strip(' "')
        config['offcut_path'] = request.form.get('offcut_path', '').strip(' "') # Will be empty if not provided
        config['stock_path'] = request.form.get('stock_path', '').strip(' "')
        config['default_length'] = request.form.get('default_length', type=int, default=96)
        config['default_width'] = request.form.get('default_width', type=int, default=48)
        
        # Save the entire updated config
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        message = "Configuration saved successfully!"
    
    # Run processing if required paths are set
    if all([config.get('hhos_path'), config.get('stock_path')]):
        try:
            # Decide which offcut file to use: the user's override or the app's managed one
            offcut_to_use = config.get('offcut_path') or MANAGED_OFFCUT_PATH
            
            hhos_df, offcut_df, stock_df = process_uploaded_files(
                config['hhos_path'], offcut_to_use, config['stock_path']
            )
            if hhos_df is not None:
                print("--- Data processed successfully ---")
                print(f"Using offcut file: {offcut_to_use}")
                message = message or "Data loaded successfully from saved paths."
            else:
                error = "Processing failed. Check file paths and console."
        except Exception as e:
            error = f"An error occurred: {e}"
            print(f"Error details: {e}")

    # Pass the full config object to the template
    return render_template('index.html', message=message, error=error, config=config)

if __name__ == '__main__':
    setup_app()
    app.run(debug=True)