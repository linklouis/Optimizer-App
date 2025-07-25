import pandas as pd
import numpy as np

def extract_hhos_data(hhos_df: pd.DataFrame) -> pd.DataFrame:
    """
    Parses a semicolon-delimited string from the first column of a DataFrame,
    cleans the data, and returns a structured DataFrame.
    """
    processed_df = (hhos_df[0].str.split(';', expand=True)
                    .replace(r'^\s*$', np.nan, regex=True)  # Replace empty strings or whitespace-only strings with NaN
                    .dropna(axis=1, how='all')  # Drop columns where all values are NaN
                    .astype(str))
    processed_df.columns = ['part name', 'number', 'client', 'count', 'width', 'length']
    return processed_df

def process_uploaded_files(hhos_file: str, offcut_file: str, stock_file: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Reads uploaded files into Pandas DataFrames and performs initial processing.
    Returns a tuple of three DataFrames: (hhos_df, offcut_df, stock_df).
    """
    try:
        # Read files directly into Pandas DataFrames
        hhos_df = pd.read_csv(hhos_file, header=None)
        offcut_df = pd.read_excel(offcut_file)
        stock_df = pd.read_excel(stock_file)

        hhos_df = extract_hhos_data(hhos_df)
        
        # --- You can add more data processing logic here in the future ---
        # For example, cleaning data, checking columns, etc.

        return hhos_df, offcut_df, stock_df

    except Exception as e:
        # You can handle errors more gracefully here
        # TODO probably have this also display on the page
        print(f"Error processing files: {e}")
        return None, None, None