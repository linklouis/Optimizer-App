def run_optimization(sheet_length, sheet_width, parts, kerf=0.0, allow_rotation=True):
    """
    Places parts onto a sheet, accounting for kerf and rotation.
    """
    parts_to_place = []
    for part in parts:
        for _ in range(int(part['quantity'])):
            parts_to_place.append({
                'part name': part['part name'],
                'label': part['label'],
                'length': part['length'],
                'width': part['width'],
                'rotated': False # Add rotation flag
            })

    parts_to_place.sort(key=lambda p: p['length'] * p['width'], reverse=True) # Sort by area

    placed_parts = []
    remaining_parts = list(parts_to_place) # Start with all parts as remaining
    
    shelf_x = 0.0
    shelf_y = 0.0
    shelf_length = 0.0

    while len(remaining_parts) > 0:
        part_placed_on_shelf = False
        
        # Find the next part that can fit
        for i, part in enumerate(remaining_parts):
            
            # Try both original and rotated orientations
            orientations = [(part['length'], part['width'], False)]
            if allow_rotation and part['length'] != part['width']:
                orientations.append((part['width'], part['length'], True))

            for part_l, part_w, rotated in orientations:
                if shelf_y == 0 and shelf_x == 0: # Starting the very first shelf
                    shelf_length = part_l

                # Does it fit vertically on the sheet?
                if shelf_y + part_l <= sheet_length:
                    # Does it fit horizontally on the current shelf?
                    if shelf_x + part_w <= sheet_width:
                        
                        # --- Place the part ---
                        part_to_place = remaining_parts.pop(i)
                        part_to_place['rotated'] = rotated
                        placed_parts.append({**part_to_place, 'x': shelf_x, 'y': shelf_y})
                        
                        # Advance shelf position, accounting for kerf
                        shelf_x += part_w + kerf
                        part_placed_on_shelf = True
                        break # Move to the next part
            if part_placed_on_shelf:
                break
        
        # If no part could be placed on the current shelf, start a new one
        if not part_placed_on_shelf:
            if shelf_length == 0: # No parts could be placed at all
                break
            
            shelf_y += shelf_length + kerf # Start new shelf above, accounting for kerf
            shelf_x = 0.0
            shelf_length = 0.0 # Reset shelf length; it will be set by the next part

            # If the new shelf is off the board, we're done
            if shelf_y >= sheet_length:
                break
                
    return placed_parts, remaining_parts