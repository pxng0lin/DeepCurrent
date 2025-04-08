#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "requests",
#     "rich",
#     "aiohttp",
#     "diskcache",
#     "pdfplumber",
#     "markdown",
#     "beautifulsoup4",
#     "PyMuPDF",
#     "ollama>=0.1.6"
# ]
# ///

"""
Smart Contract and Protocol Documentation Analyzer – V3.1
---------------------------------------------------------
This app extends the DeepCurrent V3 capabilities to analyze:
1. Smart contracts from a given directory
2. Protocol documentation from PDF files
3. Protocol documentation from Markdown (.md) files
4. Protocol documentation from URLs

For smart contracts, it generates:
   • Functions Report
   • Journey Report
   • Journey Diagram (Mermaid, starting with "flowchart TD")
   • Call Diagram (Mermaid)

For documentation, it generates:
   • Summary
   • Key Highlights
   • Contract Breakdown
   • Function Breakdown
   • Mechanics Diagrams (Mermaid)

All outputs are saved in a timestamped folder and stored in a SQLite database.
"""

import os, sys, sqlite3, hashlib, requests, re, asyncio, aiohttp, json, argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from diskcache import Cache
from rich import print
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.markdown import Markdown
from rich.syntax import Syntax
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
import tempfile
import random, time
import ollama  # Import the ollama-python client

# New imports for handling different document types
import pdfplumber
import markdown
from bs4 import BeautifulSoup

# Try to import PyMuPDF but make it optional
try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

console = Console()

# -------------------------------
# Global Configurations
# -------------------------------
# Ollama settings
OLLAMA_API_URL = "http://localhost:11434/api/generate"  # Updated to match test_ollama.py

# OpenRouter settings
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data policy settings for OpenRouter
OPENROUTER_DATA_USAGE = "null"  # Set to 'null' to use models without sharing data
OPENROUTER_API_KEY = ""  # Will be prompted for this

# General LLM settings
MAX_TOKENS = 10000
MODEL_NAME = "deepseek-r1:32b"  # Default for Ollama
ANALYSIS_MODEL = None
QUERY_MODEL = None
LLM_PROVIDER = "ollama"  # Can be 'ollama' or 'openrouter'

# Default OpenRouter models (all free)
OPENROUTER_MODELS = [
    "deepseek/deepseek-v3-base:free",
    "google/gemini-2.5-pro-exp-03-25:free", 
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "open-r1/olympiccoder-32b:free",
    "allenai/molmo-7b-d:free",
    "bytedance-research/ui-tars-72b:free",
    "qwen/qwen2.5-vl-3b-instruct:free",
    "qwen/qwen2.5-vl-32b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "featherless/qwerky-72b:free"
]

# Cache configuration
cache = Cache('.cache')
MAX_CACHE_SIZE = 1024 * 1024 * 1024  # 1GB cache size
CACHE_EXPIRY = 60 * 60 * 24  # 24 hours

# Async session
async_session = None

# -------------------------------
# SQLite Database Setup
# -------------------------------
DB_NAME = "smart_contracts_analysis.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    # Original contracts table
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
    
    # New documentation table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            source_type TEXT,  -- 'pdf', 'md', 'url'
            source_path TEXT,
            content TEXT,
            summary TEXT,
            key_highlights TEXT,
            contract_breakdown TEXT,
            function_breakdown TEXT,
            mechanics_diagram TEXT,
            analysed_at TEXT
        )
    """)
    
    # Q&A sessions table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS qa_sessions (
            id TEXT PRIMARY KEY,
            session_folder TEXT,
            timestamp TEXT,
            qa_data TEXT  -- JSON string containing array of Q&A pairs
        )
    """)
    
    # Vulnerability detection library table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vuln_detection_library (
            id TEXT PRIMARY KEY,
            vuln_type TEXT,
            details TEXT,  -- JSON string with questions, examples, insights
            template TEXT,  -- Detection template
            created_at TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def update_db_schema():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Update contracts table if needed
    cur.execute("PRAGMA table_info(contracts)")
    columns = [col[1] for col in cur.fetchall()]
    needed = ["functions_report", "journey_report", "journey_diagram", "call_diagram"]
    for col in needed:
        if col not in columns:
            cur.execute(f"ALTER TABLE contracts ADD COLUMN {col} TEXT")
            console.print(f"[bold green]Database schema updated:[/bold green] '{col}' column added to contracts table.")
    
    # Check if documents table exists, create if not
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                source_type TEXT,
                source_path TEXT,
                content TEXT,
                summary TEXT,
                key_highlights TEXT,
                contract_breakdown TEXT,
                function_breakdown TEXT,
                mechanics_diagram TEXT,
                analysed_at TEXT
            )
        """)
        console.print("[bold green]Database schema updated:[/bold green] 'documents' table created.")
    
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

def save_document_analysis(doc_id, source_type, source_path, content, summary, key_highlights, 
                           contract_breakdown, function_breakdown, mechanics_diagram):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
       INSERT OR REPLACE INTO documents
       (id, source_type, source_path, content, summary, key_highlights, contract_breakdown, 
        function_breakdown, mechanics_diagram, analysed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (doc_id, source_type, source_path, content, summary, key_highlights, contract_breakdown, 
           function_breakdown, mechanics_diagram, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# -------------------------------
# LLM API Interaction Functions
# -------------------------------
async def init_async_session():
    global async_session
    if async_session is None:
        async_session = aiohttp.ClientSession()

async def close_async_session():
    global async_session
    if async_session:
        await async_session.close()
        async_session = None

async def call_openrouter_async(prompt, model=None, max_retries=3, initial_backoff=1):
    """Call OpenRouter API with streaming support - following their documentation"""
    if model is None:
        model = "deepseek/deepseek-v3-base:free"  # Default OpenRouter model

    # Check cache first
    cache_key = hashlib.sha256((prompt + model + "openrouter").encode()).hexdigest()
    cached_response = cache.get(cache_key)
    if cached_response:
        return cached_response

    # Convert the prompt to OpenRouter's expected format
    messages = [{"role": "user", "content": prompt}]

    # Add required headers including HTTP_REFERER and X-Title for data policy - exactly as in docs
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/pxng0lin/DeepCurrent",  # Required for data policies
        "X-Title": "DeepCurrent Smart Contract Analyzer",  # Required for data policies
        "X-Data-Usage": OPENROUTER_DATA_USAGE  # Control data usage policy
    }
    
    # Simplified payload following the OpenRouter examples
    payload = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    
    # Retry with exponential backoff
    retry_count = 0
    backoff = initial_backoff
    
    # Try main model first, then fall back to backup models if needed
    backup_models = OPENROUTER_MODELS.copy()
    if model in backup_models:
        backup_models.remove(model)
    
    models_to_try = [model] + backup_models
    
    for attempt, current_model in enumerate(models_to_try):
        if attempt > 0:
            console.print(f"[yellow]Attempting fallback model: {current_model}[/yellow]")
            payload["model"] = current_model
        
        try:
            await init_async_session()
            console.print(f"[cyan]Calling OpenRouter with model: {current_model}[/cyan]")
            response = ""
            
            async with async_session.post(OPENROUTER_API_URL, 
                                        json=payload, 
                                        headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    console.print(f"[yellow]Error {resp.status} from OpenRouter: {error_text}[/yellow]")
                    if attempt < len(models_to_try) - 1:
                        await asyncio.sleep(backoff)  # Wait before retry
                        backoff *= 2  # Exponential backoff
                        continue
                    else:
                        # Last model failed, return fallback response
                        return "Unable to generate analysis. Please try a different model or provider."
                
                # Process the streaming response
                async for chunk in resp.content:
                    if not chunk:
                        continue
                        
                    # Convert chunk to text and handle 'data: ' prefix
                    chunk_text = chunk.decode('utf-8')
                    if chunk_text.startswith('data: '):
                        chunk_text = chunk_text[6:]
                    
                    # Skip [DONE] messages
                    if chunk_text.strip() == '[DONE]':
                        continue
                        
                    # Parse JSON chunk
                    try:
                        chunk_data = json.loads(chunk_text)
                        if 'choices' in chunk_data and chunk_data['choices'] and 'delta' in chunk_data['choices'][0]:
                            delta = chunk_data['choices'][0]['delta']
                            if 'content' in delta and delta['content']:
                                response += delta['content']
                    except json.JSONDecodeError as e:
                        console.print(f"[yellow]JSON decode error: {e}[/yellow]")
                        console.print(f"Problematic chunk: {chunk_text}")
            
            # Store in cache and return accumulated response
            if response:
                cache.set(cache_key, response, expire=CACHE_EXPIRY)
                return response
            else:
                if attempt < len(models_to_try) - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    # Last model failed, return fallback response
                    return "Unable to generate analysis. Please try a different model or provider."
                    
        except Exception as e:
            console.print(f"[yellow]Error with {current_model}: {e}[/yellow]")
            if attempt < len(models_to_try) - 1:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            else:
                # Last model failed, return fallback response
                return "Unable to generate analysis. Please try a different model or provider."
    
    # We should never reach here, but just in case
    return "Unable to generate analysis. Please try a different model or provider."

async def call_ollama_async(prompt, model=None, max_retries=3, initial_backoff=1):
    """Call Ollama API using chat API from ollama-python library"""
    if model is None:
        model = MODEL_NAME

    # Check cache first
    cache_key = hashlib.sha256((prompt + model + "ollama").encode()).hexdigest()
    cached_response = cache.get(cache_key)
    if cached_response:
        return cached_response

    # Retry with exponential backoff
    retry_count = 0
    backoff = initial_backoff
    
    while retry_count <= max_retries:
        try:
            console.print(f"[cyan]Calling Ollama with model: {model}[/cyan]")
            
            # Use the ollama-python client with chat API
            response = ollama.chat(
                model=model,
                messages=[
                    {
                        'role': 'system',
                        'content': 'You are a smart contract and protocol documentation analyzer.'
                    },
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ],
                options={
                    "temperature": 0.1
                }
            )
            
            # Extract content from Pydantic model response
            content = ""
            if hasattr(response, 'message') and hasattr(response.message, 'content'):
                content = response.message.content
            
            # Store in cache and return content
            if content:
                cache.set(cache_key, content, expire=CACHE_EXPIRY)
                return content
            else:
                if retry_count < max_retries:
                    retry_count += 1
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    return "Unable to generate analysis. Please try a different model or provider."
                    
        except Exception as e:
            console.print(f"[yellow]Error connecting to Ollama: {e}[/yellow]")
            if retry_count < max_retries:
                retry_count += 1
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            else:
                return "Unable to generate analysis. Please try a different model or provider."
    
    # We should never reach here, but just in case
    return "Unable to generate analysis. Please try a different model or provider."

async def call_llm_async(prompt, model=None, max_retries=3, initial_backoff=1):
    """Main async function to call LLM - routes to appropriate provider"""
    if LLM_PROVIDER == "openrouter":
        return await call_openrouter_async(prompt, model, max_retries, initial_backoff)
    else:  # Default to Ollama
        return await call_ollama_async(prompt, model, max_retries, initial_backoff)

# Define both synchronous and async versions of the LLM call function
def call_llm(prompt, model=None, max_retries=3, backoff_factor=2):
    """Synchronous LLM call function with retries and improved error handling"""
    if model is None:
        model = ANALYSIS_MODEL
    
    # Cut down extremely long prompts to avoid API issues
    max_prompt_length = 12000  # Characters
    if len(prompt) > max_prompt_length:
        prompt = prompt[:max_prompt_length] + "\n\n[Note: Prompt was truncated due to length]\n"
        
    if LLM_PROVIDER == "openrouter":
        retries = 0
        wait_time = 1  # Initial wait time in seconds
        
        while retries <= max_retries:
            try:
                headers = {
                    "HTTP-Referer": "https://github.com/",
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}"
                }
                
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a smart contract and protocol documentation analyzer."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 20000,
                    "temperature": 0.1,
                }
                
                response = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60  # Add explicit timeout
                )
                
                # Check if response is valid
                response.raise_for_status()
                response_json = response.json()
                
                # Validate response structure
                if "choices" not in response_json or len(response_json["choices"]) == 0:
                    raise ValueError(f"Invalid API response structure: 'choices' missing or empty - {response_json}")
                    
                if "message" not in response_json["choices"][0]:
                    raise ValueError(f"Invalid API response structure: 'message' missing - {response_json}")
                    
                if "content" not in response_json["choices"][0]["message"]:
                    raise ValueError(f"Invalid API response structure: 'content' missing - {response_json}")
                
                return response_json["choices"][0]["message"]["content"]
                
            except requests.exceptions.RequestException as e:
                # Network errors, timeouts, HTTP errors
                console.print(f"[bold yellow]OpenRouter API request failed (attempt {retries+1}/{max_retries+1}):[/bold yellow] {e}")
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                # Response parsing errors
                console.print(f"[bold yellow]Error parsing OpenRouter API response (attempt {retries+1}/{max_retries+1}):[/bold yellow] {e}")
            except Exception as e:
                # Any other unexpected errors
                console.print(f"[bold red]Unexpected error with OpenRouter API (attempt {retries+1}/{max_retries+1}):[/bold red] {e}")
            
            # If we've reached max retries, break out and return fallback response
            if retries >= max_retries:
                break
                
            # Exponential backoff with jitter
            sleep_time = wait_time * (1 + random.random() * 0.1)  # Add up to 10% jitter
            console.print(f"[yellow]Retrying in {sleep_time:.1f} seconds...[/yellow]")
            time.sleep(sleep_time)
            wait_time *= backoff_factor  # Exponential backoff
            retries += 1
        
        # If we got here, all retries failed
        console.print(f"[bold red]All {max_retries+1} attempts to call OpenRouter API failed.[/bold red]")
        
        # Provide a minimally useful fallback response
        return """I apologize, but I couldn't process your request due to API connectivity issues. 
