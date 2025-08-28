from flask import Flask, request, render_template, flash, session, jsonify, Response, redirect, url_for
import os
import json
import pandas as pd
import traceback
import time
import uuid
import cProfile
import pstats
import threading

from data_processing import process_uploaded_files, extract_hhos_data
from optimizer import optimize_multiple_sheets, _update_bounding_parameters_from_solution
from config_manager import load_config, save_config, update_config_from_form, DEFAULT_CONFIG
from geometry_utils import find_all_empty_rectangles, consolidate_empty_space
from results_manager import (
    calculate_ideal_score, 
    recalculate_global_summary,
    standardize_layouts,
    prepare_results_for_render
)

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- Global Task Management ---
tasks = {}
tasks_lock = threading.Lock()

def get_initial_optional_parts(stock_inv_df, config):
    if not config['add_stock_parts'] or stock_inv_df is None: return []
    required_refs = config.get('required_stock_quantities', {}).keys()
    mask = (stock_inv_df['internal_ref'].isin(config['selected_stock_parts'])) & (stock_inv_df['current_stock'] < stock_inv_df['target_stock']) & (~stock_inv_df['internal_ref'].isin(required_refs))
    needy_stock = stock_inv_df[mask].copy()
    if needy_stock.empty: return []
    needy_stock['is_stock_part'] = True
    return needy_stock.to_dict('records')

