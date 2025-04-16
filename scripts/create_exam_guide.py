import google.generativeai as genai
import chromadb
import os
import sys
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv
import time
import asyncio # Import asyncio

# --- Configuration ---
# Input JSON file with topics
DEFAULT_TOPICS_FILE = "exam_topics_structured.json"
# ChromaDB configuration (MUST match indexer)
CHROMA_PATH = "chroma_db_vision"
COLLECTION_NAME = "study_material_vision_v1"
# Embedding model (MUST match indexer)
EMBEDDING_MODEL_NAME = 'models/text-embedding-004'
# Generative model for synthesis (Using Pro as requested)
GENERATIVE_MODEL_NAME = 'gemini-1.5-pro-latest'
# Number of relevant chunks to retrieve
N_RESULTS = 10 # Retrieve more context for better synthesis potential
# Output Markdown file
DEFAULT_OUTPUT_FILE = "Exam_Guide_Async.md" # Changed default name slightly
# Concurrency limit for LLM generation calls
MAX_CONCURRENT_GENERATIONS = 5 # Adjust based on API limits and system resources

# --- Functions (Adapted from rag_exam_solver.py and previous version) ---

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
        # Assuming chromadb client is thread-safe enough for this async usage pattern
        # (queries happen before the main async gather)
        chroma_client = chromadb.PersistentClient(path=chroma_path)
        print(f"Getting collection: {collection_name}")
        collection = chroma_client.get_collection(name=collection_name)
        print(f"Successfully connected to collection '{collection_name}'.")
        return collection
    except Exception as e:
        print(f"Error connecting to ChromaDB collection '{collection_name}': {e}")
        print("Please ensure the indexer script has run successfully and the path is correct.")
        sys.exit(1)

async def embed_query_async(query_text):
    """Embeds the user's query asynchronously."""
    try:
        # Use the async version of embed_content
        result = await genai.embed_content_async(
            model=EMBEDDING_MODEL_NAME,
            content=query_text,
            task_type="RETRIEVAL_QUERY"
        )
        return result['embedding']
    except Exception as e:
        print(f"Error embedding query '{query_text[:50]}...': {e}")
        return None

def query_chroma(collection, query_embedding, n_results=N_RESULTS):
    """Queries the ChromaDB collection for relevant documents (remains sync)."""
    # Keep this synchronous for simplicity, as it's usually fast
    # and called sequentially before the async generation gather.
    if query_embedding is None:
        return None
    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=['documents', 'metadatas', 'distances']
        )
        return results
    except Exception as e:
        print(f"Error querying ChromaDB: {e}")
        return None

def format_retrieved_context(results):
    """Formats the retrieved documents and metadata into a string for the LLM prompt (remains sync)."""
    context_str = ""
    if not results or not results.get('ids', [[]])[0]:
        return "No relevant context found in the local knowledge base."

    ids = results['ids'][0]
    documents = results['documents'][0]
    metadatas = results['metadatas'][0]
    distances = results['distances'][0]

    context_str += "Retrieved Context from Local Study Material:\n\n"
    for i, (doc, meta, dist) in enumerate(zip(documents, metadatas, distances)):
        context_str += f"--- Context Chunk {i+1} (Source: {meta.get('source_file', 'N/A')}, Page: {meta.get('source_page', 'N/A')}, Distance: {dist:.4f}) ---\n"
        context_str += f"Text Content:\n{doc}\n\n"
        visual_desc = meta.get('visual_descriptions')
        table_desc = meta.get('table_descriptions')
        equation_desc = meta.get('equation_descriptions')
        if visual_desc and visual_desc.strip():
             context_str += f"*Visual Elements Description (from page {meta.get('source_page', 'N/A')}):*\n{visual_desc}\n\n"
        if table_desc and table_desc.strip():
             context_str += f"*Table Content Summary (from page {meta.get('source_page', 'N/A')}):*\n{table_desc}\n\n"
        if equation_desc and equation_desc.strip():
             context_str += f"*Key Equations (from page {meta.get('source_page', 'N/A')}):*\n{equation_desc}\n\n"
        context_str += "---\n\n"
    return context_str.strip()