The system will continue with limited functionality.
Please check your API key or network connection and try again later."""

    else:  # Use Ollama
        try:
            # Use the ollama-python client with chat API
            response = ollama.chat(
                model=model,
                messages=[
                    {
                        'role': 'system',
                        'content': 'You are a smart contract and protocol documentation analyzer.'
                    },
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ],
                options={
                    "temperature": 0.1
                }
            )
            
            # Access attribute from Pydantic model
            if hasattr(response, 'message') and hasattr(response.message, 'content'):
                return response.message.content
            return 'No response from Ollama'
        except Exception as e:
            console.print(f"[bold red]Error calling Ollama API:[/bold red] {e}")
            return f"Error generating response: {e}"

# -------------------------------
# Helper: Extract Mermaid Code
# -------------------------------
def extract_mermaid_code(text):
    """Extract mermaid code from a text string"""
    mermaid_pattern = r'```mermaid([\s\S]*?)```'
    matches = re.findall(mermaid_pattern, text)
    
    if matches:
        # Found mermaid code block(s)
        return '```mermaid' + matches[0] + '```'
    else:
        # Try another common format
        alternate_pattern = r'(flowchart [A-Z][A-Z][\s\S]*?)(?:##|$|\Z)'
        matches = re.findall(alternate_pattern, text)
        if matches:
            return '```mermaid\n' + matches[0].strip() + '\n```'
        return None

# -------------------------------
# Document Content Extraction
# -------------------------------
def extract_pdf_content(pdf_path):
    """Extract text content from a PDF file"""
    text_content = ""
    try:
        # Using PDFPlumber to extract text
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]Extracting PDF content... {task.description}"),
            ) as progress:
                task = progress.add_task(f"Page 1/{total_pages}", total=total_pages)
                
                for i, page in enumerate(pdf.pages):
                    progress.update(task, description=f"Page {i+1}/{total_pages}", advance=1)
                    page_text = page.extract_text() or ""
                    text_content += f"\n--- Page {i+1} ---\n{page_text}"
        
        console.print(f"[green]Successfully extracted content from {pdf_path} ({total_pages} pages)[/green]")
        return text_content
    except Exception as e:
        console.print(f"[bold red]Error extracting PDF content:[/bold red] {e}")
        
        # Try alternative method with PyMuPDF if pdfplumber fails and PyMuPDF is available
        if fitz is not None:
            try:
                console.print("[yellow]Trying alternative PDF extraction method...[/yellow]")
                doc = fitz.open(pdf_path)
                text_content = ""
                total_pages = len(doc)
                
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold blue]Extracting PDF content (alternative method)... {task.description}"),
                ) as progress:
                    task = progress.add_task(f"Page 1/{total_pages}", total=total_pages)
                    
                    for i in range(total_pages):
                        progress.update(task, description=f"Page {i+1}/{total_pages}", advance=1)
                        page = doc.load_page(i)
                        text_content += f"\n--- Page {i+1} ---\n{page.get_text()}"
                
                console.print(f"[green]Successfully extracted content using alternative method[/green]")
                return text_content
            except Exception as alt_e:
                console.print(f"[bold red]Alternative extraction also failed:[/bold red] {alt_e}")
                return f"Error extracting PDF content: {e}\nAlternative method error: {alt_e}"
        else:
            console.print("[yellow]PyMuPDF (fitz) is not available for alternative PDF extraction[/yellow]")
            return f"Error extracting PDF content: {e}\nPyMuPDF not available for alternative extraction."

def extract_markdown_content(md_path):
    """Extract and convert markdown content to plain text"""
    try:
        with open(md_path, 'r', encoding='utf-8') as file:
            md_content = file.read()
        
        # Convert to HTML (for structure) and then extract plain text
        html_content = markdown.markdown(md_content)
        soup = BeautifulSoup(html_content, 'html.parser')
        text_content = soup.get_text(separator='\n\n')
        
        console.print(f"[green]Successfully extracted content from {md_path}[/green]")
        return text_content
    except Exception as e:
        console.print(f"[bold red]Error extracting markdown content:[/bold red] {e}")
        return f"Error extracting markdown content: {e}"

async def extract_url_content(url):
    """Extract content from a website URL (async version)"""
    try:
        await init_async_session()
        console.print(f"[cyan]Fetching content from URL: {url}[/cyan]")
        
        async with async_session.get(url) as response:
            if response.status != 200:
                error_text = await response.text()
                console.print(f"[bold red]Error {response.status} from URL: {error_text}[/bold red]")
                return f"Error fetching URL content: HTTP {response.status}"
            
            html_content = await response.text()
            
            # Parse HTML and extract meaningful content
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove script and style elements that would clutter the text
            for script in soup(["script", "style", "header", "footer", "nav"]):
                script.extract()
            
            # Extract text with some formatting maintained
            paragraphs = []
            for p in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'li']):
                text = p.get_text(strip=True)
                if text and len(text) > 5:  # Ignore very short or empty elements
                    if p.name.startswith('h'):
                        paragraphs.append(f"\n## {text}\n")
                    else:
                        paragraphs.append(text)
            
            # If we couldn't find structured content, fall back to all text
            if not paragraphs:
                text_content = soup.get_text(separator='\n\n')
            else:
                text_content = '\n'.join(paragraphs)
            
            console.print(f"[green]Successfully extracted content from {url}[/green]")
            return text_content
    except Exception as e:
        console.print(f"[bold red]Error fetching URL content:[/bold red] {e}")
        return f"Error fetching URL content: {e}"

def extract_url_content_sync(url):
    """Extract content from a website URL (synchronous version)"""
    try:
        console.print(f"[cyan]Fetching content from URL: {url}[/cyan]")
        
        # Use requests for synchronous HTTP request
        response = requests.get(url)
        if response.status_code != 200:
            console.print(f"[bold red]Error {response.status_code} from URL[/bold red]")
            return f"Error fetching URL content: HTTP {response.status_code}"
        
        html_content = response.text
        
        # Parse HTML and extract meaningful content
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove script and style elements that would clutter the text
        for script in soup(["script", "style", "header", "footer", "nav"]):
            script.extract()
        
        # Extract text with some formatting maintained
        paragraphs = []
        for p in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'li']):
            text = p.get_text(strip=True)
            if text and len(text) > 5:  # Ignore very short or empty elements
                if p.name.startswith('h'):
                    paragraphs.append(f"\n## {text}\n")
                else:
                    paragraphs.append(text)
        
        # If we couldn't find structured content, fall back to all text
        if not paragraphs:
            text_content = soup.get_text(separator='\n\n')
        else:
            text_content = '\n'.join(paragraphs)
        
        console.print(f"[green]Successfully extracted content from {url}[/green]")
        return text_content
    except Exception as e:
        console.print(f"[bold red]Error fetching URL content:[/bold red] {e}")
        return f"Error fetching URL content: {e}"

def extract_document_content(source_path, source_type=None):
    """Extract content from a document based on its type"""
    if source_type is None:
        # Try to determine the source type from the path
        if source_path.lower().endswith('.pdf'):
            source_type = 'pdf'
        elif source_path.lower().endswith(('.md', '.markdown')):
            source_type = 'md'
        elif source_path.lower().startswith(('http://', 'https://')):
            source_type = 'url'
        else:
            console.print("[bold red]Could not determine source type from path.[/bold red]")
            return None, None
    
    content = None
    if source_type == 'pdf':
        content = extract_pdf_content(source_path)
    elif source_type == 'md':
        content = extract_markdown_content(source_path)
    elif source_type == 'url':
        # Use the synchronous version instead of asyncio.run to avoid nested event loops
        content = extract_url_content_sync(source_path)
    else:
        console.print(f"[bold red]Unsupported source type: {source_type}[/bold red]")
    
    return content, source_type

# -------------------------------
# Document Analysis Generation
# -------------------------------
def generate_documentation_summary(doc_content):
    """Generate a summary of the documentation"""
    prompt = f"""
# Protocol Documentation Analysis Task: Generate Summary

Analyze the following protocol documentation and generate a concise summary that captures the essence of the protocol.

