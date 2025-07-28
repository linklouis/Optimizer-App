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
        raise ValueError("HHOS data is missing expected columns. It should have at least 6 semicolon-separated values.")

    processed_df.columns = ['material', 'number', 'part name', 'quantity', 'length', 'width'] + processed_df.columns[6:].tolist()
    
    for col in ['length', 'width', 'quantity']:
        processed_df[col] = pd.to_numeric(processed_df[col], errors='coerce')
    
    processed_df['label'] = processed_df['part name'].str.split('-').str[-1]
    processed_df.dropna(subset=['length', 'width', 'quantity'], inplace=True)
    
    return processed_df

def standardize_sheet_data(df: pd.DataFrame, file_type: str, default_length: float = None, default_width: float = None) -> pd.DataFrame:
    """
    Standardizes column names and types for stock and offcut DataFrames.
    If default dimensions are provided, it filters out rows matching them from the dataframe.
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
        rows_before = len(df)
        df = df[~condition]
        rows_removed = rows_before - len(df)
        if rows_removed > 0:
            print(f"INFO: Removed {rows_removed} row(s) from the '{file_type}' list matching default dimensions ({default_length}x{default_width}).")

    return df

def process_stock_part_inventory(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardizes the stock part inventory, extracting dimensions from the 'Name' column.
    """
    # Use the first 4 columns as per the user's provided file format
    if df.shape[1] < 4:
        raise ValueError("Stock part inventory file must have at least 4 columns for Internal Reference, Name, Quantity On Hand, and Target Quantity.")
    
    df = df.iloc[:, :4].copy()
    df.columns = ['internal_ref', 'part_name', 'current_stock', 'target_stock']

    dimension_regex = re.compile(r'(\d+\.?\d*)\s*x\s*(\d+\.?\d*)\s*$')

    extracted_dims = df['part_name'].str.extract(dimension_regex)
    df['length'] = extracted_dims[0]
    df['width'] = extracted_dims[1]
    
    for col in ['length', 'width', 'current_stock', 'target_stock']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df.dropna(subset=['length', 'width', 'current_stock', 'target_stock'], inplace=True)
    
    df[['current_stock', 'target_stock']] = df[['current_stock', 'target_stock']].astype(int)
    
    return df


def process_uploaded_files(hhos_file: str, offcut_file: str, stock_inv_file: str, default_length: float, default_width: float) -> tuple:
    """
    Reads all uploaded files into Pandas DataFrames and performs initial processing.
    """
    hhos_df, offcut_df, stock_inv_df = (pd.DataFrame(), None, None)
    
    try:
        if hhos_file and os.path.exists(hhos_file):
            hhos_df = pd.read_csv(hhos_file, header=None)
            hhos_df = extract_hhos_data(hhos_df)

        if offcut_file and os.path.exists(offcut_file):
            if offcut_file.lower().endswith('.csv'):
                temp_offcut_df = pd.read_csv(offcut_file)
            else:
                temp_offcut_df = pd.read_excel(offcut_file)
            
            if not temp_offcut_df.empty:
                offcut_df = standardize_sheet_data(temp_offcut_df, "Offcut", default_length, default_width)
        
        if stock_inv_file and os.path.exists(stock_inv_file):
            if stock_inv_file.lower().endswith('.csv'):
                stock_inv_df = pd.read_csv(stock_inv_file)
            else:
                stock_inv_df = pd.read_excel(stock_inv_file)
            stock_inv_df = process_stock_part_inventory(stock_inv_df)

        return hhos_df, offcut_df, stock_inv_df

    except Exception as e:
        raise e