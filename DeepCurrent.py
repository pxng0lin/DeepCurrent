#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "requests",
#     "rich"
# ]
# ///

"""
DeepCurrent Analyser
------------------------
This app uses a local LLM to analyse smart contracts from a given directory.
It works in several phases:
  1. Phase 1 – Functions Report: Generates a detailed functions report listing all functions with parameters, visibility, modifiers, and potential vulnerabilities.
  2. Phase 2 – Journey Report: Generates a plain-text user journey report for the contract.
  3. Phase 3 – Diagram Generation: Using the reports as context, it generates Mermaid diagram text in two parts:
       a. A journey diagram that visually represents the user journey.
       b. A function call diagram that represents the function call graph.
       
Each run is saved in an output folder named "analysis_YYYYMMDD_HHMMSS". After model selection, you can choose to analyse a new directory or browse historical sessions.
Within a session, you can view individual contract reports, query them, or check/regenerate missing diagrams.

Before starting, you will select the model for initial analysis and the model for querying.
Supported models:
  • deepseek-r1
  • qwen2.5-coder:3b
  • gemma3:4b
  * deepseek-coder:6.7b

All outputs are saved (UTF-8) and stored in a local SQLite database.
"""

import os
import sys
import sqlite3
import hashlib
import requests
from datetime import datetime

# Import Rich components
from rich import print
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

console = Console()

# Constants for fallback diagrams
DEFAULT_JOURNEY_DIAGRAM = "flowchart TD\n    A[Default Diagram]\n"
DEFAULT_CALL_DIAGRAM = "flowchart TD\n    A[Default Diagram]\n"

# ------------------------------------------------------------------
# Global Configurations and LLM API Settings
# ------------------------------------------------------------------
LLM_API_URL = "http://localhost:11434/v1/completions"
MAX_TOKENS = 6000
# Global default model (will be set by user selection)
MODEL_NAME = "deepseek-r1"
# We'll use these globals for analysis and query phases:
ANALYSIS_MODEL = None
QUERY_MODEL = None

# ------------------------------------------------------------------
# SQLite Database Setup
# ------------------------------------------------------------------
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
    if "journey_diagram" not in columns:
        cur.execute("ALTER TABLE contracts ADD COLUMN journey_diagram TEXT")
        console.print("[bold green]Database schema updated:[/bold green] 'journey_diagram' column added.")
    conn.commit()
    conn.close()

def save_analysis(contract_id, filename, content, functions_report, journey_report, journey_diagram, call_diagram):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO contracts 
        (id, filename, content, functions_report, journey_report, journey_diagram, call_diagram, analysed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        contract_id, filename, content, functions_report, journey_report, journey_diagram, call_diagram,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

# ------------------------------------------------------------------
# LLM API Interaction Function
# ------------------------------------------------------------------
def call_llm(prompt, model=None):
    if model is None:
        model = MODEL_NAME
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS
    }
    try:
        response = requests.post(LLM_API_URL, json=payload, headers=headers)
        if response.status_code == 200:
            result = response.json()
            return result.get("choices", [{}])[0].get("text", "").strip()
        else:
            console.print(f"[bold red]Error: HTTP {response.status_code}[/bold red]")
            return ""
    except Exception as e:
        console.print(f"[bold red]LLM API call failed:[/bold red] {e}")
        return ""

# ------------------------------------------------------------------
# Helper Functions for Diagram Regeneration
# ------------------------------------------------------------------
def is_valid_mermaid(diagram_text):
    stripped = diagram_text.strip()
    return (stripped.startswith("flowchart TD") or stripped.startswith("sequenceDiagram")) and "Default Diagram" not in stripped

