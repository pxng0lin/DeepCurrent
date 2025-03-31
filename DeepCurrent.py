#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "requests",
#     "rich"
# ]
# ///

"""
Smart Contract Analyzer – V1 with Improved Analysis Prompt and Diagram Regeneration Fix
----------------------------------------------------------------------------------------
This app uses a local LLM (default: deepseek-r1) to analyze smart contracts (.sol files)
from a given directory. It generates separate outputs:
   • Functions Report
   • Journey Report
   • Journey Diagram (Mermaid, starting with "flowchart TD")
   • Call Diagram (Mermaid)
These outputs are saved in a timestamped folder and stored in a SQLite database.
The contract submenu includes options to view each report, query them, and check/regenerate diagrams.
"""

import os, sys, sqlite3, hashlib, requests, re
from datetime import datetime
from rich import print
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

console = Console()

# -------------------------------
# Global Configurations
# -------------------------------
LLM_API_URL = "http://localhost:11434/v1/completions"
MAX_TOKENS = 3000
MODEL_NAME = "deepseek-r1"  # Default; can be changed at startup
ANALYSIS_MODEL = None
QUERY_MODEL = None

# -------------------------------
# SQLite Database Setup
# -------------------------------
DB_NAME = "smart_contracts_analysis.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
       CREATE TABLE IF NOT EXISTS contracts (
           id TEXT PRIMARY KEY,
           filename TEXT,
           content TEXT,
           functions_report TEXT,
           journey_report TEXT,
           journey_diagram TEXT,
           call_diagram TEXT,
           analysed_at TEXT
       )
    """)
    conn.commit()
    conn.close()

def update_db_schema():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(contracts)")
    columns = [col[1] for col in cur.fetchall()]
    needed = ["functions_report", "journey_report", "journey_diagram", "call_diagram"]
    for col in needed:
        if col not in columns:
            cur.execute(f"ALTER TABLE contracts ADD COLUMN {col} TEXT")
            console.print(f"[bold green]Database schema updated:[/bold green] '{col}' column added.")
    conn.commit()
    conn.close()

def save_analysis(contract_id, filename, content, functions_report, journey_report, journey_diagram, call_diagram):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
       INSERT OR REPLACE INTO contracts
       (id, filename, content, functions_report, journey_report, journey_diagram, call_diagram, analysed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (contract_id, filename, content, functions_report, journey_report, journey_diagram, call_diagram, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# -------------------------------
# LLM API Interaction Function
# -------------------------------
def call_llm(prompt, model=None):
    if model is None:
        model = MODEL_NAME
    headers = {"Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "temperature": 0.7, "max_tokens": MAX_TOKENS}
    try:
        response = requests.post(LLM_API_URL, json=payload, headers=headers)
        if response.status_code == 200:
            return response.json().get("choices", [{}])[0].get("text", "").strip()
        else:
            console.print(f"[bold red]Error: HTTP {response.status_code}[/bold red]")
            return ""
    except Exception as e:
        console.print(f"[bold red]LLM API call failed:[/bold red] {e}")
        return ""

# -------------------------------
# Helper: Extract Mermaid Code
# -------------------------------
def extract_mermaid_code(text):
    pattern = r"```mermaid\s*(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    stripped = text.strip()
    if stripped.startswith("flowchart TD") or stripped.startswith("sequenceDiagram"):
        return stripped
    return stripped

# -------------------------------
# Phase 1 – Functions Report
# -------------------------------
def generate_functions_report(contract_content):
    prompt = (
       "Phase 1: Generate a detailed functions report for the following smart contract code.\n"
       "Include an OVERVIEW that describes the contract's purpose, its role in the protocol, and key design patterns.\n"
       "Then, list every function along with its parameters, visibility, modifiers, and any potential vulnerabilities.\n"
       "Return the complete report as plain text.\n\n"
       "Smart Contract Code:\n---------------------\n" + contract_content + "\n---------------------\n"
    )
    console.print("Generating functions report...")
    output = call_llm(prompt, model=ANALYSIS_MODEL)
    console.print("Functions report generated:")
    console.print(output)
    return output if output else "[No functions report produced]"

# -------------------------------
# Phase 2 – Journey Report
# -------------------------------
def generate_journey_report(contract_content):
    prompt = (
       "Phase 2: Generate a detailed user journey for the following smart contract code.\n"
       "Describe step-by-step how different user types (e.g., admin, regular user) interact with the contract, including key functions and transitions.\n"
       "Return the journey narrative as plain text.\n\n"
       "Smart Contract Code:\n---------------------\n" + contract_content + "\n---------------------\n"
    )
    console.print("Generating journey report...")
    output = call_llm(prompt, model=ANALYSIS_MODEL)
    console.print("Journey report generated:")
    console.print(output)
    return output if output else "[No journey report produced]"

# -------------------------------
# Phase 3a – Journey Diagram Generation
# -------------------------------
def generate_journey_diagram(journey_report):
    prompt = (
       "Phase 3a: Based on the following user journey description, generate ONLY valid Mermaid diagram text using 'flowchart TD' that visually represents the flow.\n"
       "Your output MUST start with 'flowchart TD' and include ONLY the diagram code, exactly in the format as in the sample below.\n"
       "Do not include any extra commentary or annotations.\n\n"
       "Sample Diagram:\n"
       "---------------------\n"
       "flowchart TD\n"
       "    A[Input] --> C[OptionHandler]\n"
       "    C -->| getOrder | D[executeOrder]\n"
       "    D -->| authorizenot? | E[Fail, 'Invalid sender']\n"
       "    D -->| ordernotvalid? | F[Fail, 'Invalid order']\n"
       "    D -->| validordertypenot? | G[Fail, 'Invalid order type']\n"
       "    D -->| spotnotinrangenot? | H[Fail, 'Spot price out of bounds']\n"
       "    D -->| premiumcalculated? | I[prefix calculation]\n"
       "    I -->| fromerc0decimalsnot? | J[Fail, 'ERC20 decimals mismatch in oToken conversion']\n"
       "    I -->| toerc1decimalsnot? | K[Fail, 'ERC20 decimals mismatch in strike conversion']\n"
       "    J --> L[transfer oTokens]\n"
       "    K --> L\n"
       "    K -->| toerc1collateralnot? | M[Fail, 'ERC20 decimals mismatch for collateral asset']\n\n"
       "User Journey Description:\n"
       "-------------------------\n" + journey_report + "\n-------------------------\n"
    )
    console.print("Generating journey diagram...")
    output = call_llm(prompt, model=ANALYSIS_MODEL)
    console.print("Journey diagram generated:")
    console.print(output)
    extracted = extract_mermaid_code(output)
    if not extracted.startswith("flowchart TD"):
        extracted = "flowchart TD\n    A[Default Diagram]\n"
        console.print("No valid diagram generated; using default journey diagram.")
    return extracted

# -------------------------------
# Phase 3b – Call Diagram Generation
# -------------------------------
def generate_call_diagram(functions_report):
    prompt = (
       "Phase 3b: Based on the following functions report, generate ONLY valid Mermaid diagram text using 'flowchart TD' that represents the function call graph.\n"
       "Node labels must be simple without extra symbols or annotations; use proper diamond notation for conditions if needed.\n"
       "Return ONLY the diagram code in EXACTLY the format as shown in the sample below, without any additional commentary.\n\n"
       "Sample Diagram:\n"
       "---------------------\n"
       "flowchart TD\n"
       "    A[Input] --> C[OptionHandler]\n"
       "    C -->| getOrder | D[executeOrder]\n"
       "    D -->| authorizenot? | E[Fail, 'Invalid sender']\n"
       "    D -->| ordernotvalid? | F[Fail, 'Invalid order']\n"
       "    D -->| validordertypenot? | G[Fail, 'Invalid order type']\n"
       "    D -->| spotnotinrangenot? | H[Fail, 'Spot price out of bounds']\n"
       "    D -->| premiumcalculated? | I[prefix calculation]\n"
       "    I -->| fromerc0decimalsnot? | J[Fail, 'ERC20 decimals mismatch in oToken conversion']\n"
       "    I -->| toerc1decimalsnot? | K[Fail, 'ERC20 decimals mismatch in strike conversion']\n"
       "    J --> L[transfer oTokens]\n"
       "    K --> L\n"
       "    K -->| toerc1collateralnot? | M[Fail, 'ERC20 decimals mismatch for collateral asset']\n\n"
       "    D -->| safeTransfer | N[LiquidityPool]\n"
       "    N -->| safeTransfer | O[oCollateral]\n\n"
       "    D -->| updateOTokenHoldings | P[OptionHandler]\n\n"
       "Return only the diagram code.\n\n"
       "Functions Report:\n"
       "-----------------\n" + functions_report + "\n-----------------\n"
    )
    console.print("Generating call diagram...")
    output = call_llm(prompt, model=ANALYSIS_MODEL)
    console.print("Call diagram generated:")
    console.print(output)
    extracted = extract_mermaid_code(output)
    if not extracted.startswith("flowchart TD"):
        extracted = "flowchart TD\n    A[Default Diagram]\n"
        console.print("No valid diagram generated; using default call diagram.")
    return extracted

# -------------------------------
# File Management Functions
# -------------------------------
def read_contract_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        console.print(f"Error reading {filepath}: {e}")
        return ""

def save_file(content, filename, output_dir):
    path = os.path.join(output_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        console.print(f"Saved '{filename}' to {output_dir}")
    except Exception as e:
        console.print(f"Error saving {filename}: {e}")

def read_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

# -------------------------------
# Interactive Menu Functions
# -------------------------------
def view_file(filename, output_dir):
    path = os.path.join(output_dir, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        console.print(f"\n--- {filename} ---\n")
        console.print(content)
        console.print("\n--- End of File ---\n")
    except Exception as e:
        console.print(f"Error reading {filename}: {e}")

def query_report(report_content, contract_base, output_dir):
    query = Prompt.ask("Enter your query about the report")
    original_file = os.path.join(output_dir, f"{contract_base}_original.sol")
    try:
        with open(original_file, "r", encoding="utf-8") as f:
            original_content = f.read()
    except Exception:
        original_content = "[Original code not available]"
    prompt = (
       "Based on the following materials:\n"
       "================ Functions Report ================\n" + read_file(os.path.join(output_dir, f"{contract_base}_functions_report.md")) + "\n\n"
       "================ Journey Report ================\n" + read_file(os.path.join(output_dir, f"{contract_base}_journey_report.md")) + "\n\n"
       "================ Journey Diagram ================\n" + read_file(os.path.join(output_dir, f"{contract_base}_journey_diagram.md")) + "\n\n"
       "================ Call Diagram ================\n" + read_file(os.path.join(output_dir, f"{contract_base}_call_diagram.md")) + "\n\n"
       "================ Original Code ================\n" + original_content + "\n\n"
       "Please answer the following query in detail:\n" + query + "\n"
    )
    console.print("Querying LLM with extended context...")
    answer = call_llm(prompt, model=QUERY_MODEL)
    console.print("LLM Answer:")
    console.print(answer)
    return answer

def check_and_regenerate_diagrams(contract_base, output_dir):
    # Check Journey Diagram
    jd_file = os.path.join(output_dir, f"{contract_base}_journey_diagram.md")
    try:
        with open(jd_file, "r", encoding="utf-8") as f:
            jd_content = f.read().strip()
    except Exception as e:
        console.print(f"Error reading {jd_file}: {e}")
        jd_content = ""
    if not jd_content.startswith("flowchart TD") or "Default Diagram" in jd_content:
        choice = Prompt.ask("The journey diagram appears to be missing or default. Regenerate it? (y/n)", default="n")
        if choice.lower() == "y":
            journey_report = read_file(os.path.join(output_dir, f"{contract_base}_journey_report.md"))
            new_jd = generate_journey_diagram(journey_report)
            save_file(new_jd, f"{contract_base}_journey_diagram.md", output_dir)
    # Check Call Diagram
    cd_file = os.path.join(output_dir, f"{contract_base}_call_diagram.md")
    try:
        with open(cd_file, "r", encoding="utf-8") as f:
            cd_content = f.read().strip()
    except Exception as e:
        console.print(f"Error reading {cd_file}: {e}")
        cd_content = ""
    if not cd_content.startswith("flowchart TD") or "Default Diagram" in cd_content:
        choice = Prompt.ask("The call diagram appears to be missing or default. Regenerate it? (y/n)", default="n")
        if choice.lower() == "y":
            functions_report = read_file(os.path.join(output_dir, f"{contract_base}_functions_report.md"))
            new_cd = generate_call_diagram(functions_report)
            save_file(new_cd, f"{contract_base}_call_diagram.md", output_dir)

def show_contract_menu(contract_base, output_dir):
    while True:
        console.print(f"\n--- Menu for Contract: {contract_base} ---")
        console.print("1. View Functions Report")
        console.print("2. View Journey Report")
        console.print("3. View Journey Diagram")
        console.print("4. View Call Diagram")
        console.print("5. Query a Report")
        console.print("6. Check and Regenerate Missing Diagrams")
        console.print("7. Back to Session Menu")
        choice = Prompt.ask("Enter your choice")
        if choice == "1":
            view_file(f"{contract_base}_functions_report.md", output_dir)
        elif choice == "2":
            view_file(f"{contract_base}_journey_report.md", output_dir)
        elif choice == "3":
            view_file(f"{contract_base}_journey_diagram.md", output_dir)
        elif choice == "4":
            view_file(f"{contract_base}_call_diagram.md", output_dir)
        elif choice == "5":
            query_report("", contract_base, output_dir)
        elif choice == "6":
            check_and_regenerate_diagrams(contract_base, output_dir)
        elif choice == "7":
            break
        else:
            console.print("Invalid choice. Try again.")

def contract_menu_in_session(session_folder):
    while True:
        console.print(f"\n--- Session: {session_folder} ---")
        reports = [f for f in os.listdir(session_folder) if f.endswith("_functions_report.md")]
        if not reports:
            console.print("No analysis reports found in this session.")
            break
        table = Table(title="Contracts in Session", show_header=True, header_style="bold magenta")
        table.add_column("No.", justify="right")
        table.add_column("Contract")
        contract_bases = []
        for idx, report in enumerate(reports, start=1):
            base = report.replace("_functions_report.md", "")
            contract_bases.append(base)
            table.add_row(str(idx), base)
        console.print(table)
        console.print(f"{len(contract_bases)+1}. Back to Sessions Menu")
        choice = Prompt.ask("Enter the number of the contract to view its menu, or go back")
        try:
            choice_num = int(choice)
            if choice_num == len(contract_bases) + 1:
                break
            elif 1 <= choice_num <= len(contract_bases):
                show_contract_menu(contract_bases[choice_num - 1], session_folder)
            else:
                console.print("Invalid selection. Try again.")
        except ValueError:
            console.print("Invalid input. Enter a number.")

def browse_sessions():
    sessions = [d for d in os.listdir(os.getcwd()) if os.path.isdir(d) and d.startswith("analysis_")]
    if not sessions:
        console.print("No analysis sessions found.")
        return
    table = Table(title="Analysis Sessions", show_header=True, header_style="bold magenta")
    table.add_column("No.", justify="right")
    table.add_column("Session (Folder Name)")
    for idx, session in enumerate(sessions, start=1):
        table.add_row(str(idx), session)
    console.print(table)
    choice = Prompt.ask("Enter the number of the session to view its contracts (or type 'exit' to quit)")
    if choice.lower() == "exit":
        return
    try:
        choice_num = int(choice)
        if 1 <= choice_num <= len(sessions):
            selected_session = sessions[choice_num - 1]
            contract_menu_in_session(selected_session)
        else:
            console.print("Invalid session number.")
    except ValueError:
        console.print("Invalid input.")

# -------------------------------
# Process a Single Contract
# -------------------------------
def process_contract(filepath, output_dir):
    console.print(f"\nProcessing: {filepath}")
    content = read_contract_file(filepath)
    if not content:
        console.print("No content read from file.")
        return
    contract_id = hashlib.sha256((filepath + content).encode()).hexdigest()
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    # Save the original contract file for reference
    save_file(content, f"{base_name}_original.sol", output_dir)
    # Generate and save individual outputs
    functions_report = generate_functions_report(content)
    save_file(functions_report, f"{base_name}_functions_report.md", output_dir)
    journey_report = generate_journey_report(content)
    save_file(journey_report, f"{base_name}_journey_report.md", output_dir)
    journey_diagram = generate_journey_diagram(journey_report)
    save_file(journey_diagram, f"{base_name}_journey_diagram.md", output_dir)
    call_diagram = generate_call_diagram(functions_report)
    save_file(call_diagram, f"{base_name}_call_diagram.md", output_dir)
    console.print("Detailed analysis outputs created and saved.")
    # Save analysis to SQLite DB
    save_analysis(contract_id, os.path.basename(filepath), content, functions_report, journey_report, journey_diagram, call_diagram)

# -------------------------------
# Main Application Flow
# -------------------------------
def main():
    init_db()
    update_db_schema()
    global ANALYSIS_MODEL, QUERY_MODEL
    ANALYSIS_MODEL = Prompt.ask("Select model for initial analysis", choices=["deepseek-r1", "qwen2.5-coder:3b", "gemma3:4b", "deepseek-coder:6.7b"], default="deepseek-r1")
    QUERY_MODEL = Prompt.ask("Select model for querying", choices=["deepseek-r1", "qwen2.5-coder:3b", "gemma3:4b", "deepseek-coder:6.7b"], default="deepseek-r1")
    
    while True:
        console.print("\n--- Main Menu ---")
        console.print("1. Analyse a new smart contracts directory")
        console.print("2. Browse historical analysis sessions")
        console.print("3. Exit")
        choice = Prompt.ask("Enter your choice")
        if choice == "1":
            directory = Prompt.ask("Enter the path to the smart contracts directory")
            if not os.path.isdir(directory):
                console.print("The provided directory does not exist.")
                continue
            # Create an output folder with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_folder = os.path.join(os.getcwd(), f"analysis_{timestamp}")
            os.makedirs(output_folder, exist_ok=True)
            console.print(f"Output folder created: {output_folder}")
            # Process all .sol files in the directory
            contract_files = [os.path.join(directory, f) for f in os.listdir(directory)
                              if os.path.isfile(os.path.join(directory, f)) and f.endswith(".sol")]
            if not contract_files:
                console.print("No smart contract files (.sol) found in the directory.")
                continue
            for filepath in contract_files:
                process_contract(filepath, output_folder)
            console.print("Analysis complete for all contracts in the directory.")
            contract_menu_in_session(output_folder)
        elif choice == "2":
            browse_sessions()
        elif choice == "3":
            console.print("Exiting. Goodbye!")
            break
        else:
            console.print("Invalid choice. Try again.")

if __name__ == "__main__":
    main()
