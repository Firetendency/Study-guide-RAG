import google.generativeai as genai
import chromadb
import os
import sys
import argparse
import asyncio
import re
import uuid
from pathlib import Path
from dotenv import load_dotenv
import time

# --- Configuration ---
# Directory containing the structured Markdown files from process_pdfs_vision.py
DEFAULT_INPUT_DIR = 'processed_markdown_vision'
# ChromaDB configuration
CHROMA_PATH = "chroma_db_vision" # Use a NEW directory for this index
COLLECTION_NAME = "study_material_vision_v1" # New collection name
# Embedding model (MUST match future query model)
EMBEDDING_MODEL_NAME = 'models/text-embedding-004'
# Chunking strategy
CHUNK_SIZE = 1000 # Target characters per chunk
CHUNK_OVERLAP = 200 # Characters overlap between chunks
# Batch size for adding to ChromaDB
CHROMA_BATCH_SIZE = 100
# Concurrency limit for embedding calls
MAX_CONCURRENT_EMBEDDINGS = 10

# --- Functions ---

def configure_api():
    """Loads API key from .env and configures the Gemini API."""
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("Error: GOOGLE_API_KEY environment variable not set or .env file missing key.")
        sys.exit(1)
    try:
        genai.configure(api_key=api_key)
        print("Successfully configured the Google Generative AI API.")
    except Exception as e:
        print(f"Error configuring the Google Generative AI API: {e}")
        sys.exit(1)

def recursive_character_text_splitter(text, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP):
    """Simple recursive character splitter (conceptual implementation)."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start_index = 0
    while start_index < len(text):
        end_index = min(start_index + chunk_size, len(text))
        chunks.append(text[start_index:end_index])
        # Move start index for the next chunk, considering overlap
        start_index += chunk_size - chunk_overlap
        # Ensure we don't get stuck if overlap is too large or chunk size too small
        if start_index >= end_index:
             # This might happen if chunk_size <= chunk_overlap. Advance by a minimal amount.
             # Or if the last chunk was exactly chunk_size.
             # A more robust splitter would handle separators, but this is basic.
             if start_index < len(text): # Avoid infinite loop if already at end
                 start_index = end_index # Just start next chunk after the previous one ends
             else:
                 break # We've processed the whole text

    # Filter out potential empty strings if logic leads to them
    return [chunk for chunk in chunks if chunk.strip()]


def parse_vision_markdown_page(page_content):
    """Extracts main text and structured descriptions from a single page's Markdown."""
    data = {
        "main_text": "",
        "visual_descriptions": "",
        "table_descriptions": "",
        "equation_descriptions": ""
    }

    # Try to find the main text (content before known structured headings)
    # This regex looks for the start up to the first H4 heading or end of string
    main_text_match = re.search(r'(.*?)(?=\n#### |\Z)', page_content, re.DOTALL | re.IGNORECASE)
    if main_text_match:
        data["main_text"] = main_text_match.group(1).strip()
    else:
         # Fallback if no H4 headings found, assume all is main text initially
        data["main_text"] = page_content.strip()


    # Extract content under specific headings
    headings_map = {
        "Visual Elements Description": "visual_descriptions",
        "Table Content": "table_descriptions",
        "Key Equations": "equation_descriptions"
    }

    for heading_text, key in headings_map.items():
        # Regex: Find '#### Heading Text\n' followed by content until next '####' or end of string
        pattern = rf'\n#### {re.escape(heading_text)}\s*\n(.*?)(?=\n#### |\Z)'
        match = re.search(pattern, page_content, re.DOTALL | re.IGNORECASE)
        if match:
            data[key] = match.group(1).strip()
            # Optional: Remove this section from main_text if it was incorrectly included initially
            # (Might be safer to leave main_text broad, as RAG can filter)

    return data

async def embed_batch(batch_texts, semaphore):
    """Embeds a batch of text chunks asynchronously."""
    async with semaphore:
        print(f"  Embedding batch of {len(batch_texts)} chunks...")
        try:
            result = await genai.embed_content_async(
                model=EMBEDDING_MODEL_NAME,
                content=batch_texts,
                task_type="RETRIEVAL_DOCUMENT"
            )
            print(f"  Successfully embedded batch.")
            return result['embedding']
        except Exception as e:
            print(f"  Error embedding batch: {e}")
            # Return None or empty list to signal failure for this batch
            return None

