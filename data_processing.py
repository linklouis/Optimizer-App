import pandas as pd
import numpy as np
import os
import re

def extract_hhos_data(hhos_df: pd.DataFrame) -> pd.DataFrame:
    """
    Parses a semicolon-delimited string from the first column of a DataFrame,
    cleans the data, and returns a structured DataFrame.
    """
    if hhos_df.empty or hhos_df.shape[1] == 0:
        return pd.DataFrame()
        
    processed_df = (hhos_df.iloc[:, 0].str.split(';', expand=True)
                    .replace(r'^\s*$', np.nan, regex=True)
                    .dropna(axis=1, how='all'))
    
    if processed_df.shape[1] < 6:
        raise ValueError("HHOS data requires at least 6 semicolon-separated values.")

    # Standardize to snake_case for consistency
    processed_df.columns = ['material', 'number', 'part_name', 'quantity', 'length', 'width'] + processed_df.columns[6:].tolist()
    
    for col in ['length', 'width', 'quantity']:
        processed_df[col] = pd.to_numeric(processed_df[col], errors='coerce')
    
    processed_df['label'] = processed_df['part_name'].str.split('-').str[-1]
    processed_df.dropna(subset=['length', 'width', 'quantity'], inplace=True)
    
    return processed_df

def standardize_sheet_data(df: pd.DataFrame, file_type: str, default_length: float = None, default_width: float = None) -> pd.DataFrame:
    """
    Standardizes column names and types for stock and offcut DataFrames.
    """
    if df.shape[1] < 3:
        raise ValueError(f"{file_type} file must have at least 3 columns for length, width, and quantity.")
    
    df = df.iloc[:, :3].copy()
    df.columns = ['length', 'width', 'quantity']
    
    for col in ['length', 'width', 'quantity']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
        
    df.dropna(subset=['length', 'width'], inplace=True)
    df['quantity'] = df['quantity'].fillna(1).astype(int)
    
    if default_length is not None and default_width is not None:
        condition = (
            ((df['length'] == default_length) & (df['width'] == default_width)) |
            ((df['length'] == default_width) & (df['width'] == default_length))
        )
        df = df[~condition]

    return df

def process_stock_part_inventory(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardizes the stock part inventory, extracting dimensions from the 'Name' column.
    """
    if df.shape[1] < 4:
        raise ValueError("Stock inventory needs at least 4 columns: Ref, Name, On Hand, Target.")
    
    df = df.iloc[:, :4].copy()
    # Standardize to snake_case
    df.columns = ['internal_ref', 'part_name', 'current_stock', 'target_stock']

    dimension_regex = re.compile(r'(\d+\.?\d*)\s*x\s*(\d+\.?\d*)\s*$')

    extracted_dims = df['part_name'].str.extract(dimension_regex)
    df['length'] = extracted_dims[0]
    df['width'] = extracted_dims[1]

    # Clean the part name for display
    df['part_name_clean'] = df['part_name'].str.replace(dimension_regex, '', regex=True).str.strip().str.rstrip(' -')
    
    for col in ['length', 'width', 'current_stock', 'target_stock']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df.dropna(subset=['length', 'width', 'current_stock', 'target_stock'], inplace=True)
    df[['current_stock', 'target_stock']] = df[['current_stock', 'target_stock']].astype(int)
    
    return df

def process_uploaded_files(hhos_path: str, offcut_path: str, stock_inv_path: str, default_length: float, default_width: float) -> tuple:
    """
    Reads and processes all input files into Pandas DataFrames.
    """
    hhos_df, offcut_df, stock_inv_df = pd.DataFrame(), None, None
    
    try:
        if hhos_path and os.path.exists(hhos_path):
            hhos_df = extract_hhos_data(pd.read_csv(hhos_path, header=None))

        # Use a managed offcut path if the user-provided one is empty/non-existent
        effective_offcut_path = offcut_path if offcut_path and os.path.exists(offcut_path) else None
        if effective_offcut_path:
            reader = pd.read_csv if effective_offcut_path.lower().endswith('.csv') else pd.read_excel
            temp_offcut_df = reader(effective_offcut_path)
            if not temp_offcut_df.empty:
                offcut_df = standardize_sheet_data(temp_offcut_df, "Offcut", default_length, default_width)
        
        if stock_inv_path and os.path.exists(stock_inv_path):
            reader = pd.read_csv if stock_inv_path.lower().endswith('.csv') else pd.read_excel
            stock_inv_df = process_stock_part_inventory(reader(stock_inv_path))

        return hhos_df, offcut_df, stock_inv_df

    except Exception as e:
        # Re-raise to be caught by the Flask error handler
        raise e