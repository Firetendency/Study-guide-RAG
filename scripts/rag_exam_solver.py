import google.generativeai as genai
import chromadb
import os
import sys
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv

# --- Configuration ---
# ChromaDB configuration (MUST match indexer)
CHROMA_PATH = "chroma_db_vision"
COLLECTION_NAME = "study_material_vision_v1"
# Embedding model (MUST match indexer)
EMBEDDING_MODEL_NAME = 'models/text-embedding-004'
# Generative model for synthesis
GENERATIVE_MODEL_NAME = 'gemini-1.5-pro-latest' # Or 'gemini-1.5-pro-latest' for potentially higher quality
# Number of relevant chunks to retrieve
N_RESULTS = 10
# Default output directory
DEFAULT_OUTPUT_DIR = "exam_solutions_rag"

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

def connect_to_chroma(chroma_path, collection_name):
    """Connects to an existing ChromaDB collection."""
    try:
        print(f"Connecting to ChromaDB client at: {chroma_path}")
        chroma_client = chromadb.PersistentClient(path=chroma_path)
        print(f"Getting collection: {collection_name}")
        collection = chroma_client.get_collection(name=collection_name)
        print(f"Successfully connected to collection '{collection_name}'.")
        return collection
    except Exception as e:
        print(f"Error connecting to ChromaDB collection '{collection_name}': {e}")
        print("Please ensure the indexer script has run successfully and the path is correct.")
        sys.exit(1)

def embed_query(query_text):
    """Embeds the user's query using the specified embedding model."""
    try:
        print(f"Embedding query using {EMBEDDING_MODEL_NAME}...")
        result = genai.embed_content(
            model=EMBEDDING_MODEL_NAME,
            content=query_text,
            task_type="RETRIEVAL_QUERY" # Use RETRIEVAL_QUERY type for querying
        )
        print("Query embedding successful.")
        return result['embedding']
    except Exception as e:
        print(f"Error embedding query: {e}")
        sys.exit(1)

def query_chroma(collection, query_embedding, n_results=N_RESULTS):
    """Queries the ChromaDB collection for relevant documents."""
    try:
        print(f"Querying ChromaDB for {n_results} relevant documents...")
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=['documents', 'metadatas', 'distances'] # Include metadata and distances
        )
        print(f"Found {len(results.get('ids', [[]])[0])} results.")
        return results
    except Exception as e:
        print(f"Error querying ChromaDB: {e}")
        return None # Return None to indicate failure

def format_retrieved_context(results):
    """Formats the retrieved documents and metadata into a string for the LLM prompt."""
    context_str = ""
    if not results or not results.get('ids', [[]])[0]:
        return "No relevant context found in the knowledge base."

    # Accessing the first list because we query with one embedding
    ids = results['ids'][0]
    documents = results['documents'][0]
    metadatas = results['metadatas'][0]
    distances = results['distances'][0]

    context_str += "Retrieved Context from Study Material:\n\n"
    for i, (doc, meta, dist) in enumerate(zip(documents, metadatas, distances)):
        context_str += f"--- Context Chunk {i+1} (Source: {meta.get('source_file', 'N/A')}, Page: {meta.get('source_page', 'N/A')}, Distance: {dist:.4f}) ---\n"
        context_str += f"Text Content:\n{doc}\n\n"

        # Add descriptions from metadata if they exist
        visual_desc = meta.get('visual_descriptions')
        table_desc = meta.get('table_descriptions')
        equation_desc = meta.get('equation_descriptions')

        if visual_desc and visual_desc.strip():
             context_str += f"Visual Elements Description (from page {meta.get('source_page', 'N/A')}):\n{visual_desc}\n\n"
        if table_desc and table_desc.strip():
             context_str += f"Table Content Summary (from page {meta.get('source_page', 'N/A')}):\n{table_desc}\n\n"
        if equation_desc and equation_desc.strip():
             context_str += f"Key Equations (from page {meta.get('source_page', 'N/A')}):\n{equation_desc}\n\n"
        context_str += "---\n\n"

    return context_str.strip()


def construct_rag_prompt(exam_query, formatted_context):
    """Constructs the prompt for the generative LLM."""

    prompt = f"""You are an expert teaching assistant. Your task is to provide a clear, step-by-step explanation or guide to solve the following exam problem/topic, using ONLY the provided context from the study materials.

Exam Problem/Topic:
\"\"\"
{exam_query}
\"\"\"

Relevant Context from Study Materials:
\"\"\"
{formatted_context}
\"\"\"

Instructions:
1.  Analyze the exam problem/topic.
2.  Synthesize an explanation or a step-by-step guide using the provided context.
3.  Explicitly reference the source file and page number (e.g., "According to Lecture 3, Page 5...") when using information from the context.
4.  If the context includes descriptions of visuals, tables, or equations relevant to the problem, incorporate them into your explanation.
5.  If the context does not contain sufficient information to fully answer, clearly state what information is missing or cannot be determined from the provided text. DO NOT MAKE UP INFORMATION.
6.  Structure the answer clearly (e.g., use bullet points, numbered steps).

Provide your explanation below:
"""
    return prompt

