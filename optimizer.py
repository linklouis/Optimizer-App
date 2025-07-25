def run_optimization(sheet_length, sheet_width, parts):
    """
    Places parts onto a sheet using a simple shelf-based guillotine algorithm.

    Args:
        sheet_length (float): The length of the sheet to place parts on.
        sheet_width (float): The width of the sheet to place parts on.
        parts (list of dict): A list of parts to place, each with 'part name', 
                                'length', 'width', and 'quantity'.

    Returns:
        tuple: A tuple containing:
            - list: A list of placed parts with their x, y coordinates.
            - list: A list of parts that could not be placed.
    """
    # Create a flat list of all individual parts to be placed
    parts_to_place = []
    for part in parts:
        for _ in range(int(part['quantity'])):
            parts_to_place.append({
                'part name': part['part name'],
                'length': part['length'],
                'width': part['width']
            })

    # Sort parts by length (longest first) as a simple heuristic
    parts_to_place.sort(key=lambda p: p['length'], reverse=True)

    placed_parts = []
    
    # --- Shelf Algorithm Logic ---
    shelf_x = 0.0
    shelf_y = 0.0
    shelf_length = 0.0 # This represents the length of the current shelf

    remaining_parts = []
    for part in parts_to_place:
        part_l = part['length']
        part_w = part['width']

        # If the shelf is empty, start it with the current part's length
        if shelf_length == 0.0:
            shelf_length = part_l

        # Try to place the part on the current shelf
        if shelf_y + part_l <= sheet_length:
            if shelf_x + part_w <= sheet_width:
                # Place the part
                placed_parts.append({
                    **part,
                    'x': shelf_x,
                    'y': shelf_y
                })
                shelf_x += part_w
            else:
                # Part doesn't fit on this shelf, move to the next shelf
                shelf_y += shelf_length
                shelf_x = 0.0
                shelf_length = part_l # New shelf starts with this part's length

                # Try placing the part on the new shelf
                if shelf_y + part_l <= sheet_length:
                    placed_parts.append({
                        **part,
                        'x': shelf_x,
                        'y': shelf_y
                    })
                    shelf_x += part_w
                else:
                    # Part doesn't fit on the sheet at all
                    remaining_parts.append(part)
        else:
            # Part is too long for the remaining space
            remaining_parts.append(part)
            
    return placed_parts, remaining_parts