def regenerate_journey_diagram_from_contract(contract_content):
    prompt = (
        "Based on the following smart contract code, generate ONLY valid Mermaid diagram text using 'flowchart TD' "
        "that visually represents the user journey. Your output MUST start with 'flowchart TD' and include only diagram code. "
        "Do not include any extra commentary, quotes, or annotations. Use the sample below as a guide:\n\n"
        "Sample Diagram:\n"
        "---------------------\n"
        "flowchart TD\n"
        "    Start[Start]\n"
        "        --> PrepareLoan[Prepare loan data]\n"
        "            --> Claim[Call claim function]\n"
        "            --> RedeemNote[Redeem note]\n"
        "        |\n"
        "        --> Repay_Loan[Prepare for repayment]\n"
        "            | Check if active\n"
        "                --> ForceRepay[Force repay]\n"
        "        |\n"
        "        | CalculateInterest[Calculate prorated interest]\n"
        "        | DeterminePrincipal[Calculate principal amount]\n"
        "        | ValidateRepayment[Check if enough to cover interest]\n"
        "            | If Not Enough--> Revert[Invalid transaction]\n"
        "        |\n"
        "        | DistributeFees[Fee calculation based on basis points]\n"
        "        --> Repay[Lender receives payment]\n"
        "    End[End of process]\n"
        "---------------------\n\n"
        "Smart Contract Code:\n"
        "---------------------\n"
        f"{contract_content}\n"
        "---------------------\n"
    )
    return call_llm(prompt, model=ANALYSIS_MODEL)

def regenerate_call_diagram_from_contract(contract_content):
    prompt = (
        "Based on the following smart contract code, generate ONLY valid Mermaid diagram text using 'flowchart TD' "
        "that represents the function call graph. Your output MUST start with 'flowchart TD' and include only diagram code. "
        "Node labels must be simple with no extra symbols, quotes, or annotations, and use proper diamond notation for conditions if needed. "
        "Use the sample below as a guide:\n\n"
        "Sample Diagram:\n"
        "---------------------\n"
        "flowchart TD\n"
        "    Start[Start]\n"
        "        --> PrepareLoan[Prepare loan data]\n"
        "            --> Claim[Call claim function]\n"
        "            --> RedeemNote[Redeem note]\n"
        "        |\n"
        "        --> Repay_Loan[Prepare for repayment]\n"
        "            | Check if active\n"
        "                --> ForceRepay[Force repay]\n"
        "        |\n"
        "        | CalculateInterest[Calculate prorated interest]\n"
        "        | DeterminePrincipal[Calculate principal amount]\n"
        "        | ValidateRepayment[Check if enough to cover interest]\n"
        "            | If Not Enough--> Revert[Invalid transaction]\n"
        "        |\n"
        "        | DistributeFees[Fee calculation based on basis points]\n"
        "        --> Repay[Lender receives payment]\n"
        "    End[End of process]\n"
        "---------------------\n\n"
        "Smart Contract Code:\n"
        "---------------------\n"
        f"{contract_content}\n"
        "---------------------\n"
    )
    return call_llm(prompt, model=ANALYSIS_MODEL)

# ------------------------------------------------------------------
# Phase 1: Generate Functions Report
# ------------------------------------------------------------------
def generate_functions_report(contract_content):
    prompt = (
        "Phase 1: Generate a detailed functions report for the following smart contract code. "
        "List every function with its parameters, visibility, and any modifiers, and note potential vulnerabilities or manipulation opportunities. "
        "Return the complete report as plain text.\n\n"
        "Smart Contract Code:\n"
        "---------------------\n"
        f"{contract_content}\n"
        "---------------------\n"
    )
    console.rule("[bold blue]Generating Functions Report[/bold blue]")
    output = call_llm(prompt, model=ANALYSIS_MODEL)
    console.print("[bold green]Functions Report Generated:[/bold green]")
    console.print(output)
    return output if output else "[No functions report produced]"

# ------------------------------------------------------------------
# Phase 2: Generate Journey Report
# ------------------------------------------------------------------
def generate_journey_report(contract_content):
    prompt = (
        "Phase 2: Generate a user journey for the following smart contract code. "
        "Provide a clear, plain-text description of the sequence and flow of the contract functions. "
        "Return the journey description as plain text.\n\n"
        "Smart Contract Code:\n"
        "---------------------\n"
        f"{contract_content}\n"
        "---------------------\n"
    )
    console.rule("[bold blue]Generating Journey Report[/bold blue]")
    output = call_llm(prompt, model=ANALYSIS_MODEL)
    console.print("[bold green]Journey Report Generated:[/bold green]")
    console.print(output)
    return output if output else "[No journey report produced]"

