import google.generativeai as genai
import os
import sys
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv

# --- Constants ---
# Model for analyzing the exam summaries
ANALYSIS_MODEL_NAME = 'gemini-1.5-flash-latest'

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

def extract_topics_from_summary(model, summary_content):
    """Uses the LLM to extract topics from a single exam summary."""
    
    # Prompt designed to extract a newline-separated list of topics
    extraction_prompt = f"""Your task is to carefully analyze the following text, which is a summary of a past university exam paper or problem set for a specific course.

Identify and list the main topics, specific concepts, key definitions, types of calculations, or kinds of problems that were tested or covered in this exam according to the summary.

Focus on extracting distinct, actionable items that a student would need to study or practice. Be concise.

Output ONLY a list of these topics/items, with each distinct item on a new line. Do not include introductory phrases like "Here are the topics:" or bullet points. Just the list items, one per line.

Exam Summary Text:
---
{summary_content}
---

List of Topics/Items Tested:
"""

    try:
        response = model.generate_content(extraction_prompt)
        # Process the response: split into lines, strip whitespace, remove empty lines
        extracted_topics = [line.strip() for line in response.text.splitlines() if line.strip()]
        return extracted_topics
    except Exception as e:
        print(f"  Error calling generative model for topic extraction: {e}")
        # Optionally print the response object if available in the exception context
        return [] # Return empty list on error

# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract key topics from past exam summaries using an LLM.")
    parser.add_argument("--exam_dir", required=True, help="Path to the directory containing Markdown summaries of past exams.")
    parser.add_argument("--output_file", required=True, help="Path to the output file (e.g., exam_topics.json or exam_topics.txt).")
    args = parser.parse_args()

    # 1. Configure Google API
    configure_api()

    # 2. Initialize Generative Model
    try:
        print(f"Initializing generative model: {ANALYSIS_MODEL_NAME}")
        model = genai.GenerativeModel(ANALYSIS_MODEL_NAME)
        print("Generative model initialized.")
    except Exception as e:
        print(f"Error initializing generative model '{ANALYSIS_MODEL_NAME}': {e}")
        sys.exit(1)

    # 3. Topic Storage (using a set for automatic deduplication)
    unique_topics = set()
    
    # 4. Iterate Through Exam Summaries
    exam_dir_path = Path(args.exam_dir)
    if not exam_dir_path.is_dir():
        print(f"Error: Exam directory not found at '{args.exam_dir}'")
        sys.exit(1)

    print(f"Scanning for .md files in '{exam_dir_path}'...")
    md_files = list(exam_dir_path.rglob("*.md"))

    if not md_files:
        print("No .md files found in the specified exam directory.")
        sys.exit(0)

    print(f"Found {len(md_files)} exam summary files to process.")

    for md_file in md_files:
        print(f"\nProcessing exam summary: {md_file}...")
        try:
            # 5. Read Summary Content
            with open(md_file, 'r', encoding='utf-8') as f:
                summary_content = f.read()

            if not summary_content.strip():
                print("  Skipping empty file.")
                continue

            # 6. Call LLM for Topic Extraction & Parse Response
            extracted_topics = extract_topics_from_summary(model, summary_content)

            if extracted_topics:
                print(f"  Extracted {len(extracted_topics)} potential topics.")
                # Log the topics extracted from this specific file for review
                # print(f"    Topics: {extracted_topics}") 
                
                # 7. Store Unique Topics
                # The set automatically handles duplicates
                unique_topics.update(extracted_topics) 
            else:
                print("  No topics extracted or error occurred.")

        except Exception as e:
            print(f"  Error processing file {md_file}: {e}")
            # Continue to the next file

    # 8. Save Unique Topics
    if not unique_topics:
        print("\nNo topics were extracted from any files. Output file will not be created.")
        sys.exit(0)
        
    output_path = Path(args.output_file)
    # Ensure the output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True) 
    
    # Convert set to a sorted list for consistent output
    topic_list = sorted(list(unique_topics)) 
    print(f"\nTotal unique topics extracted: {len(topic_list)}")

    try:
        print(f"Saving unique topics to: {output_path}")
        file_extension = output_path.suffix.lower()

        if file_extension == '.json':
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(topic_list, f, indent=2) # Save as JSON array with indentation
        elif file_extension == '.txt':
            with open(output_path, 'w', encoding='utf-8') as f:
                for topic in topic_list:
                    f.write(topic + '\n') # Save each topic on a new line
        else:
            print(f"Warning: Unknown output file extension '{file_extension}'. Saving as plain text (.txt format).")
            with open(output_path, 'w', encoding='utf-8') as f:
                for topic in topic_list:
                    f.write(topic + '\n')
                    
        print("Successfully saved topics.")

    except Exception as e:
        print(f"Error saving output file to '{output_path}': {e}")
        sys.exit(1)

    print("\nScript finished.") 