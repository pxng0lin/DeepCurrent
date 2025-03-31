# DeepCurrent
DeepCurrent is a command-line tool that leverages a local LLM to analyse smart contract files. It provides detailed reports and visual diagrams (Mermaid) to help understand, audit, and document smart contracts.

## Features

- **Functions Report:**  
  Lists each function in the contract along with parameters, visibility, modifiers, and potential vulnerabilities.

- **Journey Report:**  
  Provides a textual description outlining the user journey through contract functions.

- **Mermaid Diagram Generation:**  
  Generates diagrams for visualising:
  - **User Journey** through the contract.
  - **Function Call Graph** detailing interactions and dependencies.
 
**Example Output:**

```
  flowchart TD
      A[Input] --> C[OptionHandler]
      C -->| getOrder | D[executeOrder]
      D --> E[Fail, 'Invalid sender']
      D --> F[Fail, 'Invalid order']
      D --> G[Fail, 'Invalid order type']
      D --> H[Fail, 'Spot price out of bounds']
      I[prefix calculation] --> J[Fail, 'ERC20 decimals mismatch in oToken conversion']
      I --> K[Fail, 'ERC20 decimals mismatch in strike conversion']
      D --> N[LiquidityPool]
          N --> O[oCollateral]
      D safeTransfer --> N
```
**Example Diagram (transferred to Mermaid.live)**

![image](https://github.com/user-attachments/assets/08a46a32-8be1-4d88-87e8-8988913580e0)


- **Interactive Sessions:**  
  Each run generates an output directory (`analysis_YYYYMMDD_HHMMSS`). Historical sessions can be browsed, with reports and diagrams interactively queried or regenerated.

- **Model Selection:**  
  Supports multiple LLM models for analysis and querying:
  - `deepseek-r1`
  - `qwen2.5-coder:3b`
  - `gemma3:4b`
  - `deepseek-coder:6.7b`

- **SQLite Database:**  
  Stores analysis outputs persistently in `smart_contracts_analysis.db`.

--------

## Prerequisites

Before running, ensure you have installed the following:

### 1. Astral UV

This script uses Astral UV for dependency management and running Python scripts. Install Astral UV following their official documentation:

- **Astral UV Documentation:** [https://docs.astral.sh/uv/](https://docs.astral.sh/uv/)

### 2. Ollama (Local LLM API Server)

The analyser depends on Ollama to run the required LLM models locally. Follow the Ollama installation instructions for your operating system:

- **Ollama Installation:** [https://ollama.com/download](https://ollama.com/download)

Once installed, run Ollama and download the preferred models as above, for example:

```bash
ollama pull deepseek-r1
```

Ensure Ollama is running locally at:

```
http://localhost:11434/v1/completions
```

## Installation and Usage

### Clone the Repository

```bash
git clone <repository_url>
cd <repository_directory>
```
--------

### Running DeepCurrent

All Python dependencies are managed automatically by Astral UV. No manual installation of Python packages is required.

Run the script directly with Astral UV:

```bash
uv run DeepCurrent.py
```
### Example of menu:
![image](https://github.com/user-attachments/assets/0d3efef8-28b2-4854-818f-95a4366ecd57)

#### Option 2:
![image](https://github.com/user-attachments/assets/82cdc478-c3d9-4440-9094-6d7fdfc2f72c)

#### Chosen Session ID:
![image](https://github.com/user-attachments/assets/3db6ed4f-a248-4f06-8095-c21cebe3236d)

#### Analysis menu:
![image](https://github.com/user-attachments/assets/bd73fa50-df7f-4e4e-907e-6c7591f7698d)

-------

## How to Use DeepCurrent

Upon running the script, you will be prompted:

1. **Model Selection:**  
   Select an LLM model for initial analysis and querying.

2. **Main Menu Options:**  
   - **Analyse a New Directory:** Provide the path to your `.sol` contract files.
   - **Browse Historical Sessions:** Review past analyses and regenerate diagrams as needed.
   - **Exit:** Close the application.

-------

### Output Structure

Results are stored in a timestamped directory (`analysis_YYYYMMDD_HHMMSS`) containing:

- Individual markdown reports:
  - `<contract_name>_functions_report.md`
  - `<contract_name>_journey_report.md`
- Mermaid diagrams:
  - `<contract_name>_journey_diagram.md`
  - `<contract_name>_call_diagram.md`
- Combined final report:
  - `<contract_name>_analysis_report.md`
- Original `.sol` contract file (for regeneration of diagrams if needed).
- SQLite database entries (`smart_contracts_analysis.db`) for ongoing reference.

## Contributing

Contributions are welcome! Please fork the repository and submit pull requests to suggest improvements, fix bugs, or add features.