def load_topics(topics_file):
    """Loads exam topics from a JSON file (remains sync)."""
    try:
        with open(topics_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list): topics = data
            elif isinstance(data, dict) and 'topics' in data and isinstance(data['topics'], list): topics = data['topics']
            else:
                print(f"Error: Expected JSON file '{topics_file}' to contain a list of topics (or a dict with a 'topics' key).")
                sys.exit(1)
            if not topics: print(f"Warning: No topics found in '{topics_file}'.")
            else: print(f"Loaded {len(topics)} topics from '{topics_file}'.")
            return topics
    except FileNotFoundError: print(f"Error: Topics file not found at '{topics_file}'"); sys.exit(1)
    except json.JSONDecodeError: print(f"Error: Could not decode JSON from '{topics_file}'."); sys.exit(1)
    except Exception as e: print(f"An unexpected error occurred loading topics: {e}"); sys.exit(1)

def construct_augmented_rag_prompt(exam_topic, formatted_context):
    """Constructs the prompt for the generative LLM, requesting augmentation (remains sync)."""
    prompt = f"""You are an expert teaching assistant creating a comprehensive study guide. Your task is to explain the following exam topic thoroughly, combining information from provided local documents with your own general knowledge.

Exam Topic:
\"\"\"
{exam_topic}
\"\"\"

Relevant Context Retrieved from Local Study Materials:
\"\"\"
{formatted_context}
\"\"\"

Instructions:
1.  **Analyze the Exam Topic:** Understand the core concepts required.
2.  **Explain using Local Context:** First, synthesize a clear explanation based *ONLY* on the "Relevant Context Retrieved from Local Study Materials" provided above.
    *   Explicitly reference the source file and page number (e.g., "According to Lecture 3, Page 5...") when using this information.
    *   Incorporate any relevant visual, table, or equation descriptions found in the local context.
    *   Use clear Markdown formatting (headings, lists, bolding, code blocks if applicable).
3.  **Identify Gaps:** After explaining based on local context, explicitly state if the provided local context is insufficient to fully cover the topic or if certain aspects are missing. Mention what these gaps are. If the context *is* sufficient, state that.
4.  **Augment with General Knowledge:** If you identified gaps OR if you believe your general knowledge can provide significant valuable additions (like broader context, alternative approaches, real-world examples, or deeper insights not present in the local documents), add a section titled "### Additional Information (General Knowledge)".
    *   In this section, use your own knowledge base to fill the identified gaps or provide the supplementary information.
    *   **Crucially, make it clear that this additional information comes from your general knowledge and not the specific local documents.**
5.  **Final Formatting:** Ensure the entire explanation for the topic is well-structured, uses excellent Markdown formatting, and is easy to read and understand. Start the explanation for the topic with a Level 2 Markdown heading (e.g., `## Topic Name`).

Provide the complete, well-formatted explanation for the exam topic below:
"""
    return prompt


async def generate_guide_explanation_async(prompt, topic_name, semaphore):
    """Generates the explanation for a single topic asynchronously using the generative LLM."""
    async with semaphore: # Acquire semaphore before making API call
        print(f"  Generating explanation for topic: '{topic_name[:60]}...'")
        # No explicit sleep needed, semaphore manages concurrency.
        try:
            model = genai.GenerativeModel(GENERATIVE_MODEL_NAME)
            # Use generate_content_async
            response = await model.generate_content_async(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.6),
                safety_settings={
                     'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_ONLY_HIGH',
                     'HARM_CATEGORY_HARASSMENT': 'BLOCK_ONLY_HIGH',
                     'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_ONLY_HIGH',
                     'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_ONLY_HIGH',
                }
            )
            print(f"  Successfully generated explanation for '{topic_name[:60]}...'")

            # Text extraction logic (same as before)
            if hasattr(response, 'text'): return response.text
            elif hasattr(response, 'parts') and response.parts: return "".join(part.text for part in response.parts if hasattr(part, 'text'))
            elif response.candidates and hasattr(response.candidates[0].content, 'parts') and response.candidates[0].content.parts: return "".join(part.text for part in response.candidates[0].content.parts if hasattr(part, 'text'))
            else:
                 print(f"Warning: Could not extract text from LLM response object for topic '{topic_name}'.")
                 print(f"Full Response: {response}")
                 return f"\n\n*Error: Could not generate explanation for this topic due to response format issues.*\n\n"

        except Exception as e:
            print(f"  Error generating explanation for topic '{topic_name}': {e}")
            error_message = f"\n\n*Error generating explanation for this topic: {e}*"
            # Check for prompt feedback even in async errors
            if 'response' in locals() and hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                 error_message += f"\n*Prompt Feedback: {response.prompt_feedback}*"
            error_message += "\n\n"
            return error_message


