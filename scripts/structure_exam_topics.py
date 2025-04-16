import google.generativeai as genai
import os
import sys
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv
import time
import re # Added for parsing

# --- Configuration ---
DEFAULT_INPUT_TOPICS_FILE = "exam_topics.json"
DEFAULT_OUTPUT_TOPICS_FILE = "exam_topics_structured.json"
GENERATIVE_MODEL_NAME = 'gemini-1.5-pro-latest' # Use Pro for reasoning tasks

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

def load_topics(topics_file):
    """Loads exam topics from a JSON file."""
    try:
        with open(topics_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list): topics = data
            elif isinstance(data, dict) and 'topics' in data and isinstance(data['topics'], list): topics = data['topics']
            else:
                print(f"Error: Expected JSON file '{topics_file}' to contain a list of topics or a dict with 'topics' key.")
                sys.exit(1)
            if not topics: print(f"Warning: No topics found in '{topics_file}'."); return []
            print(f"Loaded {len(topics)} topics from '{topics_file}'.")
            return topics
    except FileNotFoundError: print(f"Error: Topics file not found at '{topics_file}'"); sys.exit(1)
    except json.JSONDecodeError: print(f"Error: Could not decode JSON from '{topics_file}'."); sys.exit(1)
    except Exception as e: print(f"An unexpected error occurred loading topics: {e}"); sys.exit(1)

def construct_structuring_prompt(topic_list):
    """Constructs the prompt to ask the LLM to structure the topics."""

    # Format the list nicely for the prompt
    formatted_topic_list = "\n".join(f"- {topic}" for topic in topic_list)

    prompt = f"""You are an expert curriculum designer tasked with structuring a list of technical exam topics for optimal learning.

Analyze the following list of exam topics:
\"\"\"
{formatted_topic_list}
\"\"\"

Instructions:
1.  **Identify Dependencies:** Determine if some topics are prerequisites for others.
2.  **Estimate Difficulty:** Roughly categorize each topic's conceptual difficulty (e.g., Foundational, Intermediate, Advanced).
3.  **Determine Logical Flow:** Based on dependencies and difficulty, determine the most logical order to learn these topics, starting with foundational concepts and building up.
4.  **Output Format:** Respond ONLY with a valid JSON list containing the original topic strings, reordered according to the logical flow you determined. Do not include the difficulty categories or any other commentary in the final JSON output, just the ordered list of topic strings.

Example Input Topics:
- Calculus I Basics
- Introduction to Differential Equations
- Advanced Multivariable Calculus

Example JSON Output:
```json
[
  "Calculus I Basics",
  "Advanced Multivariable Calculus",
  "Introduction to Differential Equations"
]
```

Provide the reordered JSON list below:
"""
    return prompt

def structure_topics_with_llm(prompt):
    """Sends the structuring prompt to the LLM and attempts to parse the JSON response."""
    try:
        print(f"Sending topics to {GENERATIVE_MODEL_NAME} for structuring...")
        model = genai.GenerativeModel(GENERATIVE_MODEL_NAME)
        response = model.generate_content(
            prompt,
            # Relax safety slightly for potentially complex topic interactions, but be mindful
             safety_settings={
                 'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_LOW_AND_ABOVE',
                 'HARM_CATEGORY_HARASSMENT': 'BLOCK_LOW_AND_ABOVE',
                 'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_LOW_AND_ABOVE',
                 'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_LOW_AND_ABOVE',
             }
            )
        print("Received response from LLM.")

        # --- Attempt to parse JSON from the response ---
        raw_text = ""
        # Standard text extraction
        if hasattr(response, 'text'): raw_text = response.text
        elif hasattr(response, 'parts') and response.parts: raw_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
        elif response.candidates and hasattr(response.candidates[0].content, 'parts') and response.candidates[0].content.parts: raw_text = "".join(part.text for part in response.candidates[0].content.parts if hasattr(part, 'text'))
        else: print("Warning: Could not extract text from LLM response."); return None

        # Clean the text: Find the JSON block (often enclosed in ```json ... ```)
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', raw_text, re.DOTALL)
        if json_match:
            json_string = json_match.group(1).strip()
        else:
            # If no ```json block, assume the whole text might be the JSON (or try finding '['/'{')
            json_string = raw_text.strip()
            # Basic cleanup if it's not just the JSON list
            first_bracket = json_string.find('[')
            last_bracket = json_string.rfind(']')
            if first_bracket != -1 and last_bracket != -1:
                 json_string = json_string[first_bracket : last_bracket + 1]


        print("Attempting to parse JSON from LLM response...")
        parsed_json = json.loads(json_string)

        if isinstance(parsed_json, list):
            print(f"Successfully parsed reordered list of {len(parsed_json)} topics.")
            return parsed_json
        else:
            print("Error: LLM response parsed, but it was not a JSON list.")
            print(f"Parsed Data: {parsed_json}")
            return None

    except json.JSONDecodeError as json_err:
        print(f"Error: Could not decode JSON from LLM response: {json_err}")
        print("--- LLM Raw Response Text ---")
        print(raw_text)
        print("--- End LLM Raw Response ---")
        return None
    except Exception as e:
        print(f"Error during LLM structuring: {e}")
        if 'response' in locals() and response and response.prompt_feedback:
             print(f"Prompt Feedback: {response.prompt_feedback}")
        return None


def save_topics(topic_list, output_file):
    """Saves the list of topics to a JSON file."""
    try:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            # Save as a simple JSON list
            json.dump(topic_list, f, indent=2, ensure_ascii=False)
        print(f"Successfully saved structured topics to: {output_path}")
    except Exception as e:
        print(f"Error saving structured topics to '{output_file}': {e}")


# --- Main Execution Entry Point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Use an LLM to structure exam topics based on difficulty and logical flow.")
    parser.add_argument("--input_file", default=DEFAULT_INPUT_TOPICS_FILE, help=f"Path to the input JSON file containing exam topics (default: {DEFAULT_INPUT_TOPICS_FILE}).")
    parser.add_argument("--output_file", default=DEFAULT_OUTPUT_TOPICS_FILE, help=f"Path to save the structured topics JSON file (default: {DEFAULT_OUTPUT_TOPICS_FILE}).")

    args = parser.parse_args()

    # 1. Configure API
    configure_api()

    # 2. Load Topics
    original_topics = load_topics(args.input_file)
    if not original_topics:
        print("Exiting as no topics were loaded.")
        sys.exit(0)

    # 3. Construct Prompt
    prompt = construct_structuring_prompt(original_topics)

    # 4. Get Structured Topics from LLM
    structured_topics = structure_topics_with_llm(prompt)

    # 5. Save Results
    if structured_topics:
        # Basic validation: Check if the number of topics matches
        if len(structured_topics) != len(original_topics):
            print(f"Warning: Number of topics in structured list ({len(structured_topics)}) does not match original ({len(original_topics)}). Saving anyway.")
        # Could add check to ensure all original topics are present if needed
        save_topics(structured_topics, args.output_file)
    else:
        print("Could not obtain structured topics from the LLM. No output file saved.")
        sys.exit(1)

    print("\nScript finished.") 