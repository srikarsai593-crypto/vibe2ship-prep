import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def breakdown_agent(user_panic_text: str) -> str:
    """
    11:00 AM Blueprint Item: Extracts tasks and targets deadlines 
    forcing a structured JSON format response.
    """
    system_prompt = """
    You are 'The Last-Minute Life Saver' extraction engine. Take the user's messy text input and break it into individual actionable items.
    You MUST return your response as a valid JSON object matching this structure:
    {
       "tasks": [
          {"task_name": "Clear task details", "eta_minutes": 45}
       ]
    }
    """
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_panic_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
                response_mime_type="application/json"
            )
        )
        return response.text
    except Exception as e:
        print(f"Breakdown agent warning handler triggered: {e}")
        return '{"tasks": []}'

def priority_orchestrator(raw_tasks_json: str) -> str:
    """
    2:30 PM Blueprint Item: Takes extracted tasks and routes them 
    through an Eisenhower Urgency Matrix with color classifications.
    """
    system_prompt = """
    You are the Priority Orchestrator. Analyze the provided list of tasks and assign each one an explicit priority ranking.
    You MUST sort them from most urgent to least urgent and categorize them into: 'Critical', 'Important', or 'Flexible'.
    Return a clean JSON object structure matching this:
    {
       "prioritized_tasks": [
          {"task_name": "string", "eta_minutes": 45, "priority_tier": "Critical/Important/Flexible"}
       ]
    }
    """
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Prioritize these tasks: {raw_tasks_json}",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
                response_mime_type="application/json"
            )
        )
        return response.text
    except Exception as e:
        print(f"Priority orchestrator warning handler triggered: {e}")
        return raw_tasks_json