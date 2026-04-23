# Interactive CLI tool to select the active data source for the pipeline.
# Updates the 'active_source' setting in config.json to control whether
# the pipeline processes a local folder, Gmail, or Microsoft 365 / Outlook.

import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / 'config.json'

def main():
    """Prompts the user to select a data source and saves the choice to config.json."""
    if not CONFIG_FILE.exists():
        print(f"Error: Could not find configuration file at {CONFIG_FILE}.")
        return

    try:
        with CONFIG_FILE.open('r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error reading configuration: {e}")
        return

    current_source = config.get("active_source", "local")
    print(f"--- Data Source Selection ---")
    print(f"Current source is: '{current_source}'\n")
    print("Select the data source to be processed in the next steps:")
    print("1 - Local Folder (uses the unified 'input_folder' setting)")
    print("2 - Gmail (fetches emails into the unified 'input_folder')")
    print("3 - Microsoft 365 / Outlook (fetches emails via Microsoft Graph API)")

    while True:
        choice = input("\nEnter your choice (1, 2 or 3): ").strip()
        if choice == '1':
            selected_source = "local"
            break
        elif choice == '2':
            selected_source = "gmail"
            break
        elif choice == '3':
            selected_source = "outlook"
            break
        else:
            print("Invalid input. Please enter 1, 2 or 3.")

    if selected_source != current_source:
        config["active_source"] = selected_source
        try:
            with CONFIG_FILE.open('w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"\nSuccess! Active source changed from '{current_source}' to '{selected_source}'.")
        except Exception as e:
            print(f"\nError saving configuration: {e}")
    else:
        print(f"\nActive source is already set to '{selected_source}'. No changes made.")

    if selected_source == "gmail":
        print("\nNext step: Run 'python 0_extract_mail.py' to fetch new emails from Gmail.")
    elif selected_source == "outlook":
        print("\nNext step: Run 'python 0_extract_mail.py' to fetch new emails from Outlook.")
    else:
         print("\nNext step: Run 'python 1_extract_text.py' to process your configured local folder.")

if __name__ == "__main__":
    main()