# --- Main Application Logic ---
def run_optimization_task(task_id, config):
    """The function that will run in a background thread."""
    pr = cProfile.Profile()
    pr.enable()

    try:
        # Create a cancellation event and store it in the task dictionary
        cancel_event = threading.Event()
        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]['cancel_event'] = cancel_event

        def progress_callback(progress_data):
            with tasks_lock:
                if task_id in tasks:
                    tasks[task_id].setdefault('progress', {}).update(progress_data)
        
        opt_start_time = time.time()
        
        # --- Corrected HHOS Data Processing ---
        # Process HHOS files here to ensure unique_ids are generated correctly before filtering
        all_hhos_parts_df = []
        DELIMITER = ':::'
        for hhos_file in config.get('hhos_files', []):
            path = hhos_file.get('path')
            if path and os.path.exists(path):
                try:
                    temp_df = extract_hhos_data(pd.read_csv(path, header=None))
                    temp_df['quantity'] *= hhos_file.get('multiplier', 1)
                    temp_df['unique_id'] = [f"{path}{DELIMITER}{i}" for i in range(len(temp_df))]
                    all_hhos_parts_df.append(temp_df)
                except Exception:
                    # Silently ignore files that fail to load in the backend
                    continue
        
        hhos_df = pd.concat(all_hhos_parts_df, ignore_index=True) if all_hhos_parts_df else pd.DataFrame()

        # Process other files
        _, offcut_df, stock_inv_df = process_uploaded_files([], config['offcut_path'], config['stock_inv_path'], config['default_length'], config['default_width'])
        
        if not hhos_df.empty:
            part_quantities = config.get('part_quantities', {})
            if part_quantities:
                hhos_df['quantity'] = hhos_df.apply(lambda r: part_quantities.get(r['unique_id'], r['quantity']), axis=1)
            
            # This filter now works correctly because unique_id exists
            selected_hhos_ids = config.get('selected_hhos_parts', [])
            hhos_df = hhos_df[hhos_df['unique_id'].isin(selected_hhos_ids)]

        hhos_parts = hhos_df.to_dict('records') if not hhos_df.empty else []
        for part in hhos_parts: part['is_optional_filler'] = False

        required_stock_parts, optional_stock_parts = [], []
        if stock_inv_df is not None:
            required_qtys = config.get('required_stock_quantities', {})
            for ref_id, qty in required_qtys.items():
                part_series = stock_inv_df[stock_inv_df['internal_ref'] == ref_id]
                if not part_series.empty:
                    part_dict = part_series.iloc[0].to_dict()
                    part_dict.update({'quantity': qty, 'is_stock_part': True, 'is_optional_filler': False, 'label': part_dict.get('part_name_clean', 'STOCK')})
                    required_stock_parts.append(part_dict)
                    
            if config['add_stock_parts']:
                mask = (stock_inv_df['internal_ref'].isin(config['selected_stock_parts'])) & (stock_inv_df['current_stock'] < stock_inv_df['target_stock']) & (~stock_inv_df['internal_ref'].isin(required_qtys.keys()))
                needy_stock = stock_inv_df[mask].copy()
                if not needy_stock.empty:
                    needy_stock['is_stock_part'] = True
                    needy_stock['is_optional_filler'] = True
                    optional_stock_parts = needy_stock.to_dict('records')

        parts_to_cut = hhos_parts + required_stock_parts
        is_stock_only_job = not parts_to_cut and optional_stock_parts
        stock_sheets = [{'id': 'default_sheet', 'length': config['default_length'], 'width': config['default_width'], 'quantity': float('inf'), 'type': 'Default'}]
        initial_optional_parts = get_initial_optional_parts(stock_inv_df, config)
        ideal_score = calculate_ideal_score(parts_to_cut, initial_optional_parts, offcut_df, config)
        
        # --- Data for callbacks ---
        total_initial_parts = sum(p.get('quantity', 1) for p in parts_to_cut)
        reference_area = config['default_length'] * config['default_width']

        def preview_callback(layouts, remaining, score, elapsed_time, max_values, previous_layouts=None):
            """Prepares and sends a preview and a log entry of a new best solution."""
            # --- Start: Log Generation Logic ---
            # This logic handles the complexity of a dynamic scoring system. When a new, better
            # solution is found, the parameters for normalizing the score (max_values) are updated.
            # To provide a fair "apples-to-apples" comparison for the log, we must rescore both
            # the new solution AND the previous one against the SAME updated normalization parameters.
            log_entry = {}
            # 1. Calculate the new max_values based on the new best solution
            new_max_values, _ = _update_bounding_parameters_from_solution(
                layouts, config, total_initial_parts, optional_stock_parts, reference_area, max_values
            )
            # 2. Rescore the new solution with its corresponding new max_values
            new_temp_obj = {'layouts': layouts, 'ideal_score': ideal_score, 'max_values': new_max_values}
            new_recalculated = recalculate_global_summary(new_temp_obj, config)
            log_entry['new_score'] = new_recalculated.get('final_score', 0)

            # 3. Rescore the previous solution using the new max_values for a fair comparison
            if previous_layouts:
                prev_temp_obj = {'layouts': previous_layouts, 'ideal_score': ideal_score, 'max_values': new_max_values}
                prev_recalculated = recalculate_global_summary(prev_temp_obj, config)
                log_entry['previous_rescored'] = prev_recalculated.get('final_score', 0)
            # --- End: Log Generation Logic ---

            final_layouts = standardize_layouts(layouts, initial_optional_parts, config) if config.get('standardize_stock_layouts', False) and layouts else layouts
            
            # Use the already calculated score and max_values for the preview render object
            score_percentage = new_recalculated.get('score_percentage', 0.0)
            preview_result = prepare_results_for_render(
                final_layouts, remaining, log_entry['new_score'], config, elapsed_time, ideal_score, score_percentage, new_max_values
            )
            
            with tasks_lock:
                if task_id in tasks:
                    progress = tasks[task_id].setdefault('progress', {})
                    progress['best_solution_preview'] = preview_result
                    progress['log_entry'] = log_entry # Add log entry to progress data
        
        max_offcut_quantity = 1.0
        if offcut_df is not None and not offcut_df.empty and 'quantity' in offcut_df.columns:
            max_val = offcut_df['quantity'].max()
            if pd.notna(max_val):
                max_offcut_quantity = float(max_val)

        if config.get('use_offcuts') and offcut_df is not None and not offcut_df.empty:
            selected_ids = [int(i) for i in config.get('selected_offcuts', [])]
            filtered_offcuts = offcut_df.loc[offcut_df.index.isin(selected_ids)] if selected_ids else offcut_df
            if not filtered_offcuts.empty:
                offcuts_with_id = filtered_offcuts.copy()
                offcuts_with_id['id'] = 'offcut_' + offcuts_with_id.index.astype(str)
                offcuts_with_id['type'] = 'Offcut'
                stock_sheets.extend(offcuts_with_id.to_dict('records'))

        layouts, remaining, score, final_tree_update, max_values = optimize_multiple_sheets(
            parts_to_cut, stock_sheets, config, optional_stock_parts, is_stock_only_job, progress_callback,
            max_offcut_quantity=max_offcut_quantity,
            cancellation_event=cancel_event,
            preview_callback=preview_callback
        )
        opt_elapsed_time = time.time() - opt_start_time
        
        final_layouts = standardize_layouts(layouts, initial_optional_parts, config) if config.get('standardize_stock_layouts', False) and layouts else layouts
        
        temp_result_obj = { 'layouts': final_layouts, 'ideal_score': ideal_score, 'max_values': max_values }
        
        updated_result_obj = recalculate_global_summary(temp_result_obj, config)
        final_score = updated_result_obj.get('final_score', score)
        score_percentage = updated_result_obj.get('score_percentage', 0.0)

        optimization_result = prepare_results_for_render(
            final_layouts, remaining, final_score, config, opt_elapsed_time, ideal_score, score_percentage, max_values
        )
        
        if optimization_result:
            optimization_result['final_tree_update'] = final_tree_update
        
        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]['status'] = 'complete'
                tasks[task_id]['result'] = optimization_result if optimization_result else {"error": "No layouts could be generated."}
    
    except Exception as e:
        traceback.print_exc()
        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]['status'] = 'error'
                tasks[task_id]['result'] = {"error": f"An error occurred: {e}"}
    finally:
        # --- Save Profiler Results ---
        pr.disable()
        pr.dump_stats("stats.prof") # Saves the results to a file

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        # This branch handles the "Load / Refresh Files" submission
        config = load_config()
        # Store old multipliers before updating to detect changes
        old_multipliers = {f['path']: f.get('multiplier', 1) for f in config.get('hhos_files', [])}

        config = update_config_from_form(config, request.form)
        
        new_multipliers = {f['path']: f.get('multiplier', 1) for f in config.get('hhos_files', [])}
        
        DELIMITER = ':::'
        part_quantities_to_clear = []

        # Identify parts whose file-level multiplier has changed
        for path, new_mult in new_multipliers.items():
            old_mult = old_multipliers.get(path)
            # If a multiplier changed, clear any manual overrides for parts from that file
            if old_mult is not None and int(old_mult) != int(new_mult):
                if 'part_quantities' in config:
                    for part_id in config['part_quantities']:
                        if part_id.startswith(f"{path}{DELIMITER}"):
                            part_quantities_to_clear.append(part_id)
        
        # Reset the quantities for those parts so the new multiplier takes effect
        if part_quantities_to_clear and 'part_quantities' in config:
            for part_id in part_quantities_to_clear:
                config['part_quantities'].pop(part_id, None)

        save_config(config)
        # Redirect to the GET route to show the updated page
        return redirect(url_for('home'))

    # This is the existing GET request logic
    config = load_config()
    hhos_file_contents, all_stock_parts_list, all_offcuts_list = [], [], []

    all_hhos_part_ids = []
    # Use a more robust delimiter for unique IDs
    DELIMITER = ':::'
    for hhos_file in config.get('hhos_files', []):
        path, content_item = hhos_file.get('path'), {'path': hhos_file.get('path'), 'parts': [], 'error': None}
        content_item['multiplier'] = hhos_file.get('multiplier', 1) # Add multiplier for display
        if path and os.path.exists(path):
            try:
                temp_df = extract_hhos_data(pd.read_csv(path, header=None))
                temp_df['quantity'] *= hhos_file.get('multiplier', 1)
                temp_df['unique_id'] = [f"{path}{DELIMITER}{i}" for i in range(len(temp_df))]
                all_hhos_part_ids.extend(temp_df['unique_id'].tolist())
                part_quantities = config.get('part_quantities', {})
                if part_quantities: temp_df['quantity'] = temp_df.apply(lambda r: part_quantities.get(r['unique_id'], r['quantity']), axis=1)
                content_item['parts'] = temp_df.to_dict('records')
            except Exception as e: content_item['error'] = str(e)
        else: content_item['error'] = "File not found."
        hhos_file_contents.append(content_item)

    # Auto-select parts from newly added files
    if config.get('hhos_files'):
        paths_with_selected_parts = set()
        for part_id in config.get('selected_hhos_parts', []):
            if DELIMITER in part_id:
                paths_with_selected_parts.add(part_id.split(DELIMITER)[0])
        
        current_paths = {f.get('path') for f in config.get('hhos_files', [])}
        new_paths = current_paths - paths_with_selected_parts
        
        if new_paths:
            newly_selected_parts = [
                pid for pid in all_hhos_part_ids if DELIMITER in pid and pid.split(DELIMITER)[0] in new_paths
            ]
            config['selected_hhos_parts'].extend(newly_selected_parts)

    try:
        _, offcut_df, stock_inv_df = process_uploaded_files(config.get('hhos_files', []), config['offcut_path'], config['stock_inv_path'], config['default_length'], config['default_width'])
        if stock_inv_df is not None: all_stock_parts_list = stock_inv_df.to_dict('records')
        if offcut_df is not None:
            if not config.get('selected_offcuts') and config.get('offcut_path'): config['selected_offcuts'] = [str(i) for i in offcut_df.index]
            offcut_df_for_template = offcut_df.copy()
            offcut_df_for_template['id'] = offcut_df_for_template.index.astype(str)
            all_offcuts_list = offcut_df_for_template.to_dict('records')
    except Exception as e:
        flash(f"Error processing files: {e}", "danger")
        traceback.print_exc()

    optimization_result = session.get('optimization_result')
    default_weights = {k: v for k, v in DEFAULT_CONFIG.items() if k.startswith('w_')}

    return render_template('index.html',
                           config=config,
                           result=optimization_result,
                           all_stock_parts=all_stock_parts_list,
                           hhos_file_contents=hhos_file_contents,
                           all_offcuts=all_offcuts_list,
                           default_weights=default_weights,
                           default_config=DEFAULT_CONFIG)

