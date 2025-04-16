# Enhanced Study Guide Generation using Vision RAG

## Problem & Motivation

Technical courses often rely heavily on dense slide decks (PDFs) as primary learning material. These materials can be difficult to navigate, lack comprehensive explanations, and may not adequately connect concepts to practical application, especially when preparing for exams that test deeper understanding. The limited structure and explanation within the source PDFs makes traditional studying and information retrieval challenging. 

This project addresses these limitations by leveraging Large Language Models (LLMs) and Retrieval-Augmented Generation (RAG) to:

1.  **Extract & Structure:** Process disorganized PDF lecture notes, exercises, and past exams into a more structured format.
2.  **Deep Contextualization:** Go beyond simple text extraction by using a multimodal vision model (Gemini 1.5 Pro) to analyze entire PDF pages as images, capturing text content *and* descriptions of visual elements (diagrams, charts, tables, equations).
3.  **Intelligent Organization:** Use an LLM to analyze dependencies between exam topics and structure them into a logical learning path.
4.  **Augmented Generation:** Create a comprehensive study guide that **retrieves** relevant information (including visual context) from the processed documents and **augments** it with the broader knowledge base of Gemini 1.5 Pro to fill gaps and provide richer explanations, specifically tailored to the defined exam topics.

## Features

- **Vision-Based PDF Processing:** Uses `gemini-1.5-pro` to analyze rendered PDF pages, extracting text and generating descriptions for visual elements.
- **Vector Indexing (RAG Core):** Chunks the processed text and stores it along with descriptive metadata (source file, page, visual descriptions) in a ChromaDB vector database for efficient retrieval.
- **LLM-Powered Topic Structuring:** Analyzes a list of exam topics to determine dependencies and suggests a logical study order.
- **Augmented Study Guide Generation:** For each structured topic:
    - Retrieves the most relevant text chunks and associated visual/table/equation descriptions from the ChromaDB index.
    - Instructs `gemini-1.5-pro` to first explain the topic based *only* on the retrieved context, citing sources.
    - Prompts the LLM to identify gaps in the retrieved context and supplement the explanation using its internal knowledge base, clearly differentiating the two sources.
- **Query Interface:** Allows asking specific questions against the indexed knowledge base.
- **Asynchronous Processing:** Utilizes `asyncio` for faster API calls during guide generation.

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd <repository-directory>
    ```
2.  **Create a virtual environment (Recommended):**
    ```bash
    python -m venv venv
    # Activate (Windows PowerShell)
    .\venv\Scripts\Activate.ps1
    # Activate (Bash/Zsh)
    # source venv/bin/activate
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Create `.env` file:** Create a file named `.env` in the project root directory and add your Google API key:
    ```
    GOOGLE_API_KEY="YOUR_API_KEY_HERE"
    ```
5.  **Prepare Input Data:**
    *   Place your source PDF files into relevant directories (e.g., `Documents/Lectures`, `Documents/Exercises`, `Documents/Past Years`). The specific paths are passed as arguments to the processing script.
    *   Ensure you have an `exam_topics.json` file in the root directory containing a JSON list of topics you want the guide to cover.

## Workflow

Run the scripts in the following order from the project root directory:

1.  **Process PDFs into Structured Markdown (`scripts/process_pdfs_vision.py`):**
    *   Renders each PDF page as an image and sends it to the vision model for analysis (text + visual description).
    *   Saves structured markdown files (one per PDF) to the output directory.
    *   Adjust directory paths as needed.
    ```bash
    python scripts/process_pdfs_vision.py --lecture_dir "Documents/Lectures" --exercise_dir "Documents/Exercises" --exam_dir "Documents/Past Years" --output_dir "processed_markdown_vision"
    ```

2.  **Index Markdown into ChromaDB (`scripts/indexer_vision.py`):**
    *   Reads the generated markdown files.
    *   Chunks the text content.
    *   Stores text chunks and associated metadata (source file, page, visual/table/equation descriptions) in the ChromaDB vector store (`chroma_db_vision`).
    ```bash
    python scripts/indexer_vision.py --input_dir "processed_markdown_vision" --chroma_path "chroma_db_vision"
    ```