def save_guide(guide_content, output_file):
    """Saves the compiled guide content to a Markdown file (remains sync)."""
    try:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f: f.write(guide_content)
        print(f"\nSuccessfully saved exam guide to: {output_path}")
    except Exception as e: print(f"\nError saving exam guide to '{output_file}': {e}")

# --- Async Main Function ---
async def main_async(args):
    # 1. Configure API (Sync)
    configure_api()

    # 2. Load Topics (Sync)
    topics = load_topics(args.topics_file)
    if not topics: print("Exiting as no topics were loaded."); sys.exit(0)

    # 3. Connect to ChromaDB (Sync)
    collection = connect_to_chroma(args.chroma_path, args.collection_name)

    # 4. Prepare Generation Tasks
    generation_tasks = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_GENERATIONS)
    processed_topics_count = 0

    print(f"\n--- Preparing to Generate Explanations for {len(topics)} Topics (Concurrency: {MAX_CONCURRENT_GENERATIONS}) ---")
    start_prep_time = time.time()

    # Embed and Query Sequentially (or could batch embeddings async later if needed)
    topic_data_for_generation = [] # Store (topic, prompt) tuples
    for i, topic in enumerate(topics):
        print(f"  Preparing topic {i+1}/{len(topics)}: '{topic[:60]}...'")
        query_embedding = await embed_query_async(topic) # Embed async
        if query_embedding is None:
             topic_data_for_generation.append((topic, None)) # Mark as failed embedding
             continue

        retrieved_results = query_chroma(collection, query_embedding, n_results=args.n_results) # Query sync
        formatted_context = format_retrieved_context(retrieved_results)
        augmented_prompt = construct_augmented_rag_prompt(topic, formatted_context)
        topic_data_for_generation.append((topic, augmented_prompt))

    prep_end_time = time.time()
    print(f"--- Finished Preparing Prompts in {prep_end_time - start_prep_time:.2f} seconds ---")

    # Create async tasks for generation
    print(f"\n--- Starting Concurrent Generation ---")
    start_gen_time = time.time()
    for topic, prompt in topic_data_for_generation:
        if prompt is None: # Handle embedding failures
             # Create a dummy task that returns an error message
             async def error_task(t=topic): return f"## {t}\n\n*Error: Could not embed this topic query. Skipping.*\n\n"
             generation_tasks.append(error_task())
        else:
             generation_tasks.append(generate_guide_explanation_async(prompt, topic, semaphore))

    # 5. Run Generation Tasks Concurrently
    explanations = await asyncio.gather(*generation_tasks)
    gen_end_time = time.time()
    print(f"--- Finished Generating Explanations in {gen_end_time - start_gen_time:.2f} seconds ---")


    # 6. Compile and Save Guide (Sync)
    guide_sections = [f"# Exam Study Guide\n\nThis guide covers key topics based on local study materials and augmented with general knowledge.\n\n"]
    guide_sections.extend([f"{exp}\n\n---\n" for exp in explanations]) # Add explanations and separators

    final_guide_content = "\n".join(guide_sections)
    save_guide(final_guide_content, args.output_file)

# --- Main Execution Entry Point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate an augmented study guide from exam topics using RAG and general LLM knowledge (Async).")
    parser.add_argument("--topics_file", default=DEFAULT_TOPICS_FILE, help=f"Path to the JSON file containing structured exam topics (default: {DEFAULT_TOPICS_FILE}).")
    parser.add_argument("--chroma_path", default=CHROMA_PATH, help=f"Path to the ChromaDB database directory (default: {CHROMA_PATH}).")
    parser.add_argument("--collection_name", default=COLLECTION_NAME, help=f"ChromaDB collection name (default: {COLLECTION_NAME}).")
    parser.add_argument("--n_results", type=int, default=N_RESULTS, help=f"Number of relevant chunks to retrieve (default: {N_RESULTS}).")
    parser.add_argument("--output_file", default=DEFAULT_OUTPUT_FILE, help=f"Path to save the final Markdown exam guide (default: {DEFAULT_OUTPUT_FILE}).")
    parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENT_GENERATIONS, help=f"Max concurrent LLM generation calls (default: {MAX_CONCURRENT_GENERATIONS}).")

    args = parser.parse_args()
    # Update global concurrency limit if provided via args
    MAX_CONCURRENT_GENERATIONS = args.concurrency

    # Run the async main function
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")

    print("\nScript finished.") 