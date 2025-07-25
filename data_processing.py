import pandas as pd
import numpy as np

def extract_hhos_data(hhos_df: pd.DataFrame) -> pd.DataFrame:
    """
    Parses a semicolon-delimited string from the first column of a DataFrame,
    cleans the data, and returns a structured DataFrame.
    """
    processed_df = (hhos_df[0].str.split(';', expand=True)
                    .replace(r'^\s*$', np.nan, regex=True)
                    .dropna(axis=1, how='all')
                    .astype(str))
    processed_df.columns = ['material', 'number', 'part name', 'quantity', 'length', 'width']
    return processed_df

def extract_offcut_data(offcut_df: pd.DataFrame) -> pd.DataFrame:
    # Assuming the input might be an excel file with these columns
    if 'SQFT' in offcut_df.columns:
        offcut_df.drop(['SQFT', 'Notes'], axis='columns', inplace=True, errors='ignore')
    
    # Standardize column names
    offcut_df.columns = ['length', 'width', 'quantity']
    return offcut_df

def process_uploaded_files(hhos_file: str, offcut_file: str, stock_file: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Reads uploaded files into Pandas DataFrames and performs initial processing.
    Returns a tuple of three DataFrames: (hhos_df, offcut_df, stock_df).
    """
    try:
        # Read HHOS and Stock files
        hhos_df = pd.read_csv(hhos_file, header=None)
        stock_df = pd.read_excel(stock_file)

        # Conditionally read the offcut file based on its extension
        if offcut_file.lower().endswith('.csv'):
            offcut_df = pd.read_csv(offcut_file)
        elif offcut_file.lower().endswith('.xlsx'):
            offcut_df = pd.read_excel(offcut_file)
        else:
            raise ValueError(f"Unsupported file type for offcuts: {offcut_file}")

        # Process the DataFrames
        hhos_df = extract_hhos_data(hhos_df)
        offcut_df = extract_offcut_data(offcut_df)

        return hhos_df, offcut_df, stock_df

    except Exception as e:
        print(f"Error processing files: {e}")
        return None, None, None