@app.route('/start_optimization', methods=['POST'])
def start_optimization():
    config = load_config()
    
    # Manually grab the offcut path and temporary flag from the form
    form_offcut_path = request.form.get('offcut_path', '').strip(' "')
    
    # Update and save the persistent config (this will skip saving the offcut path if temporary)
    config = update_config_from_form(config, request.form)
    save_config(config)

    # Now, for THIS RUN ONLY, set the config to use the path from the form.
    config['offcut_path'] = form_offcut_path

    task_id = str(uuid.uuid4())
    with tasks_lock:
        tasks[task_id] = {'status': 'running', 'progress': {}, 'result': None}
    
    thread = threading.Thread(target=run_optimization_task, args=(task_id, config))
    thread.daemon = True
    thread.start()
    
    return jsonify({"task_id": task_id})

@app.route('/stream/<task_id>')
def stream(task_id):
    def event_stream():
        while True:
            progress_data = None
            with tasks_lock:
                task = tasks.get(task_id)
                if not task:
                    break
                
                # Atomically get and clear the progress data to prevent re-sending
                if 'progress' in task and task['progress']:
                    progress_data = task['progress']
                    task['progress'] = {} # Clear it after reading
                
                status = task['status']

            if progress_data:
                yield f"event: progress\ndata: {json.dumps(progress_data)}\n\n"
            
            if status in ['complete', 'error']:
                with tasks_lock:
                    result = tasks[task_id]['result']
                    if task_id in tasks:
                        del tasks[task_id]
                
                yield f"event: done\ndata: {json.dumps(result)}\n\n"
                break
            
            time.sleep(0.1)

    return Response(event_stream(), mimetype='text/event-stream')