def generate_explanation(prompt):
    """Generates the explanation using the generative LLM."""
    try:
        print(f"Generating explanation using {GENERATIVE_MODEL_NAME}...")
        model = genai.GenerativeModel(GENERATIVE_MODEL_NAME)
        # Adjust safety settings and generation config as needed
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                # candidate_count=1, # Defaults to 1
                # stop_sequences=['...'],
                # max_output_tokens=...,
                temperature=0.7 # Adjust for creativity vs factualness
            ),
            # safety_settings=... # Consider adjusting if responses are blocked
            )
        print("Explanation generation successful.")
        # Accessing the text part of the response
        if hasattr(response, 'text'):
            return response.text
        elif hasattr(response, 'parts') and response.parts:
             # Handle potential multi-part responses if necessary
            return "".join(part.text for part in response.parts if hasattr(part, 'text'))
        else:
            # Fallback for unexpected response structure
            print("Warning: Could not extract text from LLM response object.")
            print(f"Full Response: {response}") # Log the full response for debugging
            # Try accessing candidate text if available
            if response.candidates and hasattr(response.candidates[0].content, 'parts') and response.candidates[0].content.parts:
                return "".join(part.text for part in response.candidates[0].content.parts if hasattr(part, 'text'))
            return "Error: Could not extract explanation from the response."

    except Exception as e:
        print(f"Error during explanation generation: {e}")
        # Print response details if available, even in case of error
        if 'response' in locals():
             print(f"Response object before error (may be partial): {response}")
        return f"Error generating explanation: {e}"

def save_output(output_dir, query, context, explanation):
    """Saves the query, context, and explanation to a JSON file."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Create a filename based on the query (simple sanitization)
    safe_filename = "".join(c if c.isalnum() else "_" for c in query[:50]).strip("_")
    if not safe_filename:
        safe_filename = "rag_output"
    output_file = output_path / f"{safe_filename}.json"

    output_data = {
        "query": query,
        "retrieved_context": context, # The formatted context string
        "generated_explanation": explanation
    }

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
        print(f"Successfully saved output to: {output_file}")
    except Exception as e:
        print(f"Error saving output to JSON file: {e}")

# --- Main Execution Entry Point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Use RAG to generate explanations for exam problems based on indexed study material.")
    parser.add_argument("query", help="The exam problem description or topic keywords.")
    parser.add_argument("--chroma_path", default=CHROMA_PATH, help=f"Path to the ChromaDB database directory (default: {CHROMA_PATH}).")
    parser.add_argument("--collection_name", default=COLLECTION_NAME, help=f"ChromaDB collection name (default: {COLLECTION_NAME}).")
    parser.add_argument("--n_results", type=int, default=N_RESULTS, help=f"Number of relevant chunks to retrieve (default: {N_RESULTS}).")
    parser.add_argument("--output_dir", default=DEFAULT_OUTPUT_DIR, help=f"Directory to save the JSON output (default: {DEFAULT_OUTPUT_DIR}).")
    parser.add_argument("--no_save", action="store_true", help="Print the explanation to console instead of saving to a file.")

    args = parser.parse_args()

    # 1. Configure API
    configure_api()

    # 2. Connect to ChromaDB
    collection = connect_to_chroma(args.chroma_path, args.collection_name)

    # 3. Embed Query
    query_embedding = embed_query(args.query)

    # 4. Query ChromaDB
    retrieved_results = query_chroma(collection, query_embedding, n_results=args.n_results)

    # 5. Format Context
    formatted_context = format_retrieved_context(retrieved_results)

    # 6. Construct Prompt
    rag_prompt = construct_rag_prompt(args.query, formatted_context)

    # 7. Generate Explanation
    explanation = generate_explanation(rag_prompt)

    # 8. Output/Save Result
    if args.no_save:
        print("\n--- Generated Explanation ---")
        print(explanation)
        print("\n--- Retrieved Context ---")
        print(formatted_context) # Also print context for console output
    else:
        save_output(args.output_dir, args.query, formatted_context, explanation)
        # Optionally print a shorter confirmation or the beginning of the explanation
        print("\n--- Generated Explanation (saved to file) ---")
        print(explanation[:500] + "..." if len(explanation) > 500 else explanation)


    print("\nScript finished.") 