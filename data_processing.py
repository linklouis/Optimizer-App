import pandas as pd
import numpy as np
import os

def extract_hhos_data(hhos_df: pd.DataFrame) -> pd.DataFrame:
    """
    Parses a semicolon-delimited string from the first column of a DataFrame,
    cleans the data, and returns a structured DataFrame.
    """
    processed_df = (hhos_df[0].str.split(';', expand=True)
                    .replace(r'^\s*$', np.nan, regex=True)
                    .dropna(axis=1, how='all'))
    
    if processed_df.shape[1] < 6:
        raise ValueError("HHOS data is missing expected columns. It should have at least 6 semicolon-separated values.")

    processed_df.columns = ['material', 'number', 'part name', 'quantity', 'length', 'width']
    
    for col in ['length', 'width', 'quantity']:
        processed_df[col] = pd.to_numeric(processed_df[col], errors='coerce')
    
    processed_df['label'] = processed_df['part name'].str.split('-').str[-1]
    processed_df.dropna(subset=['length', 'width', 'quantity'], inplace=True)
    
    return processed_df

def standardize_sheet_data(df: pd.DataFrame, file_type: str) -> pd.DataFrame:
    """Standardizes column names and types for stock and offcut DataFrames."""
    if df.shape[1] < 3:
        raise ValueError(f"{file_type} file must have at least 3 columns for length, width, and quantity.")
    
    df = df.iloc[:, 0:3].copy()
    df.columns = ['length', 'width', 'quantity']
    
    for col in ['length', 'width', 'quantity']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
        
    df.dropna(subset=['length', 'width'], inplace=True)
    df['quantity'] = df['quantity'].fillna(1).astype(int) # Default quantity to 1 if missing
    
    return df


def process_uploaded_files(hhos_file: str, offcut_file: str, stock_file: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Reads uploaded files into Pandas DataFrames and performs initial processing.
    """
    offcut_df = None
    try:
        hhos_df = pd.read_csv(hhos_file, header=None)
        hhos_df = extract_hhos_data(hhos_df)

        stock_df = pd.read_excel(stock_file)
        stock_df = standardize_sheet_data(stock_df, "Stock")

        if os.path.exists(offcut_file):
            if offcut_file.lower().endswith('.csv'):
                temp_offcut_df = pd.read_csv(offcut_file)
            elif offcut_file.lower().endswith(('.xlsx', '.xls')):
                temp_offcut_df = pd.read_excel(offcut_file)
            else:
                 raise ValueError(f"Unsupported file type for offcuts: {offcut_file}")
            
            if not temp_offcut_df.empty:
                offcut_df = standardize_sheet_data(temp_offcut_df, "Offcut")

        return hhos_df, offcut_df, stock_df

    except Exception as e:
        raise e