# ------------------------------------------------------------------
# Phase 3a: Generate Mermaid Journey Diagram using Journey Report
# ------------------------------------------------------------------
def generate_journey_diagram(journey_report):
    prompt = (
        "Phase 3a: Based on the following user journey description, generate ONLY valid Mermaid diagram text "
        "using 'flowchart TD' that visually represents the flow. Your output MUST start with 'flowchart TD' and contain only diagram code. "
        "Do not include any extra commentary, quotes, or annotations in the node labels. Use the sample below as a guide:\n\n"
        "Sample Diagram:\n"
        "---------------------\n"
        "flowchart TD\n"
        "    Start[Start]\n"
        "        --> PrepareLoan[Prepare loan data]\n"
        "            --> Claim[Call claim function]\n"
        "            --> RedeemNote[Redeem note]\n"
        "        |\n"
        "        --> Repay_Loan[Prepare for repayment]\n"
        "            | Check if active\n"
        "                --> ForceRepay[Force repay]\n"
        "        |\n"
        "        | CalculateInterest[Calculate prorated interest]\n"
        "        | DeterminePrincipal[Calculate principal amount]\n"
        "        | ValidateRepayment[Check if enough to cover interest]\n"
        "            | If Not Enough--> Revert[Invalid transaction]\n"
        "        |\n"
        "        | DistributeFees[Fee calculation based on basis points]\n"
        "        --> Repay[Lender receives payment]\n"
        "    End[End of process]\n"
        "---------------------\n\n"
        "User Journey Description:\n"
        "-------------------------\n"
        f"{journey_report}\n"
        "-------------------------\n"
    )
    console.rule("[bold blue]Generating Mermaid Journey Diagram[/bold blue]")
    output = call_llm(prompt, model=ANALYSIS_MODEL)
    console.print("[bold green]User Journey Mermaid Diagram Generated:[/bold green]")
    console.print(output)
    if not is_valid_mermaid(output):
        output = DEFAULT_JOURNEY_DIAGRAM
        console.print("[bold yellow]No valid diagram generated; using default journey diagram.[/bold yellow]")
    return output

# ------------------------------------------------------------------
# Phase 3b: Generate Mermaid Call Diagram using Functions Report
# ------------------------------------------------------------------
def generate_call_diagram(functions_report):
    prompt = (
        "Phase 3b: Based on the following functions report, generate ONLY valid Mermaid diagram text "
        "using 'flowchart TD' that represents the function call graph. Your output MUST start with 'flowchart TD' and contain only diagram code. "
        "Node labels must be simple with no extra symbols, quotes, or annotations. Use proper diamond notation for conditions if needed.\n\n"
        "Functions Report:\n"
        "-----------------\n"
        f"{functions_report}\n"
        "-----------------\n"
    )
    console.rule("[bold blue]Generating Mermaid Call Diagram[/bold blue]")
    output = call_llm(prompt, model=ANALYSIS_MODEL)
    console.print("[bold green]Function Call Mermaid Diagram Generated:[/bold green]")
    console.print(output)
    if not is_valid_mermaid(output):
        output = DEFAULT_CALL_DIAGRAM
        console.print("[bold yellow]No valid diagram generated; using default call diagram.[/bold yellow]")
    return output

# ------------------------------------------------------------------
# File Management Functions (using UTF-8)
# ------------------------------------------------------------------
def read_contract_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        console.print(f"[bold red]Error reading {filepath}:[/bold red] {e}")
        return ""

