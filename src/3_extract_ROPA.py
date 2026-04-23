import os
import sys
import json
import logging
import argparse
from pathlib import Path
from pandas import DataFrame
import pandas as pd

from database import get_session, create_db_and_tables
from models import RopaRecord
from sqlmodel import delete
from config_manager import get_config

# 1. Paths and Configuration
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

def parse_arguments():
    parser = argparse.ArgumentParser(description="Extract and normalize ROPA columns")
    parser.add_argument("--file", type=str, help="Absolute path to the ROPA file to process")
    parser.add_argument("--mapping", type=str, help="JSON string dict mapping required fields to actual Excel columns")
    args, _ = parser.parse_known_args()
    return args

CLI_ARGS = parse_arguments()


from utils_logging import setup_logging

def select_input_file(folder_path: Path, valid_extensions: list) -> Path | None:
    """
    List only supported files (by extension) in the given folder
    and ask the user to select one by number.
    """
    # FIX 3: Prevent errors if the folder doesn't exist
    if not folder_path.exists() or not folder_path.is_dir():
        print(f"Error: The folder '{folder_path}' does not exist.")
        return None

    try:
        all_files = [f for f in folder_path.iterdir() if f.is_file()]
    except Exception as e:
        print(f"Error reading folder '{folder_path}': {e}")
        return None

    supported_files = [f for f in all_files if f.suffix.lower() in valid_extensions]
    if not supported_files:
        print(f"No supported files found in folder: {folder_path}")
        return None

    print("\nSupported files in folder:")
    for idx, fpath in enumerate(supported_files, start=1):
        print(f"{idx}. {fpath.name}")

    while True:
        try:
            # FIX 2: Added a 'q' option to quit gracefully
            selection = input("\nEnter the number of the file to process (or 'q' to quit): ").strip()
            if selection.lower() == 'q':
                return None
            
            index = int(selection)
            if 1 <= index <= len(supported_files):
                return supported_files[index - 1]
            else:
                print("Number out of range. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except KeyboardInterrupt:
            # FIX 2: Handle Ctrl+C gracefully
            print("\nOperation cancelled by user.")
            return None

def read_file(file_path: Path):
    # Reads the file based on its extension
    ext = file_path.suffix.lower()
    try:
        if ext == ".csv":
            return pd.read_csv(file_path)
        elif ext in [".xls", ".xlsx"]:
            return pd.read_excel(file_path)
        elif ext == ".ods":
            return pd.read_excel(file_path, engine="odf")
        else:
            print(f"Unsupported file format: {ext}")
            return None
    except ImportError as ie:
        # FIX 3: Explicitly warn about missing Excel/ODS libraries
        print(f"\nMissing required library to read {ext} files: {ie}")
        print("Try running: pip install openpyxl odfpy xlrd")
        return None
    except Exception as e:
        print(f"Error reading file {file_path.name}: {e}")
        return None

def prompt_for_mapping(required_field: str, available_columns: list) -> str | None:
    """
    Prompt the user to select the column number corresponding to the required field.
    """
    while True:
        try:
            selection = input(f"Select the column number for '{required_field}' (or 'q' to quit): ").strip()
            if selection.lower() == 'q':
                return None
                
            index = int(selection)
            if 1 <= index <= len(available_columns):
                return available_columns[index - 1]
            else:
                print("Number out of range. Try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except KeyboardInterrupt:
            print("\nOperation cancelled by user.")
            return None

def main():
    # Setup unified logging
    setup_logging("3_extract_ROPA")

    config_section = get_config("extract_ROPA.py", {})
    if not config_section:
        print("Section 'extract_ROPA.py' not found in the configuration database.")
        return

    ropa_folder_str = config_section.get("ropa_folder")
    if not ropa_folder_str:
        print("Missing required configuration variable: ropa_folder")
        return

    ropa_folder = PROJECT_ROOT / ropa_folder_str

    valid_extensions = [".xls", ".xlsx", ".ods", ".csv"]

    # 2. Ask the user which file to process within the folder, or use CLI
    if CLI_ARGS.file:
        input_file = Path(CLI_ARGS.file)
        if not input_file.exists():
            print(f"ERROR: Provided file {input_file} does not exist.")
            return
    else:
        input_file = select_input_file(ropa_folder, valid_extensions)
        
    if input_file is None:
        return

    print(f"\nSelected file: {input_file.name}")

    # 3. Read the selected file
    df = read_file(input_file)
    if df is None:
        return

    # FIX 1: Replace any empty cells (NaN) with an empty string to avoid JSON parsing errors downstream
    df = df.fillna("")

    # 4. Display available columns with progressive numbering
    available_columns = df.columns.tolist()
    print("\nAvailable columns:")
    for idx, col in enumerate(available_columns, start=1):
        print(f"{idx}. {col}")

    # 5. Define required English fields and build the mapping
    required_fields = [
        "Processing Activity",
        "Lawful Bases",
        "Data Subject Categories",
        "Personal Data Categories",
        "Recipients Categories",
        "International Transfers",
        "Retention Periods"
    ]
    
    if CLI_ARGS.mapping:
        try:
            mapping = json.loads(CLI_ARGS.mapping)
        except Exception as e:
            print(f"ERROR parsing mapping CLI JSON: {e}")
            return
        
        # If mapping is empty, auto-map columns whose names match required fields
        if not mapping:
            for field in required_fields:
                if field in available_columns:
                    mapping[field] = field
            if mapping:
                print("\nAuto-detected matching columns:")
                for field, col in mapping.items():
                    print(f"  '{field}' <-- '{col}'")
            else:
                print("\nWARNING: No columns match the required fields. All fields will be empty.")
    else:
        mapping = {}
        print("\nFor each required field, select the corresponding Excel column by entering its number.")
        for field in required_fields:
            selected_column = prompt_for_mapping(field, available_columns)
            if selected_column is None: # User chose to quit
                return
            mapping[field] = selected_column

    print("\nMapping selected:")
    for field, col in mapping.items():
        print(f"'{field}' <-- '{col}'")

    # 6. Extract the columns according to the mapping and rename them
    try:
        extracted_data = pd.DataFrame()
        for field in required_fields:
            col_name = mapping.get(field)
            if not col_name or str(col_name).strip() == "" or col_name == "__EMPTY__":
                extracted_data[field] = ""
            else:
                extracted_data[field] = df[col_name].astype(str)
    except KeyError as e:
         print(f"Error extracting columns. Check mapping. Details: {e}")
         return

    # 7. Removes newline (\n and \r) and replaces them with a space
    extracted_data = extracted_data.replace(r'[\r\n]+', ' ', regex=True)
    
    # 8. Ensure no new NaNs were generated during regex operations
    extracted_data = extracted_data.fillna("")

    # 9. Add an "id" column with a 4-digit progressive ID for each row
    extracted_data.insert(0, "id", [f"{i:04d}" for i in range(1, len(extracted_data) + 1)])

    # 10. Save the extracted data to Database
    create_db_and_tables()
    try:
        with get_session() as session:
            session.exec(delete(RopaRecord))
            records = []
            for _, row in extracted_data.iterrows():
                ret_p = str(row['Retention Periods']).strip()
                if ret_p.replace('.', '', 1).isdigit():
                    ret_p = f"+{int(float(ret_p))} days"
                    
                rec = RopaRecord(
                    id=row['id'],
                    activity=row['Processing Activity'],
                    lawful_bases=row['Lawful Bases'],
                    subject_categories=row['Data Subject Categories'],
                    personal_data_categories=row['Personal Data Categories'],
                    recipients_categories=row['Recipients Categories'],
                    international_transfers=row['International Transfers'],
                    retention_periods=ret_p
                )
                records.append(rec)
            session.add_all(records)
            session.commit()
            print("\nData saved successfully in Database.")
    except Exception as e:
        print(f"Error writing database: {e}")

if __name__ == "__main__":
    main()