## Documentation Content:
{doc_content[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Provide an executive summary (2-3 paragraphs) that explains what this protocol does and its primary purpose.
2. Highlight the core functionality and use cases.
3. Indicate the blockchain or technology it's built for (if specified).
4. Note any distinguishing features or innovations.

Format your response as clean markdown.
"""
    
    console.print("[cyan]Generating documentation summary...[/cyan]")
    summary = call_llm(prompt, ANALYSIS_MODEL)
    return summary

def generate_key_highlights(doc_content):
    """Generate key highlights from the documentation"""
    prompt = f"""
# Protocol Documentation Analysis Task: Extract Key Highlights

Analyze the following protocol documentation and extract the key highlights and important aspects.

## Documentation Content:
{doc_content[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Identify 5-10 key highlights or important aspects of this protocol.
2. For each highlight, provide a brief explanation of why it's significant.
3. Include any notable security considerations, constraints, or limitations.
4. Note any governance mechanisms or token economics (if applicable).

Format your response as a bulleted list in clean markdown.
"""
    
    console.print("[cyan]Extracting key highlights...[/cyan]")
    highlights = call_llm(prompt, ANALYSIS_MODEL)
    return highlights

def generate_contract_breakdown(doc_content):
    """Generate a breakdown of contracts mentioned in the documentation"""
    prompt = f"""
# Protocol Documentation Analysis Task: Contract Breakdown

Analyze the following protocol documentation and provide a detailed breakdown of all the smart contracts mentioned.

## Documentation Content:
{doc_content[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Identify all smart contracts or contract interfaces mentioned in the documentation.
2. For each contract, provide:
   - Contract name
   - Purpose and responsibility within the protocol
   - Key state variables (if mentioned)
   - Important interactions with other contracts
   - Access control or permissions
3. Organize the contracts by their role in the protocol (e.g., core, periphery, governance).

Format your response as clean markdown with clear hierarchical structure.
"""
    
    console.print("[cyan]Generating contract breakdown...[/cyan]")
    contract_breakdown = call_llm(prompt, ANALYSIS_MODEL)
    return contract_breakdown

def generate_function_breakdown(doc_content):
    """Generate a breakdown of key functions mentioned in the documentation"""
    prompt = f"""
# Protocol Documentation Analysis Task: Function Breakdown

Analyze the following protocol documentation and provide a detailed breakdown of the key functions mentioned.

## Documentation Content:
{doc_content[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Identify the most important functions mentioned in the documentation.
2. For each function, provide:
   - Function name and signature (if available)
   - Purpose and what it accomplishes
   - Key parameters and return values
   - Important modifiers or restrictions
   - Any notable side effects or state changes
3. Organize functions by contract or by protocol flow/lifecycle.

Format your response as clean markdown with code blocks where appropriate.
"""
    
    console.print("[cyan]Generating function breakdown...[/cyan]")
    function_breakdown = call_llm(prompt, ANALYSIS_MODEL)
    return function_breakdown

def generate_mechanics_diagram(doc_content):
    """Generate mermaid diagrams for protocol mechanics"""
    prompt = f"""
# Protocol Documentation Analysis Task: Mechanics Diagram

Analyze the following protocol documentation and create detailed Mermaid diagrams that visualize the protocol's mechanics.

## Documentation Content:
{doc_content[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Create multiple Mermaid diagrams to represent different aspects of the protocol:
   - Core protocol flow (flowchart TD)
   - Contract interaction diagram (flowchart LR)
   - User journey/interaction flows (flowchart TD)
   - Token or asset flows (flowchart LR)
2. Label all nodes and edges clearly.
3. Use appropriate colors and styles to differentiate between actors, contracts, and processes.
4. Include brief explanatory text before each diagram.

## IMPORTANT SYNTAX REQUIREMENTS:
1. DO NOT use parentheses '(' or ')' in node IDs or labels as they cause syntax errors in Mermaid
2. Instead, use one of these alternatives:
   - Replace parentheses with square brackets '[' and ']'
   - Replace parentheses with curly braces '{' and '}'
   - Simply remove the parentheses
   - Use special formatting like HTML entities, e.g., &lpar; and &rpar; if within HTML spans
3. For function descriptions, use a hyphen or colon instead of parentheses
   Example: "checkAccess - onlyOwner" instead of "checkAccess (onlyOwner)"

For each diagram, use the following Mermaid syntax:
```mermaid
flowchart TD or flowchart LR
... your diagram nodes and connections here ...
```

Provide at least 3 different diagrams covering different aspects of the protocol.
"""
    
    console.print("[cyan]Generating mechanics diagrams...[/cyan]")
    diagrams_raw = call_llm(prompt, ANALYSIS_MODEL)
    
    # As a fallback, also clean up any remaining parentheses in the generated diagrams
    # Replace problematic patterns in node definitions
    # This regex targets function descriptions in brackets but preserves HTML spans
    import re
    mechanics_diagrams = re.sub(r'\[([^\]<>]*?)\(([^\)]+?)\)([^\]<>]*?)\]', r'[\1 - \2\3]', diagrams_raw)
    
    return mechanics_diagrams

# -------------------------------
# Smart Contract Analysis Functions
# -------------------------------

def generate_functions_report(contract_content):
    """Generate a detailed report of all functions in the smart contract"""
    prompt = f"""
# Smart Contract Analysis Task: Functions Report

Analyze the following smart contract and create a detailed functions report.

## Contract Content:
{contract_content[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Identify and document all functions in the contract, including:
   - Function name and signature
   - Purpose and functionality
   - Input parameters and their purpose
   - Return values and their meaning
   - Visibility (public, private, internal, external)
   - Modifiers and their impact
   - View/pure status
   - State variables read or modified
2. Analyze each function for:
   - Potential security concerns
   - Gas optimization opportunities
   - Edge cases or limitations
3. Format the report in clear Markdown with appropriate headings and sections
"""
    
    console.print("[cyan]Generating functions report...[/cyan]")
    functions_report = call_llm(prompt, ANALYSIS_MODEL)
    
    return functions_report

def generate_journey_report(contract_content):
    """Generate a report on the contract's user journey and workflow"""
    prompt = f"""
# Smart Contract Analysis Task: User Journey Report

Analyze the following smart contract and create a comprehensive user journey report.

## Contract Content:
{contract_content[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Identify and document the main flows and user journeys through the contract:
   - Contract initialization and setup
   - Main interaction paths for different user types
   - Key state transitions and logic branches
   - Error handling and recovery paths
2. Analyze the overall architecture and design patterns:
   - Contract architecture and component relationships
   - Design patterns used and their implementation
   - Dependency flows and inheritance relationships
3. Evaluate the contract from a user experience perspective:
   - Potential user friction points
   - Optimization recommendations
   - Security considerations for users
4. Format the report in clear Markdown with appropriate headings and sections
"""
    
    console.print("[cyan]Generating journey report...[/cyan]")
    journey_report = call_llm(prompt, ANALYSIS_MODEL)
    
    return journey_report

def generate_journey_diagram(journey_report):
    """Generate a Mermaid diagram visualizing the contract journey"""
    prompt = f"""
# Smart Contract Diagram Task: User Journey Visualization

Based on the following journey report, create a comprehensive Mermaid diagram that visualizes the contract's user journeys and workflows.

## Journey Report:
{journey_report[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Create a detailed Mermaid flowchart diagram (TD - top-down) that shows:
   - Main user journeys through the contract
   - Key decision points and conditional paths
   - State transitions and their triggers
   - Different user roles and their interactions
2. Use appropriate colors and styles to distinguish different components
3. Include clear labels for all nodes and connections
4. Focus on the most important flows while maintaining readability

## IMPORTANT SYNTAX REQUIREMENTS:
1. DO NOT use parentheses '(' or ')' in node IDs or labels as they cause syntax errors in Mermaid
2. Instead, use one of these alternatives:
   - Replace parentheses with square brackets '[' and ']'
   - Replace parentheses with curly braces '{' and '}'
   - Simply remove the parentheses
   - Use special formatting like HTML entities, e.g., &lpar; and &rpar; if within HTML spans
3. For function descriptions, use a hyphen or colon instead of parentheses
   Example: "checkAccess - onlyOwner" instead of "checkAccess (onlyOwner)"

Provide ONLY the Mermaid diagram code in the following format:
```mermaid
flowchart TD
... your diagram nodes and connections here ...
```
"""
    
    console.print("[cyan]Generating journey diagram...[/cyan]")
    journey_diagram_raw = call_llm(prompt, ANALYSIS_MODEL)
    
    # As a fallback, also clean up any remaining parentheses in the generated diagram
    if "```mermaid" in journey_diagram_raw:
        # Extract just the mermaid code
        mermaid_start = journey_diagram_raw.find("```mermaid")
        mermaid_end = journey_diagram_raw.find("```", mermaid_start + 10)
        if mermaid_end > mermaid_start:
            mermaid_code = journey_diagram_raw[mermaid_start:mermaid_end+3]
            
            # Replace problematic patterns in node definitions
            # This regex targets function descriptions in brackets but preserves HTML spans
            import re
            cleaned_mermaid = re.sub(r'\[([^\]<>]*?)\(([^\)]+?)\)([^\]<>]*?)\]', r'[\1 - \2\3]', mermaid_code)
            
            # Rebuild the response
            journey_diagram_raw = journey_diagram_raw[:mermaid_start] + cleaned_mermaid + journey_diagram_raw[mermaid_end+3:]
    
    return journey_diagram_raw

def generate_call_diagram(functions_report):
    """Generate a Mermaid diagram showing function call relationships"""
    prompt = f"""
# Smart Contract Diagram Task: Function Call Visualization

Based on the following functions report, create a comprehensive Mermaid diagram that visualizes the function call relationships in the contract.

## Functions Report:
{functions_report[:20000]}  # Limiting to 20k chars to avoid token limits

## Instructions:
1. Create a detailed Mermaid diagram that shows:
   - Functions and their relationships
   - Call patterns between functions
   - Access control and visibility relationships
   - Inheritance and override patterns
2. IMPORTANT: Use ONLY one diagram type - flowchart LR (left to right)
3. Use colors and styles to distinguish different function types (public, private, etc.)
4. Include clear labels for all nodes and connections
5. Keep the diagram to a single flowchart - DO NOT include multiple diagram types

## IMPORTANT SYNTAX REQUIREMENTS:
1. DO NOT use parentheses '(' or ')' in node IDs or labels as they cause syntax errors in Mermaid
2. Instead, use one of these alternatives:
   - Replace parentheses with square brackets '[' and ']'
   - Replace parentheses with curly braces '{' and '}'
   - Simply remove the parentheses
   - Use special formatting like HTML entities, e.g., &lpar; and &rpar; if within HTML spans
3. For visibility and access modifiers, use a hyphen or colon instead of parentheses
   Example: "Constructor - internal" instead of "Constructor (internal)"
4. DO NOT include a classDiagram section - only use a single flowchart LR

Provide ONLY the Mermaid diagram code in the following format:
```mermaid
flowchart LR
... your diagram nodes and connections here ...
```
"""
    
    console.print("[cyan]Generating call diagram...[/cyan]")
    call_diagram_raw = call_llm(prompt, ANALYSIS_MODEL)
    
    # As a fallback, also clean up any remaining parentheses in the generated diagram
    if "```mermaid" in call_diagram_raw:
        # Extract just the mermaid code
        mermaid_start = call_diagram_raw.find("```mermaid")
        mermaid_end = call_diagram_raw.find("```", mermaid_start + 10)
        if mermaid_end > mermaid_start:
            mermaid_code = call_diagram_raw[mermaid_start:mermaid_end+3]
            
            # Replace problematic patterns in node definitions
            # This regex targets function descriptions in brackets but preserves HTML spans
            import re
            cleaned_mermaid = re.sub(r'\[([^\]<>]*?)\(([^\)]+?)\)([^\]<>]*?)\]', r'[\1 - \2\3]', mermaid_code)
            
            # Make sure there's only one diagram type
            if "classDiagram" in cleaned_mermaid:
                # Keep only the flowchart part
                flowchart_part = cleaned_mermaid.split("classDiagram")[0]
                # Ensure it ends with proper markdown code block closing
                if "```" not in flowchart_part:
                    flowchart_part += "\n```"
                cleaned_mermaid = flowchart_part
            
            # Rebuild the response
            call_diagram_raw = call_diagram_raw[:mermaid_start] + cleaned_mermaid + call_diagram_raw[mermaid_end+3:]
    
    return call_diagram_raw

# -------------------------------
# File Management Functions
# -------------------------------
def read_contract_file(filepath):
    """Read the contents of a contract file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        console.print(f"[bold red]Error reading file:[/bold red] {e}")
        return None

def save_file(content, filename, output_dir):
    """Save content to a file in the output directory"""
    filepath = os.path.join(output_dir, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(content)
        return filepath
    except Exception as e:
        console.print(f"[bold red]Error saving file:[/bold red] {e}")
        return None

def read_file(filepath):
    """Read the contents of a file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        console.print(f"[bold red]Error reading file:[/bold red] {e}")
        return None

# -------------------------------
# Document Processing Functions
# -------------------------------
async def process_document_async(source_path, output_dir, source_type=None, progress=None):
    """Process a document (PDF, Markdown, or URL) and generate analysis"""
    # Extract the document content based on its type
    doc_content, detected_source_type = extract_document_content(source_path, source_type)
    
    if not doc_content:
        console.print(f"[bold red]Failed to extract content from {source_path}[/bold red]")
        return
    
    source_type = detected_source_type if detected_source_type else source_type
    
    # Generate a unique ID for the document
    doc_id = hashlib.md5((source_path + source_type).encode()).hexdigest()
    
    # Prepare output filenames
    doc_base = os.path.basename(source_path)
    if source_type == 'url':
        # For URLs, use the domain as base name
        parsed_url = urlparse(source_path)
        doc_base = parsed_url.netloc
    
    # Create a subdirectory for this document's analysis
    doc_dir = os.path.join(output_dir, f"{doc_base}_{doc_id[:8]}")
    os.makedirs(doc_dir, exist_ok=True)
    
    # Save the extracted content
    content_path = save_file(doc_content, "content.txt", doc_dir)
    if not content_path:
        console.print(f"[bold red]Failed to save extracted content from {source_path}[/bold red]")
        return
    
    # Create a task ID for progress tracking
    task_id = None
    if progress:
        task_id = progress.add_task("Generating documentation summary...", total=1)
    
    # Generate and save analysis components
    summary = generate_documentation_summary(doc_content)
    summary_path = save_file(summary, "summary.md", doc_dir)
    
    if progress and task_id is not None:
        progress.update(task_id, description="Extracting key highlights...")
    key_highlights = generate_key_highlights(doc_content)
    highlights_path = save_file(key_highlights, "key_highlights.md", doc_dir)
    
    if progress and task_id is not None:
        progress.update(task_id, description="Generating contract breakdown...")
    contract_breakdown = generate_contract_breakdown(doc_content)
    contract_path = save_file(contract_breakdown, "contract_breakdown.md", doc_dir)
    
    if progress and task_id is not None:
        progress.update(task_id, description="Generating function breakdown...")
    function_breakdown = generate_function_breakdown(doc_content)
    function_path = save_file(function_breakdown, "function_breakdown.md", doc_dir)
    
    if progress and task_id is not None:
        progress.update(task_id, description="Generating mechanics diagrams...")
    mechanics_diagram = generate_mechanics_diagram(doc_content)
    diagram_path = save_file(mechanics_diagram, "mechanics_diagrams.md", doc_dir)
    
    # Complete the task
    if progress and task_id is not None:
        progress.update(task_id, advance=1, description=f"Completed {os.path.basename(doc_dir)}")
    
    # Save to database
    save_document_analysis(
        doc_id, source_type, source_path, doc_content, summary, 
        key_highlights, contract_breakdown, function_breakdown, mechanics_diagram
    )
    
    console.print(f"[bold green]Document analysis complete![/bold green] Results saved to {doc_dir}")
    return {
        "doc_id": doc_id,
        "doc_dir": doc_dir,
        "summary": summary,
        "key_highlights": key_highlights,
        "contract_breakdown": contract_breakdown,
        "function_breakdown": function_breakdown,
        "mechanics_diagram": mechanics_diagram
    }

def process_document(source_path, output_dir, source_type=None):
    """Synchronous wrapper for document processing"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        asyncio.run(process_document_async(source_path, output_dir, source_type, progress))

# -------------------------------
# Interactive Menu Functions
# -------------------------------
def view_file(filename, output_dir):
    """View the contents of a file"""
    filepath = os.path.join(output_dir, filename)
    if os.path.exists(filepath):
        content = read_file(filepath)
        if content:
            console.print(f"\n[bold magenta]Contents of {filename}:[/bold magenta]")
            console.print(content)
        else:
            console.print(f"[bold red]Failed to read file: {filepath}[/bold red]")
    else:
        console.print(f"[bold red]File not found: {filepath}[/bold red]")

def query_report(report_content, base_name, output_dir):
    """Allow the user to ask questions about a report"""
    if not report_content:
        console.print("[bold red]No report content available to query.[/bold red]")
        return
    
    console.print(f"[cyan]You can ask questions about the {base_name}.[/cyan]")
    console.print("[cyan]Type 'exit' or 'back' to return to the previous menu.[/cyan]")
    
    while True:
        question = Prompt.ask("Your question")
        if question.lower() in ['exit', 'back', 'quit']:
            break
        
        # Prepare the prompt with the report content and the question
        prompt = f"""
# Question Answering Task

## Context Information:
{report_content[:50000]}  # Limiting to first 50k chars if very large

## Question:
{question}

## Instructions:
1. Answer the question based ONLY on the information in the context provided above.
2. If the question cannot be answered from the context, say so clearly.
3. Be concise but complete in your answer.
4. Format your response using markdown where appropriate.
"""
        
        console.print("[cyan]Generating answer...[/cyan]")
        answer = call_llm(prompt, QUERY_MODEL)
        
        # Save the Q&A to a file
        qa_file = os.path.join(output_dir, f"{base_name}_queries.md")
        with open(qa_file, 'a', encoding='utf-8') as f:
            f.write(f"\n\n## Question: {question}\n\n{answer}\n\n---\n")
        
        console.print("\n[bold]Answer:[/bold]")
        console.print(answer)
        console.print(f"\n[dim]This Q&A has been saved to {qa_file}[/dim]")

def document_menu(doc_dir):
    """Show menu for document analysis results"""
    doc_base = os.path.basename(doc_dir)
    
    summary_path = os.path.join(doc_dir, "summary.md")
    highlights_path = os.path.join(doc_dir, "key_highlights.md")
    contract_path = os.path.join(doc_dir, "contract_breakdown.md")
    function_path = os.path.join(doc_dir, "function_breakdown.md")
    diagram_path = os.path.join(doc_dir, "mechanics_diagrams.md")
    
    while True:
        console.print(f"\n[bold]Document Analysis Menu: {doc_base}[/bold]")
        console.print("1. View Summary")
        console.print("2. View Key Highlights")
        console.print("3. View Contract Breakdown")
        console.print("4. View Function Breakdown")
        console.print("5. View Mechanics Diagrams")
        console.print("6. Query Analysis")
        console.print("7. Back to Previous Menu")
        choice = Prompt.ask("Enter your choice")
        
        if choice == "1":
            view_file("summary.md", doc_dir)
        elif choice == "2":
            view_file("key_highlights.md", doc_dir)
        elif choice == "3":
            view_file("contract_breakdown.md", doc_dir)
        elif choice == "4":
            view_file("function_breakdown.md", doc_dir)
        elif choice == "5":
            view_file("mechanics_diagrams.md", doc_dir)
        elif choice == "6":
            # Combine all reports for querying
            all_content = ""
            for path in [summary_path, highlights_path, contract_path, function_path, diagram_path]:
                if os.path.exists(path):
                    content = read_file(path)
                    if content:
                        all_content += "\n\n" + content
            query_report(all_content, doc_base, doc_dir)
        elif choice == "7":
            break
        else:
            console.print("Invalid choice. Try again.")

def documents_menu_in_session(session_folder):
    """Browse documents analyzed in the current session"""
    # Find all document directories in the session
    doc_dirs = [os.path.join(session_folder, d) for d in os.listdir(session_folder) 
               if os.path.isdir(os.path.join(session_folder, d)) and not d.endswith(".sol")]
    
    if not doc_dirs:
        console.print("[bold yellow]No document analyses found in this session.[/bold yellow]")
        return
    
    while True:
        console.print("\n[bold]Documents in this session:[/bold]")
        for i, doc_dir in enumerate(doc_dirs, 1):
            doc_base = os.path.basename(doc_dir)
            console.print(f"{i}. {doc_base}")
        console.print(f"{len(doc_dirs) + 1}. Back to Previous Menu")
        
        try:
            choice = int(Prompt.ask("Enter your choice"))
            if choice == len(doc_dirs) + 1:
                break
            elif 1 <= choice <= len(doc_dirs):
                document_menu(doc_dirs[choice - 1])
            else:
                console.print("Invalid choice. Try again.")
        except ValueError:
            console.print("Please enter a number.")

def browse_documents():
    """Browse all document analyses in the database"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""SELECT id, source_type, source_path, analysed_at FROM documents ORDER BY analysed_at DESC""")
    documents = cur.fetchall()
    conn.close()
    
    if not documents:
        console.print("[bold yellow]No document analyses found in the database.[/bold yellow]")
        return
    
    while True:
        console.print("\n[bold]Document Analysis Sessions:[/bold]")
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#")
        table.add_column("Document Type")
        table.add_column("Source")
        table.add_column("Analyzed At")
        
        for i, doc in enumerate(documents, 1):
            doc_id, source_type, source_path, analysed_at = doc
            table.add_row(str(i), source_type, source_path, analysed_at)
        
        console.print(table)
        console.print(f"{len(documents) + 1}. Back to Main Menu")
        
        try:
            choice = int(Prompt.ask("Enter your choice"))
            if choice == len(documents) + 1:
                break
            elif 1 <= choice <= len(documents):
                doc_id = documents[choice - 1][0]
                display_document_from_db(doc_id)
            else:
                console.print("Invalid choice. Try again.")
        except ValueError:
            console.print("Please enter a number.")

def display_document_from_db(doc_id):
    """Display document analysis from the database"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT source_type, source_path, summary, key_highlights, contract_breakdown, function_breakdown, mechanics_diagram
        FROM documents WHERE id = ?
    """, (doc_id,))
    result = cur.fetchone()
    conn.close()
    
    if not result:
        console.print(f"[bold red]Document with ID {doc_id} not found![/bold red]")
        return
    
    source_type, source_path, summary, key_highlights, contract_breakdown, function_breakdown, mechanics_diagram = result
    
    # Create a temporary directory for the session
    temp_dir = tempfile.mkdtemp(prefix="document_analysis_")
    doc_base = os.path.basename(source_path) if source_type != 'url' else urlparse(source_path).netloc
    doc_dir = os.path.join(temp_dir, f"{doc_base}_{doc_id[:8]}")
    os.makedirs(doc_dir, exist_ok=True)
    
    # Save all components to the temporary directory
    save_file(summary, "summary.md", doc_dir)
    save_file(key_highlights, "key_highlights.md", doc_dir)
    save_file(contract_breakdown, "contract_breakdown.md", doc_dir)
    save_file(function_breakdown, "function_breakdown.md", doc_dir)
    save_file(mechanics_diagram, "mechanics_diagrams.md", doc_dir)
    
    # Show document menu
    document_menu(doc_dir)
    
    # Clean up the temporary directory
    try:
        import shutil
        shutil.rmtree(temp_dir)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not clean up temporary directory: {e}[/yellow]")

def contract_menu_in_session(session_folder):
    """Browse contracts analyzed in the current session"""
    # Find all contract directories in the session
    contract_dirs = [os.path.join(session_folder, d) for d in os.listdir(session_folder) 
                  if os.path.isdir(os.path.join(session_folder, d)) and d.endswith(".sol")]
    
    if not contract_dirs:
        console.print("[bold yellow]No contract analyses found in this session.[/bold yellow]")
        return
    
    while True:
        console.print("\n[bold]Contracts in this session:[/bold]")
        for i, contract_dir in enumerate(contract_dirs, 1):
            contract_base = os.path.basename(contract_dir)
            console.print(f"{i}. {contract_base}")
        console.print(f"{len(contract_dirs) + 1}. Back to Previous Menu")
        
        try:
            choice = int(Prompt.ask("Enter your choice"))
            if choice == len(contract_dirs) + 1:
                break
            elif 1 <= choice <= len(contract_dirs):
                show_contract_menu(os.path.basename(contract_dirs[choice - 1]), session_folder)
            else:
                console.print("Invalid choice. Try again.")
        except ValueError:
            console.print("Please enter a number.")

def session_menu(session_folder):
    """Menu for a specific analysis session"""
    while True:
        console.print(f"\n[bold]Session Menu: {os.path.basename(session_folder)}[/bold]")
        console.print("1. Browse Contracts")
        console.print("2. Browse Documents")
        console.print("3. Add Smart Contracts to this Session")
        console.print("4. Add Document Analysis to this Session")
        console.print("5. Ask Questions About the Analysis")
        console.print("6. Scan Contracts for Vulnerabilities")
        console.print("7. Manage Vulnerability Library")
        console.print("8. Back to Previous Menu")
        choice = Prompt.ask("Enter your choice")
        
        if choice == "1":
            contract_menu_in_session(session_folder)
        elif choice == "2":
            documents_menu_in_session(session_folder)
        elif choice == "3":
            # Add more contracts to this session
            directory = Prompt.ask("Enter the path to the smart contracts directory")
            if not os.path.isdir(directory):
                console.print("The provided directory does not exist.")
                continue
            
            # Process all .sol files in the directory
            contract_files = [os.path.join(directory, f) for f in os.listdir(directory)
                            if os.path.isfile(os.path.join(directory, f)) and f.endswith(".sol")]
            if not contract_files:
                console.print("No smart contract files (.sol) found in the directory.")
                continue
                
            # Process contracts in parallel
            asyncio.run(process_contracts_parallel(contract_files, session_folder))
            console.print("Analysis complete for all contracts in the directory.")
        elif choice == "4":
            # Add document analysis to this session
            analyze_document(session_folder)
        elif choice == "5":
            # Ask questions about the analysis
            ask_questions_about_analysis(session_folder)
        elif choice == "6":
            # Scan contracts for vulnerabilities
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM vuln_detection_library")
            library_count = cur.fetchone()[0]
            conn.close()
            
            if library_count == 0:
                console.print("[yellow]Vulnerability detection library is empty. Building library first...[/yellow]")
                if build_vulnerability_detection_library():
                    scan_contracts_for_vulnerabilities(session_folder)
            else:
                scan_contracts_for_vulnerabilities(session_folder)
        elif choice == "7":
            # Manage vulnerability library
            manage_vulnerability_library()
        elif choice == "8":
            break
        else:
            console.print("Invalid choice. Try again.")

def browse_sessions():
    """Browse all analysis sessions"""
    # Get a list of all analysis folders
    session_dirs = [d for d in os.listdir() if os.path.isdir(d) and d.startswith("analysis_")]
    session_dirs.sort(reverse=True)  # Latest first
    
    if not session_dirs:
        console.print("[bold yellow]No analysis sessions found.[/bold yellow]")
        return
    
    while True:
        console.print("\n[bold]Analysis Sessions:[/bold]")
        for i, session in enumerate(session_dirs, 1):
            # Try to parse the timestamp
            try:
                timestamp = datetime.strptime(session[9:], "%Y%m%d_%H%M%S")
                formatted_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
                console.print(f"{i}. {session} ({formatted_time})")
            except ValueError:
                console.print(f"{i}. {session}")
                
        console.print(f"{len(session_dirs) + 1}. Back to Main Menu")
        
        try:
            choice = int(Prompt.ask("Enter your choice"))
            if choice == len(session_dirs) + 1:
                break
            elif 1 <= choice <= len(session_dirs):
                session_menu(session_dirs[choice - 1])
            else:
                console.print("Invalid choice. Try again.")
        except ValueError:
            console.print("Please enter a number.")


# -------------------------------
# Analysis Q&A Function
def ask_questions_about_analysis(session_folder):
    """Allow users to ask questions about the analysis with context from all analyses and diagrams"""
    # Check if the session has any content to query
    contracts = get_contracts_in_session(session_folder)
    documents = get_documents_in_session(session_folder)
    
    if not contracts and not documents:
        console.print("[bold red]No analysis data found in this session. Please add contracts or documents first.[/bold red]")
        return
    
    # Gather all analysis data into context
    analysis_context = gather_analysis_context(session_folder, contracts, documents)
    
    if not analysis_context:
        console.print("[bold red]Could not find sufficient analysis data to answer questions.[/bold red]")
        return
    
    console.print("\n[bold green]Ask questions about the analysis:[/bold green]")
    console.print("You can ask about contracts, documents, relationships, vulnerabilities, and more.")
    console.print("Type 'exit' to return to the session menu.")
    
    # Create a unique ID for this Q&A session
    session_id = f"qa_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.path.basename(session_folder)}"
    qa_pairs = []
    
    # Interactive Q&A loop
    while True:
        question = Prompt.ask("\n[bold]Your question[/bold]")
        
        if question.lower() in ["exit", "quit", "back"]:
            break
        
        # Build prompt with context and question
        prompt = f"""
# Protocol Analysis Question Answering Task

## Analysis Context:
{analysis_context}

## User Question:
{question}

## Instructions:
1. Answer the question based on the analysis context provided
2. If the answer cannot be found in the context, clearly state that
3. Use specific information from the analysis to support your answer
4. Format the answer in a clear, concise manner
5. If the question asks for code examples, provide them

Please provide a helpful, accurate response based solely on the information in the context:
"""
        
        console.print("\n[cyan]Thinking...[/cyan]")
        response = call_llm(prompt, QUERY_MODEL)
        
        # Format and display the response
        console.print("\n[bold green]Answer:[/bold green]")
        console.print(Panel(Markdown(response), title="Analysis Response", width=100))
        
        # Store Q&A pair
        qa_pairs.append({
            "question": question,
            "answer": response,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    
    # Only save if we have some Q&A pairs
    if qa_pairs:
        # Save Q&A session to database
        save_qa_session(session_id, session_folder, qa_pairs)
        
        # Ask user if they want to export the Q&A session
        export_choice = Prompt.ask("\nDo you want to export the Q&A session?", choices=["yes", "no"], default="yes")
        
        if export_choice.lower() == "yes":
            export_format = Prompt.ask("Select export format", choices=["md", "pdf"], default="md")
            export_qa_session(session_id, session_folder, qa_pairs, export_format)

def gather_analysis_context(session_folder, contracts, documents):
    """Gather relevant analysis data to provide context for queries"""
    context_data = []
    
    # Add session information
    context_data.append(f"Analysis Session: {os.path.basename(session_folder)}")
    
    # Add contract analysis information
    if contracts:
        context_data.append("\n## SMART CONTRACT ANALYSES")
        for contract_dir in contracts:
            contract_name = os.path.basename(contract_dir)
            context_data.append(f"\n### Contract: {contract_name}")
            
            # Add relevant analysis files
            analysis_files = [
                ("summary.md", "Summary"),
                ("vulnerabilities.md", "Vulnerabilities"),
                ("function_breakdown.md", "Function Breakdown"),
                ("call_diagram.md", "Call Diagram"),
                ("journey_diagram.md", "Journey Diagram")
            ]
            
            for filename, section_name in analysis_files:
                file_path = os.path.join(contract_dir, filename)
                if os.path.exists(file_path):
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            context_data.append(f"\n#### {section_name}:\n{content[:5000]}")
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not read {filename} for {contract_name}: {str(e)}[/yellow]")
    
    # Add document analysis information
    if documents:
        context_data.append("\n## DOCUMENTATION ANALYSES")
        for doc_dir in documents:
            doc_name = os.path.basename(doc_dir)
            context_data.append(f"\n### Document: {doc_name}")
            
            # Add relevant analysis files
            analysis_files = [
                ("summary.md", "Summary"),
                ("function_breakdown.md", "Function Breakdown"),
                ("mechanics_diagram.md", "Mechanics Diagram")
            ]
            
            for filename, section_name in analysis_files:
                file_path = os.path.join(doc_dir, filename)
                if os.path.exists(file_path):
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            context_data.append(f"\n#### {section_name}:\n{content[:5000]}")
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not read {filename} for {doc_name}: {str(e)}[/yellow]")
    
    # Combine all context data
    combined_context = "\n".join(context_data)
    
    # Limit context size to avoid token limits
    max_context_size = 50000  # Adjust based on model's limitations
    if len(combined_context) > max_context_size:
        console.print(f"[yellow]Warning: Analysis context is very large. Truncating to {max_context_size} characters.[/yellow]")
        combined_context = combined_context[:max_context_size] + "\n...(content truncated due to size)..."
    
    return combined_context

# -------------------------------
# Vulnerability Detection Functions
def import_vulnerability_reports(vectorisation_db_path="vectorisation.db"):
    """Import vulnerability reports from the vectorisation database"""
    try:
        # Check if the vectorisation database exists
        if not os.path.exists(vectorisation_db_path):
            console.print(f"[bold red]Vectorisation database not found at {vectorisation_db_path}[/bold red]")
            return None
        
        # Connect to the vectorisation database
        vector_conn = sqlite3.connect(vectorisation_db_path)
        vector_cur = vector_conn.cursor()
        
        # Get all reports and their structured analyses
        vector_cur.execute("""
            SELECT r.id, r.source, r.analysis_summary, p.pattern 
            FROM reports r 
            LEFT JOIN patterns p ON r.id = p.report_id
        """)
        
        reports = []
        for row in vector_cur.fetchall():
            report_id, source, analysis_summary, pattern = row
            try:
                # Parse the analysis summary JSON
                analysis = json.loads(analysis_summary) if analysis_summary else {}
                
                # Create a report object
                report = {
                    "id": report_id,
                    "source": source,
                    "vuln_type": analysis.get("vuln_type", "Unknown"),
                    "questions": analysis.get("questions", []),
                    "vulnerable_code": analysis.get("vulnerable_code", ""),
                    "fixed_code": analysis.get("fixed_code", ""),
                    "researcher_insights": analysis.get("researcher_insights", ""),
                    "pattern": pattern or ""
                }
                reports.append(report)
            except json.JSONDecodeError:
                console.print(f"[yellow]Warning: Could not parse analysis summary for report {report_id}[/yellow]")
        
        vector_conn.close()
        return reports
    
    except Exception as e:
        console.print(f"[bold red]Error importing vulnerability reports: {str(e)}[/bold red]")
        return None

def build_vulnerability_detection_library():
    """Build a vulnerability detection library from imported reports using enhanced LLM analysis"""
    # Import vulnerability reports
    reports = import_vulnerability_reports()
    if not reports:
        console.print("[bold red]No vulnerability reports found to build detection library.[/bold red]")
        return False
    
    console.print(f"[green]Imported {len(reports)} vulnerability reports.[/green]")
    
    # Perform deep LLM analysis on all reports first to better categorize them
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True
    ) as progress:
        task = progress.add_task("[cyan]Analyzing vulnerability reports with LLM...", total=len(reports))
        
        # Enhanced reports with LLM analysis
        enhanced_reports = []
        
        for report in reports:
            # Check if the report has necessary components
            if not report.get("vulnerable_code") and not report.get("researcher_insights"):
                console.print(f"[yellow]Skipping report from {report['source']} - insufficient data for analysis[/yellow]")
                enhanced_reports.append(report)
                progress.update(task, advance=1)
                continue

            # Prepare a shortened analysis prompt for the LLM
            # Use a summary of the vulnerable code to avoid token limits
            vuln_code = report.get("vulnerable_code", "")
            vuln_code = vuln_code[:2000] if len(vuln_code) > 2000 else vuln_code
            
            fixed_code = report.get("fixed_code", "")
            fixed_code = fixed_code[:2000] if len(fixed_code) > 2000 else fixed_code
            
            insights = report.get("researcher_insights", "")
            insights = insights[:1000] if len(insights) > 1000 else insights
            
            # Format questions more concisely
            questions_str = "No questions available"
            if report.get("questions"):
                questions = report["questions"][:3]  # Limit to 3 questions
                questions_str = "\n".join([f"- {q}" for q in questions])

            analysis_prompt = f"""
# Smart Contract Vulnerability Analysis

## Vulnerability Report Data:
- Source: {report["source"]}
- Type: {report["vuln_type"]}

## Researcher Questions:
{questions_str}

## Vulnerable Code Sample:
```solidity
{vuln_code}
```

## Fixed Code Sample:
```solidity
{fixed_code}
```

## Researcher Insights:
{insights}

## Analysis Task:
Provide a deep analysis of this vulnerability with the following structured output:
1. Enhanced vulnerability categorization (more precise than the original type if possible)
2. Detection patterns (regexp, code patterns, or indicators that would help identify this vulnerability)
3. Key risk factors and impact assessment
4. Common variations of this vulnerability that might appear differently

Return as a valid JSON object with these keys: "category", "detection_patterns", "risk_factors", "variations"
"""
            
            # Get enhanced analysis from LLM
            enhanced_analysis = call_llm(analysis_prompt, ANALYSIS_MODEL)
            
            # If the LLM response starts with an error message, use original data
            if enhanced_analysis.startswith("I apologize") or "API connectivity issues" in enhanced_analysis:
                console.print(f"[yellow]LLM service unavailable for {report['source']}. Using original data.[/yellow]")
                enhanced_reports.append(report)
                progress.update(task, advance=1)
                continue
            
            # Try to extract JSON data, handling various formats the LLM might return
            try:
                # Try to find JSON in the response if it's not pure JSON
                if not enhanced_analysis.strip().startswith("{"):
                    # Look for JSON object in the response
                    json_match = re.search(r'\{.*\}', enhanced_analysis, re.DOTALL)
                    if json_match:
                        enhanced_analysis = json_match.group(0)
                
                analysis_data = json.loads(enhanced_analysis)
                
                # Validate expected fields
                required_fields = ["category", "detection_patterns", "risk_factors", "variations"]
                missing_fields = [field for field in required_fields if field not in analysis_data]
                
                if missing_fields:
                    console.print(f"[yellow]Warning: Missing fields {missing_fields} in LLM analysis for {report['source']}.[/yellow]")
                    # Fill in missing fields with empty values
                    for field in missing_fields:
                        analysis_data[field] = [] if field in ["detection_patterns", "risk_factors", "variations"] else "Unknown"
                
                # Use original data plus enhanced analysis
                enhanced_report = report.copy()
                enhanced_report["enhanced_analysis"] = analysis_data
                
                # If the LLM suggested a better category, use it
                if analysis_data.get("category") and analysis_data["category"] != "Unknown":
                    enhanced_report["vuln_type"] = analysis_data["category"]
                    
                enhanced_reports.append(enhanced_report)
                
            except (json.JSONDecodeError, AttributeError) as e:
                console.print(f"[yellow]Warning: Could not parse LLM analysis for {report['source']}: {str(e)}. Using original data.[/yellow]")
                enhanced_reports.append(report)
            
            progress.update(task, advance=1)
    
    # Group reports by vulnerability type
    vuln_groups = {}
    for report in enhanced_reports:
        vuln_type = report["vuln_type"]
        if vuln_type not in vuln_groups:
            vuln_groups[vuln_type] = []
        vuln_groups[vuln_type].append(report)
    
    # For each vulnerability type, aggregate details and generate a detection template
    library_entries = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True
    ) as progress:
        task = progress.add_task("[cyan]Building vulnerability detection library...", total=len(vuln_groups))
        
        for vuln_type, reports in vuln_groups.items():
            # Aggregate details
            questions = []
            vulnerable_examples = []
            fixed_examples = []
            insights = []
            patterns = []
            
            # Enhanced data
            detection_patterns = []
            risk_factors = []
            variations = []
            
            for report in reports:
                if report["questions"]:
                    questions.extend(report["questions"])
                if report["vulnerable_code"]:
                    vulnerable_examples.append(report["vulnerable_code"])
                if report["fixed_code"]:
                    fixed_examples.append(report["fixed_code"])
                if report["researcher_insights"]:
                    insights.append(report["researcher_insights"])
                if report["pattern"]:
                    patterns.append(report["pattern"])
                
                # Add enhanced data if available
                if "enhanced_analysis" in report:
                    enhanced = report["enhanced_analysis"]
                    if enhanced.get("detection_patterns"):
                        if isinstance(enhanced["detection_patterns"], list):
                            detection_patterns.extend(enhanced["detection_patterns"])
                        else:
                            detection_patterns.append(enhanced["detection_patterns"])
                    if enhanced.get("risk_factors"):
                        if isinstance(enhanced["risk_factors"], list):
                            risk_factors.extend(enhanced["risk_factors"])
                        else:
                            risk_factors.append(enhanced["risk_factors"])
                    if enhanced.get("variations"):
                        if isinstance(enhanced["variations"], list):
                            variations.extend(enhanced["variations"])
                        else:
                            variations.append(enhanced["variations"])
            
            # Remove duplicates
            questions = list(set(questions))
            
            # Aggregate details with enhanced data
            details = {
                "questions": questions[:3],  # Limit to top 3 questions
                "vulnerable_examples": vulnerable_examples,
                "fixed_examples": fixed_examples,
                "insights": insights,
                "patterns": patterns,
                "detection_patterns": detection_patterns,
                "risk_factors": risk_factors,
                "variations": variations
            }
            
            # Generate detection template using LLM with enhanced data
            template = generate_enhanced_detection_template(vuln_type, details)
            
            # Add to library entries
            entry_id = hashlib.md5(vuln_type.encode()).hexdigest()
            library_entries.append({
                "id": entry_id,
                "vuln_type": vuln_type,
                "details": json.dumps(details),
                "template": template,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            
            progress.update(task, advance=1)
    
    # Save to the database
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    for entry in library_entries:
        cur.execute("""
            INSERT OR REPLACE INTO vuln_detection_library (id, vuln_type, details, template, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (entry["id"], entry["vuln_type"], entry["details"], entry["template"], entry["created_at"]))
    
    conn.commit()
    conn.close()
    
    console.print(f"[green]Built enhanced vulnerability detection library with {len(library_entries)} vulnerability types.[/green]")
    return True

def generate_enhanced_detection_template(vuln_type, details):
    """Generate a sophisticated detection template using enhanced LLM analysis"""
    # Format examples for the prompt
    vulnerable_examples = "\n\n".join([f"```solidity\n{ex}\n```" for ex in details["vulnerable_examples"][:3]])
    fixed_examples = "\n\n".join([f"```solidity\n{ex}\n```" for ex in details["fixed_examples"][:3]])
    
    # Format enhanced data
    detection_patterns = json.dumps(details.get("detection_patterns", [])[:5], indent=2)
    risk_factors = json.dumps(details.get("risk_factors", [])[:3], indent=2)
    variations = json.dumps(details.get("variations", [])[:3], indent=2)
    
    # Build the prompt with enhanced data
    prompt = f"""
# Advanced Vulnerability Detection Template Generation

## Vulnerability Type:
{vuln_type}

## Key Questions:
{json.dumps(details["questions"], indent=2)}

## LLM-identified Detection Patterns:
{detection_patterns}

## Risk Factors:
{risk_factors}

## Vulnerability Variations:
{variations}

## Vulnerable Code Examples:
{vulnerable_examples}

## Fixed Code Examples:
{fixed_examples}

## Task:
Create a comprehensive and precise detection template for identifying this vulnerability in Solidity smart contracts.
Your template should include:
1. Key patterns, signatures, and code structures to look for
2. Regex patterns for identifying vulnerable constructs where appropriate
3. Contextual factors that must be present for this vulnerability to exist
4. Common variations of this vulnerability

Focus on creating a detection template that minimizes false positives and can capture variations of this vulnerability.
Provide ONLY the detection template with NO explanation or other text.
"""
    
    # Call LLM to generate enhanced template
    template = call_llm(prompt, ANALYSIS_MODEL)
    return template

def view_vulnerability_library():
    """Display all vulnerability types in the detection library"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, vuln_type, created_at FROM vuln_detection_library ORDER BY vuln_type")
    entries = cur.fetchall()
    conn.close()
    
    if not entries:
        console.print("[yellow]No vulnerability detection templates found in the library.[/yellow]")
        return []
    
    # Display as a table
    table = Table(title="Vulnerability Detection Library")
    table.add_column("ID", style="cyan")
    table.add_column("Vulnerability Type", style="green")
    table.add_column("Created At", style="blue")
    
    for entry_id, vuln_type, created_at in entries:
        table.add_row(entry_id[:8], vuln_type, created_at)
    
    console.print(table)
    return entries

def delete_vulnerability_library_entry(entry_id):
    """Delete a specific entry from the vulnerability detection library"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Get the vuln type first for reporting
    cur.execute("SELECT vuln_type FROM vuln_detection_library WHERE id = ?", (entry_id,))
    result = cur.fetchone()
    
    if not result:
        console.print(f"[yellow]No entry found with ID {entry_id}[/yellow]")
        conn.close()
        return False
    
    vuln_type = result[0]
    
    # Delete the entry
    cur.execute("DELETE FROM vuln_detection_library WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    
    console.print(f"[green]Deleted vulnerability detection template for: {vuln_type}[/green]")
    return True

def delete_all_vulnerability_library():
    """Delete all entries from the vulnerability detection library"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM vuln_detection_library")
    conn.commit()
    conn.close()
    
    console.print("[green]Deleted all vulnerability detection templates from the library.[/green]")
    return True

def manage_vulnerability_library():
    """Menu to view, delete, or rebuild the vulnerability detection library"""
    while True:
        console.print("\n[bold]Vulnerability Detection Library Management[/bold]")
        console.print("1. View Library Entries")
        console.print("2. Delete Specific Entry")
        console.print("3. Delete All Entries")
        console.print("4. Build/Rebuild Library")
        console.print("5. Back to Previous Menu")
        
        choice = Prompt.ask("Enter your choice", default="5")
        
        if choice == "1":
            view_vulnerability_library()
        elif choice == "2":
            # Show entries first so user can choose
            entries = view_vulnerability_library()
            if entries:
                entry_id = Prompt.ask("Enter the ID of the entry to delete")
                delete_vulnerability_library_entry(entry_id)
        elif choice == "3":
            confirm = Prompt.ask("Are you sure you want to delete ALL entries? (yes/no)", choices=["yes", "no"], default="no")
            if confirm.lower() == "yes":
                delete_all_vulnerability_library()
        elif choice == "4":
            build_vulnerability_detection_library()
        elif choice == "5":
            break
        else:
            console.print("Invalid choice. Try again.")

def generate_detection_template(vuln_type, details):
    """Generate a robust detection template for a vulnerability type using LLM"""
    # Format examples for the prompt
    vulnerable_examples = "\n\n".join([f"```solidity\n{ex}\n```" for ex in details["vulnerable_examples"][:3]])
    fixed_examples = "\n\n".join([f"```solidity\n{ex}\n```" for ex in details["fixed_examples"][:3]])
    patterns = "\n".join(details["patterns"][:3]) if details["patterns"] else "No patterns available"
    
    # Build the prompt
    prompt = f"""
# Vulnerability Detection Template Generation

## Vulnerability Type:
{vuln_type}

## Key Questions:
{json.dumps(details["questions"], indent=2)}

## Vulnerable Code Examples:
{vulnerable_examples}

## Fixed Code Examples:
{fixed_examples}

## Existing Patterns:
{patterns}

## Task:
Create a robust detection template for identifying this vulnerability in Solidity smart contracts.
The template should include:
1. Key patterns and signatures to look for in the code
2. Common vulnerable constructs or functions
3. Conditions that must be present for the vulnerability
4. A regex pattern if applicable

Provide ONLY the detection template with NO explanation or other text.
"""
    
    # Call LLM to generate template
    template = call_llm(prompt, ANALYSIS_MODEL)
    return template

def load_contract_content(contract_path):
    """Load contract content from a contract directory in a session"""
    contract_id = os.path.basename(contract_path)
    content_file = os.path.join(contract_path, "content.txt")
    
    if os.path.exists(content_file):
        try:
            with open(content_file, 'r', encoding='utf-8') as f:
                content = f.read()
            return {
                "id": contract_id,
                "filename": contract_id,
                "path": contract_path,
                "content": content
            }
        except Exception as e:
            console.print(f"[yellow]Warning: Could not read content for {contract_id}: {str(e)}[/yellow]")
    else:
        # Try to find a .sol file in the contract directory
        sol_files = [f for f in os.listdir(contract_path) if f.endswith('.sol')]
        if sol_files:
            try:
                with open(os.path.join(contract_path, sol_files[0]), 'r', encoding='utf-8') as f:
                    content = f.read()
                return {
                    "id": contract_id,
                    "filename": contract_id,
                    "path": contract_path,
                    "content": content
                }
            except Exception as e:
                console.print(f"[yellow]Warning: Could not read .sol file for {contract_id}: {str(e)}[/yellow]")
    
    # If we couldn't load content, return minimum structure
    return {
        "id": contract_id,
        "filename": contract_id,
        "path": contract_path,
        "content": ""
    }

def find_existing_partial_reports(session_folder):
    """Find existing partial vulnerability reports in the session folder"""
    reports = []
    for filename in os.listdir(session_folder):
        if filename.startswith("vulnerability_report_partial_") and filename.endswith(".md"):
            report_path = os.path.join(session_folder, filename)
            # Get the timestamp from the filename
            timestamp_str = filename.replace("vulnerability_report_partial_", "").replace(".md", "")
            try:
                timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S") if timestamp_str else None
                reports.append({
                    "path": report_path,
                    "timestamp": timestamp,
                    "filename": filename
                })
            except ValueError:
                # Skip files with invalid timestamp format
                pass
    
    # Sort by timestamp, newest first
    return sorted(reports, key=lambda x: x["timestamp"], reverse=True) if reports else []

def extract_analyzed_contracts_from_report(report_path):
    """Extract contract filenames that have already been analyzed from a partial report"""
    analyzed_contracts = []
    
    try:
        with open(report_path, "r") as f:
            content = f.read()
            
        # Extract contract names using regex
        import re
        contract_sections = re.findall(r"## Contract: ([^\n]+)", content)
        analyzed_contracts = contract_sections
        
        # Also check for interrupted status
        interrupted_match = re.search(r"Processed (\d+) of (\d+) contracts", content)
        analyzed_count = int(interrupted_match.group(1)) if interrupted_match else len(contract_sections)
        total_count = int(interrupted_match.group(2)) if interrupted_match else None
        
        return {
            "analyzed_contracts": analyzed_contracts,
            "analyzed_count": analyzed_count,
            "total_count": total_count
        }
    except Exception as e:
        console.print(f"[yellow]Error extracting contract info from report: {str(e)}[/yellow]")
        return {"analyzed_contracts": [], "analyzed_count": 0, "total_count": None}

def extract_vulnerabilities_from_report(report_path):
    """Extract vulnerability findings from a partial report"""
    findings = []
    
    try:
        with open(report_path, "r") as f:
            content = f.read()
        
        # Process the report section by section
        import re
        contract_sections = re.split(r"## Contract: ([^\n]+)", content)
        
        # Skip the first section (header)
        current_contract = None
        
        for i, section in enumerate(contract_sections):
            if i == 0:
                continue  # Skip header
                
            if i % 2 == 1:  # This is a contract name
                current_contract = section.strip()
            else:  # This is a vulnerability section
                if not current_contract:
                    continue
                    
                # Extract vulnerabilities for this contract
                vuln_sections = re.split(r"### ([^\(\n]+) \(([^\)]+)\)", section)
                
                for j in range(1, len(vuln_sections), 3):
                    if j+2 >= len(vuln_sections):
                        break
                        
                    vuln_type = vuln_sections[j].strip()
                    severity = vuln_sections[j+1].strip()
                    details = vuln_sections[j+2].strip()
                    
                    # Extract line numbers
                    line_match = re.search(r"\*\*Lines:\*\* ([^\n]+)", details)
                    line_numbers = line_match.group(1).strip() if line_match else "Not specified"
                    
                    # Extract description
                    desc_match = re.search(r"\*\*Description:\*\* ([^\n]+(?:\n(?!\*\*)[^\n]*)*)", details)
                    description = desc_match.group(1).strip() if desc_match else ""
                    
                    # Extract fix suggestion
                    fix_match = re.search(r"\*\*Suggested Fix:\*\* ([^\n]+(?:\n(?!\*\*)[^\n]*)*)", details)
                    fix = fix_match.group(1).strip() if fix_match else ""
                    
                    findings.append({
                        "filename": current_contract,
                        "vuln_type": vuln_type,
                        "severity": severity,
                        "line_numbers": line_numbers,
                        "description": description,
                        "fix": fix,
                        "source": "previous_report"
                    })
        
        return findings
    except Exception as e:
        console.print(f"[yellow]Error extracting findings from report: {str(e)}[/yellow]")
        return []

def scan_contracts_for_vulnerabilities(session_folder):
    """Scan analyzed contracts against the vulnerability detection library"""
    # Check for existing partial reports
    existing_reports = find_existing_partial_reports(session_folder)
    
    # Generate a timestamp for new partial reports
    scan_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    partial_report_path = os.path.join(session_folder, f"vulnerability_report_partial_{scan_timestamp}.md")
    
    # If existing partial reports are found, ask if user wants to continue
    existing_findings = []
    analyzed_contracts = []
    if existing_reports:
        console.print(f"[cyan]Found {len(existing_reports)} existing partial vulnerability reports.[/cyan]")
        for i, report in enumerate(existing_reports[:3]):  # Show up to 3 most recent
            console.print(f"  {i+1}. {report['filename']} ({report['timestamp'].strftime('%Y-%m-%d %H:%M:%S')})")
        
        resume_options = [
            "Start a new scan from scratch",
            "Continue the most recent scan",
            "Review and enhance the most recent findings"
        ]
        
        if len(existing_reports) > 1:
            resume_options.append("Select a specific partial report to continue")
        
        # Display options clearly to the user
        console.print("\n[bold]Available options:[/bold]")
        for i, option in enumerate(resume_options):
            console.print(f"  [bold]{i+1}.[/bold] {option}")
        
        resume_choice = Prompt.ask(
            "\nHow would you like to proceed?", 
            choices=[str(i+1) for i in range(len(resume_options))],
            default="1"
        )
        
        resume_choice = int(resume_choice)
        selected_report = None
        
        if resume_choice == 1:  # New scan
            pass  # Just continue with a new scan
        elif resume_choice == 2:  # Continue most recent
            selected_report = existing_reports[0]
            console.print(f"[cyan]Continuing from {selected_report['filename']}[/cyan]")
        elif resume_choice == 3:  # Enhance most recent
            selected_report = existing_reports[0]
            console.print(f"[cyan]Reviewing and enhancing findings from {selected_report['filename']}[/cyan]")
        elif resume_choice == 4 and len(existing_reports) > 1:  # Select specific
            report_choice = Prompt.ask(
                "Which report would you like to continue?",
                choices=[str(i+1) for i in range(len(existing_reports))],
                default="1"
            )
            selected_report = existing_reports[int(report_choice) - 1]
            console.print(f"[cyan]Selected {selected_report['filename']}[/cyan]")
        
        if selected_report:
            # Extract analyzed contracts from the report
            contract_info = extract_analyzed_contracts_from_report(selected_report['path'])
            analyzed_contracts = contract_info['analyzed_contracts']
            
            # Extract existing findings if enhancing
            if resume_choice == 3:  # Enhance mode
                existing_findings = extract_vulnerabilities_from_report(selected_report['path'])
                console.print(f"[cyan]Extracted {len(existing_findings)} existing findings for enhancement[/cyan]")
    
    # Get contracts in the current session
    contract_paths = get_contracts_in_session(session_folder)
    if not contract_paths:
        console.print("[bold red]No contracts found in this session.[/bold red]")
        return
    
    # Load contract content
    contracts = []
    contracts_to_skip = []
    
    # If continuing from a partial report, identify contracts to skip or prioritize
    continue_mode = len(analyzed_contracts) > 0 and resume_choice == 2
    enhance_mode = len(existing_findings) > 0 and resume_choice == 3
    
    # Track which contracts have already been analyzed
    analyzed_filenames = set()
    if continue_mode:
        for contract_name in analyzed_contracts:
            analyzed_filenames.add(os.path.basename(contract_name))
    
    for path in contract_paths:
        contract = load_contract_content(path)
        contract_filename = os.path.basename(path)
        
        if contract["content"]:
            # If in continue mode, skip already analyzed contracts
            if continue_mode and contract_filename in analyzed_filenames:
                console.print(f"[dim]Skipping already analyzed: {contract_filename}[/dim]")
                contracts_to_skip.append(contract)
            else:
                contracts.append(contract)
        else:
            console.print(f"[yellow]Skipping {contract_filename}: No content available[/yellow]")
    
    if not contracts and not (enhance_mode and existing_findings):
        if continue_mode and contracts_to_skip:
            console.print("[yellow]All contracts have already been analyzed in the previous scan.[/yellow]")
            # Ask if the user wants to proceed with enhancement instead
            enhance_choice = Prompt.ask(
                "Would you like to enhance the existing findings instead?", 
                choices=["yes", "no"], 
                default="yes"
            )
            if enhance_choice.lower() == "yes":
                enhance_mode = True
                resume_choice = 3
                existing_findings = extract_vulnerabilities_from_report(selected_report['path'])
                console.print(f"[cyan]Extracted {len(existing_findings)} existing findings for enhancement[/cyan]")
            else:
                return
        elif not enhance_mode:
            console.print("[bold red]No contract content available for scanning.[/bold red]")
            return
    
    # Check if the vulnerability detection library exists
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM vuln_detection_library")
    count = cur.fetchone()[0]
    
    if count == 0:
        # Library is empty, ask to build it
        console.print("[yellow]Vulnerability detection library is empty.[/yellow]")
        build_choice = Prompt.ask("Build the library now?", choices=["yes", "no"], default="yes")
        
        if build_choice.lower() == "yes":
            success = build_vulnerability_detection_library()
            if not success:
                conn.close()
                return
        else:
            console.print("[yellow]Vulnerability scanning cancelled.[/yellow]")
            conn.close()
            return
    
    # Get vulnerability types and details for LLM context
    cur.execute("SELECT id, vuln_type, details FROM vuln_detection_library")
    vuln_library = cur.fetchall()
    conn.close()
    
    if not vuln_library:
        console.print("[bold red]No vulnerability detection templates available.[/bold red]")
        return
    
    # Initialize findings list and track progress
    all_findings = []
    
    # If in enhancement mode, start with existing findings
    if enhance_mode and existing_findings:
        all_findings = existing_findings.copy()
        console.print(f"[cyan]Starting with {len(all_findings)} existing findings to enhance[/cyan]")
    
    try:
        # Create header for partial report
        with open(partial_report_path, "w") as f:
            if enhance_mode:
                f.write(f"# Enhanced Vulnerability Scan (In Progress) - {scan_timestamp}\n\n")
                f.write(f"*This report enhances previous findings with additional analysis.*\n\n")
            else:
                f.write(f"# Vulnerability Scan (In Progress) - {scan_timestamp}\n\n")
                f.write(f"*This is a partial report generated during scanning. Process may have been interrupted.*\n\n")
        
        # Start analyzing contracts with LLM directly
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True
        ) as progress:
            # Create appropriate task description based on mode
            task_desc = "[cyan]Analyzing contracts for vulnerabilities...[/cyan]"
            if continue_mode:
                task_desc = "[cyan]Continuing vulnerability analysis of remaining contracts...[/cyan]"
            elif enhance_mode:
                task_desc = "[cyan]Enhancing vulnerability analysis of contracts...[/cyan]"
            
            # Set up progress tracking
            total_contracts = len(contracts)
            if enhance_mode and not contracts:
                # If only enhancing existing findings without new contracts
                total_contracts = len(existing_findings)
                
            task = progress.add_task(task_desc, total=total_contracts)
            
            # Process each contract
            for i, contract in enumerate(contracts):
                progress.update(task, advance=1)
                
                try:
                    # In enhance mode, add context from existing findings
                    if enhance_mode:
                        # Get existing findings for this contract
                        contract_existing_findings = [f for f in existing_findings if f["filename"] == contract["filename"]]
                        console.print(f"[dim]Found {len(contract_existing_findings)} existing findings for {contract['filename']}[/dim]")
                        
                        # Send contract directly to LLM with enhanced context
                        contract_findings = analyze_contract_with_llm_enhanced(
                            contract, 
                            vuln_library, 
                            contract_existing_findings
                        )
                    else:
                        # Regular analysis
                        contract_findings = analyze_contract_with_llm(contract, vuln_library)
                    
                    # Add to our cumulative findings
                    # If enhancing, replace existing findings for this contract
                    if enhance_mode:
                        # Remove existing findings for this contract
                        all_findings = [f for f in all_findings if f["filename"] != contract["filename"]]
                        # Add the enhanced findings
                        all_findings.extend(contract_findings)
                    else:
                        all_findings.extend(contract_findings)
                    
                    # Save partial report after each contract is analyzed
                    if contract_findings:
                        # Append to partial report
                        with open(partial_report_path, "a") as f:
                            f.write(f"## Contract: {contract['filename']}\n\n")
                            for finding in contract_findings:
                                f.write(f"### {finding.get('vuln_type', 'Unknown Vulnerability')} ({finding.get('severity', 'Medium')})\n\n")
                                
                                # Add a source indicator for enhanced findings
                                if enhance_mode and finding.get('enhanced', False):
                                    f.write(f"**Source:** Enhanced Analysis\n\n")
                                    
                                f.write(f"**Lines:** {finding.get('line_numbers', 'Not specified')}\n\n")
                                f.write(f"**Description:** {finding.get('description', 'No description')}\n\n")
                                if 'fix' in finding:
                                    f.write(f"**Suggested Fix:** {finding.get('fix')}\n\n")
                                f.write("---\n\n")
                except Exception as e:
                    console.print(f"[yellow]Error analyzing contract {contract['filename']}: {str(e)}[/yellow]")
                    # Document the error in partial report
                    with open(partial_report_path, "a") as f:
                        f.write(f"## Error analyzing contract: {contract['filename']}\n\n")
                        f.write(f"```\n{str(e)}\n```\n\n")
            
            # If in enhance mode with no new contracts, enhance existing findings as a group
            if enhance_mode and not contracts and existing_findings:
                progress.update(task, advance=1)
                console.print("[cyan]Enhancing existing findings as a group...[/cyan]")
                
                # Group findings by contract
                findings_by_contract = {}
                for finding in existing_findings:
                    contract_name = finding["filename"]
                    if contract_name not in findings_by_contract:
                        findings_by_contract[contract_name] = []
                    findings_by_contract[contract_name].append(finding)
                
                # For each contract with findings, try to reload and enhance
                for contract_name, contract_findings in findings_by_contract.items():
                    # Try to find the contract file
                    try:
                        contract_path = None
                        for path in contract_paths:
                            if os.path.basename(path) == contract_name:
                                contract_path = path
                                break
                        
                        if contract_path:
                            contract = load_contract_content(contract_path)
                            if contract["content"]:
                                # Enhance the findings for this contract
                                enhanced_findings = analyze_contract_with_llm_enhanced(
                                    contract, 
                                    vuln_library, 
                                    contract_findings
                                )
                                
                                # Replace existing findings for this contract
                                all_findings = [f for f in all_findings if f["filename"] != contract_name]
                                all_findings.extend(enhanced_findings)
                                
                                # Update partial report
                                with open(partial_report_path, "a") as f:
                                    f.write(f"## Contract: {contract_name} (Enhanced)\n\n")
                                    for finding in enhanced_findings:
                                        f.write(f"### {finding.get('vuln_type', 'Unknown Vulnerability')} ({finding.get('severity', 'Medium')})\n\n")
                                        f.write(f"**Source:** Enhanced Analysis\n\n")
                                        f.write(f"**Lines:** {finding.get('line_numbers', 'Not specified')}\n\n")
                                        f.write(f"**Description:** {finding.get('description', 'No description')}\n\n")
                                        if 'fix' in finding:
                                            f.write(f"**Suggested Fix:** {finding.get('fix')}\n\n")
                                        f.write("---\n\n")
                        else:
                            console.print(f"[yellow]Contract file not found for {contract_name}[/yellow]")
                    except Exception as e:
                        console.print(f"[yellow]Error enhancing {contract_name}: {str(e)}[/yellow]")
        
        # Generate final report if findings exist
        if all_findings:
            report_path = save_vulnerability_report(session_folder, all_findings)
            console.print(f"[green]Found {len(all_findings)} potential vulnerabilities. Report saved to {report_path}[/green]")
            
            # Remove partial report since we have a final one
            if os.path.exists(partial_report_path):
                os.remove(partial_report_path)
        else:
            console.print("[green]No vulnerabilities detected in the analyzed contracts.[/green]")
            # Remove empty partial report
            if os.path.exists(partial_report_path):
                os.remove(partial_report_path)
    
    except KeyboardInterrupt:
        # Handle user interruption gracefully
        console.print("\n[yellow]Vulnerability scanning interrupted by user.[/yellow]")
        if all_findings:
            # Update partial report to indicate interruption
            with open(partial_report_path, "a") as f:
                f.write("\n## Scan Interrupted\n\n")
                f.write(f"Scan was interrupted at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}. ")
                f.write(f"Processed {i+1} of {len(contracts)} contracts.\n")
            console.print(f"[green]Partial findings saved to {partial_report_path}[/green]")
        return
    
    except Exception as e:
        # Handle other exceptions
        console.print(f"\n[bold red]Error during vulnerability scanning: {str(e)}[/bold red]")
        if all_findings:
            # Update partial report to indicate error
            with open(partial_report_path, "a") as f:
                f.write("\n## Scan Error\n\n")
                f.write(f"Scan encountered an error at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}. ")
                f.write(f"Processed {i+1} of {len(contracts)} contracts.\n")
                f.write(f"```\n{str(e)}\n```\n")
            console.print(f"[green]Partial findings saved to {partial_report_path}[/green]")
        return

def analyze_contract_with_llm(contract, vuln_library):
    """Analyze a smart contract directly with LLM to detect vulnerabilities"""
    # Process vulnerability library information for prompt context
    vuln_context = []
    for vuln_id, vuln_type, details_json in vuln_library:
        try:
            if details_json:
                details = json.loads(details_json)
                vuln_info = {
                    "type": vuln_type,
                    "description": details.get("description", "No description available"),
                    "examples": details.get("vulnerable_examples", [])[:1],  # Just one example to save space
                    "insights": details.get("insights", [])[:1]  # Just one insight to save space
                }
                vuln_context.append(vuln_info)
        except json.JSONDecodeError:
            vuln_context.append({"type": vuln_type, "description": "No details available"})
    
    # Get content to analyze - limit to first 12000 chars to avoid token limits
    contract_content = contract["content"][:12000] if len(contract["content"]) > 12000 else contract["content"]
    
    # Build a comprehensive prompt for vulnerability analysis
    prompt = f"""
# Smart Contract Vulnerability Analysis

## Contract: {contract["filename"]}

```solidity
{contract_content}
```

## Common Smart Contract Vulnerabilities to Check For:
{json.dumps([v["type"] for v in vuln_context[:10]], indent=2)}

## Task:
1. Thoroughly analyze this smart contract for security vulnerabilities, including but not limited to:
   - Reentrancy vulnerabilities
   - Integer overflow/underflow
   - Unchecked external calls
   - Access control issues
   - Front-running vulnerabilities
   - Gas optimization issues
   - Logic errors
   - Any other security concerns

2. For each vulnerability found, provide:
   - Vulnerability type
   - Exact line numbers or code sections affected
   - Clear explanation of why it's vulnerable
   - Severity rating (High/Medium/Low) with justification
   - Suggested fix or mitigation strategy

3. Return your findings as a valid JSON array with these fields. Format your response EXACTLY as follows with no other text or explanations:

[
  {{"vuln_type": "TYPE", "line_numbers": "LINES", "description": "DESCRIPTION", "severity": "SEVERITY", "fix": "FIX"}},
  {{"vuln_type": "TYPE2", "line_numbers": "LINES", "description": "DESCRIPTION", "severity": "SEVERITY", "fix": "FIX"}}
]

If no vulnerabilities are found after thorough analysis, return an empty JSON array: []

Make absolutely sure your response can be parsed as valid JSON. Do not include backticks, markdown formatting, or any other text before or after the JSON array.
"""
    
    console.print("[cyan]Analyzing contract for vulnerabilities with LLM...[/cyan]")
    response = call_llm(prompt, ANALYSIS_MODEL)
    
    # Parse the response - with enhanced error handling
    try:
        # Try to extract just the JSON part from the response
        # First, look for array brackets
        response_cleaned = response.strip()
        
        # Find the first '[' and last ']' to extract just the JSON array
        start_idx = response_cleaned.find('[')
        end_idx = response_cleaned.rfind(']')
        
        if start_idx >= 0 and end_idx > start_idx:
            # Extract just the JSON array part
            json_content = response_cleaned[start_idx:end_idx+1]
            findings = json.loads(json_content)
        else:
            # Try parsing the whole response as JSON anyway
            findings = json.loads(response_cleaned)
        
        # If we get here, JSON parsing succeeded
        if not isinstance(findings, list):
            # Handle case where response is valid JSON but not an array
            console.print("[yellow]Warning: LLM response is valid JSON but not an array. Converting to array.[/yellow]")
            findings = [findings]  # Convert to list
            
        # Add contract metadata to each finding
        for finding in findings:
            finding["contract_id"] = contract["id"]
            finding["filename"] = contract["filename"]
            finding["detection_method"] = "llm_analysis"
            finding["confidence"] = "high"  # LLM direct analysis is high confidence
            
            # Ensure required fields exist
            if "vuln_type" not in finding:
                finding["vuln_type"] = "Unknown Vulnerability"
            if "severity" not in finding:
                finding["severity"] = "Medium"  # Default severity
        return findings
        
    except json.JSONDecodeError as e:
        # Enhanced fallback for parsing failures
        console.print(f"[yellow]Warning: Could not parse LLM response as JSON: {str(e)}[/yellow]")
        console.print("[yellow]Attempting to extract vulnerability information from response...[/yellow]")
        
        # More sophisticated extraction from the raw response
        vuln_types = [
            "reentrancy", "overflow", "underflow", "access control", "front-running", 
            "integer overflow", "integer underflow", "race condition", "denial of service", 
            "dos", "unchecked external call", "gas optimization", "unauthorized access", 
            "logic error", "missing validation", "improper input validation"
        ]
        
        # Extract lines mentioning vulnerabilities
        found_issues = []
        for line in response.splitlines():
            for vt in vuln_types:
                if vt.lower() in line.lower():
                    found_issues.append({
                        "vuln_type": vt.title(),
                        "line": line.strip()
                    })
                    break
        
        if found_issues:
            console.print(f"[green]Extracted {len(found_issues)} potential issues from response[/green]")
            return [{
                "contract_id": contract["id"],
                "filename": contract["filename"],
                "vuln_type": issue["vuln_type"],
                "description": f"Extracted from LLM response: {issue['line']}",
                "severity": "Medium",
                "detection_method": "llm_analysis_fallback",
                "confidence": "low"
            } for issue in found_issues[:5]]
            
        console.print("[red]Could not extract any vulnerability information from the response[/red]")
        return []

def analyze_contract_with_llm_enhanced(contract, vuln_library, existing_findings):
    """Analyze a smart contract with LLM for enhanced vulnerability detection using existing findings as context.
    
    Args:
        contract (dict): Contract information including filename and content
        vuln_library (list): List of vulnerability types and descriptions
        existing_findings (list): List of existing vulnerability findings for this contract
        
    Returns:
        list: List of enhanced findings, building upon the existing ones
    """
    # Process vulnerability library information for prompt context
    vuln_context = []
    for vuln_id, vuln_type, details_json in vuln_library:
        try:
            if details_json:
                details = json.loads(details_json)
                vuln_info = {
                    "type": vuln_type,
                    "description": details.get("description", "No description available"),
                    "examples": details.get("vulnerable_examples", [])[:1],  # Just one example to save space
                    "insights": details.get("insights", [])[:1]  # Just one insight to save space
                }
                vuln_context.append(vuln_info)
        except json.JSONDecodeError:
            vuln_context.append({"type": vuln_type, "description": "No details available"})
    
    # Get content to analyze - limit to first 12000 chars to avoid token limits
    contract_content = contract["content"][:12000] if len(contract["content"]) > 12000 else contract["content"]
    
    # Format existing findings for the prompt
    existing_findings_text = ""
    if existing_findings:
        existing_findings_text = "\n## Previous Findings (Requiring Enhancement):\n"
        for i, finding in enumerate(existing_findings, 1):
            existing_findings_text += f"{i}. **{finding.get('vuln_type', 'Unknown Vulnerability')}** - "
            existing_findings_text += f"Lines: {finding.get('line_numbers', 'Not specified')} - "
            existing_findings_text += f"Severity: {finding.get('severity', 'Medium')}\n"
            # Add a condensed description to save tokens
            desc = finding.get('description', 'No description')
            if len(desc) > 150:
                desc = desc[:150] + "..."
            existing_findings_text += f"   Description: {desc}\n\n"
    
    # Build a comprehensive prompt for enhanced vulnerability analysis
    prompt = f"""
# Smart Contract Vulnerability Analysis - Enhancement Mode

## Contract: {contract["filename"]}

```solidity
{contract_content}
```
{existing_findings_text}
## Common Smart Contract Vulnerabilities to Check For:
{json.dumps([v["type"] for v in vuln_context[:10]], indent=2)}

## Task:
1. Review and enhance the existing findings listed above.
2. Thoroughly analyze this smart contract for additional security vulnerabilities, including but not limited to:
   - Reentrancy vulnerabilities
   - Integer overflow/underflow
   - Unchecked external calls
   - Access control issues
   - Front-running vulnerabilities
   - Gas optimization issues
   - Logic errors
   - Any other security concerns

3. For each vulnerability (both enhanced existing ones and newly found ones), provide:
   - Vulnerability type
   - Exact line numbers or code sections affected
   - Clear explanation of why it's vulnerable
   - Severity rating (High/Medium/Low) with justification
   - Suggested fix or mitigation strategy

4. Return your findings as a valid JSON array with these fields. Format your response EXACTLY as follows with no other text or explanations:

[
  {{"vuln_type": "TYPE", "line_numbers": "LINES", "description": "DESCRIPTION", "severity": "SEVERITY", "fix": "FIX", "enhanced": true}},
  {{"vuln_type": "TYPE2", "line_numbers": "LINES", "description": "DESCRIPTION", "severity": "SEVERITY", "fix": "FIX", "enhanced": true}}  
]

If no vulnerabilities are found after thorough analysis, return an empty JSON array: []

Make absolutely sure your response can be parsed as valid JSON. Do not include backticks, markdown formatting, or any other text before or after the JSON array.
"""
    
    console.print("[cyan]Enhancing vulnerability analysis with LLM...[/cyan]")
    response = call_llm(prompt, ANALYSIS_MODEL)
    
    # Parse the response - with enhanced error handling
    try:
        # Try to extract just the JSON part from the response
        # First, look for array brackets
        response_cleaned = response.strip()
        
        # Find the first '[' and last ']' to extract just the JSON array
        start_idx = response_cleaned.find('[')
        end_idx = response_cleaned.rfind(']')
        
        if start_idx >= 0 and end_idx > start_idx:
            # Extract just the JSON array part
            json_content = response_cleaned[start_idx:end_idx+1]
            enhanced_findings = json.loads(json_content)
        else:
            # Try parsing the whole response as JSON anyway
            enhanced_findings = json.loads(response_cleaned)
        
        # If we get here, JSON parsing succeeded
        if not isinstance(enhanced_findings, list):
            # Handle case where response is valid JSON but not an array
            console.print("[yellow]Warning: LLM response is valid JSON but not an array. Converting to array.[/yellow]")
            enhanced_findings = [enhanced_findings]  # Convert to list
            
        # Add contract metadata to each finding
        for finding in enhanced_findings:
            finding["contract_id"] = contract["id"]
            finding["filename"] = contract["filename"]
            finding["detection_method"] = "llm_enhanced_analysis"
            finding["confidence"] = "high"  # LLM direct analysis is high confidence
            
            # Ensure required fields exist
            if "vuln_type" not in finding:
                finding["vuln_type"] = "Unknown Vulnerability"
            if "severity" not in finding:
                finding["severity"] = "Medium"  # Default severity
            if "enhanced" not in finding:
                finding["enhanced"] = True  # Mark as enhanced
                
        return enhanced_findings
        
    except json.JSONDecodeError as e:
        # Enhanced fallback for parsing failures
        console.print(f"[yellow]Warning: Could not parse LLM enhanced response as JSON: {str(e)}[/yellow]")
        console.print("[yellow]Attempting to extract vulnerability information from response...[/yellow]")
        
        # More sophisticated extraction from the raw response
        vuln_types = [
            "reentrancy", "overflow", "underflow", "access control", "front-running", 
            "integer overflow", "integer underflow", "race condition", "denial of service", 
            "dos", "unchecked external call", "gas optimization", "unauthorized access", 
            "logic error", "missing validation", "improper input validation"
        ]
        
        # Extract lines mentioning vulnerabilities
        found_issues = []
        for line in response.splitlines():
            for vt in vuln_types:
                if vt.lower() in line.lower():
                    found_issues.append({
                        "vuln_type": vt.title(),
                        "line": line.strip()
                    })
                    break
        
        if found_issues:
            console.print(f"[green]Extracted {len(found_issues)} potential enhanced issues from response[/green]")
            return [{
                "contract_id": contract["id"],
                "filename": contract["filename"],
                "vuln_type": issue["vuln_type"],
                "description": f"Enhanced finding extracted from LLM response: {issue['line']}",
                "severity": "Medium",
                "detection_method": "llm_enhanced_analysis_fallback",
                "confidence": "low",
                "enhanced": True
            } for issue in found_issues[:5]]
        
        # If fallback extraction fails, preserve the original findings
        console.print("[yellow]Could not extract enhanced information. Preserving original findings.[/yellow]")
        # Make a deep copy of the original findings to avoid modifying the original objects
        preserved_findings = []
        for finding in existing_findings:
            # Create a new dict with the same contents
            new_finding = finding.copy()
            new_finding["enhanced"] = False
            new_finding["detection_method"] = "preserved_original"
            preserved_findings.append(new_finding)
        
        return preserved_findings

# Keeping the original function for backwards compatibility
def verify_vulnerabilities_with_llm(contract, findings):
    """Legacy function for verifying vulnerabilities - now uses the new analyze_contract_with_llm"""
    # Get details about the vulnerability types
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    vuln_ids = [f["vuln_id"] for f in findings]
    placeholders = ",".join(["?" for _ in vuln_ids])
    cur.execute(f"SELECT id, vuln_type, details FROM vuln_detection_library WHERE id IN ({placeholders})", vuln_ids)
    
    # Convert to format expected by analyze_contract_with_llm
    vuln_library = cur.fetchall()
    conn.close()
    
    # Use the new function for analysis
    return analyze_contract_with_llm(contract, vuln_library)

def save_vulnerability_report(session_folder, findings):
    """Generate and save a vulnerability report based on findings"""
    # Create a timestamp for the report filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(session_folder, f"vulnerability_report_{timestamp}.md")
    
    # Group findings by contract
    by_contract = {}
    for finding in findings:
        if finding["filename"] not in by_contract:
            by_contract[finding["filename"]] = []
        by_contract[finding["filename"]].append(finding)
    
    # Generate the report
    with open(report_path, "w") as f:
        f.write(f"# Vulnerability Scan Report\n\n")
        f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"## Summary\n\n")
        f.write(f"- Total vulnerabilities found: {len(findings)}\n")
        f.write(f"- Contracts affected: {len(by_contract)}\n\n")
        
        for contract, contract_findings in by_contract.items():
            f.write(f"## {contract}\n\n")
            
            for i, finding in enumerate(contract_findings, 1):
                f.write(f"### {i}. {finding['vuln_type']}\n\n")
                f.write(f"- **Confidence**: {finding['confidence']}\n")
                if 'severity' in finding:
                    f.write(f"- **Severity**: {finding['severity']}\n")
                if 'location' in finding:
                    f.write(f"- **Location**: {finding['location']}\n")
                if 'explanation' in finding:
                    f.write(f"- **Explanation**: {finding['explanation']}\n")
                if 'fix' in finding:
                    f.write(f"\n**Suggested Fix**:\n\n```solidity\n{finding['fix']}\n```\n\n")
                f.write("\n")
    
    return report_path

# -------------------------------
# Q&A Session Functions
def save_qa_session(session_id, session_folder, qa_pairs):
    """Save a Q&A session to the database"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        
        # Convert Q&A pairs to JSON string
        qa_data = json.dumps(qa_pairs, ensure_ascii=False, indent=2)
        
        # Current timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert or replace the Q&A session
        cur.execute("""
            INSERT OR REPLACE INTO qa_sessions (id, session_folder, timestamp, qa_data)
            VALUES (?, ?, ?, ?)
        """, (session_id, session_folder, timestamp, qa_data))
        
        conn.commit()
        console.print(f"[green]Q&A session saved to database[/green]")
        
    except Exception as e:
        console.print(f"[bold red]Error saving Q&A session to database: {str(e)}[/bold red]")
    finally:
        if conn:
            conn.close()

def export_qa_session(session_id, session_folder, qa_pairs, export_format):
    """Export a Q&A session as markdown or PDF"""
    # Create output filename
    output_filename = f"{session_id}.{export_format}"
    output_path = os.path.join(session_folder, output_filename)
    
    try:
        if export_format == "md":
            # Export as Markdown
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# Q&A Session: {os.path.basename(session_folder)}\n\n")
                f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                for i, qa in enumerate(qa_pairs, 1):
                    f.write(f"## Question {i}: {qa['question']}\n\n")
                    f.write(f"*Asked on: {qa['timestamp']}*\n\n")
                    f.write(f"{qa['answer']}\n\n")
                    f.write("---\n\n")
            
            console.print(f"[green]Q&A session exported as Markdown to: {output_path}[/green]")
        
        elif export_format == "pdf":
            # Check if we can generate PDFs
            if not fitz:
                console.print("[bold yellow]PyMuPDF (fitz) is not installed. Falling back to Markdown export.[/bold yellow]")
                export_qa_session(session_id, session_folder, qa_pairs, "md")
                return
                
            # First create markdown content
            md_content = f"# Q&A Session: {os.path.basename(session_folder)}\n\n"
            md_content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            
            for i, qa in enumerate(qa_pairs, 1):
                md_content += f"## Question {i}: {qa['question']}\n\n"
                md_content += f"*Asked on: {qa['timestamp']}*\n\n"
                md_content += f"{qa['answer']}\n\n"
                md_content += "---\n\n"
            
            # Convert markdown to HTML using Python's markdown library
            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Q&A Session</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ color: #2c3e50; }}
        h2 {{ color: #3498db; margin-top: 30px; }}
        .timestamp {{ color: #7f8c8d; font-style: italic; }}
        .answer {{ margin-top: 15px; }}
        hr {{ margin: 30px 0; border: none; border-top: 1px solid #ecf0f1; }}
    </style>
</head>
<body>
    {markdown.markdown(md_content)}
</body>
</html>"""
            
            # Create PDF with PyMuPDF
            doc = fitz.open()
            doc.new_page(width=595, height=842)  # A4 size
            page = doc[0]
            page.insert_html(html_content)
            doc.save(output_path)
            doc.close()
            
            console.print(f"[green]Q&A session exported as PDF to: {output_path}[/green]")
    
    except Exception as e:
        console.print(f"[bold red]Error exporting Q&A session: {str(e)}[/bold red]")
        # Fallback to markdown if PDF export fails
        if export_format == "pdf":
            console.print("[yellow]Falling back to Markdown export...[/yellow]")
            export_qa_session(session_id, session_folder, qa_pairs, "md")

# -------------------------------
# Session Management Helpers
def get_or_create_session():
    """Get an existing session or create a new one"""
    # Look for existing sessions in the current directory
    sessions = [d for d in os.listdir(os.getcwd()) 
               if os.path.isdir(os.path.join(os.getcwd(), d)) and d.startswith("analysis_")]
    
    if sessions:
        # Sort sessions by creation time (newest first)
        sessions.sort(reverse=True)
        
        # Ask if user wants to use an existing session
        console.print("\n[bold]Recent analysis sessions:[/bold]")
        for i, session in enumerate(sessions[:5], 1):  # Show the 5 most recent sessions
            console.print(f"{i}. {session}")
        
        use_existing = Prompt.ask("Use an existing session or create new?", 
                                choices=["existing", "new"], default="new")
        
        if use_existing == "existing":
            session_choice = Prompt.ask("Select session number")
            try:
                session_index = int(session_choice) - 1
                if 0 <= session_index < len(sessions[:5]):
                    session_folder = os.path.join(os.getcwd(), sessions[session_index])
                    console.print(f"Using existing session: {session_folder}")
                    return session_folder
            except ValueError:
                console.print("Invalid selection. Creating a new session.")
    
    # Create a new session
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_folder = os.path.join(os.getcwd(), f"analysis_{timestamp}")
    os.makedirs(output_folder, exist_ok=True)
    console.print(f"Output folder created: {output_folder}")
    return output_folder

def analyze_document(output_folder):
    """Handle document analysis workflow"""
    # Select document type
    doc_type = Prompt.ask(
        "Select document type", 
        choices=["pdf", "md", "url", "auto-detect"], 
        default="auto-detect"
    )
    
    if doc_type == "auto-detect":
        doc_type = None  # Will be auto-detected based on file extension or URL format
    
    # Get the source path based on the selected type
    if doc_type == "pdf" or doc_type == "md" or doc_type is None:
        source_path = Prompt.ask("Enter the path to the documentation file")
        if not os.path.isfile(source_path):
            console.print("The provided file does not exist.")
            return None
    elif doc_type == "url":
        source_path = Prompt.ask("Enter the URL to the documentation")
        if not source_path.startswith(("http://", "https://")):
            console.print("Invalid URL format. URL must start with http:// or https://")
            return None
    
    # Process the document
    result = process_document(source_path, output_folder, doc_type)
    
    if result:
        console.print("Analysis complete for the documentation.")
        return result
    
    return None

def get_contracts_in_session(session_folder):
    """Get all contract folders in a session"""
    if not os.path.isdir(session_folder):
        return []
    
    # First try to find the 'contracts' subdirectory
    contracts_dir = os.path.join(session_folder, "contracts")
    if os.path.isdir(contracts_dir):
        # Look for .sol files directly in the contracts directory
        contracts = []
        for item in os.listdir(contracts_dir):
            if item.endswith(".sol"):
                contracts.append(os.path.join(contracts_dir, item))
        if contracts:
            return contracts
    
    # Fallback: look for contract directories (those with a .sol extension)
    contracts = []
    for item in os.listdir(session_folder):
        item_path = os.path.join(session_folder, item)
        if os.path.isdir(item_path) and item.endswith(".sol"):
            contracts.append(item_path)
    
    return contracts

def get_documents_in_session(session_folder):
    """Get all document folders in a session"""
    if not os.path.isdir(session_folder):
        return []
    
    # Look for document directories (those not ending with .sol)
    documents = []
    for item in os.listdir(session_folder):
        item_path = os.path.join(session_folder, item)
        if os.path.isdir(item_path) and not item.endswith(".sol"):
            documents.append(item_path)
    
    return documents

# -------------------------------
# Contract Analysis Functions
def analyze_smart_contract(code, contract_name):
    """Analyze smart contract code and generate analysis results"""
    console.print("[bold blue]Analyzing smart contract code...[/bold blue]")
    
    # Initialize the analysis result
    analysis_result = {
        "contract_name": contract_name,
        "analysis": "",
        "recommendations": "",
        "vulnerabilities": ""
    }
    
    try:
        # Use global LLM provider and model name settings
        global LLM_PROVIDER, MODEL_NAME
        
        # Prepare a comprehensive prompt for the LLM
        prompt = f"""Analyze the following Solidity smart contract '{contract_name}':

```solidity
{code}
```

Provide a detailed security analysis with the following sections:
1. Overall purpose and functionality of the contract
2. Security assessment and potential vulnerabilities
3. Best practices and recommendations

Be specific and thorough in your analysis."""
        
        # Call the appropriate model based on the provider type
        response = call_llm(prompt, MODEL_NAME)
        
        # Extract the analysis content from the response
        # Handle different response types (string or dictionary)
        if isinstance(response, str):
            # Response is already a string
            analysis = response
        elif isinstance(response, dict):
            # Response is a dictionary, extract content based on provider format
            if 'response' in response:
                # Ollama response format
                analysis = response.get('response', "No analysis generated")
            elif 'choices' in response and len(response['choices']) > 0:
                # OpenRouter response format
                analysis = response['choices'][0].get('message', {}).get('content', "No analysis generated")
            else:
                analysis = "No analysis generated"
        else:
            analysis = "Unexpected response format from model"
        
        # Parse the analysis into the appropriate sections
        sections = parse_analysis_sections(analysis)
        
        # Update the analysis result
        analysis_result["analysis"] = sections.get("overview", "No overview provided")
        analysis_result["recommendations"] = sections.get("recommendations", "No recommendations provided")
        analysis_result["vulnerabilities"] = sections.get("vulnerabilities", "No vulnerabilities identified")
        
        return analysis_result
    
    except Exception as e:
        console.print(f"[bold red]Error during analysis:[/bold red] {str(e)}")
        analysis_result["analysis"] = f"Analysis error: {str(e)}"
        return analysis_result

def parse_analysis_sections(analysis_text):
    """Parse the analysis text into different sections"""
    sections = {
        "overview": "",
        "vulnerabilities": "",
        "recommendations": ""
    }
    
    # Simple parsing logic - can be enhanced for better section detection
    if "vulnerability" in analysis_text.lower() or "security" in analysis_text.lower():
        parts = analysis_text.split("\n\n")
        
        for part in parts:
            lower_part = part.lower()
            if "overview" in lower_part or "purpose" in lower_part or "functionality" in lower_part:
                sections["overview"] += part + "\n\n"
            elif "vulnerability" in lower_part or "security issue" in lower_part or "risk" in lower_part:
                sections["vulnerabilities"] += part + "\n\n"
            elif "recommendation" in lower_part or "best practice" in lower_part or "improvement" in lower_part:
                sections["recommendations"] += part + "\n\n"
            else:
                # Add to overview if not matched to another section
                sections["overview"] += part + "\n\n"
    else:
        # If no clear sections, just use the entire text as the overview
        sections["overview"] = analysis_text
    
    return sections

# -------------------------------
# Contract and Document Menu Functions
def contract_menu(contract_path):
    """Display and analyze a specific contract"""
    contract_name = os.path.basename(contract_path)
    
    console.print(f"\n[bold]Contract:[/bold] {contract_name}")
    
    # Handle the case where contract_path is a directory containing a .sol file
    # First, check if it's a directory
    if os.path.isdir(contract_path):
        # Look for the main contract file in the directory (same name as directory without .sol)
        main_file = os.path.join(contract_path, contract_name)
        if os.path.exists(main_file):
            contract_file_path = main_file
        else:
            # Try to find any .sol file in the directory
            sol_files = [f for f in os.listdir(contract_path) if f.endswith('.sol')]
            if sol_files:
                contract_file_path = os.path.join(contract_path, sol_files[0])
            else:
                console.print("[bold red]Could not find contract file in directory.[/bold red]")
                return
    else:
        contract_file_path = contract_path
    
    # Determine if analysis exists
    session_folder = os.path.dirname(contract_path)  # The session directory
    analysis_path = os.path.join(session_folder, "contract_analyses", f"{contract_name}.json")
    has_analysis = os.path.exists(analysis_path)
    
    # Check for additional analysis artifacts
    contract_folder = contract_path if os.path.isdir(contract_path) else None
    if not contract_folder and contract_name.endswith('.sol'):
        potential_folder = os.path.join(session_folder, contract_name)
        if os.path.isdir(potential_folder):
            contract_folder = potential_folder
    
    # Look for diagrams and reports
    diagrams = []
    reports = []
    if contract_folder:
        for item in os.listdir(contract_folder):
            if item.endswith('.md') and ('diagram' in item or 'chart' in item or 'flow' in item):
                diagrams.append(os.path.join(contract_folder, item))
            elif item.endswith('.md') and ('report' in item or 'analysis' in item or 'functions' in item):
                reports.append(os.path.join(contract_folder, item))
    
    while True:
        console.print("\n[bold]Contract Actions:[/bold]")
        console.print("1. View Contract Code")
        option_idx = 2
        
        if has_analysis:
            console.print(f"{option_idx}. View Analysis")
            option_idx += 1
        else:
            console.print(f"{option_idx}. Analyze Contract")
            option_idx += 1
        
        if diagrams:
            console.print(f"{option_idx}. View Diagrams ({len(diagrams)} available)")
            diagrams_option = option_idx
            option_idx += 1
        else:
            diagrams_option = None
            
        if reports:
            console.print(f"{option_idx}. View Reports ({len(reports)} available)")
            reports_option = option_idx
            option_idx += 1
        else:
            reports_option = None
            
        console.print(f"{option_idx}. Back to Contracts List")
        max_option = option_idx
        
        try:
            choice = int(Prompt.ask("Enter your choice"))
            
            if choice == 1:
                # View contract code
                try:
                    with open(contract_file_path, 'r', encoding='utf-8') as f:
                        code = f.read()
                    
                    console.print(f"\n[bold]Contract Code:[/bold] {contract_name}")
                    syntax = Syntax(code, "solidity", theme="monokai", line_numbers=True)
                    console.print(syntax)
                except Exception as e:
                    console.print(f"[bold red]Error reading contract code:[/bold red] {str(e)}")
                    console.print(f"[dim]Path attempted: {contract_file_path}[/dim]")
                
                console.print("Press Enter to continue...")
                input()
                
            elif choice == 2 and has_analysis:
                # View analysis
                with open(analysis_path, 'r') as f:
                    analysis = json.load(f)
                
                console.print(f"\n[bold]Contract Analysis:[/bold] {contract_name}")
                if "analysis" in analysis:
                    console.print(Panel(analysis["analysis"], title="Analysis", border_style="green"))
                if "recommendations" in analysis:
                    console.print(Panel(analysis["recommendations"], title="Recommendations", border_style="blue"))
                if "vulnerabilities" in analysis:
                    console.print(Panel(analysis["vulnerabilities"], title="Potential Vulnerabilities", border_style="red"))
                
                console.print("Press Enter to continue...")
                input()
                
            elif choice == 2 and not has_analysis:
                # Analyze contract
                console.print(f"\n[bold]Analyzing contract:[/bold] {contract_name}")
                
                try:
                    # Read contract code
                    with open(contract_file_path, 'r', encoding='utf-8') as f:
                        code = f.read()
                    
                    # Analyze with the current model
                    analysis_result = analyze_smart_contract(code, contract_name)
                    
                    # Save analysis
                    os.makedirs(os.path.dirname(analysis_path), exist_ok=True)
                    with open(analysis_path, 'w') as f:
                        json.dump(analysis_result, f, indent=4)
                    
                    console.print("[bold green]Analysis complete![/bold green]")
                    has_analysis = True
                except Exception as e:
                    console.print(f"[bold red]Error analyzing contract:[/bold red] {str(e)}")
                    console.print(f"[dim]Path attempted: {contract_file_path}[/dim]")
                
            elif diagrams_option and choice == diagrams_option:
                # View diagrams
                console.print("\n[bold]Available Diagrams:[/bold]")
                for i, diagram in enumerate(diagrams, 1):
                    diagram_name = os.path.basename(diagram)
                    console.print(f"{i}. {diagram_name}")
                
                try:
                    diagram_choice = int(Prompt.ask("Enter diagram number (0 to cancel)"))
                    if 1 <= diagram_choice <= len(diagrams):
                        selected_diagram = diagrams[diagram_choice - 1]
                        with open(selected_diagram, 'r', encoding='utf-8') as f:
                            diagram_content = f.read()
                        
                        console.print(f"\n[bold]Diagram:[/bold] {os.path.basename(selected_diagram)}")
                        console.print(Markdown(diagram_content))
                        console.print("Press Enter to continue...")
                        input()
                except ValueError:
                    console.print("Invalid choice.")
            
            elif reports_option and choice == reports_option:
                # View reports
                console.print("\n[bold]Available Reports:[/bold]")
                for i, report in enumerate(reports, 1):
                    report_name = os.path.basename(report)
                    console.print(f"{i}. {report_name}")
                
                try:
                    report_choice = int(Prompt.ask("Enter report number (0 to cancel)"))
                    if 1 <= report_choice <= len(reports):
                        selected_report = reports[report_choice - 1]
                        with open(selected_report, 'r', encoding='utf-8') as f:
                            report_content = f.read()
                        
                        console.print(f"\n[bold]Report:[/bold] {os.path.basename(selected_report)}")
                        console.print(Markdown(report_content))
                        console.print("Press Enter to continue...")
                        input()
                except ValueError:
                    console.print("Invalid choice.")
            
            elif choice == max_option:
                # Back to list
                break
                
            else:
                console.print("Invalid choice. Try again.")
                
        except ValueError:
            console.print("Please enter a number.")

def contract_menu_in_session(session_folder):
    """Browse contracts in a session"""
    contracts = get_contracts_in_session(session_folder)
    
    if not contracts:
        console.print("[bold yellow]No contracts found in this session.[/bold yellow]")
        return
    
    while True:
        console.print("\n[bold]Available Contracts:[/bold]")
        for i, contract in enumerate(contracts, 1):
            console.print(f"{i}. {os.path.basename(contract)}")
        console.print(f"{len(contracts) + 1}. Back to Session Menu")
        
        try:
            choice = int(Prompt.ask("Enter your choice"))
            if choice == len(contracts) + 1:
                break
            elif 1 <= choice <= len(contracts):
                contract_menu(contracts[choice - 1])
            else:
                console.print("Invalid choice. Try again.")
        except ValueError:
            console.print("Please enter a number.")

def documents_menu_in_session(session_folder):
    """Browse documents in a session"""
    documents = get_documents_in_session(session_folder)
    
    if not documents:
        console.print("[bold yellow]No documents found in this session.[/bold yellow]")
        return
    
    while True:
        console.print("\n[bold]Available Documents:[/bold]")
        for i, doc in enumerate(documents, 1):
            console.print(f"{i}. {os.path.basename(doc)}")
        console.print(f"{len(documents) + 1}. Back to Session Menu")
        
        try:
            choice = int(Prompt.ask("Enter your choice"))
            if choice == len(documents) + 1:
                break
            elif 1 <= choice <= len(documents):
                document_menu(documents[choice - 1])
            else:
                console.print("Invalid choice. Try again.")
        except ValueError:
            console.print("Please enter a number.")

# -------------------------------
# Process a Single Contract
# -------------------------------
async def process_contract_async(filepath, output_dir, progress=None):
    """Process a smart contract file and generate analysis"""
    # Extract contract id and name
    contract_id = hashlib.md5(filepath.encode()).hexdigest()
    contract_name = os.path.basename(filepath)
    
    # Read contract content
    contract_content = read_contract_file(filepath)
    if not contract_content:
        console.print(f"[bold red]Failed to read contract: {filepath}[/bold red]")
        return
    
    # Create a subdirectory for this contract's analysis
    contract_dir = os.path.join(output_dir, contract_name)
    os.makedirs(contract_dir, exist_ok=True)
    
    # Save the original contract file
    contract_path = save_file(contract_content, "original.sol", contract_dir)
    
    # Create a dictionary to store all generated reports
    reports = {}
    
    # Progress indicator for each phase
    task_id = None
    if progress:
        task_id = progress.add_task(f"Analyzing {contract_name} - Functions", total=1)
    
    # Phase 1: Generate Functions Report
    functions_report = generate_functions_report(contract_content)
    functions_path = save_file(functions_report, "functions_report.md", contract_dir)
    reports["functions_report"] = functions_report
    
    if progress and task_id is not None:
        progress.update(task_id, description=f"Analyzing {contract_name} - Journey")
    
    # Phase 2: Generate Journey Report
    journey_report = generate_journey_report(contract_content)
    journey_path = save_file(journey_report, "journey_report.md", contract_dir)
    reports["journey_report"] = journey_report
    
    if progress and task_id is not None:
        progress.update(task_id, description=f"Analyzing {contract_name} - Journey Diagram")
    
    # Phase 3a: Generate Journey Diagram
    journey_diagram = generate_journey_diagram(journey_report)
    journey_diagram_path = save_file(journey_diagram, "journey_diagram.md", contract_dir)
    reports["journey_diagram"] = journey_diagram
    
    if progress and task_id is not None:
        progress.update(task_id, description=f"Analyzing {contract_name} - Call Diagram")
    
    # Phase 3b: Generate Call Diagram
    call_diagram = generate_call_diagram(functions_report)
    call_diagram_path = save_file(call_diagram, "call_diagram.md", contract_dir)
    reports["call_diagram"] = call_diagram
    
    # Complete the task
    if progress and task_id is not None:
        progress.update(task_id, advance=1, description=f"Completed {contract_name}")
    
    # Save all reports to database
    save_analysis(
        contract_id, contract_name, contract_content,
        functions_report, journey_report, journey_diagram, call_diagram
    )
    
    console.print(f"[bold green]Contract analysis complete![/bold green] Results saved to {contract_dir}")
    
    return reports

def process_contract(filepath, output_dir):
    """Process a single contract (synchronous wrapper)"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        asyncio.run(process_contract_async(filepath, output_dir, progress))

# -------------------------------
# Batch Processing Functions
# -------------------------------
async def process_contracts_parallel(contract_files, output_folder, max_workers=3):
    """Process multiple contracts in parallel"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Processing contracts... {task.description}"),
        console=console
    ) as progress:
        tasks = [process_contract_async(filepath, output_folder, progress) for filepath in contract_files]
        await asyncio.gather(*tasks)

async def process_documents_parallel(document_paths, output_folder, source_types=None, max_workers=3):
    """Process multiple documents in parallel"""
    if source_types is None:
        source_types = [None] * len(document_paths)  # Auto-detect for all documents
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Processing documents... {task.description}"),
        console=console
    ) as progress:
        tasks = []
        for i, doc_path in enumerate(document_paths):
            source_type = source_types[i] if i < len(source_types) else None
            tasks.append(process_document_async(doc_path, output_folder, source_type, progress))
        
        await asyncio.gather(*tasks)

# -------------------------------
# API Connection Testing 
# -------------------------------
def test_ollama_connection():
    """Test the connection to the Ollama API and print available models using ollama-python"""
    try:
        # Use the ollama-python client to list models
        response = ollama.list()
        
        # Extract models - ollama-python uses Pydantic models, not dictionaries
        models = []
        if hasattr(response, 'models') and isinstance(response.models, list):
            models = response.models
            
            console.print("[bold green]Connected to Ollama successfully![/bold green]")
            console.print(f"[bold]Available Models: {len(models)}[/bold]")
            
            model_table = Table(show_header=True, header_style="bold magenta")
            model_table.add_column("Model Name")
            model_table.add_column("Size")
            model_table.add_column("Modified")
            
            # Extract full model data for debugging first
            for i, model in enumerate(models):
                console.print(f"[dim]Model {i+1} data: {dir(model)}[/dim]")
                if hasattr(model, 'model'):
                    console.print(f"[dim]Model attribute: {model.model}[/dim]")
            
            for model in models:
                # For Ollama models, the actual model name is in the 'model' attribute
                name = "Unknown"
                if hasattr(model, 'model'):
                    name = model.model
                
                # Handle size - convert to GB for readability
                size_str = "Unknown"
                try:
                    if hasattr(model, 'size'):
                        size = model.size
                        # Convert bytes to GB
                        if isinstance(size, int) or (isinstance(size, str) and size.isdigit()):
                            if isinstance(size, str):
                                size = int(size)
                            size_gb = size / (1024**3)
                            size_str = f"{size_gb:.2f} GB"
                        else:
                            # Just use the string representation
                            size_str = str(size)
                except Exception as e:
                    console.print(f"[dim]Error getting size for {name}: {e}[/dim]")
                    
                # Handle modified timestamp - simpler approach
                modified = "Unknown"
                if hasattr(model, 'modified_at'):
                    try:
                        # Just use the string representation 
                        modified = str(model.modified_at)
                    except:
                        pass
                
                model_table.add_row(name, size_str, modified)
            
            console.print(model_table)
            return models
        else:
            console.print("[bold red]Error: No models found in Ollama response[/bold red]")
            console.print("[yellow]Make sure Ollama is running and has models installed.[/yellow]")
            return []
    except Exception as e:
        console.print(f"[bold red]Failed to connect to Ollama:[/bold red] {e}")
        console.print("[yellow]Make sure Ollama is running on localhost:11434 and try again.[/yellow]")
        return []

def call_openrouter_sync(prompt, model=None):
    """Call OpenRouter API without streaming for fallback"""
    if model is None:
        model = "deepseek/deepseek-v3-base:free"  # Default OpenRouter model

    # Check cache first
    cache_key = hashlib.sha256((prompt + model + "openrouter_sync").encode()).hexdigest()
    cached_response = cache.get(cache_key)
    if cached_response:
        return cached_response

    # Convert the prompt to OpenRouter's expected format
    messages = [{"role": "user", "content": prompt}]

    # Add required headers including HTTP_REFERER and X-Title for data policy - exactly as in docs
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/pxng0lin/DeepCurrent",  # Required for data policies
        "X-Title": "DeepCurrent Smart Contract Analyzer",  # Required for data policies
        "X-Data-Usage": OPENROUTER_DATA_USAGE  # Control data usage policy
    }
    
    # Simplified payload
    payload = {
        "model": model,
        "messages": messages
    }
    
    # Try main model first, then fall back to backup models if needed
    backup_models = OPENROUTER_MODELS.copy()
    if model in backup_models:
        backup_models.remove(model)
    
    models_to_try = [model] + backup_models
    
    for attempt, current_model in enumerate(models_to_try):
        if attempt > 0:
            console.print(f"[yellow]Attempting fallback model: {current_model}[/yellow]")
            payload["model"] = current_model
        
        try:
            console.print(f"[cyan]Calling OpenRouter with model: {current_model} (non-streaming)[/cyan]")
            response = requests.post(OPENROUTER_API_URL, json=payload, headers=headers)
            
            if response.status_code == 200:
                response_data = response.json()
                if 'choices' in response_data and response_data['choices'] and 'message' in response_data['choices'][0]:
                    message = response_data['choices'][0]['message']
                    content = message.get('content', '')
                    
                    # Store in cache and return
                    cache.set(cache_key, content, expire=CACHE_EXPIRY)
                    return content
                else:
                    console.print(f"[yellow]Unexpected response format from {current_model}[/yellow]")
                    if attempt < len(models_to_try) - 1:
                        continue
                    else:
                        return "Unable to generate analysis. Please try a different model or provider."
            else:
                console.print(f"[yellow]Error {response.status_code} from {current_model}: {response.text}[/yellow]")
                if attempt < len(models_to_try) - 1:
                    continue
                else:
                    # Last model failed, return default response
                    console.print("[bold red]All models failed in synchronous mode[/bold red]")
                    return "# Smart Contract Analysis\n\nUnable to generate a complete analysis at this time. Please try a different model or provider."
        except Exception as e:
            console.print(f"[yellow]API call failed with {current_model}: {e}[/yellow]")
            if attempt < len(models_to_try) - 1:
                continue
    
    # If we get here, all models have failed
    return "# Smart Contract Analysis\n\nUnable to generate a complete analysis at this time. Please try a different model or provider."

def test_openrouter_connection(api_key):
    """Test the connection to OpenRouter and verify the API key and return only free models"""
    try:
        # Include required headers for data policy compliance
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/pxng0lin/DeepCurrent",  # Required for data policies
            "X-Title": "DeepCurrent Smart Contract Analyzer",  # Required for data policies
            "X-Data-Usage": OPENROUTER_DATA_USAGE  # Control data usage policy
        }
        response = requests.get("https://openrouter.ai/api/v1/models", headers=headers)
        if response.status_code == 200:
            all_models = response.json().get("data", [])
            
            # Filter for free models only
            free_models = []
            for model in all_models:
                model_id = model.get("id", "Unknown")
                # Check if model is free - either by having ':free' suffix or $0 pricing
                price = model.get('pricing', {}).get('prompt', -1)
                if ":free" in model_id or price == 0:
                    free_models.append(model)
            
            console.print("[bold green]Connected to OpenRouter successfully![/bold green]")
            console.print(f"[bold]Available Free Models: {len(free_models)} of {len(all_models)}[/bold]")
            
            model_table = Table(show_header=True, header_style="bold magenta")
            model_table.add_column("Model ID")
            model_table.add_column("Context Length")
            model_table.add_column("Pricing")
            
            for model in free_models:
                model_id = model.get("id", "Unknown")
                context_length = model.get("context_length", "Unknown")
                pricing = f"${model.get('pricing', {}).get('prompt', 0)} per 1M tokens (prompt)"
                model_table.add_row(model_id, str(context_length), pricing)
            console.print(model_table)
            return free_models
        else:
            console.print(f"[bold red]Error connecting to OpenRouter: HTTP {response.status_code}[/bold red]")
            console.print(f"Response: {response.text}")
            return []
    except Exception as e:
        console.print(f"[bold red]Failed to connect to OpenRouter:[/bold red] {e}")
        return []

# -------------------------------
# Main Application Flow
# -------------------------------
def main():
    init_db()
    update_db_schema()
    global ANALYSIS_MODEL, QUERY_MODEL, LLM_PROVIDER, OPENROUTER_API_KEY, OPENROUTER_DATA_USAGE
    
    # Set up the argument parser
    parser = argparse.ArgumentParser(description="DeepCurrent Protocol and Smart Contract Analysis Tool")
    parser.add_argument("--data-usage", choices=["enable", "null", "disabled"], default="null",
                      help="OpenRouter data usage policy: enable, null, or disabled")
    args = parser.parse_args()
    
    # Set data usage policy from command line
    OPENROUTER_DATA_USAGE = args.data_usage
    
    # Choose LLM provider with a numbered menu
    console.print("Choose LLM provider:")
    console.print("1. Ollama (local)")
    console.print("2. OpenRouter (cloud)")
    provider_choice = Prompt.ask("Enter your choice", choices=["1", "2"], default="1")
    
    # Set provider based on numbered choice
    LLM_PROVIDER = "ollama" if provider_choice == "1" else "openrouter"
    
    if LLM_PROVIDER == "ollama":
        # Test Ollama connection and get available models
        models = test_ollama_connection()
        
        # Extract model names from the 'model' attribute
        model_names = []
        try:
            if models and isinstance(models, list):
                for model in models:
                    if hasattr(model, 'model'):
                        model_name = str(model.model)
                        if model_name and model_name != "None":
                            model_names.append(model_name)
        except Exception as e:
            console.print(f"[yellow]Error extracting model names: {e}[/yellow]")
        
        # Fallback to default models if we couldn't get any from Ollama
        if not model_names:
            model_names = ["deepseek-r1:32b", "gemma3:27b"]
            console.print("[yellow]Using default model list since no models were found from Ollama.[/yellow]")
        
        ANALYSIS_MODEL = Prompt.ask("Select model for initial analysis", choices=model_names, default=model_names[2] if model_names else "deepseek-r1:32b")
        QUERY_MODEL = Prompt.ask("Select model for querying", choices=model_names, default=model_names[2] if model_names else "gemma3:27b")
    
    else:  # OpenRouter
        # Get API key
        OPENROUTER_API_KEY = Prompt.ask("Enter your OpenRouter API key")
        
        # Test connection and get available models
        models = test_openrouter_connection(OPENROUTER_API_KEY)
        model_ids = [model.get("id") for model in models] if models else OPENROUTER_MODELS
        
        # Set default model to deepseek/deepseek-r1-distill-llama-70b:free
        default_model = "deepseek/deepseek-r1-distill-llama-70b:free"
        # Check if the default model is available in the model list
        if models and default_model not in model_ids:
            # If not available, use the first model from the list
            default_model = model_ids[0] if model_ids else "google/gemini-2.5-pro-exp-03-25:free"
            
        ANALYSIS_MODEL = Prompt.ask("Select model for initial analysis", choices=model_ids, default=default_model)
        QUERY_MODEL = Prompt.ask("Select model for querying", choices=model_ids, default=default_model)
        
        console.print(f"[green]Using OpenRouter with models: {ANALYSIS_MODEL} and {QUERY_MODEL}[/green]")
    
    try:
        while True:
            console.print("\n--- Main Menu ---")
            console.print("1. Analyse smart contracts directory")
            console.print("2. Analyse protocol documentation")
            console.print("3. Browse historical analysis sessions")
            console.print("4. Browse document analyses")
            console.print("5. Clear analysis cache")
            console.print("6. Exit")
            choice = Prompt.ask("Enter your choice")
            
            if choice == "1":
                # Smart contract analysis
                directory = Prompt.ask("Enter the path to the smart contracts directory")
                if not os.path.isdir(directory):
                    console.print("The provided directory does not exist.")
                    continue
                    
                # Get or create session folder
                output_folder = get_or_create_session()
                
                # Process all .sol files in the directory
                contract_files = [os.path.join(directory, f) for f in os.listdir(directory)
                                if os.path.isfile(os.path.join(directory, f)) and f.endswith(".sol")]
                if not contract_files:
                    console.print("No smart contract files (.sol) found in the directory.")
                    continue
                    
                # Process contracts in parallel
                asyncio.run(process_contracts_parallel(contract_files, output_folder))
                console.print("Analysis complete for all contracts in the directory.")
                
                # Ask if user wants to add document analysis to this session
                add_docs = Prompt.ask("Would you like to add document analysis to this session?", choices=["yes", "no"], default="no")
                if add_docs == "yes":
                    analyze_document(output_folder)
                else:
                    session_menu(output_folder)
                
            elif choice == "2":
                # Protocol documentation analysis
                output_folder = get_or_create_session()
                
                # Process document
                result = analyze_document(output_folder)
                
                if result:
                    # Ask if user wants to add contract analysis to this session
                    add_contracts = Prompt.ask("Would you like to add smart contract analysis to this session?", choices=["yes", "no"], default="no")
                    if add_contracts == "yes":
                        # Prompt for smart contract directory
                        directory = Prompt.ask("Enter the path to the smart contracts directory")
                        if not os.path.isdir(directory):
                            console.print("The provided directory does not exist.")
                        else:
                            # Process contract files
                            contract_files = [os.path.join(directory, f) for f in os.listdir(directory)
                                            if os.path.isfile(os.path.join(directory, f)) and f.endswith(".sol")]
                            if not contract_files:
                                console.print("No smart contract files (.sol) found in the directory.")
                            else:
                                # Process contracts in parallel
                                asyncio.run(process_contracts_parallel(contract_files, output_folder))
                                console.print("Analysis complete for all contracts in the directory.")
                            
                    # Go to the session menu
                    session_menu(output_folder)
                
            elif choice == "3":
                browse_sessions()
            elif choice == "4":
                browse_documents()
            elif choice == "5":
                cache.clear()
                console.print("[green]Cache cleared successfully![/green]")
            elif choice == "6":
                console.print("Exiting. Goodbye!")
                break
            else:
                console.print("Invalid choice. Try again.")
    finally:
        # Clean up
        asyncio.run(close_async_session())
        cache.close()

if __name__ == "__main__":
    main()