async def process_md_file_async(md_path, semaphore):
    """Reads, parses, chunks, and prepares data for a single MD file for indexing."""
    print(f"Processing file: {md_path}")
    results_for_chroma = [] # List to hold tuples (id, embedding, doc, metadata)
    pdf_name = md_path.stem.replace('_vision_processed', '') # Infer PDF name

    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split content by page comments
        pages = re.split(r'\n\n<!-- Page \d+ -->\n\n', content)
        # The first split part might be before the first comment (e.g., the title)
        # The actual page content starts from index 1 if the split works as expected
        
        current_page_num = 0 # Start page numbering from 1 potentially
        
        page_data_list = [] # Holds (text_to_chunk, page_num, descriptions) tuples

        for page_content in pages:
            if page_content.strip():
                 # Check if this chunk contains the initial document title
                 is_initial_chunk = pdf_name in page_content.splitlines()[0] if page_content else False
                 
                 # A simple heuristic: if it's not the initial title chunk, assign page number
                 # More robust: Parse page number from comment if needed, but splitting removes comments
                 if not is_initial_chunk:
                     current_page_num += 1 # Increment for actual page content
                 
                 page_num_for_meta = current_page_num if current_page_num > 0 else 1 # Assign 1 if it seems like title page

                 parsed_data = parse_vision_markdown_page(page_content)
                 main_text = parsed_data["main_text"]
                 
                 if main_text:
                     # Store descriptions together for easier metadata handling
                     descriptions = {
                         "visual": parsed_data["visual_descriptions"],
                         "table": parsed_data["table_descriptions"],
                         "equation": parsed_data["equation_descriptions"]
                     }
                     page_data_list.append((main_text, page_num_for_meta, descriptions))

        # Now chunk and prepare embedding tasks for all pages of this file
        all_chunks_for_file = []
        all_metadata_for_file = []

        for text_to_chunk, page_num, descriptions in page_data_list:
            chunks = recursive_character_text_splitter(text_to_chunk)
            for chunk_text in chunks:
                all_chunks_for_file.append(chunk_text)
                metadata = {
                    "source_file": pdf_name,
                    "source_page": page_num,
                    "visual_descriptions": descriptions["visual"],
                    "table_descriptions": descriptions["table"],
                    "equation_descriptions": descriptions["equation"]
                }
                all_metadata_for_file.append(metadata)

        # Embed all chunks from this file in batches
        if all_chunks_for_file:
            print(f"  File {pdf_name}: Preparing to embed {len(all_chunks_for_file)} chunks...")
            embedding_tasks = []
            for i in range(0, len(all_chunks_for_file), CHROMA_BATCH_SIZE):
                 batch_texts = all_chunks_for_file[i : i + CHROMA_BATCH_SIZE]
                 embedding_tasks.append(embed_batch(batch_texts, semaphore))

            embedding_results = await asyncio.gather(*embedding_tasks)

            # Combine results, handling potential errors
            all_embeddings_for_file = []
            successful_embedding = True
            for batch_embeddings in embedding_results:
                if batch_embeddings is None:
                    print(f"  ERROR: Failed to embed a batch for {pdf_name}. Skipping file.")
                    successful_embedding = False
                    break
                all_embeddings_for_file.extend(batch_embeddings)

            if successful_embedding and len(all_embeddings_for_file) == len(all_chunks_for_file):
                # Prepare data for ChromaDB addition
                for i in range(len(all_chunks_for_file)):
                    chunk_id = str(uuid.uuid4())
                    results_for_chroma.append((
                        chunk_id,
                        all_embeddings_for_file[i],
                        all_chunks_for_file[i],
                        all_metadata_for_file[i]
                    ))
            elif successful_embedding:
                 print(f"  ERROR: Mismatch in embedding count for {pdf_name}. Expected {len(all_chunks_for_file)}, got {len(all_embeddings_for_file)}. Skipping file.")
            
        return results_for_chroma # Return list of (id, embedding, doc, metadata)

    except Exception as e:
        print(f"Error processing file {md_path}: {e}")
        return [] # Return empty list on file processing error