def save_file(content, filename, output_dir):
    path = os.path.join(output_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        console.print(f"[bold green]Saved '{filename}' to {output_dir}[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Error saving {filename}:[/bold red] {e}")

# ------------------------------------------------------------------
# Updated Query Report Function
# ------------------------------------------------------------------
def query_report(report_content, contract_base, output_dir):
    query = Prompt.ask("Enter your query about the report")
    # Read additional context files
    original_file = os.path.join(output_dir, f"{contract_base}_original.sol")
    journey_diagram_file = os.path.join(output_dir, f"{contract_base}_journey_diagram.md")
    call_diagram_file = os.path.join(output_dir, f"{contract_base}_call_diagram.md")
    try:
        with open(original_file, "r", encoding="utf-8") as f:
            original_content = f.read()
    except Exception:
        original_content = "[Original code not available]"
    try:
        with open(journey_diagram_file, "r", encoding="utf-8") as f:
            journey_diagram = f.read()
    except Exception:
        journey_diagram = "[Journey diagram not available]"
    try:
        with open(call_diagram_file, "r", encoding="utf-8") as f:
            call_diagram = f.read()
    except Exception:
        call_diagram = "[Call diagram not available]"
    prompt = (
        "Based on the following materials:\n"
        "================ Report Content ================\n"
        f"{report_content}\n\n"
        "================ Original Code ================\n"
        f"{original_content}\n\n"
        "================ Journey Diagram ================\n"
        f"{journey_diagram}\n\n"
        "================ Call Diagram ================\n"
        f"{call_diagram}\n\n"
        "Please answer the following query:\n"
        f"{query}\n"
    )
    console.print("[bold blue]Querying LLM with extended context...[/bold blue]")
    answer = call_llm(prompt, model=QUERY_MODEL)
    console.print("[bold green]LLM Answer:[/bold green]")
    console.print(answer)
    return answer

# ------------------------------------------------------------------
# Additional Functions for Interactive Menu
# ------------------------------------------------------------------
def view_file(filename, output_dir):
    path = os.path.join(output_dir, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        console.print(f"\n[bold blue]--- {filename} ---[/bold blue]\n")
        console.print(content)
        console.print(f"\n[bold blue]--- End of {filename} ---[/bold blue]\n")
    except Exception as e:
        console.print(f"[bold red]Error reading {filename}:[/bold red] {e}")

def check_and_regenerate_diagrams(contract_base, output_dir):
    # Check journey diagram
    jd_file = os.path.join(output_dir, f"{contract_base}_journey_diagram.md")
    try:
        with open(jd_file, "r", encoding="utf-8") as f:
            jd_content = f.read().strip()
    except Exception as e:
        console.print(f"[bold red]Error reading {jd_file}:[/bold red] {e}")
        jd_content = ""
    if not is_valid_mermaid(jd_content):
        choice = Prompt.ask("The journey diagram appears to be missing or invalid. Regenerate it? (y/n)", default="n")
        if choice.lower() == "y":
            # Look for original smart contract file saved as {contract_base}_original.sol
            sc_file = os.path.join(output_dir, f"{contract_base}_original.sol")
            if not os.path.exists(sc_file):
                console.print(f"[bold yellow]Original contract file {sc_file} not found. Cannot regenerate journey diagram.[/bold yellow]")
            else:
                try:
                    with open(sc_file, "r", encoding="utf-8") as f:
                        contract_content = f.read()
                except Exception as e:
                    console.print(f"[bold red]Error reading {sc_file}:[/bold red] {e}")
                    contract_content = ""
                new_jd = regenerate_journey_diagram_from_contract(contract_content)
                save_file(new_jd, f"{contract_base}_journey_diagram.md", output_dir)
    # Check call diagram
    cd_file = os.path.join(output_dir, f"{contract_base}_call_diagram.md")
    try:
        with open(cd_file, "r", encoding="utf-8") as f:
            cd_content = f.read().strip()
    except Exception as e:
        console.print(f"[bold red]Error reading {cd_file}:[/bold red] {e}")
        cd_content = ""
    if not is_valid_mermaid(cd_content):
        choice = Prompt.ask("The call diagram appears to be missing or invalid. Regenerate it? (y/n)", default="n")
        if choice.lower() == "y":
            sc_file = os.path.join(output_dir, f"{contract_base}_original.sol")
            if not os.path.exists(sc_file):
                console.print(f"[bold yellow]Original contract file {sc_file} not found. Cannot regenerate call diagram.[/bold yellow]")
            else:
                try:
                    with open(sc_file, "r", encoding="utf-8") as f:
                        contract_content = f.read()
                except Exception as e:
                    console.print(f"[bold red]Error reading {sc_file}:[/bold red] {e}")
                    contract_content = ""
                new_cd = regenerate_call_diagram_from_contract(contract_content)
                save_file(new_cd, f"{contract_base}_call_diagram.md", output_dir)

def show_contract_menu(contract_base, output_dir):
    while True:
        console.print(f"\n[bold blue]--- Menu for Contract: {contract_base} ---[/bold blue]")
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
            console.print("Which report do you want to query?")
            console.print("a. Functions Report")
            console.print("b. Journey Report")
            report_choice = Prompt.ask("Enter a or b").lower()
            if report_choice == "a":
                fr_file = os.path.join(output_dir, f"{contract_base}_functions_report.md")
                try:
                    with open(fr_file, "r", encoding="utf-8") as f:
                        report_content = f.read()
                    query_report(report_content, contract_base, output_dir)
                except Exception as e:
                    console.print(f"[bold red]Error reading functions report:[/bold red] {e}")
            elif report_choice == "b":
                jr_file = os.path.join(output_dir, f"{contract_base}_journey_report.md")
                try:
                    with open(jr_file, "r", encoding="utf-8") as f:
                        report_content = f.read()
                    query_report(report_content, contract_base, output_dir)
                except Exception as e:
                    console.print(f"[bold red]Error reading journey report:[/bold red] {e}")
        elif choice == "6":
            check_and_regenerate_diagrams(contract_base, output_dir)
        elif choice == "7":
            break
        else:
            console.print("[bold red]Invalid choice. Try again.[/bold red]")

def contract_menu_in_session(session_folder):
    while True:
        console.print(f"\n[bold blue]--- Session: {session_folder} ---[/bold blue]")
        reports = [f for f in os.listdir(session_folder) if f.endswith("_analysis_report.md")]
        if not reports:
            console.print("[bold red]No analysis reports found in this session.[/bold red]")
            break
        table = Table(title="Contracts in Session", show_header=True, header_style="bold magenta")
        table.add_column("No.", justify="right")
        table.add_column("Contract")
        contract_bases = []
        for idx, report in enumerate(reports, start=1):
            base = report.replace("_analysis_report.md", "")
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
                console.print("[bold red]Invalid selection. Try again.[/bold red]")
        except ValueError:
            console.print("[bold red]Invalid input. Enter a number.[/bold red]")

def browse_sessions():
    sessions = [d for d in os.listdir(os.getcwd()) if os.path.isdir(d) and d.startswith("analysis_")]
    if not sessions:
        console.print("[bold red]No analysis sessions found.[/bold red]")
        return
    table = Table(title="Analysis Sessions", show_header=True, header_style="bold magenta")
    table.add_column("No.", justify="right")
    table.add_column("Session (Chat ID)")
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
            console.print("[bold red]Invalid session number.[/bold red]")
    except ValueError:
        console.print("[bold red]Invalid input.[/bold red]")

# ------------------------------------------------------------------
# Main Processing Function
# ------------------------------------------------------------------
def process_contract(filepath, output_dir):
    console.rule(f"[bold blue]Processing: {os.path.basename(filepath)}[/bold blue]")
    content = read_contract_file(filepath)
    if not content:
        console.print("[bold red]No content read from file.[/bold red]")
        return

    contract_id = hashlib.sha256((filepath + content).encode()).hexdigest()
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    # Save the original contract file for diagram regeneration using a consistent suffix
    save_file(content, f"{base_name}_original.sol", output_dir)

    # Phase 1: Functions Report
    functions_report = generate_functions_report(content)
    save_file(functions_report, f"{base_name}_functions_report.md", output_dir)
    preliminary_report = (
        f"# Preliminary Analysis Report for {base_name}\n\n"
        f"## Functions Report\n\n{functions_report}\n"
    )
    save_file(preliminary_report, f"{base_name}_preliminary_report.md", output_dir)
    console.print("[bold green]Preliminary analysis report created and saved.[/bold green]")

    # Phase 2: Journey Report
    journey_report = generate_journey_report(content)
    save_file(journey_report, f"{base_name}_journey_report.md", output_dir)

    # Phase 3: Diagram Generation
    journey_diagram = generate_journey_diagram(journey_report)
    save_file(journey_diagram, f"{base_name}_journey_diagram.md", output_dir)
    call_diagram = generate_call_diagram(functions_report)
    save_file(call_diagram, f"{base_name}_call_diagram.md", output_dir)

    # Phase 4: Final Combined Report
    final_report = (
        f"# Final Analysis Report for {base_name}\n\n"
        f"## Functions Report\n\n{functions_report}\n\n"
        f"## Journey Report\n\n{journey_report}\n\n"
        f"## User Journey Diagram (Mermaid)\n\n{journey_diagram}\n\n"
        f"## Function Call Diagram (Mermaid)\n\n{call_diagram}\n"
    )
    save_file(final_report, f"{base_name}_analysis_report.md", output_dir)
    save_analysis(contract_id, os.path.basename(filepath), content, functions_report, journey_report, journey_diagram, call_diagram)

# ------------------------------------------------------------------
# Main Application Flow
# ------------------------------------------------------------------
def main():
    # Model selection
    analysis_model = Prompt.ask("Select model for initial analysis", choices=["deepseek-r1", "qwen2.5-coder:3b", "gemma3:4b", "deepseek-coder:6.7b"], default="deepseek-r1")
    query_model = Prompt.ask("Select model for querying", choices=["deepseek-r1", "qwen2.5-coder:3b", "gemma3:4b", "deepseek-coder:6.7b"], default="deepseek-r1")
    global ANALYSIS_MODEL, QUERY_MODEL, MODEL_NAME
    ANALYSIS_MODEL = analysis_model
    QUERY_MODEL = query_model
    MODEL_NAME = ANALYSIS_MODEL  # For all initial analysis calls

    console.print(f"[bold green]Using analysis model:[/bold green] {ANALYSIS_MODEL}")
    console.print(f"[bold green]Using query model:[/bold green] {QUERY_MODEL}")

    init_db()
    update_db_schema()

    # Present main menu options immediately after model selection
    while True:
        console.print("\n[bold blue]--- Main Menu ---[/bold blue]")
        console.print("1. Analyse a new directory")
        console.print("2. Browse historical sessions")
        console.print("3. Exit")
        main_choice = Prompt.ask("Enter your choice")
        if main_choice == "1":
            directory = Prompt.ask("Enter the path to the smart contracts directory")
            if not os.path.isdir(directory):
                console.print("[bold red]The provided directory does not exist.[/bold red]")
                continue
            output_folder = os.path.join(os.getcwd(), "analysis_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
            os.makedirs(output_folder, exist_ok=True)
            console.print(f"[bold green]Output folder created: {output_folder}[/bold green]")
            contract_files = [os.path.join(directory, f) for f in os.listdir(directory)
                              if os.path.isfile(os.path.join(directory, f)) and f.endswith(".sol")]
            if not contract_files:
                console.print("[bold red]No smart contract files (.sol) found in the directory.[/bold red]")
                continue
            console.print(f"[bold green]Found {len(contract_files)} contract(s). Starting analysis...[/bold green]\n")
            for filepath in contract_files:
                process_contract(filepath, output_folder)
            console.print("\n[bold green]All contracts processed successfully.[/bold green]")
            # After processing, go to session menu for the new output folder
            contract_menu_in_session(output_folder)
        elif main_choice == "2":
            browse_sessions()
        elif main_choice == "3":
            console.print("[bold green]Exiting application.[/bold green]")
            break
        else:
            console.print("[bold red]Invalid choice. Please try again.[/bold red]")

if __name__ == "__main__":
    main()