3.  **Structure Exam Topics (`scripts/structure_exam_topics.py`, Optional but Recommended):**
    *   Uses an LLM to reorder topics from `exam_topics.json` into a more logical learning flow based on dependencies and difficulty.
    *   Saves the ordered list to `exam_topics_structured.json`.
    ```bash
    python scripts/structure_exam_topics.py --input_file "exam_topics.json" --output_file "exam_topics_structured.json"
    ```

4.  **Create the Augmented Study Guide (`scripts/create_exam_guide.py`):**
    *   Reads the (structured) topics list.
    *   For each topic, performs **RAG**: queries ChromaDB for relevant context (text + metadata).
    *   Instructs the LLM to generate an explanation, first using the **Retrieved** context, then **Augmenting** with general knowledge where needed.
    *   Saves the final guide as a Markdown file.
    ```bash
    # Uses exam_topics_structured.json by default if it exists
    python scripts/create_exam_guide.py --output_file "Exam_Guide_Structured_Async.md"
    ```
    *   *Alternatively, to use the original topic ordering:* 
        ```bash
        python scripts/create_exam_guide.py --topics_file "exam_topics.json" --output_file "Exam_Guide_Unstructured_Async.md"
        ```

5.  **Query the Index (`scripts/rag_exam_solver.py`, Optional):**
    *   Ask specific questions directly against the indexed knowledge base.
    ```bash
    python scripts/rag_exam_solver.py "Your specific question about the material?"
    ```
    *   Add `--no_save` to print the result to the console.

## Limitations

- **No Direct Image Extraction:** This pipeline does *not* extract images as separate files. Instead, it relies on the LLM's *description* of visual elements based on the rendered page image. The quality of these descriptions depends on the LLM's capabilities and the clarity of the source PDF page.
- **Cost and Complexity:** Using a powerful vision model like Gemini 1.5 Pro for every page is computationally more intensive and costly than simple text extraction. Rendering pages also adds processing time.
- **Handling of Poorly Structured PDFs:** While the vision approach is more robust than methods relying solely on PDF structure parsing, extremely messy or unconventional slide layouts (e.g., heavy use of vector graphics misinterpreted as text, overlapping elements) can still challenge the LLM's analysis and description generation.
- **Structuring Dependency:** The quality of the topic structuring in `structure_exam_topics.py` depends entirely on the LLM's understanding of the topic relationships. Reviewing the `exam_topics_structured.json` file is recommended.
- **Data Cleaning and Repeation:** The final output has some repetitions but these were very minimum for the specific course that I have used, so I didn't bother refining it.
- **Hallucination Risk:** While the RAG approach grounds the LLM in provided context, there's always a possibility the LLM might misunderstand the context or hallucinate during the augmentation step. The prompt attempts to mitigate this by asking the LLM to clearly separate retrieved vs. general knowledge.

## Scripts Overview

Located in the `scripts/` directory:

*   `process_pdfs_vision.py`: PDF processing using vision model.
*   `indexer_vision.py`: Indexes processed markdown into ChromaDB.
*   `structure_exam_topics.py`: Reorders topics using an LLM.
*   `create_exam_guide.py`: Generates the final augmented study guide (using structured topics by default).
*   `rag_exam_solver.py`: Answers specific queries using RAG.

## Configuration Files

*   `.env`: Stores the Google API key.
*   `requirements.txt`: Lists Python dependencies.
*   `exam_topics.json`: Input list of exam topics.
*   `exam_topics_structured.json`: Output list of LLM-structured topics. 

## Notes
**1.The sample_output.md is only a very small chunk of the actual final output as it would be put me in a legally grey area if I posted the full output.**

**2.The Project still needs a lot of refinement, particularly in areas of data cleaning and output cleaning. However, the current iteration meets my bare minimum demand to help me study better for this course.**