async def main_async(args):
    # 1. Configure API
    configure_api()

    # 2. Initialize ChromaDB
    try:
        print(f"Initializing ChromaDB client at: {args.chroma_path}")
        chroma_client = chromadb.PersistentClient(path=args.chroma_path)
        print(f"Getting or creating collection: {args.collection_name}")
        # Specify embedding function if needed, or rely on adding embeddings directly
        collection = chroma_client.get_or_create_collection(name=args.collection_name)
        print(f"Collection '{args.collection_name}' ready.")
    except Exception as e:
        print(f"Error initializing ChromaDB: {e}")
        sys.exit(1)

    # 3. Discover Markdown Files
    input_dir_path = Path(args.input_dir)
    if not input_dir_path.is_dir():
        print(f"Error: Input directory not found at '{input_dir_path}'")
        sys.exit(1)

    print(f"Scanning for *_vision_processed.md files in '{input_dir_path}' recursively...")
    md_files = sorted(list(input_dir_path.rglob("*_vision_processed.md")))

    if not md_files:
        print("No processed Markdown files found.")
        sys.exit(0)
    print(f"Found {len(md_files)} files to index.")

    # 4. Create Concurrent Tasks for Files
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_EMBEDDINGS)
    tasks = [process_md_file_async(md_path, semaphore) for md_path in md_files]

    # 5. Run File Processing Tasks Concurrently
    print(f"\n--- Running {len(tasks)} File Processing Tasks Concurrently (Embedding concurrency: {MAX_CONCURRENT_EMBEDDINGS}) ---")
    start_time = time.time()
    results_list = await asyncio.gather(*tasks) # Returns list of lists of tuples
    end_time = time.time()
    print(f"--- All File Processing Tasks Completed in {end_time - start_time:.2f} seconds ---")

    # 6. Collect all data for ChromaDB
    all_ids = []
    all_embeddings = []
    all_documents = []
    all_metadatas = []

    print("\nCollecting results for indexing...")
    for file_results in results_list:
        for chunk_id, embedding, doc, metadata in file_results:
            all_ids.append(chunk_id)
            all_embeddings.append(embedding)
            all_documents.append(doc)
            all_metadatas.append(metadata)
    
    total_chunks = len(all_ids)
    print(f"Prepared {total_chunks} total chunks for indexing.")

    # 7. Add to ChromaDB in Batches (Synchronously)
    if total_chunks > 0:
        print(f"\nAdding {total_chunks} chunks to ChromaDB in batches of {CHROMA_BATCH_SIZE}...")
        added_count = 0
        try:
            for i in range(0, total_chunks, CHROMA_BATCH_SIZE):
                batch_end = min(i + CHROMA_BATCH_SIZE, total_chunks)
                print(f"  Adding batch {i // CHROMA_BATCH_SIZE + 1} ({i+1}-{batch_end})...")
                collection.add(
                    ids=all_ids[i:batch_end],
                    embeddings=all_embeddings[i:batch_end],
                    documents=all_documents[i:batch_end],
                    metadatas=all_metadatas[i:batch_end]
                )
                added_count += (batch_end - i)
            print(f"\nSuccessfully added {added_count} chunks to ChromaDB collection '{args.collection_name}'.")
        except Exception as e:
            print(f"\nERROR during ChromaDB add operation: {e}")
            print(f"  Only {added_count} chunks might have been added before the error.")
    else:
        print("No chunks were generated to add to ChromaDB.")


# --- Main Execution Entry Point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Index vision-processed Markdown files into ChromaDB for RAG.")
    parser.add_argument("--input_dir", default=DEFAULT_INPUT_DIR, help=f"Directory containing *_vision_processed.md files (default: {DEFAULT_INPUT_DIR}).")
    parser.add_argument("--chroma_path", default=CHROMA_PATH, help=f"Path for ChromaDB persistence (default: {CHROMA_PATH}).")
    parser.add_argument("--collection_name", default=COLLECTION_NAME, help=f"ChromaDB collection name (default: {COLLECTION_NAME}).")
    args = parser.parse_args()

    # Run the async main function
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")

    print("\nScript finished.") 