@app.route('/update_layout', methods=['POST'])
def update_layout():
    data = request.get_json()
    result = data.get('current_state')
    if not result:
        return jsonify({"error": "No current_state provided."}), 400
    
    action, layout_sig, item_index = data.get('action'), data.get('layout_signature'), data.get('item_index')
    if not all([action, layout_sig, item_index is not None]): 
        return jsonify({"error": "Missing required data."}), 400
    
    config = load_config()
    
    layout_to_modify = next((g for g in result['layouts'] if g.get('signature') == layout_sig), None)
            
    if not layout_to_modify: 
        return jsonify({"error": "Layout group not found."}), 404

    if action in ['remove_stock_part', 'remove_job_part']:
        if item_index < 0 or item_index >= len(layout_to_modify['placed_parts']): 
            return jsonify({"error": "Part index out of bounds."}), 400
        
        quantity = layout_to_modify.get('quantity', 1)
        part_to_remove = layout_to_modify['placed_parts'].pop(item_index)

        if action == 'remove_job_part':
            parts_to_add_back = [part_to_remove.copy() for _ in range(quantity)]
            result.setdefault('remaining_parts', []).extend(parts_to_add_back)

        if not layout_to_modify['placed_parts']:
            result['layouts'] = [lo for lo in result['layouts'] if lo.get('signature') != layout_sig]
        else:
            tiled_empty_space = find_all_empty_rectangles(layout_to_modify['sheet_info'], layout_to_modify['placed_parts'])
            new_offcuts = consolidate_empty_space(tiled_empty_space, config['kerf'])
            layout_to_modify['offcuts'] = new_offcuts
            
            # This needs get_layout_signature, which is in optimizer.py.
            # For simplicity, we can keep it here or import it.
            # Let's assume an import is better.
            from optimizer import get_layout_signature
            use_req_sig = config.get('standardize_stock_layouts', False)
            new_signature = get_layout_signature(layout_to_modify, required_only=use_req_sig)
            layout_to_modify['signature'] = new_signature

    elif action == 'remove_offcut':
        usable_offcuts = [r for r in layout_to_modify.get('offcuts', []) if r['length'] >= config['min_offcut_dim'] and r['width'] >= config['min_offcut_dim']]
        
        if item_index < 0 or item_index >= len(usable_offcuts): 
            return jsonify({"error": "Offcut index out of bounds."}), 400
        
        offcut_to_remove = usable_offcuts[item_index]
        original_offcuts = layout_to_modify['offcuts']
        for i in range(len(original_offcuts) - 1, -1, -1):
            oc = original_offcuts[i]
            if all(abs(oc[k] - offcut_to_remove[k]) < 1e-6 for k in ['x', 'y', 'length', 'width']):
                original_offcuts.pop(i)
                break
    else: 
        return jsonify({"error": "Invalid action."}), 400
    
    updated_result = recalculate_global_summary(result, config)
    return jsonify(updated_result)

@app.route('/restore_state', methods=['POST'])
def restore_state():
    new_state = request.get_json()
    if not new_state or 'layouts' not in new_state or 'final_score' not in new_state:
        return jsonify({"error": "Invalid state data provided."}), 400
    return jsonify(new_state)

@app.route('/cancel_optimization/<task_id>', methods=['POST'])
def cancel_optimization(task_id):
    """Signals the optimization thread to cancel."""
    with tasks_lock:
        if task_id in tasks and 'cancel_event' in tasks[task_id]:
            tasks[task_id]['cancel_event'].set() # Set the event to signal cancellation
    return jsonify({"message": "Cancellation requested."})


if __name__ == '__main__':
    app.run(debug=True, port=5001)