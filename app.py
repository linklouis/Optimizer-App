from flask import Flask, request, render_template
from data_processing import process_uploaded_files
import os
import json

app = Flask(__name__)

CONFIG_FILE = 'config.json'

# Establish the base configuration with your default size
DEFAULT_CONFIG = {
    'default_width': 96,
    'default_length': 48,
    'hhos_path': '',
    'offcut_path': '',
    'stock_path': ''
}

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
        config['offcut_path'] = request.form.get('offcut_path', '').strip(' "')
        config['stock_path'] = request.form.get('stock_path', '').strip(' "')
        config['default_width'] = request.form.get('default_width', type=int, default=96)
        config['default_length'] = request.form.get('default_length', type=int, default=48)
        
        # Save the entire updated config
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        message = "Configuration saved successfully!"
    
    # Run processing if paths are set
    if all([config.get('hhos_path'), config.get('offcut_path'), config.get('stock_path')]):
        try:
            hhos_df, offcut_df, stock_df = process_uploaded_files(
                config['hhos_path'], config['offcut_path'], config['stock_path']
            )
            if hhos_df is not None:
                print("--- Data processed successfully ---")
                print(f"Default Board Size: {config['default_width']}x{config['default_length']}")
                message = message or "Data loaded successfully from saved paths."
            else:
                error = "Processing failed. Check file paths and console."
        except Exception as e:
            error = f"An error occurred: {e}"
            print(f"Error details: {e}")

    # Pass the full config object to the template
    return render_template('index.html', message=message, error=error, config=config)

if __name__ == '__main__':
    app.run(debug=True)